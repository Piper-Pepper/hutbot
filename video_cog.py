# video_cog.py
import asyncio
import contextlib
import io
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
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

MAX_VIDEO_RENDER_SECONDS = 15
VIDEO_CHOICES = [5, 10, 15]

VIDEO_POLL_SECONDS = 6
VIDEO_HARD_TIMEOUT_SECONDS = 1800
VIDEO_ADAPTIVE_TIMEOUT_SECONDS = 720
VIDEO_MAX_CONSECUTIVE_5XX = 8
VIDEO_5XX_WINDOW_SECONDS = 180

TIER_RULES: dict[int, dict[str, int]] = {
    1: {"role_id": 1377051179615522926, "level": 4, "video_budget_sec": 10},
    2: {"role_id": 1375147276413964408, "level": 11, "video_budget_sec": 15},
    3: {"role_id": 1376592697606930593, "level": 21, "video_budget_sec": 20},
    4: {"role_id": 1381791848875430069, "level": 33, "video_budget_sec": 25},
    5: {"role_id": 1375666588404940830, "level": 43, "video_budget_sec": 30},
    6: {"role_id": 1375584380914896978, "level": 69, "video_budget_sec": 40},
    7: {"role_id": 1346414581643219029, "level": 99, "video_budget_sec": 300},
}
DEFAULT_VIDEO_BUDGET_SEC = 0

VIDEO_QUOTA_FILE = os.getenv("VIDEO_QUOTA_FILE", "goonhut_video_quota.json")
VIDEO_WINDOW_SECONDS = 24 * 60 * 60


class RollingQuotaStore:
    def __init__(self, file_path: str, window_seconds: int = VIDEO_WINDOW_SECONDS):
        self.file_path = Path(file_path)
        self.window_seconds = int(window_seconds)
        self.lock = asyncio.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {}
        try:
            raw = self.file_path.read_text(encoding="utf-8")
            obj = json.loads(raw) if raw else {}
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _write(self, obj: dict[str, Any]):
        if self.file_path.parent and str(self.file_path.parent) != ".":
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def _key(self, guild_id: int, user_id: int) -> str:
        return f"{guild_id}:{user_id}"

    def _normalize(self, entry: dict[str, Any], now_ts: int) -> dict[str, int]:
        start = int(entry.get("start", 0) or 0)
        used = int(entry.get("used", 0) or 0)
        if start > 0 and (now_ts - start) >= self.window_seconds:
            start = 0
            used = 0
        if used < 0:
            used = 0
        return {"start": start, "used": used}

    async def peek(self, guild_id: int, user_id: int, limit: int) -> dict[str, int]:
        now_ts = int(time.time())
        limit = max(0, int(limit))

        async with self.lock:
            db = self._read()
            key = self._key(guild_id, user_id)
            entry = self._normalize(db.get(key, {}), now_ts)
            db[key] = entry
            self._write(db)

            used = entry["used"]
            start = entry["start"]
            remaining = max(0, limit - used)
            reset_in = max(0, self.window_seconds - (now_ts - start)) if start > 0 else 0
            return {"used": used, "limit": limit, "remaining": remaining, "start": start, "reset_in": reset_in}

    async def reserve(self, guild_id: int, user_id: int, limit: int, amount: int) -> tuple[bool, dict[str, int], Optional[dict[str, int]]]:
        now_ts = int(time.time())
        limit = max(0, int(limit))
        amount = max(0, int(amount))

        async with self.lock:
            db = self._read()
            key = self._key(guild_id, user_id)
            entry = self._normalize(db.get(key, {}), now_ts)

            used = entry["used"]
            start = entry["start"]

            if limit <= 0 or used + amount > limit:
                state = {
                    "used": used,
                    "limit": limit,
                    "remaining": max(0, limit - used),
                    "start": start,
                    "reset_in": max(0, self.window_seconds - (now_ts - start)) if start > 0 else 0,
                }
                db[key] = entry
                self._write(db)
                return False, state, None

            if start <= 0:
                start = now_ts
                entry["start"] = start

            entry["used"] = used + amount
            db[key] = entry
            self._write(db)

            used2 = entry["used"]
            state = {
                "used": used2,
                "limit": limit,
                "remaining": max(0, limit - used2),
                "start": start,
                "reset_in": max(0, self.window_seconds - (now_ts - start)),
            }
            token = {"guild_id": guild_id, "user_id": user_id, "amount": amount, "start": start}
            return True, state, token

    async def rollback(self, token: Optional[dict[str, int]]):
        if not token:
            return

        guild_id = int(token.get("guild_id", 0))
        user_id = int(token.get("user_id", 0))
        amount = int(token.get("amount", 0))
        token_start = int(token.get("start", 0))
        if guild_id <= 0 or user_id <= 0 or amount <= 0:
            return

        now_ts = int(time.time())
        async with self.lock:
            db = self._read()
            key = self._key(guild_id, user_id)
            entry = self._normalize(db.get(key, {}), now_ts)

            cur_start = entry["start"]
            used = entry["used"]

            if cur_start == token_start and used > 0:
                used = max(0, used - amount)
                entry["used"] = used
                if used == 0:
                    entry["start"] = 0

            db[key] = entry
            self._write(db)


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


