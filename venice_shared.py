# venice_shared.py
"""
Shared infrastructure for all Venice cogs.

RULES:
- Get quota stores ONLY via get_quota_store().
- TIER_RULES is the single source of truth.
- Cogs register themselves via register_starter_reposter().
- Progress embeds use build_progress_embed(quota_state=state).
- Success messages use build_generation_success_text(state, kind=...).
- Video model config lives in VIDEO_MODEL_PROFILES. Add a new animate button
  by adding one entry there. No cog change required.
- Video files NEVER live fully in memory. Use the disk-based helpers in the
  VIDEO COMPRESSION section - a 25s 720p clip is 100MB+ and reading that into
  RAM twice (bytes + BytesIO) is what triggers OOM kills.
- All future changes to reset/feedback/animate buttons go HERE.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import io
import json
import logging
import logging.handlers
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiohttp
import discord

try:
    from PIL import Image
except Exception:
    Image = None

logger = logging.getLogger("venice_shared")


# =================================================
# ENV HELPERS
# =================================================
def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    return default if not raw else raw in ("1", "true", "yes", "on")


def env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw.strip() if raw and raw.strip() else default


DISCORD_UPLOAD_LIMIT_FORCE_MB = env_int("DISCORD_UPLOAD_LIMIT_FORCE_MB", 0)
DISCORD_UPLOAD_LIMIT_FALLBACK_MB = env_int("DISCORD_UPLOAD_LIMIT_FALLBACK_MB", 10)
DISCORD_UPLOAD_SAFETY_BYTES = env_int("DISCORD_UPLOAD_SAFETY_BYTES", 512 * 1024)

DEFAULT_WINDOW_SECONDS = 24 * 60 * 60


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# =================================================
# LOGGING SETUP
# =================================================
LOG_LEVEL = env_str("LOG_LEVEL", "INFO").upper()
LOG_FILE = env_str("LOG_FILE", "goonhut_bot.log")
LOG_MAX_BYTES = env_int("LOG_MAX_BYTES", 8 * 1024 * 1024)
LOG_BACKUP_COUNT = env_int("LOG_BACKUP_COUNT", 4)

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

_logging_configured = False


def setup_logging(force: bool = False) -> None:
    """
    Configure root logging once. Call this ONCE from your entrypoint
    (main.py / bot.py) BEFORE loading any cogs.

    Without this, logger.info() calls inside the cogs go nowhere and you get
    a silent bot. Writes to both stdout (systemd/docker journal) and a
    rotating file.
    """
    global _logging_configured
    if _logging_configured and not force:
        return

    root = logging.getLogger()
    if force:
        for h in list(root.handlers):
            root.removeHandler(h)

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    root.setLevel(level)

    fmt = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    stream.setLevel(level)
    root.addHandler(stream)

    if LOG_FILE:
        with contextlib.suppress(Exception):
            fileh = logging.handlers.RotatingFileHandler(
                LOG_FILE,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            fileh.setFormatter(fmt)
            fileh.setLevel(level)
            root.addHandler(fileh)

    # discord.py is extremely chatty at DEBUG; keep it readable.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    _logging_configured = True
    root.info(
        "Logging initialised | level=%s | file=%s", LOG_LEVEL, LOG_FILE or "-"
    )


def log_memory_usage(tag: str = "") -> Optional[int]:
    """
    Log current RSS in MB. Returns the value, or None if unavailable.
    Useful for pinpointing which stage of a render blows up the process.
    """
    rss_mb: Optional[int] = None
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss_mb = int(line.split()[1]) // 1024
                    break
    except Exception:
        return None

    if rss_mb is not None:
        logger.info("MEM %s rss=%sMB", f"[{tag}]" if tag else "", rss_mb)
    return rss_mb


# =================================================
# ROLLING QUOTA STORE
# =================================================
class RollingQuotaStore:
    """
    24h rolling window quota, persisted as JSON.
    File I/O runs in a worker thread to avoid blocking the event loop.
    """

    def __init__(self, file_path: str | Path, window_seconds: int = DEFAULT_WINDOW_SECONDS):
        self.file_path = Path(file_path)
        self.window_seconds = int(window_seconds)
        self.lock = asyncio.Lock()

    def _read_sync(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {}
        try:
            raw = self.file_path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error("Quota read failed (%s): %s", self.file_path, e)
            with contextlib.suppress(Exception):
                backup = self.file_path.with_suffix(f".corrupt.{int(time.time())}")
                self.file_path.replace(backup)
                logger.error("Corrupt quota file moved to %s", backup)
            return {}

    def _write_sync(self, data: dict[str, Any]) -> None:
        parent = self.file_path.parent
        if parent and str(parent) != ".":
            parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file_path.with_suffix(self.file_path.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.file_path)

    async def _read(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._read_sync)

    async def _write(self, data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._write_sync, data)

    def _key(self, guild_id: int, user_id: int) -> str:
        return f"{guild_id}:{user_id}"

    def _normalize(self, entry: Any, now_ts: int) -> dict[str, int]:
        if not isinstance(entry, dict):
            entry = {}
        start = int(entry.get("start", 0) or 0)
        used = int(entry.get("used", 0) or 0)
        if start > 0 and (now_ts - start) >= self.window_seconds:
            start, used = 0, 0
        return {"start": start, "used": max(0, used)}

    def _state(self, entry: dict[str, int], limit: int, now_ts: int) -> dict[str, int]:
        used, start = entry["used"], entry["start"]
        if start > 0:
            reset_at = start + self.window_seconds
            reset_in = max(0, reset_at - now_ts)
        else:
            reset_at = 0
            reset_in = self.window_seconds
        return {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "start": start,
            "reset_in": reset_in,
            "reset_at": reset_at,
        }

    async def peek(self, guild_id: int, user_id: int, limit: int) -> dict[str, int]:
        now_ts = int(time.time())
        limit = max(0, int(limit))
        async with self.lock:
            db = await self._read()
            entry = self._normalize(db.get(self._key(guild_id, user_id), {}), now_ts)
            return self._state(entry, limit, now_ts)

    async def reserve(
        self, guild_id: int, user_id: int, limit: int, amount: int
    ) -> tuple[bool, dict[str, int], Optional[dict[str, int]]]:
        now_ts = int(time.time())
        limit = max(0, int(limit))
        amount = max(0, int(amount))

        async with self.lock:
            db = await self._read()
            key = self._key(guild_id, user_id)
            entry = self._normalize(db.get(key, {}), now_ts)

            if limit <= 0 or amount <= 0 or entry["used"] + amount > limit:
                return False, self._state(entry, limit, now_ts), None

            if entry["start"] <= 0:
                entry["start"] = now_ts
            entry["used"] += amount

            db[key] = entry
            await self._write(db)

            token = {
                "guild_id": guild_id,
                "user_id": user_id,
                "amount": amount,
                "start": entry["start"],
            }
            logger.debug(
                "Quota reserved: g=%s u=%s amount=%s used=%s/%s",
                guild_id, user_id, amount, entry["used"], limit,
            )
            return True, self._state(entry, limit, now_ts), token

    async def rollback(self, token: Optional[dict[str, int]]) -> None:
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
            db = await self._read()
            key = self._key(guild_id, user_id)
            entry = self._normalize(db.get(key, {}), now_ts)

            if entry["start"] == token_start and entry["used"] > 0:
                entry["used"] = max(0, entry["used"] - amount)
                if entry["used"] == 0:
                    entry["start"] = 0
                db[key] = entry
                await self._write(db)
                logger.debug(
                    "Quota rolled back: g=%s u=%s amount=%s", guild_id, user_id, amount
                )

    async def prune(self) -> int:
        now_ts = int(time.time())
        async with self.lock:
            db = await self._read()
            before = len(db)
            alive = {k: v for k, v in db.items() if self._normalize(v, now_ts)["start"] > 0}
            if len(alive) != before:
                await self._write(alive)
            return before - len(alive)


_stores: dict[str, RollingQuotaStore] = {}


def get_quota_store(
    file_path: str | Path, window_seconds: int = DEFAULT_WINDOW_SECONDS
) -> RollingQuotaStore:
    resolved = str(Path(file_path).expanduser().resolve())
    store = _stores.get(resolved)
    if store is None:
        store = RollingQuotaStore(resolved, window_seconds)
        _stores[resolved] = store
        logger.info("Quota store registered: %s (window=%ss)", resolved, window_seconds)
    elif store.window_seconds != int(window_seconds):
        logger.warning(
            "Quota store %s already registered with window=%ss; %ss ignored.",
            resolved, store.window_seconds, window_seconds,
        )
    return store


# =================================================
# TIERS - SINGLE SOURCE OF TRUTH
# =================================================
TIER_RULES: dict[int, dict[str, int]] = {
    1: {"role_id": 1377051179615522926, "level": 3,  "image_limit": 15,  "video_budget_sec": 18},
    2: {"role_id": 1375147276413964408, "level": 11, "image_limit": 20,  "video_budget_sec": 30},
    3: {"role_id": 1376592697606930593, "level": 21, "image_limit": 25,  "video_budget_sec": 42},
    4: {"role_id": 1381791848875430069, "level": 33, "image_limit": 30,  "video_budget_sec": 56},
    5: {"role_id": 1375666588404940830, "level": 42, "image_limit": 42,  "video_budget_sec": 70},
    6: {"role_id": 1375584380914896978, "level": 69, "image_limit": 69,  "video_budget_sec": 90},
    7: {"role_id": 1346414581643219029, "level": 99, "image_limit": 300, "video_budget_sec": 300},
}

DEFAULT_IMAGE_LIMIT_24H = 5
DEFAULT_VIDEO_BUDGET_SEC = 0


def tiers_asc() -> list[tuple[int, dict[str, int]]]:
    return sorted(TIER_RULES.items(), key=lambda x: x[0])


def tiers_desc() -> list[tuple[int, dict[str, int]]]:
    return sorted(TIER_RULES.items(), key=lambda x: x[0], reverse=True)


def get_member_tier(member: Optional[discord.Member]) -> int:
    if not isinstance(member, discord.Member):
        return 0
    role_ids = {r.id for r in member.roles}
    for tier, cfg in tiers_desc():
        if cfg["role_id"] in role_ids:
            return tier
    return 0


def get_image_limit_for_member(member: Optional[discord.Member]) -> int:
    tier = get_member_tier(member)
    return DEFAULT_IMAGE_LIMIT_24H if tier <= 0 else int(TIER_RULES[tier]["image_limit"])


def get_video_budget_for_member(member: Optional[discord.Member]) -> int:
    tier = get_member_tier(member)
    return DEFAULT_VIDEO_BUDGET_SEC if tier <= 0 else int(TIER_RULES[tier]["video_budget_sec"])


def next_tier(current_tier: int) -> Optional[tuple[int, dict[str, int]]]:
    for tier, cfg in tiers_asc():
        if tier > current_tier:
            return tier, cfg
    return None


def role_ids_for_tier_and_above(min_tier: int) -> list[int]:
    return [cfg["role_id"] for t, cfg in tiers_asc() if t >= min_tier]


def image_tier_line() -> str:
    return " • ".join(f"T{t}:{cfg['image_limit']}" for t, cfg in tiers_asc())


def video_tier_line() -> str:
    return " • ".join(f"T{t}:{cfg['video_budget_sec']}s" for t, cfg in tiers_asc())


def tier_for_role_id(role_id: int) -> Optional[tuple[int, dict[str, int]]]:
    for t, cfg in tiers_asc():
        if cfg["role_id"] == role_id:
            return t, cfg
    return None


# =================================================
# TEXT HELPERS
# =================================================
def trim(text: str, limit: int) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else (t[:limit] + " [...]")


def codeblock_safe(text: str) -> str:
    return (text or "").replace("```", "'''").strip()


def progress_bar(percent: int, blocks: int = 14) -> str:
    p = max(0, min(100, int(percent)))
    filled = int(blocks * p / 100)
    return "█" * filled + "░" * (blocks - filled)


def eta_text(seconds_float: float) -> str:
    s = max(0, int(round(seconds_float)))
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60}s"


def seconds_human(sec: int) -> str:
    sec = max(0, int(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def human_bytes(num: int) -> str:
    n = float(max(0, int(num)))
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit in ("B", "KB") else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def closest_aspect_ratio(aspect: str, allowed: Optional[list[str]]) -> str:
    """
    Pick the ratio in `allowed` that best matches `aspect` (W:H string).
    Comparison is numeric (W/H), not string based.

    Non-numeric tokens like 'adaptive' or 'auto' are ignored during matching
    so they never win by accident. Falls back to the first allowed value if
    parsing fails, or to `aspect` if `allowed` is empty.
    """
    if not allowed:
        return aspect

    numeric = [a for a in allowed if ":" in str(a)]
    if not numeric:
        return allowed[0]

    def _val(s: str) -> Optional[float]:
        try:
            aw, ah = str(s).split(":")
            aw_f, ah_f = float(aw), float(ah)
            return aw_f / ah_f if ah_f else None
        except Exception:
            return None

    target = _val(aspect)
    if target is None:
        return numeric[0]

    scored = [(a, _val(a)) for a in numeric]
    scored = [(a, v) for a, v in scored if v is not None]
    if not scored:
        return numeric[0]

    return min(scored, key=lambda p: abs(p[1] - target))[0]


def format_reset_line(state: dict[str, int], prefix: str = "Resets") -> str:
    reset_at = int(state.get("reset_at", 0) or 0)
    if reset_at <= 0:
        return "Fresh 24h window starts with your next generation."
    return f"{prefix} <t:{reset_at}:R> (<t:{reset_at}:t>)"


def sanitize_error_text(text: str, limit: int = 300) -> str:
    t = re.sub(r"https?://\S+", "[link removed]", (text or "").strip())
    return re.sub(r"\s+", " ", t).strip()[:limit]


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def make_safe_filename(prompt: str, ext: str = "png", fallback: str = "image") -> str:
    base = "_".join((prompt or "").split()[:5]) or fallback
    base = re.sub(r"[^a-zA-Z0-9_]", "_", base)[:60] or fallback
    ext = (ext or "png").lower().strip(".")
    return f"{base}_{int(time.time_ns())}_{uuid.uuid4().hex[:8]}.{ext}"


# =================================================
# BINARY / IMAGE HELPERS
# =================================================
def looks_like_image(binary: bytes) -> bool:
    if not binary or len(binary) < 12:
        return False
    return (
        binary.startswith(b"\x89PNG\r\n\x1a\n")
        or binary.startswith(b"\xff\xd8\xff")
        or (binary[:4] == b"RIFF" and binary[8:12] == b"WEBP")
        or binary.startswith((b"GIF87a", b"GIF89a"))
    )


def looks_like_video(binary: bytes) -> bool:
    if not binary or len(binary) < 12:
        return False
    # ISO-BMFF (mp4/mov) or Matroska/WebM.
    return binary[4:8] == b"ftyp" or binary[:4] == b"\x1a\x45\xdf\xa3"


def infer_image_ext(binary: bytes) -> str:
    if binary.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if binary.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if binary[:4] == b"RIFF" and binary[8:12] == b"WEBP":
        return "webp"
    if binary.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return "png"


def infer_image_mime(binary: bytes) -> str:
    return {
        "png": "image/png", "jpg": "image/jpeg",
        "webp": "image/webp", "gif": "image/gif",
    }.get(infer_image_ext(binary), "image/png")


def image_dimensions(binary: bytes) -> Optional[tuple[int, int]]:
    """Return (width, height) for an image blob, or None if unavailable."""
    if Image is None or not binary:
        return None
    try:
        with Image.open(io.BytesIO(binary)) as im:
            return int(im.width), int(im.height)
    except Exception:
        return None


def b64_to_bytes(s: str) -> Optional[bytes]:
    if not s:
        return None
    s = s.strip()
    if s.startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    try:
        return base64.b64decode(s)
    except (binascii.Error, ValueError):
        return None


def bytes_to_b64(binary: bytes) -> str:
    return base64.b64encode(binary).decode("utf-8")


def bytes_to_data_url(binary: bytes) -> str:
    return f"data:{infer_image_mime(binary)};base64,{bytes_to_b64(binary)}"


# Source images are base64-inlined into the queue payload when no public URL
# exists. Base64 inflates by ~33% and the JSON encoder copies it again, so an
# oversized source image is a real memory hazard. Downscale before encoding.
SOURCE_IMAGE_MAX_INLINE_BYTES = env_int("SOURCE_IMAGE_MAX_INLINE_KB", 1800) * 1024
SOURCE_IMAGE_MAX_SIDE = env_int("SOURCE_IMAGE_MAX_SIDE", 1536)


def prepare_source_image_for_upload(
    binary: bytes,
    max_bytes: int = SOURCE_IMAGE_MAX_INLINE_BYTES,
    max_side: int = SOURCE_IMAGE_MAX_SIDE,
) -> bytes:
    """
    Shrink a source image so it can safely be inlined as a data URL.
    Returns the original bytes unchanged if Pillow is missing or it already
    fits comfortably.
    """
    if not binary:
        return binary
    if Image is None:
        return binary

    dims = image_dimensions(binary)
    if len(binary) <= max_bytes and (not dims or max(dims) <= max_side):
        return binary

    try:
        img = Image.open(io.BytesIO(binary))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        img.thumbnail((max_side, max_side), resample)

        for q in (92, 86, 80, 74, 68, 60, 52, 45):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=q, optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                logger.info(
                    "Source image shrunk for inline upload: %s -> %s (q=%s)",
                    human_bytes(len(binary)), human_bytes(len(data)), q,
                )
                return data

        logger.warning(
            "Source image still %s after compression attempts.", human_bytes(len(data))
        )
        return data
    except Exception as e:
        logger.debug("prepare_source_image_for_upload failed: %s", e)
        return binary


def extract_image_from_json_obj(obj: Any, depth: int = 0) -> Optional[bytes]:
    if depth > 8:
        return None
    if isinstance(obj, dict):
        for key in (
            "image", "image_base64", "imageBase64", "b64_json",
            "base64", "upscaled_image", "edited_image",
        ):
            val = obj.get(key)
            if isinstance(val, str):
                out = b64_to_bytes(val)
                if out and looks_like_image(out):
                    return out
        for _, val in list(obj.items())[:20]:
            out = extract_image_from_json_obj(val, depth + 1)
            if out:
                return out
    elif isinstance(obj, list):
        for item in obj[:20]:
            out = extract_image_from_json_obj(item, depth + 1)
            if out:
                return out
    elif isinstance(obj, str):
        out = b64_to_bytes(obj)
        if out and looks_like_image(out):
            return out
    return None


async def extract_image_from_response(resp: aiohttp.ClientResponse) -> Optional[bytes]:
    raw = await resp.read()
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "image/" in ctype and looks_like_image(raw):
        return raw
    try:
        data = json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return raw if looks_like_image(raw) else None
    out = extract_image_from_json_obj(data)
    return out if out and looks_like_image(out) else None


def extract_urls_from_payload(payload: Any) -> list[str]:
    urls: list[str] = []
    if not isinstance(payload, (dict, list)):
        return urls
    interesting = {"download_url", "url", "result_url", "video_url", "file_url", "asset_url"}

    def walk(obj: Any, depth: int = 0):
        if depth > 8:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = str(k).lower()
                if isinstance(v, str) and v.startswith("http"):
                    if lk in interesting or "url" in lk or "download" in lk:
                        urls.append(v)
                walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, depth + 1)

    walk(payload)
    return list(dict.fromkeys(urls))


# =================================================
# TEMP FILE HELPERS
# =================================================
VENICE_TMP_DIR = env_str("VENICE_TMP_DIR", tempfile.gettempdir())


def temp_path(prefix: str = "venice", ext: str = "bin") -> Path:
    base = Path(VENICE_TMP_DIR)
    with contextlib.suppress(Exception):
        base.mkdir(parents=True, exist_ok=True)
    return base / f"{prefix}_{uuid.uuid4().hex[:10]}.{ext.lstrip('.')}"


def cleanup_temp_files(*paths: Optional[str | Path]) -> None:
    """Delete temp files, never raising. Safe to call in a finally block."""
    for p in paths:
        if not p:
            continue
        try:
            Path(p).unlink(missing_ok=True)
        except Exception as e:
            logger.debug("cleanup_temp_files failed for %s: %s", p, e)


def file_size(path: Optional[str | Path]) -> int:
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except Exception:
        return 0


def purge_stale_temp_files(max_age_seconds: int = 3600) -> int:
    """
    Remove leftover venice temp files older than max_age_seconds.
    Call periodically - a crashed render leaks its download file otherwise.
    """
    removed = 0
    cutoff = time.time() - max_age_seconds
    base = Path(VENICE_TMP_DIR)
    if not base.exists():
        return 0
    for pattern in ("vdl_*", "vcomp_*", "venice_*"):
        for p in base.glob(pattern):
            with contextlib.suppress(Exception):
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
    if removed:
        logger.info("Purged %s stale temp file(s).", removed)
    return removed


# =================================================
# VIDEO COMPRESSION (ffmpeg, disk-based)
# =================================================
FFMPEG_BIN = env_str("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = env_str("FFPROBE_BIN", "ffprobe")

# Reserve headroom for container overhead / muxing slack.
VIDEO_COMPRESS_SAFETY = 0.90

# Audio is cheap and worth keeping; dropped only as a last resort.
VIDEO_AUDIO_BITRATE_K = env_int("VIDEO_AUDIO_BITRATE_K", 64)

# Resolution ladder walked down when bitrate reduction alone is not enough.
VIDEO_SCALE_LADDER: tuple[int, ...] = (720, 640, 540, 480, 400, 360, 288)

VIDEO_COMPRESS_TIMEOUT = env_int("VIDEO_COMPRESS_TIMEOUT", 900)
VIDEO_COMPRESS_PRESET = env_str("VIDEO_COMPRESS_PRESET", "veryfast")

# Below this video bitrate the result is unwatchable; abort instead.
VIDEO_MIN_VIDEO_KBIT = env_int("VIDEO_MIN_VIDEO_KBIT", 140)


def ffmpeg_available() -> bool:
    return shutil.which(FFMPEG_BIN) is not None


def ffprobe_available() -> bool:
    return shutil.which(FFPROBE_BIN) is not None


async def _run_proc(args: list[str], timeout: int) -> tuple[int, bytes]:
    """Run a subprocess, return (returncode, stderr). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, b"binary not found"
    except Exception as e:
        return 1, str(e).encode()

    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, err or b""
    except asyncio.TimeoutError:
        logger.warning("Subprocess timed out after %ss: %s", timeout, args[0])
        with contextlib.suppress(Exception):
            proc.kill()
            await proc.wait()
        return 124, b"timeout"


