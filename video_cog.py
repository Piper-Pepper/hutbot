import asyncio
import base64
import contextlib
import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("venice_video_cog")

VENICE_API_KEY = os.getenv("VENICE_API_KEY")
VENICE_VIDEO_QUEUE_URL = os.getenv("VENICE_VIDEO_QUEUE_URL")
VENICE_VIDEO_RETRIEVE_URL = os.getenv("VENICE_VIDEO_RETRIEVE_URL")
VENICE_VIDEO_I2V_MODEL = os.getenv("VENICE_VIDEO_I2V_MODEL", "wan-2-7-image-to-video")
VENICE_VIDEO_RESOLUTION = os.getenv("VENICE_VIDEO_RESOLUTION", "720p")

VIDEO_ALLOWED_DURATIONS = [10, 15, 20, 25, 30, 40]

VIDEO_POLL_SECONDS = 6
VIDEO_HARD_TIMEOUT_SECONDS = 1800
VIDEO_ADAPTIVE_TIMEOUT_SECONDS = 720
VIDEO_MAX_CONSECUTIVE_5XX = 8
VIDEO_5XX_WINDOW_SECONDS = 180

# Tier rules (same as image cog)
TIER_RULES: dict[int, dict[str, int]] = {
    1: {"role_id": 1377051179615522926, "level": 4, "image_limit": 5, "video_max_seconds": 10},
    2: {"role_id": 1375147276413964408, "level": 11, "image_limit": 10, "video_max_seconds": 15},
    3: {"role_id": 1376592697606930593, "level": 21, "image_limit": 15, "video_max_seconds": 20},
    4: {"role_id": 1381791848875430069, "level": 33, "image_limit": 20, "video_max_seconds": 25},
    5: {"role_id": 1375666588404940830, "level": 43, "image_limit": 30, "video_max_seconds": 30},
    6: {"role_id": 1375584380914896978, "level": 69, "image_limit": 69, "video_max_seconds": 40},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _looks_like_image(binary: bytes) -> bool:
    if not binary or len(binary) < 12:
        return False
    return (
        binary.startswith(b"\x89PNG\r\n\x1a\n")
        or binary.startswith(b"\xff\xd8\xff")
        or (binary[:4] == b"RIFF" and binary[8:12] == b"WEBP")
        or binary.startswith((b"GIF87a", b"GIF89a"))
    )


def _looks_like_video(binary: bytes) -> bool:
    return bool(binary and len(binary) >= 12 and binary[4:8] == b"ftyp")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _trim(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + " [...]"


def _codeblock_safe(text: str) -> str:
    return (text or "").replace("```", "'''").strip()


def _progress_bar(percent: int, blocks: int = 14) -> str:
    p = max(0, min(100, percent))
    filled = int(blocks * p / 100)
    return "█" * filled + "░" * (blocks - filled)


def _extract_queue_id(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for key in ("queue_id", "id"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v
    nested = payload.get("data")
    if isinstance(nested, dict):
        for key in ("queue_id", "id"):
            v = nested.get(key)
            if isinstance(v, str) and v:
                return v
    return None


def _extract_urls_from_payload(payload: Any) -> list[str]:
    urls: list[str] = []
    if not isinstance(payload, (dict, list)):
        return urls

    interesting = {"download_url", "url", "result_url", "video_url", "file_url", "asset_url"}

    def walk(obj: Any):
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = str(k).lower()
                if isinstance(v, str) and v.startswith("http"):
                    if lk in interesting or "url" in lk or "download" in lk:
                        urls.append(v)
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(payload)
    return list(dict.fromkeys(urls))


def _tier_sorted_desc() -> list[tuple[int, dict[str, int]]]:
    return sorted(TIER_RULES.items(), key=lambda x: x[0], reverse=True)


def _tier_sorted_asc() -> list[tuple[int, dict[str, int]]]:
    return sorted(TIER_RULES.items(), key=lambda x: x[0])


def get_member_tier(member: Optional[discord.Member]) -> int:
    if not isinstance(member, discord.Member):
        return 0
    role_ids = {r.id for r in member.roles}
    for tier, cfg in _tier_sorted_desc():
        if cfg["role_id"] in role_ids:
            return tier
    return 0


def get_video_max_seconds_for_member(member: Optional[discord.Member]) -> int:
    tier = get_member_tier(member)
    if tier <= 0:
        return 0
    return int(TIER_RULES[tier]["video_max_seconds"])


def get_next_tier_info(current_tier: int) -> Optional[tuple[int, dict[str, int]]]:
    for tier, cfg in _tier_sorted_asc():
        if tier > current_tier:
            return tier, cfg
    return None


def _video_tier_compact_line() -> str:
    parts = [f"T{tier}:{cfg['video_max_seconds']}s" for tier, cfg in _tier_sorted_asc()]
    return " • ".join(parts)


def build_video_lock_text(member: Optional[discord.Member], requested_seconds: Optional[int] = None) -> str:
    tier = get_member_tier(member)
    max_sec = get_video_max_seconds_for_member(member)
    next_info = get_next_tier_info(tier)

    if max_sec <= 0:
        return (
            "🎬 Video rendering is locked for members without a Tier role.\n"
            f"Unlock Tier 1: <@&{TIER_RULES[1]['role_id']}> (Level {TIER_RULES[1]['level']}) "
            f"to render up to **{TIER_RULES[1]['video_max_seconds']}s**.\n"
            f"Video limits: `{_video_tier_compact_line()}`\n"
            "Keep earning XP in **Goon Hut** to unlock video renders."
        )

    if requested_seconds is not None and requested_seconds > max_sec:
        lines = [
            f"🎬 Your current max video duration is **{max_sec}s** (Tier {tier}).",
            f"Requested: **{requested_seconds}s**.",
        ]
        if next_info:
            nt, cfg = next_info
            lines.append(
                f"🚀 Next unlock: **Tier {nt}** (<@&{cfg['role_id']}>, Level {cfg['level']}) → **{cfg['video_max_seconds']}s**."
            )
        else:
            lines.append("🏆 You already have the highest video tier.")
        lines.append(f"Video limits: `{_video_tier_compact_line()}`")
        return "\n".join(lines)

    return ""


class VeniceVideoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.session_lock = asyncio.Lock()

        self.global_busy = False
        self.global_busy_lock = asyncio.Lock()

    async def _ensure_session(self):
        async with self.session_lock:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=120),
                    connector=aiohttp.TCPConnector(limit=40, ttl_dns_cache=300),
                )

    async def cog_load(self):
        await self._ensure_session()

    def cog_unload(self):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    async def _ephemeral(self, interaction: discord.Interaction, content: str):
        with contextlib.suppress(Exception):
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)

    async def _try_begin_global(self) -> bool:
        async with self.global_busy_lock:
            if self.global_busy:
                return False
            self.global_busy = True
            return True

    async def _end_global(self):
        async with self.global_busy_lock:
            self.global_busy = False

    def _build_progress_embed(
        self,
        user: discord.abc.User,
        prompt: str,
        aspect: str,
        seconds: int,
        percent: int,
        elapsed_sec: int,
        stage_text: str,
    ) -> discord.Embed:
        bar = _progress_bar(percent)
        preview = _codeblock_safe(_trim(prompt, 420))
        embed = discord.Embed(
            title="🎬 AI Video Render",
            description=f"{user.mention} • `{VENICE_VIDEO_I2V_MODEL}`",
            color=discord.Color.blurple(),
            timestamp=utc_now(),
        )
        embed.add_field(name="Prompt", value=f"```{preview}```", inline=False)
        embed.add_field(name="Progress", value=f"`{bar} {percent}%`", inline=False)
        embed.add_field(
            name="Settings",
            value=f"• Aspect: `{aspect}`\n• Duration: `{seconds}s`\n• Resolution: `{VENICE_VIDEO_RESOLUTION}`",
            inline=False
        )
        embed.add_field(name="Timing", value=f"• Elapsed: `{elapsed_sec}s`\n• Status: {stage_text}", inline=False)
        embed.set_footer(text="Image → Video")
        return embed

    def _build_result_embed(
        self,
        user: discord.abc.User,
        prompt: str,
        aspect: str,
        seconds: int,
    ) -> discord.Embed:
        preview = _codeblock_safe(_trim(prompt, 900))
        embed = discord.Embed(
            title="✅ Video Ready",
            description=f"{user.mention} your animation is complete.",
            color=discord.Color.green(),
            timestamp=utc_now(),
        )
        embed.add_field(name="Prompt", value=f"```{preview}```", inline=False)
        embed.add_field(
            name="Settings",
            value=f"• Model: `{VENICE_VIDEO_I2V_MODEL}`\n• Aspect: `{aspect}`\n• Duration: `{seconds}s`\n• Resolution: `{VENICE_VIDEO_RESOLUTION}`",
            inline=False
        )
        embed.set_footer(text="Image → Video")
        return embed

    def _build_error_embed(
        self,
        user: discord.abc.User,
        prompt: str,
        aspect: str,
        seconds: int,
        reason: str,
    ) -> discord.Embed:
        preview = _codeblock_safe(_trim(prompt, 420))
        rs = _trim(reason or "Video generation failed.", 450)
        embed = discord.Embed(
            title="❌ Video Render Failed",
            description=f"{user.mention} your animation could not be completed.",
            color=discord.Color.red(),
            timestamp=utc_now(),
        )
        embed.add_field(name="Reason", value=rs, inline=False)
        embed.add_field(name="Prompt", value=f"```{preview}```", inline=False)
        embed.add_field(
            name="Settings",
            value=f"• Model: `{VENICE_VIDEO_I2V_MODEL}`\n• Aspect: `{aspect}`\n• Duration: `{seconds}s`\n• Resolution: `{VENICE_VIDEO_RESOLUTION}`",
            inline=False
        )
        embed.set_footer(text="Image → Video")
        return embed

    async def _safe_edit_progress(self, message: Optional[discord.Message], embed: discord.Embed):
        if not message:
            return
        with contextlib.suppress(Exception):
            await message.edit(embed=embed)

    async def _safe_delete_message(self, message: Optional[discord.Message]):
        if not message:
            return
        with contextlib.suppress(Exception):
            await message.delete()

    async def _fetch_media_from_url(self, url: str, headers: dict[str, str], visited: Optional[set[str]] = None):
        if not isinstance(url, str) or not url.startswith("http"):
            return None, None
        if visited is None:
            visited = set()
        if url in visited:
            return None, None
        visited.add(url)

        await self._ensure_session()
        assert self.session is not None
        timeout = aiohttp.ClientTimeout(total=45, connect=12, sock_read=35)

        for use_auth in (True, False):
            try:
                req_headers = dict(headers) if use_auth else {}
                async with self.session.get(url, headers=req_headers, timeout=timeout) as resp:
                    body = await resp.read()
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if resp.status >= 400 or not body:
                        continue

                    if "video" in ctype or _looks_like_video(body):
                        return body, "video"
                    if "image" in ctype or _looks_like_image(body):
                        return body, "image"

                    if "json" in ctype:
                        try:
                            nested = json.loads(body.decode("utf-8", errors="ignore"))
                        except Exception:
                            nested = None
                        if nested:
                            for nested_url in _extract_urls_from_payload(nested):
                                data, kind = await self._fetch_media_from_url(nested_url, headers, visited)
                                if data:
                                    return data, kind
            except Exception:
                continue

        return None, None

    async def _queue_i2v(
        self,
        image_bytes: bytes,
        prompt: str,
        aspect: str,
        seconds: int,
    ) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
        if not VENICE_VIDEO_QUEUE_URL:
            return None, None, "VENICE_VIDEO_QUEUE_URL is missing."
        if not VENICE_API_KEY:
            return None, None, "VENICE_API_KEY is missing."
        if not _looks_like_image(image_bytes):
            return None, None, "Invalid source image."

        await self._ensure_session()
        assert self.session is not None

        headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        payload_variants = [
            {
                "model": VENICE_VIDEO_I2V_MODEL,
                "prompt": prompt,
                "resolution": VENICE_VIDEO_RESOLUTION,
                "aspect_ratio": aspect,
                "duration": f"{seconds}s",
                "image": b64,
            },
            {
                "model": VENICE_VIDEO_I2V_MODEL,
                "prompt": prompt,
                "resolution": VENICE_VIDEO_RESOLUTION,
                "aspect_ratio": aspect,
                "duration": f"{seconds}s",
                "image_base64": b64,
            },
            {
                "model": VENICE_VIDEO_I2V_MODEL,
                "prompt": prompt,
                "resolution": VENICE_VIDEO_RESOLUTION,
                "aspect_ratio": aspect,
                "duration_seconds": seconds,
                "input_image": b64,
            },
            {
                "model": VENICE_VIDEO_I2V_MODEL,
                "prompt": prompt,
                "resolution": VENICE_VIDEO_RESOLUTION,
                "aspect_ratio": aspect,
                "duration": f"{seconds}s",
                "image": f"data:image/png;base64,{b64}",
            },
        ]

        timeout = aiohttp.ClientTimeout(total=35, connect=10, sock_read=30)
        last_error = "Queue request failed."

        for attempt in range(3):
            for payload in payload_variants:
                try:
                    async with self.session.post(VENICE_VIDEO_QUEUE_URL, headers=headers, json=payload, timeout=timeout) as resp:
                        text = await resp.text()
                        try:
                            data = json.loads(text) if text else {}
                        except Exception:
                            data = {"raw": text}

                        if resp.status >= 400:
                            last_error = f"Queue error ({resp.status}): {text[:250]}"
                            if resp.status in (401, 403, 422):
                                return None, data, last_error
                            continue

                        queue_id = _extract_queue_id(data)
                        if queue_id:
                            return queue_id, data, None

                        last_error = "Queue response did not include queue_id."
                except Exception as e:
                    last_error = f"Queue request error: {e}"
                    continue

            await asyncio.sleep(1.2 * (attempt + 1))

        return None, None, last_error

    async def _wait_for_result(
        self,
        queue_id: str,
        progress_message: Optional[discord.Message],
        user: discord.abc.User,
        prompt: str,
        aspect: str,
        seconds: int,
        queue_download_url: Optional[str] = None,
    ) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
        if not VENICE_VIDEO_RETRIEVE_URL:
            return None, None, "VENICE_VIDEO_RETRIEVE_URL is missing."
        if not VENICE_API_KEY:
            return None, None, "VENICE_API_KEY is missing."

        await self._ensure_session()
        assert self.session is not None

        headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
        started = utc_now()
        hard_deadline = started + timedelta(seconds=VIDEO_HARD_TIMEOUT_SECONDS)
        adaptive_deadline = started + timedelta(seconds=VIDEO_ADAPTIVE_TIMEOUT_SECONDS)

        consecutive_5xx = 0
        total_5xx = 0
        first_5xx_at: Optional[datetime] = None
        finalize_attempts = 0
        last_percent = 8

        timeout = aiohttp.ClientTimeout(total=90, connect=15, sock_read=70)

        while True:
            now = utc_now()
            if now >= hard_deadline or now >= adaptive_deadline:
                break

            await asyncio.sleep(VIDEO_POLL_SECONDS)
            elapsed_sec = int((utc_now() - started).total_seconds())

            try:
                async with self.session.post(
                    VENICE_VIDEO_RETRIEVE_URL,
                    headers=headers,
                    json={"model": VENICE_VIDEO_I2V_MODEL, "queue_id": queue_id},
                    timeout=timeout,
                ) as response:
                    ctype = (response.headers.get("content-type") or "").lower()

                    if response.status >= 400:
                        body_text = await response.text()

                        if response.status >= 500:
                            total_5xx += 1
                            consecutive_5xx += 1
                            if first_5xx_at is None:
                                first_5xx_at = utc_now()

                            p = max(last_percent, 12)
                            await self._safe_edit_progress(
                                progress_message,
                                self._build_progress_embed(
                                    user=user,
                                    prompt=prompt,
                                    aspect=aspect,
                                    seconds=seconds,
                                    percent=p,
                                    elapsed_sec=elapsed_sec,
                                    stage_text=f"Provider error {response.status} (retry {total_5xx})..."
                                )
                            )
                            last_percent = p

                            too_many = consecutive_5xx >= VIDEO_MAX_CONSECUTIVE_5XX
                            too_long = first_5xx_at and ((utc_now() - first_5xx_at).total_seconds() >= VIDEO_5XX_WINDOW_SECONDS)
                            if too_many or too_long:
                                return None, None, "Provider unavailable (repeated 5xx errors)."
                            continue

                        consecutive_5xx = 0
                        first_5xx_at = None

                        if response.status in (401, 403):
                            return None, None, "API authentication failed (401/403)."
                        if response.status == 422:
                            return None, None, "Request rejected by provider (422)."
                        continue

                    consecutive_5xx = 0
                    first_5xx_at = None

                    if "video" in ctype:
                        blob = await response.read()
                        if _looks_like_video(blob):
                            return blob, "video", None

                    if "image" in ctype:
                        blob = await response.read()
                        if _looks_like_image(blob):
                            return blob, "image", None

                    raw = await response.text()
                    try:
                        data = json.loads(raw) if raw else {}
                    except Exception:
                        continue

                    status = str(data.get("status", "")).lower()
                    avg_ms = _safe_int(data.get("average_execution_time", 180000), 180000)
                    exec_ms = _safe_int(data.get("execution_duration", 0), 0)
                    if exec_ms <= 0:
                        exec_ms = elapsed_sec * 1000

                    expected_total_sec = int((max(avg_ms, 60000) / 1000) * 2.5) + 120
                    candidate_deadline = started + timedelta(seconds=expected_total_sec)
                    if candidate_deadline > adaptive_deadline:
                        adaptive_deadline = min(candidate_deadline, hard_deadline)

                    if status in {"failed", "error", "cancelled", "canceled"}:
                        err = data.get("error")
                        if isinstance(err, dict):
                            msg = err.get("message") or "Rendering aborted."
                        elif isinstance(err, str):
                            msg = err
                        else:
                            msg = data.get("message") or "Rendering aborted."
                        return None, None, f"Rendering aborted: {msg}"

                    if status == "completed":
                        candidate_urls: list[str] = []
                        if isinstance(queue_download_url, str) and queue_download_url.startswith("http"):
                            candidate_urls.append(queue_download_url)
                        candidate_urls.extend(_extract_urls_from_payload(data))
                        candidate_urls = list(dict.fromkeys(candidate_urls))

                        for media_url in candidate_urls:
                            media_data, media_type = await self._fetch_media_from_url(media_url, headers)
                            if media_data:
                                return media_data, media_type, None

                        finalize_attempts += 1
                        p = max(last_percent, 98)
                        await self._safe_edit_progress(
                            progress_message,
                            self._build_progress_embed(
                                user=user,
                                prompt=prompt,
                                aspect=aspect,
                                seconds=seconds,
                                percent=p,
                                elapsed_sec=elapsed_sec,
                                stage_text="Finalizing file delivery..."
                            )
                        )
                        last_percent = p

                        if finalize_attempts >= 40:
                            return None, None, "Rendering finished, but no deliverable file was returned."
                        continue

                    target_ms = max(avg_ms, 120000)
                    raw_ratio = exec_ms / max(target_ms, 1)
                    percent = min(97, max(8, int(raw_ratio * 100)))
                    if percent < last_percent:
                        percent = last_percent

                    if percent != last_percent:
                        await self._safe_edit_progress(
                            progress_message,
                            self._build_progress_embed(
                                user=user,
                                prompt=prompt,
                                aspect=aspect,
                                seconds=seconds,
                                percent=percent,
                                elapsed_sec=elapsed_sec,
                                stage_text="Rendering..."
                            )
                        )
                        last_percent = percent

            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

        return None, None, "Generation timed out."

    async def animate_image_to_video(
        self,
        interaction: discord.Interaction,
        image_bytes: bytes,
        prompt: str,
        aspect: str,
        seconds: int,
        target_channel: discord.abc.Messageable,
    ) -> bool:
        if not VENICE_API_KEY:
            await self._ephemeral(interaction, "❌ VENICE_API_KEY is missing.")
            return False
        if not VENICE_VIDEO_QUEUE_URL or not VENICE_VIDEO_RETRIEVE_URL:
            await self._ephemeral(interaction, "❌ Video API endpoints are missing in .env.")
            return False
        if not _looks_like_image(image_bytes):
            await self._ephemeral(interaction, "❌ Invalid source image.")
            return False

        if seconds not in VIDEO_ALLOWED_DURATIONS:
            await self._ephemeral(interaction, f"❌ Invalid duration. Allowed values: {', '.join(map(str, VIDEO_ALLOWED_DURATIONS))}.")
            return False

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        max_sec = get_video_max_seconds_for_member(member)

        if max_sec <= 0:
            await self._ephemeral(interaction, build_video_lock_text(member))
            return False

        if seconds > max_sec:
            await self._ephemeral(interaction, build_video_lock_text(member, requested_seconds=seconds))
            return False

        acquired = await self._try_begin_global()
        if not acquired:
            await self._ephemeral(interaction, "⏳ A video render is already running. Please wait.")
            return False

        if aspect not in {"1:1", "16:9", "9:16", "21:9", "3:2", "2:3", "3:4", "4:5"}:
            aspect = "16:9"

        progress_message: Optional[discord.Message] = None
        queue_id: Optional[str] = None

        try:
            progress_embed = self._build_progress_embed(
                user=interaction.user,
                prompt=prompt,
                aspect=aspect,
                seconds=seconds,
                percent=5,
                elapsed_sec=0,
                stage_text="Sending queue request..."
            )
            progress_message = await target_channel.send(embed=progress_embed)

            queue_id, queue_response, queue_error = await self._queue_i2v(
                image_bytes=image_bytes,
                prompt=prompt,
                aspect=aspect,
                seconds=seconds,
            )

            if not queue_id:
                await target_channel.send(
                    embed=self._build_error_embed(
                        user=interaction.user,
                        prompt=prompt,
                        aspect=aspect,
                        seconds=seconds,
                        reason=queue_error or "Queue failed."
                    )
                )
                await self._ephemeral(interaction, f"❌ Animation failed: {queue_error or 'Queue failed.'}")
                return False

            queue_download_url = None
            if isinstance(queue_response, dict):
                qdu = queue_response.get("download_url")
                if isinstance(qdu, str):
                    queue_download_url = qdu

            await self._safe_edit_progress(
                progress_message,
                self._build_progress_embed(
                    user=interaction.user,
                    prompt=prompt,
                    aspect=aspect,
                    seconds=seconds,
                    percent=8,
                    elapsed_sec=1,
                    stage_text="Queue accepted. Rendering started."
                )
            )

            media_data, media_type, error_message = await self._wait_for_result(
                queue_id=queue_id,
                progress_message=progress_message,
                user=interaction.user,
                prompt=prompt,
                aspect=aspect,
                seconds=seconds,
                queue_download_url=queue_download_url
            )

            if not media_data:
                await target_channel.send(
                    embed=self._build_error_embed(
                        user=interaction.user,
                        prompt=prompt,
                        aspect=aspect,
                        seconds=seconds,
                        reason=error_message or "Generation failed or timed out."
                    )
                )
                await self._ephemeral(interaction, f"❌ Animation failed: {error_message or 'Unknown error'}")
                return False

            if media_type != "video":
                await target_channel.send(
                    embed=self._build_error_embed(
                        user=interaction.user,
                        prompt=prompt,
                        aspect=aspect,
                        seconds=seconds,
                        reason="Provider returned non-video output."
                    )
                )
                await self._ephemeral(interaction, "❌ Provider returned non-video output.")
                return False

            guild_limit = None
            if getattr(target_channel, "guild", None):
                guild_limit = getattr(target_channel.guild, "filesize_limit", None)
            if guild_limit and len(media_data) > guild_limit:
                await target_channel.send(
                    embed=self._build_error_embed(
                        user=interaction.user,
                        prompt=prompt,
                        aspect=aspect,
                        seconds=seconds,
                        reason="Video is larger than Discord upload limit for this server."
                    )
                )
                await self._ephemeral(interaction, "❌ Video too large for Discord upload limit.")
                return False

            file = discord.File(io.BytesIO(media_data), filename="AI_video.mp4")
            result_embed = self._build_result_embed(
                user=interaction.user,
                prompt=prompt,
                aspect=aspect,
                seconds=seconds,
            )
            await target_channel.send(
                content=interaction.user.mention,
                embed=result_embed,
                file=file,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            await self._ephemeral(interaction, "✅ Animation completed and posted in this image channel.")
            return True

        except discord.Forbidden:
            await self._ephemeral(interaction, "❌ Missing Discord permissions to post video.")
            return False
        except Exception as e:
            logger.exception("animate_image_to_video failed: %s", e)
            await self._ephemeral(interaction, f"❌ Animation failed: {e}")
            return False
        finally:
            await self._safe_delete_message(progress_message)
            await self._end_global()


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceVideoCog(bot))