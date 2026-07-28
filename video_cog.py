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

# =================================================
# ENV
# =================================================
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
VENICE_VIDEO_QUEUE_URL = os.getenv("VENICE_VIDEO_QUEUE_URL")
VENICE_VIDEO_RETRIEVE_URL = os.getenv("VENICE_VIDEO_RETRIEVE_URL")
VENICE_VIDEO_I2V_MODEL = os.getenv("VENICE_VIDEO_I2V_MODEL", "wan-2-7-image-to-video")
VENICE_VIDEO_RESOLUTION = os.getenv("VENICE_VIDEO_RESOLUTION", "720p")

# =================================================
# SETTINGS
# =================================================
MAX_VIDEO_RENDER_SECONDS = 15
VIDEO_CHOICES = [5, 10, 15]

VIDEO_POLL_SECONDS = 6
VIDEO_HARD_TIMEOUT_SECONDS = 1800
VIDEO_ADAPTIVE_TIMEOUT_SECONDS = 720
VIDEO_MAX_CONSECUTIVE_5XX = 8
VIDEO_5XX_WINDOW_SECONDS = 180

SERVER_ANIM_ICON = "<a:01pepper_icon:1377636862847619213>"
VIDEO_POST_REACTIONS = [
    "1️⃣",
    "2️⃣",
    "3️⃣",
    "<:011:1346549711817146400>",
    "<:011pump:1346549688836296787>",
]

VIDEO_MODEL_RENAMES = {
    "wan-2-7-enhanced-image-to-video": "WAN27-Enh",
}

TIER_RULES: dict[int, dict[str, int]] = {
    1: {"role_id": 1377051179615522926, "level": 3, "video_budget_sec": 10},
    2: {"role_id": 1375147276413964408, "level": 11, "video_budget_sec": 20},
    3: {"role_id": 1376592697606930593, "level": 21, "video_budget_sec": 30},
    4: {"role_id": 1381791848875430069, "level": 33, "video_budget_sec": 35},
    5: {"role_id": 1375666588404940830, "level": 43, "video_budget_sec": 40},
    6: {"role_id": 1375584380914896978, "level": 69, "video_budget_sec": 50},
    7: {"role_id": 1346414581643219029, "level": 99, "video_budget_sec": 300},
}
DEFAULT_VIDEO_BUDGET_SEC = 0

VIDEO_QUOTA_FILE = os.getenv("VIDEO_QUOTA_FILE", "goonhut_video_quota.json")
VIDEO_WINDOW_SECONDS = 24 * 60 * 60

# =================================================
# QUOTA STORE (24h rolling)
# =================================================
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
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write(self, data: dict[str, Any]):
        if self.file_path.parent and str(self.file_path.parent) != ".":
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

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

    async def reserve(
        self, guild_id: int, user_id: int, limit: int, amount: int
    ) -> tuple[bool, dict[str, int], Optional[dict[str, int]]]:
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

            if int(entry["start"]) == token_start and int(entry["used"]) > 0:
                used = max(0, int(entry["used"]) - amount)
                entry["used"] = used
                if used == 0:
                    entry["start"] = 0

            db[key] = entry
            self._write(db)

# =================================================
# HELPERS
# =================================================
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
    return t if len(t) <= limit else t[:limit] + " [...]"


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


def _video_model_label(model_name: str) -> str:
    key = (model_name or "").strip()
    return VIDEO_MODEL_RENAMES.get(key, key)