async def probe_video_duration(path: str | Path) -> Optional[float]:
    """Return duration in seconds via ffprobe, or None."""
    if not ffprobe_available():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            FFPROBE_BIN,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        value = float((out or b"").decode().strip())
        return value if value > 0 else None
    except Exception as e:
        logger.debug("probe_video_duration failed: %s", e)
        return None


async def compress_video_file(
    src_path: str | Path,
    target_bytes: int,
    duration_hint: float,
    progress_cb: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[Optional[Path], str]:
    """
    Shrink a video below `target_bytes` using ffmpeg (H.264 + AAC).

    Runs entirely on disk - no full copy is ever held in memory, which is the
    whole point: a 25s 720p clip is 100MB+ and buffering that in RAM twice is
    what kills the process on small VPS boxes.

    Strategy: compute the bitrate that mathematically fits the byte budget for
    the clip's duration, then walk down a resolution ladder until the encoder
    actually lands under target. Audio is preserved as long as possible.

    Returns (output_path, note). output_path is None on failure; if the source
    already fits, the source path itself is returned unchanged.
    The caller owns cleanup of the returned file.
    """
    src = Path(src_path)
    if not src.exists():
        return None, "source file missing"

    original = src.stat().st_size
    if original <= target_bytes:
        return src, "no compression needed"

    if not ffmpeg_available():
        logger.error("ffmpeg not found (FFMPEG_BIN=%s) - cannot compress.", FFMPEG_BIN)
        return None, "ffmpeg not installed on host"

    duration = await probe_video_duration(src) or float(max(1, duration_hint))
    duration = max(1.0, duration)

    budget = int(target_bytes * VIDEO_COMPRESS_SAFETY)
    total_kbit = int((budget * 8) / duration / 1000)

    logger.info(
        "Compressing video: %s -> target %s | duration=%.1fs budget=%skbit/s",
        human_bytes(original), human_bytes(target_bytes), duration, total_kbit,
    )

    if total_kbit <= VIDEO_MIN_VIDEO_KBIT:
        return None, (
            f"clip too long for a {human_bytes(target_bytes)} limit "
            f"({total_kbit}kbit/s available)"
        )

    # (height, video_kbit, keep_audio)
    attempts: list[tuple[int, int, bool]] = []
    for height in VIDEO_SCALE_LADDER:
        with_audio = total_kbit - VIDEO_AUDIO_BITRATE_K
        if with_audio >= VIDEO_MIN_VIDEO_KBIT:
            attempts.append((height, with_audio, True))
        # Same rung muted buys back the audio budget.
        if total_kbit - 8 >= VIDEO_MIN_VIDEO_KBIT:
            attempts.append((height, total_kbit - 8, False))

    if not attempts:
        return None, "byte budget too small for any usable encode"

    for idx, (height, video_kbit, keep_audio) in enumerate(attempts):
        out_path = temp_path("vcomp", "mp4")

        if progress_cb:
            with contextlib.suppress(Exception):
                await progress_cb(
                    f"Compressing • {height}p @ {video_kbit}k "
                    f"(pass {idx + 1}/{len(attempts)})..."
                )

        args = [
            FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-vf", f"scale=-2:{height}:flags=bicubic",
            "-c:v", "libx264",
            "-preset", VIDEO_COMPRESS_PRESET,
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-b:v", f"{video_kbit}k",
            "-maxrate", f"{int(video_kbit * 1.25)}k",
            "-bufsize", f"{int(video_kbit * 2)}k",
        ]
        if keep_audio:
            args += ["-c:a", "aac", "-b:a", f"{VIDEO_AUDIO_BITRATE_K}k", "-ac", "2"]
        else:
            args += ["-an"]
        args += ["-movflags", "+faststart", str(out_path)]

        started = time.monotonic()
        code, err = await _run_proc(args, VIDEO_COMPRESS_TIMEOUT)
        took = time.monotonic() - started

        if code != 0 or not out_path.exists():
            msg = (err or b"")[:200].decode("utf-8", "ignore")
            logger.warning(
                "ffmpeg pass %s failed (code=%s, %.1fs): %s", idx + 1, code, took, msg
            )
            cleanup_temp_files(out_path)
            if code == 124:
                return None, "compression timed out"
            if code == 127:
                return None, "ffmpeg binary not found"
            continue

        size = out_path.stat().st_size
        logger.info(
            "ffmpeg pass %s done in %.1fs: %s at %sp%s",
            idx + 1, took, human_bytes(size), height,
            "" if keep_audio else " (muted)",
        )

        if size <= target_bytes:
            note = (
                f"{human_bytes(original)} → {human_bytes(size)} @ {height}p"
                + ("" if keep_audio else " • audio dropped")
            )
            return out_path, note

        cleanup_temp_files(out_path)

    return None, "could not reach target size"


# =================================================
# DISCORD UPLOAD
# =================================================
def discord_upload_limit_bytes(interaction: discord.Interaction) -> int:
    if DISCORD_UPLOAD_LIMIT_FORCE_MB > 0:
        return DISCORD_UPLOAD_LIMIT_FORCE_MB * 1024 * 1024
    inter_limit = getattr(interaction, "filesize_limit", None)
    guild_limit = getattr(interaction.guild, "filesize_limit", None) if interaction.guild else None
    candidates = [v for v in (inter_limit, guild_limit) if isinstance(v, int) and v > 0]
    return max(candidates) if candidates else DISCORD_UPLOAD_LIMIT_FALLBACK_MB * 1024 * 1024


def guild_upload_limit_bytes(guild: Optional[discord.Guild]) -> int:
    """Upload cap for a guild without needing an Interaction (10MB unboosted)."""
    if DISCORD_UPLOAD_LIMIT_FORCE_MB > 0:
        return DISCORD_UPLOAD_LIMIT_FORCE_MB * 1024 * 1024
    limit = getattr(guild, "filesize_limit", None) if guild else None
    if isinstance(limit, int) and limit > 0:
        return limit
    return DISCORD_UPLOAD_LIMIT_FALLBACK_MB * 1024 * 1024


def fit_image_for_discord(image_bytes: bytes, max_bytes: int) -> tuple[bytes, str]:
    target = max(256 * 1024, int(max_bytes - DISCORD_UPLOAD_SAFETY_BYTES))

    if len(image_bytes) <= target and looks_like_image(image_bytes):
        return image_bytes, infer_image_ext(image_bytes)
    if Image is None:
        return image_bytes, infer_image_ext(image_bytes)

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        return image_bytes, infer_image_ext(image_bytes)

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    best_data, best_ext = image_bytes, infer_image_ext(image_bytes)

    def remember(data: bytes, ext: str):
        nonlocal best_data, best_ext
        if len(data) < len(best_data):
            best_data, best_ext = data, ext

    for max_side in (4096, 3072, 2560, 2048, 1792, 1536, 1280, 1024, 896, 768, 640, 512):
        work = img.copy()
        work.thumbnail((max_side, max_side), resample)
        for q in (92, 86, 80, 74, 68, 62, 56, 50, 44, 38, 32, 28, 24):
            with contextlib.suppress(Exception):
                buf = io.BytesIO()
                work.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
                data = buf.getvalue()
                remember(data, "jpg")
                if len(data) <= target:
                    return data, "jpg"

    for max_side in (3072, 2560, 2048, 1536, 1280, 1024, 896, 768, 640, 512):
        work = img.copy()
        work.thumbnail((max_side, max_side), resample)
        for q in (90, 80, 70, 60, 50, 40, 30, 24):
            with contextlib.suppress(Exception):
                buf = io.BytesIO()
                work.save(buf, format="WEBP", quality=q, method=6)
                data = buf.getvalue()
                remember(data, "webp")
                if len(data) <= target:
                    return data, "webp"

    return best_data, best_ext


async def send_image_with_compression(
    channel: discord.abc.Messageable,
    interaction: discord.Interaction,
    image_bytes: bytes,
    embed: discord.Embed,
    content: str,
    filename_prompt: str,
    filename_fallback: str = "image",
) -> Optional[discord.Message]:
    upload_limit = discord_upload_limit_bytes(interaction)

    for scale in (1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.22, 0.18):
        target = max(256 * 1024, int(upload_limit * scale))
        data, ext = fit_image_for_discord(image_bytes, target)
        fname = make_safe_filename(filename_prompt, ext=ext, fallback=filename_fallback)

        fp = io.BytesIO(data)
        fp.seek(0)
        embed.set_image(url=f"attachment://{fname}")

        try:
            return await channel.send(
                content=content,
                embed=embed,
                file=discord.File(fp, filename=fname),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except discord.HTTPException as e:
            if e.status == 413 or getattr(e, "code", None) == 40005:
                continue
            raise
    return None


# =================================================
# MESSAGE -> IMAGE EXTRACTION
# =================================================
def extract_embed_image_urls(msg: discord.Message) -> list[str]:
    urls: list[str] = []
    for emb in msg.embeds:
        if emb.image and emb.image.url:
            urls.append(emb.image.url)
        if emb.thumbnail and emb.thumbnail.url:
            urls.append(emb.thumbnail.url)
        if isinstance(emb.url, str) and emb.url.startswith("http"):
            urls.append(emb.url)
    return list(dict.fromkeys(urls))


async def extract_image_bytes_from_message(
    msg: discord.Message,
    shared_session: Optional[aiohttp.ClientSession] = None,
) -> Optional[bytes]:
    for a in msg.attachments:
        with contextlib.suppress(Exception):
            raw = await a.read()
            if looks_like_image(raw):
                return raw

    urls = extract_embed_image_urls(msg)
    if not urls:
        return None

    own = False
    session = shared_session
    if session is None or session.closed:
        own = True
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))

    try:
        for url in urls[:8]:
            with contextlib.suppress(Exception):
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    raw = await resp.read()
                    if looks_like_image(raw):
                        return raw
    finally:
        if own and session:
            with contextlib.suppress(Exception):
                await session.close()
    return None