def _seconds_human(sec: int) -> str:
    sec = max(0, int(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


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


def _sanitize_error_text(text: str, limit: int = 300) -> str:
    t = (text or "").strip()
    t = re.sub(r"https?://\S+", "[link removed]", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit] if len(t) > limit else t


def _parse_retry_after_seconds(headers: "aiohttp.typedefs.LooseHeaders", text: str) -> int:
    retry_after = 0
    try:
        raw = headers.get("Retry-After")  # type: ignore[attr-defined]
        if raw is not None:
            retry_after = int(str(raw).strip())
    except Exception:
        retry_after = 0

    if retry_after <= 0:
        m = re.search(r"retry(?:\s+after)?\s*[:=]?\s*(\d+)", text or "", flags=re.IGNORECASE)
        if m:
            retry_after = int(m.group(1))

    return max(2, min(retry_after if retry_after > 0 else 20, 90))


class VeniceVideoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.session_lock = asyncio.Lock()

        self.global_busy = False
        self.global_busy_lock = asyncio.Lock()

        self._active_users: set[int] = set()
        self._active_users_lock = asyncio.Lock()

        self.video_quota = RollingQuotaStore(VIDEO_QUOTA_FILE)

    # -------------------------------------------------
    # lifecycle
    # -------------------------------------------------
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

    # -------------------------------------------------
    # locks
    # -------------------------------------------------
    async def _try_begin_global(self) -> bool:
        async with self.global_busy_lock:
            if self.global_busy:
                return False
            self.global_busy = True
            return True

    async def _end_global(self):
        async with self.global_busy_lock:
            self.global_busy = False

    async def _try_lock_user(self, user_id: int) -> bool:
        async with self._active_users_lock:
            if user_id in self._active_users:
                return False
            self._active_users.add(user_id)
            return True

    async def _unlock_user(self, user_id: int):
        async with self._active_users_lock:
            self._active_users.discard(user_id)

    # -------------------------------------------------
    # tier helpers
    # -------------------------------------------------
    def _tier_desc(self) -> list[tuple[int, dict[str, int]]]:
        return sorted(TIER_RULES.items(), key=lambda x: x[0], reverse=True)

    def _tier_asc(self) -> list[tuple[int, dict[str, int]]]:
        return sorted(TIER_RULES.items(), key=lambda x: x[0])

    def _video_tier_line(self) -> str:
        return " • ".join([f"T{t}:{cfg['video_budget_sec']}s" for t, cfg in self._tier_asc()])

    def _next_tier(self, current_tier: int) -> Optional[tuple[int, dict[str, int]]]:
        for tier, cfg in self._tier_asc():
            if tier > current_tier:
                return tier, cfg
        return None

    def get_member_tier(self, member: Optional[discord.Member]) -> int:
        if not isinstance(member, discord.Member):
            return 0
        role_ids = {r.id for r in member.roles}
        for tier, cfg in self._tier_desc():
            if cfg["role_id"] in role_ids:
                return tier
        return 0

    def get_budget_for_member(self, member: Optional[discord.Member]) -> int:
        tier = self.get_member_tier(member)
        if tier <= 0:
            return DEFAULT_VIDEO_BUDGET_SEC
        return int(TIER_RULES[tier]["video_budget_sec"])

    async def get_remaining_info(self, guild_id: int, member: discord.Member) -> dict[str, int]:
        tier = self.get_member_tier(member)
        budget = self.get_budget_for_member(member)
        state = await self.video_quota.peek(guild_id, member.id, budget)
        return {
            "tier": tier,
            "used": int(state["used"]),
            "limit": int(state["limit"]),
            "remaining": int(state["remaining"]),
            "reset_in": int(state["reset_in"]),
        }

    # -------------------------------------------------
    # message helpers
    # -------------------------------------------------
    async def _ephemeral(self, interaction: discord.Interaction, content: str):
        with contextlib.suppress(Exception):
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)

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

    def _build_progress_embed(
        self,
        user: discord.abc.User,
        prompt: str,
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
            value=f"• Duration: `{seconds}s`\n• Resolution: `{VENICE_VIDEO_RESOLUTION}`",
            inline=False
        )
        embed.add_field(name="Timing", value=f"• Elapsed: `{elapsed_sec}s`\n• Status: {stage_text}", inline=False)
        embed.set_footer(text="Image → Video")
        return embed

    def _build_result_embed(
        self,
        user: discord.abc.User,
        prompt: str,
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
            value=f"• Model: `{VENICE_VIDEO_I2V_MODEL}`\n• Duration: `{seconds}s`\n• Resolution: `{VENICE_VIDEO_RESOLUTION}`",
            inline=False
        )
        embed.set_footer(text="Image → Video")
        return embed

    def _build_error_embed(
        self,
        user: discord.abc.User,
        prompt: str,
        seconds: int,
        reason: str,
    ) -> discord.Embed:
        preview = _codeblock_safe(_trim(prompt, 420))
        rs = _trim(_sanitize_error_text(reason or "Video generation failed.", 420), 420)
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
            value=f"• Model: `{VENICE_VIDEO_I2V_MODEL}`\n• Duration: `{seconds}s`\n• Resolution: `{VENICE_VIDEO_RESOLUTION}`",
            inline=False
        )
        embed.set_footer(text="Image → Video")
        return embed

    # -------------------------------------------------
    # media fetch
    # -------------------------------------------------
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

    # -------------------------------------------------
    # provider calls
    # -------------------------------------------------
    async def _queue_i2v(
        self,
        image_url: str,
        prompt: str,
        seconds: int,
    ) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str], str]:
        if not VENICE_VIDEO_QUEUE_URL:
            return None, None, "VENICE_VIDEO_QUEUE_URL is missing.", "noid"
        if not VENICE_API_KEY:
            return None, None, "VENICE_API_KEY is missing.", "noid"
        if not image_url or not image_url.startswith("http"):
            return None, None, "No valid image_url provided.", "noid"

        await self._ensure_session()
        assert self.session is not None

        headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
        request_id = uuid.uuid4().hex[:8]

        # IMPORTANT:
        # Model expects visual URL input.
        # Do NOT send "image" key.
        # Do NOT send "aspect_ratio" for this model (provider returns unsupported).
        payload_variants = [
            {
                "model": VENICE_VIDEO_I2V_MODEL,
                "prompt": prompt,
                "resolution": VENICE_VIDEO_RESOLUTION,
                "duration": f"{seconds}s",
                "image_url": image_url,
            },
            {
                "model": VENICE_VIDEO_I2V_MODEL,
                "prompt": prompt,
                "resolution": VENICE_VIDEO_RESOLUTION,
                "duration_seconds": seconds,
                "reference_image_urls": [image_url],
            },
        ]

        timeout = aiohttp.ClientTimeout(total=35, connect=10, sock_read=30)
        max_attempts = 2
        last_error = "Queue request failed."

        logger.info("[VID %s] -> queue model=%s duration=%ss", request_id, VENICE_VIDEO_I2V_MODEL, seconds)

        for attempt in range(max_attempts):
            for payload in payload_variants:
                try:
                    async with self.session.post(VENICE_VIDEO_QUEUE_URL, headers=headers, json=payload, timeout=timeout) as resp:
                        text = await resp.text()
                        logger.info("[VID %s] <- queue status=%s attempt=%s", request_id, resp.status, attempt + 1)

                        if resp.status in (400, 401, 403, 404, 422):
                            msg = _sanitize_error_text(text)
                            return None, {"raw": text}, f"Queue error ({resp.status}): {msg}", request_id

                        if resp.status == 429:
                            msg = _sanitize_error_text(text)
                            wait_s = _parse_retry_after_seconds(resp.headers, text)
                            logger.warning("[VID %s] queue 429, wait=%ss msg=%s", request_id, wait_s, msg)
                            if "too many failed attempts" in (text or "").lower():
                                return None, {"raw": text}, f"Provider rate limit: {msg}", request_id
                            await asyncio.sleep(wait_s)
                            continue

                        if resp.status >= 500:
                            last_error = f"Provider error ({resp.status})"
                            await asyncio.sleep(2 + attempt * 2)
                            continue

                        try:
                            data = json.loads(text) if text else {}
                        except Exception:
                            data = {"raw": text}

                        queue_id = _extract_queue_id(data)
                        if queue_id:
                            return queue_id, data, None, request_id

                        last_error = "Queue response did not include queue_id."
                except asyncio.TimeoutError:
                    last_error = "Queue request timed out."
                except Exception as e:
                    last_error = f"Queue request error: {e}"

            await asyncio.sleep(1.5 + attempt)

        return None, None, last_error, request_id

    async def _wait_for_result(
        self,
        queue_id: str,
        progress_message: Optional[discord.Message],
        user: discord.abc.User,
        prompt: str,
        seconds: int,
        queue_download_url: Optional[str] = None,
        request_id: str = "unknown",
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
                logger.info("[VID %s] -> retrieve queue_id=%s", request_id, queue_id)
                async with self.session.post(
                    VENICE_VIDEO_RETRIEVE_URL,
                    headers=headers,
                    json={"model": VENICE_VIDEO_I2V_MODEL, "queue_id": queue_id},
                    timeout=timeout,
                ) as response:
                    ctype = (response.headers.get("content-type") or "").lower()
                    logger.info("[VID %s] <- retrieve status=%s", request_id, response.status)

                    if response.status == 429:
                        text_429 = await response.text()
                        wait_s = _parse_retry_after_seconds(response.headers, text_429)
                        logger.warning("[VID %s] retrieve 429 -> wait %ss", request_id, wait_s)
                        await asyncio.sleep(wait_s)
                        continue

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
                        if response.status == 404:
                            return None, None, "Retrieve endpoint not found (404)."
                        if response.status == 422:
                            return None, None, "Retrieve request rejected by provider (422)."
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
                    logger.info("[VID %s] poll status=%s", request_id, status or "unknown")

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
                        return None, None, f"Rendering aborted: {_sanitize_error_text(msg)}"

                    if status == "completed":
                        candidate_urls: list[str] = []
                        if isinstance(queue_download_url, str) and queue_download_url.startswith("http"):
                            candidate_urls.append(queue_download_url)
                        candidate_urls.extend(_extract_urls_from_payload(data))
                        candidate_urls = list(dict.fromkeys(candidate_urls))

                        for media_url in candidate_urls:
                            logger.info("[VID %s] fetch media url", request_id)
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
                                seconds=seconds,
                                percent=p,
                                elapsed_sec=elapsed_sec,
                                stage_text="Finalizing file delivery..."
                            )
                        )
                        last_percent = p

                        if finalize_attempts >= 25:
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

    # -------------------------------------------------
    # public entry for image cog
    # -------------------------------------------------
    async def animate_image_to_video(
        self,
        interaction: discord.Interaction,
        image_url: str,
        image_bytes: Optional[bytes],
        prompt: str,
        aspect: str,
        seconds: int,
        target_channel: discord.abc.Messageable,
    ) -> bool:
        _ = aspect  # currently not used in payload for this model

        if not VENICE_API_KEY:
            await self._ephemeral(interaction, "❌ VENICE_API_KEY is missing.")
            return False
        if not VENICE_VIDEO_QUEUE_URL or not VENICE_VIDEO_RETRIEVE_URL:
            await self._ephemeral(interaction, "❌ Video API endpoints are missing in .env.")
            return False
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._ephemeral(interaction, "❌ This action is server-only.")
            return False
        if seconds <= 0:
            await self._ephemeral(interaction, "❌ Invalid duration.")
            return False
        if seconds > MAX_VIDEO_RENDER_SECONDS:
            await self._ephemeral(interaction, "❌ Max duration per render is 15 seconds.")
            return False
        if seconds not in VIDEO_CHOICES:
            await self._ephemeral(interaction, "❌ Allowed durations are 5, 10, 15 seconds.")
            return False

        if (not image_url or not image_url.startswith("http")) and (not image_bytes or not _looks_like_image(image_bytes)):
            await self._ephemeral(interaction, "❌ No valid source image for video generation.")
            return False

        got_user_lock = await self._try_lock_user(interaction.user.id)
        if not got_user_lock:
            await self._ephemeral(interaction, "⏳ You already have a video render running. Please wait.")
            return False

        tier = self.get_member_tier(interaction.user)
        budget = self.get_budget_for_member(interaction.user)

        if budget <= 0:
            await self._ephemeral(
                interaction,
                "🎬 Video rendering is locked for members without a Tier role.\n"
                "Unlock Tier 1 to start using daily video seconds."
            )
            await self._unlock_user(interaction.user.id)
            return False

        ok_q, state_q, token = await self.video_quota.reserve(interaction.guild.id, interaction.user.id, budget, seconds)
        if not ok_q:
            nxt = self._next_tier(tier)
            msg = (
                f"⛔ Not enough video seconds left in your 24h window.\n"
                f"Used: **{state_q['used']}/{state_q['limit']}s** • Remaining: **{state_q['remaining']}s**\n"
                f"⏳ Reset in **{_seconds_human(state_q['reset_in'])}**.\n"
                f"Current tier: **T{tier}**."
            )
            if nxt:
                nt, cfg = nxt
                msg += f"\n🚀 Next unlock: **Tier {nt}** (<@&{cfg['role_id']}>, Level {cfg['level']}) → **{cfg['video_budget_sec']}s/day**."
            msg += f"\nTier budgets: `{self._video_tier_line()}`"
            await self._ephemeral(interaction, msg)
            await self._unlock_user(interaction.user.id)
            return False

        got_global = await self._try_begin_global()
        if not got_global:
            await self.video_quota.rollback(token)
            await self._ephemeral(interaction, "⏳ Another video render is currently running. Please wait.")
            await self._unlock_user(interaction.user.id)
            return False

        progress_message: Optional[discord.Message] = None
        quota_success = False

        try:
            progress_embed = self._build_progress_embed(
                user=interaction.user,
                prompt=prompt,
                seconds=seconds,
                percent=5,
                elapsed_sec=0,
                stage_text="Sending queue request..."
            )
            progress_message = await target_channel.send(embed=progress_embed)

            queue_id, queue_response, queue_error, request_id = await self._queue_i2v(
                image_url=image_url,
                prompt=prompt,
                seconds=seconds,
            )

            if not queue_id:
                await target_channel.send(
                    embed=self._build_error_embed(
                        user=interaction.user,
                        prompt=prompt,
                        seconds=seconds,
                        reason=queue_error or "Queue failed."
                    )
                )
                await self._ephemeral(interaction, f"❌ Animation failed: {_sanitize_error_text(queue_error or 'Queue failed.')}")
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
                seconds=seconds,
                queue_download_url=queue_download_url,
                request_id=request_id,
            )

            if not media_data:
                await target_channel.send(
                    embed=self._build_error_embed(
                        user=interaction.user,
                        prompt=prompt,
                        seconds=seconds,
                        reason=error_message or "Generation failed or timed out."
                    )
                )
                await self._ephemeral(interaction, f"❌ Animation failed: {_sanitize_error_text(error_message or 'Unknown error')}")
                return False

            if media_type != "video":
                await target_channel.send(
                    embed=self._build_error_embed(
                        user=interaction.user,
                        prompt=prompt,
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
                seconds=seconds,
            )
            await target_channel.send(
                content=interaction.user.mention,
                embed=result_embed,
                file=file,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

            quota_success = True
            info = await self.get_remaining_info(interaction.guild.id, interaction.user)
            await self._ephemeral(
                interaction,
                f"✅ Animation completed.\n"
                f"Remaining today: **{info['remaining']}s** of **{info['limit']}s** "
                f"(resets in **{_seconds_human(info['reset_in'])}**)."
            )
            return True

        except discord.Forbidden:
            await self._ephemeral(interaction, "❌ Missing Discord permissions to post video.")
            return False
        except Exception as e:
            logger.exception("animate_image_to_video failed: %s", e)
            await self._ephemeral(interaction, f"❌ Animation failed: {_sanitize_error_text(str(e))}")
            return False
        finally:
            if not quota_success:
                await self.video_quota.rollback(token)
            await self._safe_delete_message(progress_message)
            await self._end_global()
            await self._unlock_user(interaction.user.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceVideoCog(bot))