# =================================================
# COG
# =================================================
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
        self._ephemeral_messages: dict[tuple[int, int], list[discord.Message]] = {}

    # ---------- lifecycle ----------
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

    # ---------- locks ----------
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

    # ---------- ephemeral ----------
    def _ephemeral_key(self, interaction: discord.Interaction) -> tuple[int, int]:
        gid = interaction.guild.id if interaction.guild else 0
        return gid, interaction.user.id

    async def _track_ephemeral(self, interaction: discord.Interaction, msg: Optional[discord.Message]):
        if msg:
            self._ephemeral_messages.setdefault(self._ephemeral_key(interaction), []).append(msg)

    async def _cleanup_user_ephemerals(self, interaction: discord.Interaction, delay: float = 8.0):
        if delay > 0:
            await asyncio.sleep(delay)
        for m in self._ephemeral_messages.pop(self._ephemeral_key(interaction), []):
            with contextlib.suppress(Exception):
                await m.delete()

    async def _ephemeral(self, interaction: discord.Interaction, content: str):
        msg = None
        with contextlib.suppress(Exception):
            if interaction.response.is_done():
                msg = await interaction.followup.send(content, ephemeral=True, wait=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
                msg = await interaction.original_response()
        await self._track_ephemeral(interaction, msg)

    # ---------- tiers ----------
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
        return DEFAULT_VIDEO_BUDGET_SEC if tier <= 0 else int(TIER_RULES[tier]["video_budget_sec"])

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

    # ---------- embeds ----------
    def _build_progress_embed(
        self,
        user: discord.abc.User,
        prompt: str,
        seconds: int,
        percent: int,
        elapsed_sec: int,
        stage_text: str,
        quota_used: int,
        quota_limit: int,
        quota_remaining: int,
    ) -> discord.Embed:
        bar = _progress_bar(percent)
        preview = _codeblock_safe(_trim(prompt, 420))
        embed = discord.Embed(
            title="🎬 VIDEO RENDER",
            description=f"{user.mention}",
            color=discord.Color.purple(),
            timestamp=utc_now(),
        )
        embed.add_field(name="Prompt", value=f"```{preview}```", inline=False)
        embed.add_field(name="Progress", value=f"`{bar} {percent}%`", inline=False)
        embed.add_field(name="Timing", value=f"• Elapsed: `{elapsed_sec}s`\n• Status: {stage_text}", inline=False)
        embed.add_field(
            name="Quota (24h)",
            value=f"• Used: `{quota_used}/{quota_limit}s`\n• Remaining: `{quota_remaining}s`",
            inline=False,
        )
        return embed

    def _build_result_embed(self, prompt: str, seconds: int, guild_icon_url: Optional[str]) -> discord.Embed:
        model_label = _video_model_label(VENICE_VIDEO_I2V_MODEL)
        preview = _codeblock_safe(_trim(prompt, 1500))
        embed = discord.Embed(
            color=discord.Color.dark_magenta(),
            timestamp=utc_now(),
        )
        embed.add_field(name="Prompt", value=f"```{preview}```", inline=False)
        embed.set_footer(
            text=f"🎞️ {model_label} • 📺 {VENICE_VIDEO_RESOLUTION} • ⏱️ {seconds}s",
            icon_url=guild_icon_url,
        )
        return embed

    # ---------- cleanup ----------
    def _is_progress_leak_post(self, msg: discord.Message) -> bool:
        if not self.bot.user or msg.author.id != self.bot.user.id:
            return False
        if not msg.embeds:
            return False
        title = (msg.embeds[0].title or "").strip()
        return title == "🎬 VIDEO RENDER"

    async def _cleanup_progress_leaks(self, channel: discord.abc.Messageable, keep_ids: Optional[set[int]] = None, limit: int = 20):
        keep_ids = keep_ids or set()
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        async for msg in channel.history(limit=limit):
            if msg.id in keep_ids:
                continue
            if self._is_progress_leak_post(msg):
                with contextlib.suppress(Exception):
                    await msg.delete()

    async def _repost_image_starter(self, channel: discord.abc.Messageable):
        if not isinstance(channel, discord.TextChannel):
            return
        image_cog = self.bot.get_cog("VeniceImageCog")
        if not image_cog:
            return
        session = getattr(image_cog, "session", None)
        if session is None:
            return
        bot_user_id = self.bot.user.id if self.bot.user else None
        with contextlib.suppress(Exception):
            await image_cog.ensure_starter_message_static(channel, session, bot_user_id)

    async def _safe_edit_progress(self, message: Optional[discord.Message], embed: discord.Embed):
        if message:
            with contextlib.suppress(Exception):
                await message.edit(embed=embed)

    async def _safe_delete_message(self, message: Optional[discord.Message]):
        if message:
            with contextlib.suppress(Exception):
                await message.delete()

    # ---------- media fetch ----------
    async def _fetch_media_from_url(self, url: str, headers: dict[str, str], visited: Optional[set[str]] = None):
        if not isinstance(url, str) or not url.startswith("http"):
            return None, None
        visited = visited or set()
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

    # ---------- provider ----------
    async def _queue_i2v(self, image_url: str, prompt: str, seconds: int) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str], str]:
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

        payload = {
            "model": VENICE_VIDEO_I2V_MODEL,
            "prompt": prompt,
            "resolution": VENICE_VIDEO_RESOLUTION,
            "duration": f"{seconds}s",
            "image_url": image_url,
        }

        timeout = aiohttp.ClientTimeout(total=35, connect=10, sock_read=30)
        last_error = "Queue request failed."

        for attempt in range(2):
            try:
                async with self.session.post(VENICE_VIDEO_QUEUE_URL, headers=headers, json=payload, timeout=timeout) as resp:
                    text = await resp.text()
                    logger.info("[VID %s] queue status=%s attempt=%s", request_id, resp.status, attempt + 1)

                    if resp.status in (400, 401, 403, 404, 422):
                        return None, {"raw": text}, f"Queue error ({resp.status}): {_sanitize_error_text(text)}", request_id

                    if resp.status == 429:
                        wait_s = _parse_retry_after_seconds(resp.headers, text)
                        if "too many failed attempts" in (text or "").lower():
                            return None, {"raw": text}, f"Provider rate limit: {_sanitize_error_text(text)}", request_id
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

            await asyncio.sleep(1.2 + attempt)

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
        quota_used: int = 0,
        quota_limit: int = 0,
        quota_remaining: int = 0,
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
            if utc_now() >= hard_deadline or utc_now() >= adaptive_deadline:
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

                    if response.status == 429:
                        t429 = await response.text()
                        await asyncio.sleep(_parse_retry_after_seconds(response.headers, t429))
                        continue

                    if response.status >= 400:
                        _ = await response.text()

                        if response.status >= 500:
                            total_5xx += 1
                            consecutive_5xx += 1
                            if first_5xx_at is None:
                                first_5xx_at = utc_now()

                            p = max(last_percent, 12)
                            await self._safe_edit_progress(
                                progress_message,
                                self._build_progress_embed(
                                    user, prompt, seconds, p, elapsed_sec,
                                    f"Provider error {response.status} (retry {total_5xx})...",
                                    quota_used, quota_limit, quota_remaining
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
                        msg = err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else data.get("message")
                        return None, None, f"Rendering aborted: {_sanitize_error_text(str(msg or 'unknown'))}"

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
                                user, prompt, seconds, p, elapsed_sec,
                                "Finalizing file delivery...",
                                quota_used, quota_limit, quota_remaining
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
                                user, prompt, seconds, percent, elapsed_sec,
                                "Rendering...",
                                quota_used, quota_limit, quota_remaining
                            )
                        )
                        last_percent = percent

            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

        return None, None, "Generation timed out."

    # ---------- public api (called by image cog) ----------
    async def animate_image_to_video(
        self,
        interaction: discord.Interaction,
        image_url: str,
        image_bytes: Optional[bytes],
        prompt: str,
        aspect: str,  # kept for compatibility, not shown in output
        seconds: int,
        target_channel: discord.abc.Messageable,
    ) -> bool:
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
            await self._ephemeral(interaction, "🎬 Video rendering is locked for members without a Tier role.")
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
        keep_ids: set[int] = set()

        if isinstance(target_channel, (discord.TextChannel, discord.Thread)):
            await self._cleanup_progress_leaks(target_channel, keep_ids=set(), limit=20)

        try:
            progress_message = await target_channel.send(
                embed=self._build_progress_embed(
                    user=interaction.user,
                    prompt=prompt,
                    seconds=seconds,
                    percent=5,
                    elapsed_sec=0,
                    stage_text="Sending queue request...",
                    quota_used=int(state_q["used"]),
                    quota_limit=int(state_q["limit"]),
                    quota_remaining=int(state_q["remaining"]),
                )
            )
            keep_ids.add(progress_message.id)

            queue_id, queue_response, queue_error, request_id = await self._queue_i2v(
                image_url=image_url,
                prompt=prompt,
                seconds=seconds,
            )
            if not queue_id:
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
                    stage_text="Queue accepted. Rendering started.",
                    quota_used=int(state_q["used"]),
                    quota_limit=int(state_q["limit"]),
                    quota_remaining=int(state_q["remaining"]),
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
                quota_used=int(state_q["used"]),
                quota_limit=int(state_q["limit"]),
                quota_remaining=int(state_q["remaining"]),
            )

            if not media_data:
                await self._ephemeral(interaction, f"❌ Animation failed: {_sanitize_error_text(error_message or 'Unknown error')}")
                return False
            if media_type != "video":
                await self._ephemeral(interaction, "❌ Provider returned non-video output.")
                return False

            guild_limit = None
            guild_icon_url = None
            if getattr(target_channel, "guild", None):
                guild_limit = getattr(target_channel.guild, "filesize_limit", None)
                if target_channel.guild and target_channel.guild.icon:
                    guild_icon_url = target_channel.guild.icon.url

            if guild_limit and len(media_data) > guild_limit:
                await self._ephemeral(interaction, "❌ Video too large for Discord upload limit.")
                return False

            result_embed = self._build_result_embed(prompt=prompt, seconds=seconds, guild_icon_url=guild_icon_url)
            file = discord.File(io.BytesIO(media_data), filename="AI_video.mp4")

            video_post = await target_channel.send(
                content=f"{SERVER_ANIM_ICON} 🎬 **Video** • {interaction.user.mention} • ▶ **CLICK TO PLAY**",
                embed=result_embed,
                file=file,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            keep_ids.add(video_post.id)

            for emo in VIDEO_POST_REACTIONS:
                with contextlib.suppress(Exception):
                    await video_post.add_reaction(emo)

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

            if isinstance(target_channel, (discord.TextChannel, discord.Thread)):
                await self._cleanup_progress_leaks(target_channel, keep_ids=keep_ids, limit=25)
                await self._repost_image_starter(target_channel)

            asyncio.create_task(self._cleanup_user_ephemerals(interaction, delay=8))


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceVideoCog(bot))