def extract_source_image_url(msg: discord.Message) -> Optional[str]:
    for a in msg.attachments:
        ctype = (a.content_type or "").lower()
        name = (a.filename or "").lower()
        if ctype.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            if isinstance(a.url, str) and a.url.startswith("http"):
                return a.url
    for emb in msg.embeds:
        u = (emb.image.url if emb.image else None) or (emb.thumbnail.url if emb.thumbnail else None)
        if isinstance(u, str) and u.startswith("http"):
            return u
        if isinstance(emb.url, str) and emb.url.startswith("http"):
            return emb.url
    return None


# =================================================
# CHANNEL LOCKS
# =================================================
_channel_locks: dict[int, asyncio.Lock] = {}


def get_channel_lock(channel_id: int) -> asyncio.Lock:
    lock = _channel_locks.get(channel_id)
    if lock is None:
        lock = asyncio.Lock()
        _channel_locks[channel_id] = lock
    return lock


# =================================================
# EPHEMERAL TRACKING
# =================================================
_ephemeral_messages: dict[tuple[int, int], list[discord.Message]] = {}


def _ephemeral_key(interaction: discord.Interaction) -> tuple[int, int]:
    return (interaction.guild.id if interaction.guild else 0), interaction.user.id


async def track_ephemeral_message(interaction: discord.Interaction, msg: Optional[discord.Message]):
    if msg:
        _ephemeral_messages.setdefault(_ephemeral_key(interaction), []).append(msg)


async def cleanup_user_ephemerals(interaction: discord.Interaction, delay: float = 0.0):
    if delay > 0:
        await asyncio.sleep(delay)
    for m in _ephemeral_messages.pop(_ephemeral_key(interaction), []):
        with contextlib.suppress(Exception):
            await m.delete()


# Content prefixes that identify user-facing errors/warnings/locks.
# Messages starting with any of these are treated as persistent (not tracked
# for auto-cleanup) so the user has time to read them.
_ERROR_PREFIX_MARKERS: tuple[str, ...] = ("❌", "⛔", "🔒", "🚫", "⚠️", "🛑")


async def send_ephemeral(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    *,
    track: Optional[bool] = None,
    **kwargs,
) -> Optional[discord.Message]:
    """
    Send an ephemeral message.

    Auto-tracking rules (when `track` is None):
    - Views declaring `persistent_ephemeral = True` are NOT tracked.
      -> AnimateEphemeralView etc. stay clickable across cleanups.
    - Messages whose content starts with ❌ ⛔ 🔒 🚫 ⚠️ 🛑 are NOT tracked.
      -> Error/warning/lock messages survive cleanup_user_ephemerals() so the
         user has time to read them.
    - Everything else IS tracked and cleaned up on the next cleanup pass.

    Force behaviour explicitly with track=True/False.
    """
    payload = dict(kwargs)
    payload["ephemeral"] = True
    if content is not None:
        payload["content"] = content

    if track is None:
        view = payload.get("view")
        view_persistent = bool(getattr(view, "persistent_ephemeral", False))
        text = str(content or "").lstrip()
        is_error = text.startswith(_ERROR_PREFIX_MARKERS)
        track = not (view_persistent or is_error)

    try:
        if interaction.response.is_done():
            msg = await interaction.followup.send(wait=True, **payload)
        else:
            await interaction.response.send_message(**payload)
            msg = await interaction.original_response()
        if track:
            await track_ephemeral_message(interaction, msg)
        return msg
    except Exception as e:
        logger.debug("send_ephemeral failed: %s", e)
        return None


# =================================================
# POST DECORATION
# =================================================
SERVER_ANIM_ICON = "<a:01pepper_icon:1377636862847619213>"

RATING_REACTIONS: list[str] = [
    "1️⃣",
    "2️⃣",
    "3️⃣",
    "<:011:1346549711817146400>",
    "<:011pump:1346549688836296787>",
]

_dead_reactions: set[str] = set()


async def add_rating_reactions(
    message: Optional[discord.Message],
    reactions: Optional[list[str]] = None,
) -> int:
    if message is None:
        return 0

    ok = 0
    for emo in (reactions if reactions is not None else RATING_REACTIONS):
        if emo in _dead_reactions:
            continue
        try:
            await message.add_reaction(emo)
            ok += 1
        except discord.HTTPException as e:
            code = getattr(e, "code", None)
            if code in (10014, 50001):
                _dead_reactions.add(emo)
                logger.warning("Reaction %r not available (code=%s), skipping.", emo, code)
            elif e.status == 403:
                logger.warning("No reaction permission in #%s", message.channel)
                return ok
        except Exception as e:
            logger.debug("add_reaction(%r) failed: %s", emo, e)
    return ok


# =================================================
# QUOTA / LOCKED MESSAGES
# =================================================
def build_image_quota_text(member: Optional[discord.Member], state: dict[str, int]) -> str:
    tier = get_member_tier(member)
    lines = [
        f"⛔ Daily image limit reached: **{state['used']}/{state['limit']}** in this 24h window.",
        f"⏳ {format_reset_line(state)}",
        (f"Current: **Tier {tier}** → **{state['limit']} images/24h**" if tier > 0
         else f"Current: **No Tier** → **{state['limit']} images/24h**"),
    ]
    nxt = next_tier(tier)
    if nxt:
        nt, cfg = nxt
        lines.append(
            f"🚀 Next unlock: **Tier {nt}** (<@&{cfg['role_id']}>, Level {cfg['level']}) "
            f"→ **{cfg['image_limit']} images/24h**."
        )
    else:
        lines.append("🏆 You already have the highest image tier.")
    lines.append(f"Tier limits: `{image_tier_line()}` • Default: `{DEFAULT_IMAGE_LIMIT_24H}`")
    return "\n".join(lines)


async def send_image_quota_message(
    interaction: discord.Interaction,
    member: Optional[discord.Member],
    state: dict[str, int],
):
    await send_ephemeral(interaction, build_image_quota_text(member, state))


async def send_tier_locked_message(
    interaction: discord.Interaction,
    min_tier: int,
    feature: str = "This feature",
):
    mentions = " ".join(f"<@&{rid}>" for rid in role_ids_for_tier_and_above(min_tier))
    cfg = TIER_RULES.get(min_tier)
    lvl = f" (Level {cfg['level']}+)" if cfg else ""
    await send_ephemeral(
        interaction,
        f"🔒 {feature} requires **Tier {min_tier}+**{lvl}.\n{mentions}\n\nLevel up to gain access.",
    )


async def send_role_locked_message(
    interaction: discord.Interaction,
    required_role_id: int,
    feature: str = "This feature",
):
    match = tier_for_role_id(required_role_id)
    if match:
        tier, cfg = match
        text = (
            f"🔒 You need **Tier {tier}**, **Level {cfg['level']}** and the Role "
            f"<@&{required_role_id}> to access this feature."
        )
    else:
        text = f"🔒 You need the Role <@&{required_role_id}> to access this feature."
    await send_ephemeral(interaction, text)


async def send_resolution_lock_message(
    interaction: discord.Interaction,
    resolution: str,
    required_tier: int,
    current_tier: int,
):
    cfg = TIER_RULES.get(required_tier)
    if not cfg:
        await send_ephemeral(interaction, f"🔒 **{resolution}** is locked.")
        return
    await send_ephemeral(
        interaction,
        f"🔒 **{resolution}** is locked.\n"
        f"Required: **Tier {required_tier}+** (<@&{cfg['role_id']}>, Level {cfg['level']}+)\n"
        f"Current: **Tier {current_tier}**.",
    )


# =================================================
# PROGRESS EMBED
# =================================================
def build_progress_embed(
    *,
    title: str,
    color: discord.Color,
    user: discord.abc.User,
    prompt: str,
    percent: int,
    status_lines: list[str],
    quota_name: str,
    quota_state: dict[str, int],
    quota_unit: str = "",
    footer: Optional[str] = None,
    footer_icon: Optional[str] = None,
) -> discord.Embed:
    used = int(quota_state.get("used", 0))
    limit = int(quota_state.get("limit", 0))
    remaining = int(quota_state.get("remaining", 0))

    emb = discord.Embed(
        title=title,
        description=user.mention,
        color=color,
        timestamp=utc_now(),
    )
    emb.add_field(name="Prompt", value=f"```{codeblock_safe(trim(prompt, 420))}```", inline=False)
    emb.add_field(name="Progress", value=f"`{progress_bar(percent)} {percent}%`", inline=False)
    emb.add_field(
        name="Status",
        value="\n".join(f"• {ln}" for ln in status_lines if ln),
        inline=False,
    )
    quota_value = (
        f"• Used: `{used}/{limit}{quota_unit}`\n"
        f"• Remaining: `{remaining}{quota_unit}`\n"
        f"• {format_reset_line(quota_state)}"
    )
    emb.add_field(name=quota_name, value=quota_value, inline=False)
    if footer:
        emb.set_footer(text=footer, icon_url=footer_icon)
    return emb


# =================================================
# GENERATION SUCCESS TEXT
# =================================================
_SUCCESS_HEADERS = {
    "image": "🖼️ Image created",
    "face_image": "🎭 Face image created",
    "video": "🎬 Animation completed",
}


def build_generation_success_text(
    state: dict[str, int],
    *,
    kind: str = "image",
    unit: str = "",
    extra: str = "",
    quota_label: str = "Remaining in 24h",
) -> str:
    header = _SUCCESS_HEADERS.get(kind, "✅ Done")
    remaining = int(state.get("remaining", 0))
    limit = int(state.get("limit", 0))
    lines = [
        f"✅ {header}.",
        f"{quota_label}: **{remaining}{unit}** of **{limit}{unit}**.",
        f"⏳ {format_reset_line(state)}",
    ]
    if extra:
        lines.append(extra)
    return "\n".join(lines)


# =================================================
# PROGRESS LOOP
# =================================================
async def run_with_progress(
    task: asyncio.Task,
    progress_msg: Optional[discord.Message],
    est: float,
    start_percent: int,
    end_percent: int,
    make_embed: Callable[[int, float], discord.Embed],
    min_est: float = 6.0,
) -> float:
    started = time.monotonic()
    last_percent = -1
    span = max(0, end_percent - start_percent)

    while not task.done():
        elapsed = time.monotonic() - started
        if elapsed > est * 1.15:
            est = elapsed * 1.20
        ratio = min(0.999, elapsed / max(est, min_est))
        percent = min(end_percent, start_percent + int(ratio * span))
        eta = max(0.0, est - elapsed)

        if percent != last_percent:
            last_percent = percent
            if progress_msg:
                with contextlib.suppress(Exception):
                    await progress_msg.edit(content=None, embed=make_embed(percent, eta))
        await asyncio.sleep(0.8)

    return time.monotonic() - started


# =================================================
# OWNER LOCKED VIEW
# =================================================
class OwnerLockedView(discord.ui.View):
    def __init__(self, owner_id: int, timeout: Optional[float] = 900):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await send_ephemeral(interaction, "🚫 This menu belongs to another user.")
            return False
        return True


# =================================================
# STARTER REPOST REGISTRY
# =================================================
_starter_reposters: dict[int, Callable[[discord.TextChannel], Awaitable[None]]] = {}


def register_starter_reposter(
    channel_id: int,
    fn: Callable[[discord.TextChannel], Awaitable[None]],
) -> None:
    _starter_reposters[int(channel_id)] = fn


def unregister_starter_reposter(channel_id: int) -> None:
    _starter_reposters.pop(int(channel_id), None)


def has_starter_reposter(channel_id: int) -> bool:
    return int(channel_id) in _starter_reposters


async def repost_starter_for_channel(channel: discord.abc.Messageable) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    fn = _starter_reposters.get(channel.id)
    if fn is None:
        return False
    try:
        await fn(channel)
        return True
    except Exception as e:
        logger.warning("repost_starter_for_channel failed (%s): %s", channel.id, e)
        return False


# =================================================
# STARTER MESSAGE HELPER
# =================================================
async def refresh_starter_message(
    channel: discord.TextChannel,
    bot_user_id: Optional[int],
    content: str,
    view_factory: Callable[[], discord.ui.View],
    matcher: Callable[[discord.Message], bool],
    scan_limit: int = 12,
) -> None:
    async with get_channel_lock(channel.id):
        try:
            async for msg in channel.history(limit=scan_limit):
                if bot_user_id is not None and msg.author.id != bot_user_id:
                    continue
                if matcher(msg):
                    with contextlib.suppress(Exception):
                        await msg.delete()
            await channel.send(content, view=view_factory())
        except Exception as e:
            logger.warning("refresh_starter_message failed (%s): %s", channel.id, e)


# =================================================
# VIDEO MODEL CATALOG
# =================================================
# Hard ceiling for a single render across all models.
MAX_VIDEO_RENDER_SECONDS = 25

# Aspect ratios accepted as SOURCE hints from the image cogs. This is not the
# per-model whitelist - that lives in each profile below.
VIDEO_ALLOWED_ASPECTS = {
    "1:1", "16:9", "9:16", "21:9", "4:3", "3:4", "3:2", "2:3", "4:5",
}

# Model IDs (overridable via .env).
VENICE_VIDEO_I2V_MODEL_WAN3 = env_str(
    "VENICE_VIDEO_I2V_MODEL_WAN3", "wan-3-0-image-to-video"
)
VENICE_VIDEO_I2V_MODEL_LTX25 = env_str(
    "VENICE_VIDEO_I2V_MODEL_LTX25", "ltx-2-5-pro-image-to-video"
)
VENICE_VIDEO_I2V_MODEL_MINIMAX = env_str(
    "VENICE_VIDEO_I2V_MODEL_MINIMAX", "minimax-h3-max-image-to-video"
)

# Backwards-compatible aliases so older imports in image_cog / face_cog
# keep resolving. All three now point at live models.
VENICE_VIDEO_I2V_MODEL_ENHANCED = VENICE_VIDEO_I2V_MODEL_WAN3
VENICE_VIDEO_I2V_MODEL_STANDARD = VENICE_VIDEO_I2V_MODEL_WAN3
VENICE_VIDEO_I2V_MODEL_LTX = VENICE_VIDEO_I2V_MODEL_LTX25

# Single source of truth for the animate flow.
# To add another animate button: add ONE entry here. No cog code changes.
#
# button_label            -> label shown on the ephemeral animate button
# button_style            -> discord.ButtonStyle
# resolution              -> exact resolution string the API expects
#                            (note: MiniMax uses uppercase "768P")
# durations               -> allowed durations in seconds; the duration picker
#                            surfaces only these values
# require_aspect_ratio    -> when True, aspect_ratio is sent in the payload
# aspect_ratio_auto       -> the model's "match the source" token
#                            (WAN 3.0 = "adaptive", LTX 2.5 = "auto")
# allowed_aspect_ratios   -> explicit ratios the model accepts; used as a
#                            fallback when the auto token is unavailable
# prompt_limit            -> provider-side prompt character cap
# min_short_side          -> minimum short edge of the source image in px
# est_seconds_per_second  -> rough wall-clock cost per second of output,
#                            used to size the poll timeout per render
VIDEO_MODEL_PROFILES: dict[str, dict[str, Any]] = {
    VENICE_VIDEO_I2V_MODEL_WAN3: {
        "button_label": "🔞 WAN 3.0",
        "button_style": discord.ButtonStyle.danger,
        "resolution": "720p",
        "durations": [5, 10, 15, 20, 25],
        "require_aspect_ratio": True,
        "aspect_ratio_auto": "adaptive",
        "allowed_aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
        "prompt_limit": 20000,
        "min_short_side": 240,
        "est_seconds_per_second": 45,
    },
    VENICE_VIDEO_I2V_MODEL_LTX25: {
        "button_label": "🎥 LTX 2.5 Pro",
        "button_style": discord.ButtonStyle.success,
        "resolution": "720p",
        "durations": [6, 8, 10],
        "require_aspect_ratio": True,
        "aspect_ratio_auto": "auto",
        "allowed_aspect_ratios": ["16:9", "9:16"],
        "prompt_limit": 10000,
        "min_short_side": 0,
        "est_seconds_per_second": 30,
    },
    VENICE_VIDEO_I2V_MODEL_MINIMAX: {
        "button_label": "🔞 MiniMax H3 Max",
        "button_style": discord.ButtonStyle.primary,
        "resolution": "768P",
        "durations": [5, 8, 10, 12, 15],
        "require_aspect_ratio": False,
        "aspect_ratio_auto": None,
        "allowed_aspect_ratios": None,
        "prompt_limit": 10000,
        "min_short_side": 0,
        "est_seconds_per_second": 35,
    },
}

DEFAULT_VIDEO_MODEL = VENICE_VIDEO_I2V_MODEL_WAN3

# Union of all durations across profiles (used by legacy validation paths).
VIDEO_DURATION_CHOICES: list[int] = sorted(
    {d for prof in VIDEO_MODEL_PROFILES.values() for d in prof["durations"]}
)

_VIDEO_PROFILE_FALLBACK: dict[str, Any] = {
    "button_label": "🎬 Animate",
    "button_style": discord.ButtonStyle.primary,
    "resolution": None,
    "durations": [5, 10],
    "require_aspect_ratio": False,
    "aspect_ratio_auto": None,
    "allowed_aspect_ratios": None,
    "prompt_limit": 3000,
    "min_short_side": 0,
    "est_seconds_per_second": 40,
}


def get_video_profile(model_id: str) -> dict[str, Any]:
    """Return the profile for a video model, with sane fallbacks if unknown."""
    return VIDEO_MODEL_PROFILES.get((model_id or "").strip(), _VIDEO_PROFILE_FALLBACK)


def is_known_video_model(model_id: str) -> bool:
    return (model_id or "").strip() in VIDEO_MODEL_PROFILES


def known_video_model_labels() -> list[str]:
    return [p["button_label"] for p in VIDEO_MODEL_PROFILES.values()]


def get_model_durations(model_id: str) -> list[int]:
    return list(get_video_profile(model_id)["durations"])


def get_model_resolution(model_id: str) -> Optional[str]:
    return get_video_profile(model_id).get("resolution")


def get_model_prompt_limit(model_id: str) -> int:
    return int(get_video_profile(model_id).get("prompt_limit") or 3000)


def get_model_min_short_side(model_id: str) -> int:
    return int(get_video_profile(model_id).get("min_short_side") or 0)


def get_model_speed_factor(model_id: str) -> int:
    return int(get_video_profile(model_id).get("est_seconds_per_second") or 40)


def resolve_video_aspect_ratio(model_id: str, source_aspect: str) -> Optional[str]:
    """
    Decide the aspect_ratio value for the queue payload.

    - Model without aspect_ratio support (MiniMax) -> None, field is omitted.
    - Model with an auto token -> that token ('adaptive' for WAN 3.0,
      'auto' for LTX 2.5 Pro). The provider then matches the source image.
    - Otherwise -> closest numeric match from allowed_aspect_ratios.
    """
    profile = get_video_profile(model_id)
    if not profile.get("require_aspect_ratio"):
        return None

    auto_token = profile.get("aspect_ratio_auto")
    if auto_token:
        return auto_token

    allowed = profile.get("allowed_aspect_ratios") or []
    if not allowed:
        return None
    return closest_aspect_ratio(source_aspect or "16:9", list(allowed))


def check_source_image_for_model(
    model_id: str, image_bytes: Optional[bytes]
) -> Optional[str]:
    """
    Validate a source image against model constraints.
    Returns an error string, or None when the image is acceptable
    (or cannot be inspected, in which case the provider decides).
    """
    min_side = get_model_min_short_side(model_id)
    if min_side <= 0 or not image_bytes:
        return None
    dims = image_dimensions(image_bytes)
    if not dims:
        return None
    if min(dims) < min_side:
        return (
            f"Source image too small: short edge is {min(dims)}px, "
            f"this model requires at least {min_side}px."
        )
    return None


def estimate_render_seconds(model_id: str, output_seconds: int) -> int:
    """Rough wall-clock estimate for a render, used to size timeouts."""
    return int(max(1, output_seconds) * get_model_speed_factor(model_id)) + 120


# =================================================
# ANIMATE UI
# =================================================
class AnimatePromptModal(discord.ui.Modal):
    def __init__(
        self,
        owner_id: int,
        source_channel_id: int,
        source_message_id: int,
        base_prompt: str,
        ratio: str,
        model_id: str,
    ):
        self.owner_id = owner_id
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id
        self.base_prompt = (base_prompt or "").strip()
        self.ratio = ratio
        self.model_id = model_id
        super().__init__(title="🎬 Animate Image • Video Prompt")

        # Discord modal inputs cap at 4000 chars regardless of provider limits.
        field_max = min(4000, get_model_prompt_limit(model_id))

        self.video_prompt = discord.ui.TextInput(
            label="Video prompt",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=field_max,
            default=(self.base_prompt[:field_max] if self.base_prompt else ""),
            placeholder="Describe motion, camera movement, atmosphere...",
        )
        self.add_item(self.video_prompt)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await send_ephemeral(interaction, "🚫 This modal does not belong to you.")
            return
        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            await send_ephemeral(interaction, "❌ This action is server-only.")
            return

        if not is_known_video_model(self.model_id):
            logger.warning("Stale animate button used: %s", self.model_id)
            await send_ephemeral(
                interaction,
                "❌ This animate button points at a retired model. "
                "Generate a new image to get fresh buttons.",
            )
            return

        final_prompt = (
            (self.video_prompt.value or "").strip()
            or self.base_prompt
            or "Animate this image with natural motion."
        )

        video_cog = interaction.client.get_cog("VeniceVideoCog")
        if not video_cog:
            await send_ephemeral(interaction, "❌ VeniceVideoCog is not loaded.")
            return

        try:
            info = await video_cog.get_remaining_info(interaction.guild.id, interaction.user)
        except Exception as e:
            logger.error("get_remaining_info failed: %s", e)
            await send_ephemeral(interaction, "❌ Could not load your video quota right now.")
            return

        budget = int(info["limit"])
        if budget <= 0:
            await send_ephemeral(
                interaction, "🎬 Video rendering is locked for members without a Tier role."
            )
            return

        remaining = int(info["remaining"])
        model_durations = get_model_durations(self.model_id)
        cap = min(MAX_VIDEO_RENDER_SECONDS, remaining)
        allowed = [s for s in model_durations if s <= cap]

        if not allowed:
            min_dur = min(model_durations) if model_durations else 0
            await send_ephemeral(
                interaction,
                f"⛔ Not enough seconds for this model.\n"
                f"Used **{info['used']}/{budget}s** • remaining **{remaining}s**.\n"
                f"This model requires at least **{min_dur}s** per render.\n"
                f"⏳ {format_reset_line(info)}\n"
                f"Current tier: **T{info['tier']}**.\n"
                f"Tier budgets: `{video_tier_line()}`",
            )
            return

        profile = get_video_profile(self.model_id)
        await send_ephemeral(
            interaction,
            content=(
                f"✅ Video prompt set • **{profile['button_label']}** "
                f"({profile['resolution']}).\n"
                f"⏱ Choose length (remaining today: **{remaining}s**, "
                f"max per render: **{MAX_VIDEO_RENDER_SECONDS}s**):"
            ),
            view=AnimateDurationView(
                owner_id=self.owner_id,
                source_channel_id=self.source_channel_id,
                source_message_id=self.source_message_id,
                prompt_text=final_prompt,
                ratio=self.ratio,
                allowed_durations=allowed,
                model_id=self.model_id,
            ),
        )


class AnimateDurationView(OwnerLockedView):
    def __init__(
        self,
        owner_id: int,
        source_channel_id: int,
        source_message_id: int,
        prompt_text: str,
        ratio: str,
        allowed_durations: list[int],
        model_id: str,
    ):
        super().__init__(owner_id=owner_id, timeout=300)
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id
        self.prompt_text = prompt_text
        self.ratio = ratio
        self.model_id = model_id
        self.allowed_durations = sorted({d for d in allowed_durations if d > 0})

        if not self.allowed_durations:
            self.add_item(discord.ui.Button(
                label="No duration available",
                disabled=True,
                style=discord.ButtonStyle.secondary,
            ))
            return

        for idx, sec in enumerate(self.allowed_durations):
            style = (
                discord.ButtonStyle.success if sec <= 8
                else discord.ButtonStyle.danger if sec >= 20
                else discord.ButtonStyle.primary
            )
            b = discord.ui.Button(label=f"{sec} seconds", style=style, row=idx // 5)
            b.callback = self._make_callback(sec)
            self.add_item(b)

    def _make_callback(self, seconds: int):
        async def _cb(interaction: discord.Interaction):
            await self._run(interaction, seconds)
        return _cb

    async def _run(self, interaction: discord.Interaction, seconds: int):
        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            await send_ephemeral(interaction, "❌ This action is server-only.")
            return

        video_cog = interaction.client.get_cog("VeniceVideoCog")
        if not video_cog:
            await send_ephemeral(interaction, "❌ VeniceVideoCog is not loaded.")
            return

        if seconds > MAX_VIDEO_RENDER_SECONDS:
            await send_ephemeral(
                interaction, f"❌ Max duration per render is {MAX_VIDEO_RENDER_SECONDS} seconds."
            )
            return

        if seconds not in get_model_durations(self.model_id):
            allowed = ", ".join(f"{s}s" for s in get_model_durations(self.model_id))
            await send_ephemeral(
                interaction, f"❌ Allowed durations for this model are {allowed}."
            )
            return

        with contextlib.suppress(Exception):
            rem = await video_cog.get_remaining_info(interaction.guild.id, interaction.user)
            if int(rem.get("remaining", 0)) < seconds:
                await send_ephemeral(
                    interaction,
                    f"⛔ Not enough video seconds left.\n"
                    f"Remaining: **{rem.get('remaining', 0)}s** of **{rem.get('limit', 0)}s**.",
                )
                return

        await interaction.response.defer(ephemeral=True)

        channel = interaction.client.get_channel(self.source_channel_id)
        if channel is None:
            with contextlib.suppress(Exception):
                channel = await interaction.client.fetch_channel(self.source_channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            await send_ephemeral(interaction, "❌ Source channel not usable.")
            return

        try:
            source_message = await channel.fetch_message(self.source_message_id)
        except Exception:
            await send_ephemeral(interaction, "❌ Source image message not found.")
            return

        source_image_url = extract_source_image_url(source_message)

        face_cog = interaction.client.get_cog("VeniceFaceCog")
        image_cog = interaction.client.get_cog("VeniceImageCog")
        shared_session = (
            getattr(face_cog, "session", None) or getattr(image_cog, "session", None)
        )
        image_bytes = await extract_image_bytes_from_message(source_message, shared_session)

        if not source_image_url and not image_bytes:
            await send_ephemeral(interaction, "❌ No usable source image found.")
            return

        size_error = check_source_image_for_model(self.model_id, image_bytes)
        if size_error:
            await send_ephemeral(interaction, f"❌ {size_error}")
            return

        aspect = self.ratio if self.ratio in VIDEO_ALLOWED_ASPECTS else "16:9"
        prompt = (self.prompt_text or "").strip() or "Animate this image with natural motion."
        prompt = trim(prompt, get_model_prompt_limit(self.model_id))

        logger.info(
            "Animate request | user=%s model=%s dur=%ss ar=%s src=%s",
            interaction.user.id, self.model_id, seconds, aspect,
            "url" if source_image_url else "bytes",
        )

        await video_cog.animate_image_to_video(
            interaction=interaction,
            image_url=source_image_url or "",
            image_bytes=image_bytes,
            prompt=prompt,
            aspect=aspect,
            seconds=seconds,
            target_channel=channel,
            model_id=self.model_id,
        )
        await cleanup_user_ephemerals(interaction)


class AnimateEphemeralView(OwnerLockedView):
    """
    Ephemeral view attached to a successful image/face-image post.

    Buttons are built dynamically from VIDEO_MODEL_PROFILES, so adding a new
    animate variant is a single-line edit in that table - no cog change needed.

    persistent_ephemeral = True means send_ephemeral does NOT add this message
    to the ephemeral tracking list. Therefore cleanup_user_ephemerals() will
    NOT delete it after a video render, and the buttons remain clickable for
    the full 30 minute view timeout.
    """
    persistent_ephemeral = True

    def __init__(
        self,
        owner_id: int,
        source_channel_id: int,
        source_message_id: int,
        prompt_text: str,
        ratio: str,
    ):
        super().__init__(owner_id=owner_id, timeout=1800)
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id
        self.prompt_text = prompt_text
        self.ratio = ratio

        for idx, (model_id, profile) in enumerate(VIDEO_MODEL_PROFILES.items()):
            btn = discord.ui.Button(
                label=profile["button_label"],
                style=profile["button_style"],
                row=idx // 5,
            )
            btn.callback = self._make_callback(model_id)
            self.add_item(btn)

    def _make_callback(self, model_id: str):
        async def _cb(interaction: discord.Interaction):
            if not isinstance(interaction.user, discord.Member) or not interaction.guild:
                await send_ephemeral(interaction, "❌ This action is server-only.")
                return
            await interaction.response.send_modal(
                AnimatePromptModal(
                    owner_id=self.owner_id,
                    source_channel_id=self.source_channel_id,
                    source_message_id=self.source_message_id,
                    base_prompt=self.prompt_text,
                    ratio=self.ratio,
                    model_id=model_id,
                )
            )
        return _cb