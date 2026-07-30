# venice_face_cog.py
import asyncio
import base64
import binascii
import contextlib
import io
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

try:
    from PIL import Image
except Exception:
    Image = None

load_dotenv()
logger = logging.getLogger("venice_face_cog")

# =================================================
# ENV
# =================================================
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
VENICE_IMAGE_EDIT_URL = os.getenv(
    "VENICE_IMAGE_EDIT_URL",
    "https://api.venice.ai/api/v1/image/edit",
)

if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


DISCORD_UPLOAD_LIMIT_FORCE_MB = _env_int("DISCORD_UPLOAD_LIMIT_FORCE_MB", 0)
DISCORD_UPLOAD_LIMIT_FALLBACK_MB = _env_int("DISCORD_UPLOAD_LIMIT_FALLBACK_MB", 50)
DISCORD_UPLOAD_SAFETY_BYTES = _env_int("DISCORD_UPLOAD_SAFETY_BYTES", 512 * 1024)

# =================================================
# >>>>>>>>>>>>>>>>>>>>  HIER ANPASSEN  <<<<<<<<<<<<<<<<<<<<
# =================================================

# Channel in dem dieser Cog aktiv ist
FACE_CHANNEL_ID = 1416468498305126522

# URL zum Referenz-Gesichtsbild (öffentlich erreichbar). Wird gecacht.
FACE_REFERENCE_URL = "hhttps://cdn.discordapp.com/attachments/1383652563408392232/1532219730218450965/piper_close_up-1_nude.jpg"

# Rollen die diesen Cog benutzen dürfen (User braucht MINDESTENS EINE davon).
# Default: Tier-2-Rolle. Höhere Tier-Rollen aus dem alten Bot einfach hinzufügen.
REQUIRED_ROLE_IDS: set[int] = {
    1375147276413964408,   # Tier 2
    1376592697606930593,   # Tier 3
    1381791848875430069,   # Tier 4
    1375666588404940830,   # Tier 5
    1375584380914896978,   # Tier 6
    1346414581643219029,   # Tier 7
}

# Allgemeiner Hidden-Suffix - wird an JEDEN User-Prompt angehängt
PROMPT_HIDDEN_SUFFIX = (
    " photorealistic, sharp focus, cinematic lighting, high detail. Piper: 20years old Woman with pale skin, freckles, green eyes and red bangs. Her mouth has a slight overbite. She wears glowing green wireless headphones without cables. Her height is 155cm. 
Her Skin is sweaty and wet."
    "professional photography"
)

# Face-Instruction - sagt dem Modell wie das Referenzbild zu nutzen ist
FACE_INSTRUCTION_SUFFIX = (
    " IMPORTANT: The reference image provided shows a specific woman's face. "
    "Whenever a woman appears in the generated scene, she MUST have this exact face "
    "and identity - same facial features, same eyes, same nose, same mouth, same skin tone. "
    "Preserve her facial identity perfectly. Only the environment, pose, clothing and "
    "context should follow the text description; the face itself must remain identical "
    "to the reference."
)

# Fixe Ausgabe-Parameter
FACE_ASPECT_RATIO = "auto"
FACE_RESOLUTION = "1K"
FACE_SAFE_MODE = False
FACE_OUTPUT_FORMAT = "png"

# =================================================
# SHARED QUOTA (gleiches File wie Image-Cog!)
# =================================================
IMAGE_QUOTA_FILE = os.getenv("IMAGE_QUOTA_FILE", "goonhut_image_quota.json")
IMAGE_WINDOW_SECONDS = 24 * 60 * 60

# =================================================
# TIER RULES (identisch zum Image-Cog, damit Limits konsistent bleiben)
# =================================================
TIER_RULES: dict[int, dict[str, int]] = {
    1: {"role_id": 1377051179615522926, "level": 3,  "image_limit": 10},
    2: {"role_id": 1375147276413964408, "level": 11, "image_limit": 15},
    3: {"role_id": 1376592697606930593, "level": 21, "image_limit": 20},
    4: {"role_id": 1381791848875430069, "level": 33, "image_limit": 25},
    5: {"role_id": 1375666588404940830, "level": 43, "image_limit": 30},
    6: {"role_id": 1375584380914896978, "level": 69, "image_limit": 69},
    7: {"role_id": 1346414581643219029, "level": 99, "image_limit": 300},
}
DEFAULT_IMAGE_LIMIT_24H = 5

# =================================================
# MODELLE (Reihenfolge = Anzeigereihenfolge; erstes = Default)
# =================================================
AB18_ICON = "🔞"
MODELS: dict[str, dict[str, Any]] = {
    "qwen-edit-uncensored": {
        "label": f"🧠 Qwen Edit Uncensored {AB18_ICON}",
        "prompt_limit": 3000,
    },
    "seedream-v5-pro-edit": {
        "label": f"🌊 Seedream V5 Pro Edit {AB18_ICON}",
        "prompt_limit": 5000,
    },
    "nano-banana-2-edit": {
        "label": "🍌 Nano Banana 2 Edit",
        "prompt_limit": 10000,
    },
}
MODEL_ORDER = list(MODELS.keys())

BUTTON_MESSAGE_TEXT = "💡 Choose a model for a 🎭 face-consistent image!"
LEGACY_STARTER_TEXTS = {
    BUTTON_MESSAGE_TEXT,
    "💡 Choose Model for 🎭 face-consistent image!",
}
RECENT_SCAN_LIMIT = 12
SERVER_ANIM_ICON = "<a:01pepper_icon:1377636862847619213>"
NO_MODEL_VALUE = "__no_models__"

MAX_VIDEO_RENDER_SECONDS = 15
VIDEO_DURATION_CHOICES = [5, 10, 15]
VIDEO_ALLOWED_ASPECTS = {"1:1", "16:9", "9:16", "21:9", "3:2", "2:3", "3:4", "4:5"}


# =================================================
# QUOTA STORE (identisch zum Image-Cog - gleiche Datei, gleiches Format)
# =================================================
class RollingQuotaStore:
    def __init__(self, file_path: str, window_seconds: int = IMAGE_WINDOW_SECONDS):
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
            return {
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
                "start": start,
                "reset_in": max(0, self.window_seconds - (now_ts - start)) if start > 0 else 0,
            }

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


image_quota = RollingQuotaStore(IMAGE_QUOTA_FILE)


# =================================================
# ROLE / TIER HELPERS
# =================================================
def user_has_required_role(member: Optional[discord.Member]) -> bool:
    if not isinstance(member, discord.Member):
        return False
    return any(r.id in REQUIRED_ROLE_IDS for r in member.roles)


def get_member_tier(member: Optional[discord.Member]) -> int:
    if not isinstance(member, discord.Member):
        return 0
    role_ids = {r.id for r in member.roles}
    for tier, cfg in sorted(TIER_RULES.items(), key=lambda x: x[0], reverse=True):
        if cfg["role_id"] in role_ids:
            return tier
    return 0


def get_image_limit_for_member(member: Optional[discord.Member]) -> int:
    tier = get_member_tier(member)
    return DEFAULT_IMAGE_LIMIT_24H if tier <= 0 else int(TIER_RULES[tier]["image_limit"])


# =================================================
# LOCKS + EPHEMERAL TRACKING
# =================================================
_channel_locks: dict[int, asyncio.Lock] = {}
_ephemeral_messages: dict[tuple[int, int], list[discord.Message]] = {}


def get_channel_lock(channel_id: int) -> asyncio.Lock:
    lock = _channel_locks.get(channel_id)
    if lock is None:
        lock = asyncio.Lock()
        _channel_locks[channel_id] = lock
    return lock


def _ephemeral_key(interaction: discord.Interaction) -> tuple[int, int]:
    guild_id = interaction.guild.id if interaction.guild else 0
    return guild_id, interaction.user.id


async def track_ephemeral_message(interaction: discord.Interaction, msg: Optional[discord.Message]):
    if msg:
        _ephemeral_messages.setdefault(_ephemeral_key(interaction), []).append(msg)


async def cleanup_user_ephemerals(interaction: discord.Interaction):
    for m in _ephemeral_messages.pop(_ephemeral_key(interaction), []):
        with contextlib.suppress(Exception):
            await m.delete()


# =================================================
# FACE REFERENCE CACHE
# =================================================
class FaceReferenceCache:
    def __init__(self, url: str):
        self.url = url
        self._b64: Optional[str] = None
        self._lock = asyncio.Lock()

    async def get_base64(self, session: aiohttp.ClientSession, force: bool = False) -> Optional[str]:
        if self._b64 and not force:
            return self._b64
        async with self._lock:
            if self._b64 and not force:
                return self._b64
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with session.get(self.url, timeout=timeout) as resp:
                    if resp.status != 200:
                        logger.error("Face reference fetch failed: HTTP %s", resp.status)
                        return None
                    raw = await resp.read()
                    if not _looks_like_image(raw):
                        logger.error("Face reference URL did not return an image")
                        return None
                    self._b64 = base64.b64encode(raw).decode("utf-8")
                    logger.info("Face reference cached: %d bytes", len(raw))
                    return self._b64
            except Exception as e:
                logger.error("Face reference load error: %s", e)
                return None


face_ref_cache = FaceReferenceCache(FACE_REFERENCE_URL)


# =================================================
# HELPERS
# =================================================
def _trim(text: str, limit: int) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else (t[:limit] + " [...]")


def _codeblock_safe(text: str) -> str:
    return (text or "").replace("```", "'''").strip()


def _progress_bar(percent: int, blocks: int = 14) -> str:
    p = max(0, min(100, int(percent)))
    filled = int(blocks * p / 100)
    return "█" * filled + "░" * (blocks - filled)


def _eta_text(seconds_float: float) -> str:
    s = max(0, int(round(seconds_float)))
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60}s"


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


def get_model_label(model_id: str) -> str:
    return (MODELS.get(model_id) or {}).get("label", model_id)


def make_safe_filename(prompt: str, ext: str = "png") -> str:
    base = "_".join((prompt or "").split()[:5]) or "faceimg"
    base = re.sub(r"[^a-zA-Z0-9_]", "_", base)
    ext = (ext or "png").lower().strip(".")
    return f"{base}_{int(time.time_ns())}_{uuid.uuid4().hex[:8]}.{ext}"


def _looks_like_image(binary: bytes) -> bool:
    if not binary or len(binary) < 12:
        return False
    return (
        binary.startswith(b"\x89PNG\r\n\x1a\n")
        or binary.startswith(b"\xff\xd8\xff")
        or (binary[:4] == b"RIFF" and binary[8:12] == b"WEBP")
        or binary.startswith((b"GIF87a", b"GIF89a"))
    )


def _infer_image_ext(binary: bytes) -> str:
    if binary.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if binary.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if binary[:4] == b"RIFF" and binary[8:12] == b"WEBP":
        return "webp"
    if binary.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return "png"


def _discord_upload_limit_bytes(interaction: discord.Interaction) -> int:
    if DISCORD_UPLOAD_LIMIT_FORCE_MB > 0:
        return DISCORD_UPLOAD_LIMIT_FORCE_MB * 1024 * 1024
    inter_limit = getattr(interaction, "filesize_limit", None)
    guild_limit = getattr(interaction.guild, "filesize_limit", None) if interaction.guild else None
    candidates = [v for v in (inter_limit, guild_limit) if isinstance(v, int) and v > 0]
    return max(candidates) if candidates else DISCORD_UPLOAD_LIMIT_FALLBACK_MB * 1024 * 1024


def _fit_image_for_discord(image_bytes: bytes, max_bytes: int) -> tuple[bytes, str]:
    target = max(256 * 1024, int(max_bytes - DISCORD_UPLOAD_SAFETY_BYTES))
    if len(image_bytes) <= target and _looks_like_image(image_bytes):
        return image_bytes, _infer_image_ext(image_bytes)
    if Image is None:
        return image_bytes, _infer_image_ext(image_bytes)
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        return image_bytes, _infer_image_ext(image_bytes)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    best_data, best_ext = image_bytes, _infer_image_ext(image_bytes)

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
    return best_data, best_ext


def _b64_to_bytes(s: str) -> Optional[bytes]:
    if not s:
        return None
    s = s.strip()
    if s.startswith("data:image") and "," in s:
        s = s.split(",", 1)[1]
    try:
        return base64.b64decode(s)
    except (binascii.Error, ValueError):
        return None


def _extract_image_from_json_obj(obj: Any) -> Optional[bytes]:
    if isinstance(obj, dict):
        for key in ("image", "image_base64", "imageBase64", "b64_json", "base64", "edited_image"):
            val = obj.get(key)
            if isinstance(val, str):
                out = _b64_to_bytes(val)
                if out and _looks_like_image(out):
                    return out
        for _, val in list(obj.items())[:20]:
            out = _extract_image_from_json_obj(val)
            if out:
                return out
    elif isinstance(obj, list):
        for item in obj[:20]:
            out = _extract_image_from_json_obj(item)
            if out:
                return out
    elif isinstance(obj, str):
        out = _b64_to_bytes(obj)
        if out and _looks_like_image(out):
            return out
    return None


async def _extract_image_from_response(resp: aiohttp.ClientResponse) -> Optional[bytes]:
    raw = await resp.read()
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "image/" in ctype and _looks_like_image(raw):
        return raw
    try:
        data = json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return raw if _looks_like_image(raw) else None
    out = _extract_image_from_json_obj(data)
    return out if out and _looks_like_image(out) else None


def _extract_embed_image_urls(msg: discord.Message) -> list[str]:
    urls: list[str] = []
    for emb in msg.embeds:
        if emb.image and emb.image.url:
            urls.append(emb.image.url)
        if emb.thumbnail and emb.thumbnail.url:
            urls.append(emb.thumbnail.url)
        if emb.url and isinstance(emb.url, str) and emb.url.startswith("http"):
            urls.append(emb.url)
    return list(dict.fromkeys(urls))


async def _extract_image_bytes_from_message(
    msg: discord.Message, shared_session: Optional[aiohttp.ClientSession]
) -> Optional[bytes]:
    for a in msg.attachments:
        with contextlib.suppress(Exception):
            raw = await a.read()
            if _looks_like_image(raw):
                return raw
    urls = _extract_embed_image_urls(msg)
    if not urls:
        return None
    own = False
    session = shared_session
    if session is None or session.closed:
        own = True
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
    try:
        assert session is not None
        for url in urls[:8]:
            with contextlib.suppress(Exception):
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    raw = await resp.read()
                    if _looks_like_image(raw):
                        return raw
    finally:
        if own and session:
            with contextlib.suppress(Exception):
                await session.close()
    return None


def _extract_source_image_url(msg: discord.Message) -> Optional[str]:
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


def is_starter_message(msg: discord.Message) -> bool:
    if msg.embeds or msg.attachments:
        return False
    if msg.components:
        for row in msg.components:
            for child in row.children:
                cid = getattr(child, "custom_id", None)
                if isinstance(cid, str) and cid.startswith("venice_face_model_select:"):
                    return True
    return (msg.content or "").strip() in LEGACY_STARTER_TEXTS


def _build_progress_embed(
    user: discord.abc.User,
    prompt: str,
    model_label: str,
    percent: int,
    eta_sec: float,
    stage: str,
    quota_used: int,
    quota_limit: int,
    quota_remaining: int,
) -> discord.Embed:
    bar = _progress_bar(percent)
    preview = _codeblock_safe(_trim(prompt, 420))
    emb = discord.Embed(
        title="🎭 FACE IMAGE RENDER",
        description=user.mention,
        color=discord.Color.purple(),
        timestamp=datetime.now(timezone.utc),
    )
    emb.add_field(name="Prompt", value=f"```{preview}```", inline=False)
    emb.add_field(name="Progress", value=f"`{bar} {percent}%`", inline=False)
    emb.add_field(name="Status", value=f"• {stage}\n• ETA: `{_eta_text(eta_sec)}`", inline=False)
    emb.add_field(
        name="Quota (24h, shared)",
        value=f"• Used: `{quota_used}/{quota_limit}`\n• Remaining: `{quota_remaining}`",
        inline=False,
    )
    emb.set_footer(text=f"{model_label} • {FACE_ASPECT_RATIO} • {FACE_RESOLUTION}")
    return emb


# =================================================
# API CALL
# =================================================
async def venice_edit(
    session: aiohttp.ClientSession,
    model_id: str,
    prompt: str,
    image_b64: str,
    retries: int = 2,
) -> tuple[Optional[bytes], Optional[str]]:
    headers = {
        "Authorization": f"Bearer {VENICE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "image": image_b64,
        "aspect_ratio": FACE_ASPECT_RATIO,
        "resolution": FACE_RESOLUTION,
        "safe_mode": FACE_SAFE_MODE,
        "output_format": FACE_OUTPUT_FORMAT,
    }
    req_id = uuid.uuid4().hex[:8]
    logger.info("[FACE %s] -> POST edit model=%s prompt_len=%d", req_id, model_id, len(prompt))
    timeout = aiohttp.ClientTimeout(total=180, connect=20, sock_read=150)
    last_error: Optional[str] = None

    for attempt in range(retries + 1):
        try:
            async with session.post(VENICE_IMAGE_EDIT_URL, headers=headers, json=payload, timeout=timeout) as resp:
                logger.info("[FACE %s] <- status=%s attempt=%s", req_id, resp.status, attempt + 1)
                if resp.status == 200:
                    img = await _extract_image_from_response(resp)
                    if img and _looks_like_image(img):
                        return img, None
                    last_error = "Empty/invalid image response"
                elif resp.status in (429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                else:
                    try:
                        body = await resp.text()
                        last_error = f"HTTP {resp.status}: {body[:400]}"
                        logger.error("[FACE %s] error body: %s", req_id, body[:500])
                    except Exception:
                        last_error = f"HTTP {resp.status}"
                    return None, last_error
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = f"Transport: {e}"
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return None, last_error
        except Exception as e:
            logger.exception("[FACE %s] unexpected: %s", req_id, e)
            return None, f"Unexpected: {e}"
    return None, last_error


# =================================================
# EPHEMERAL SENDER
# =================================================
async def send_ephemeral(interaction: discord.Interaction, content: Optional[str] = None, **kwargs) -> Optional[discord.Message]:
    payload = dict(kwargs)
    payload["ephemeral"] = True
    if content is not None:
        payload["content"] = content
    try:
        if interaction.response.is_done():
            msg = await interaction.followup.send(wait=True, **payload)
        else:
            await interaction.response.send_message(**payload)
            msg = await interaction.original_response()
        await track_ephemeral_message(interaction, msg)
        return msg
    except Exception:
        return None


async def send_role_locked_message(interaction: discord.Interaction):
    mentions = " ".join(f"<@&{rid}>" for rid in sorted(REQUIRED_ROLE_IDS))
    await send_ephemeral(
        interaction,
        f"🔒 This feature requires one of these roles:\n{mentions}\n\n"
        f"Level up to gain access."
    )


async def send_quota_exhausted_message(interaction: discord.Interaction, state: dict[str, int]):
    await send_ephemeral(
        interaction,
        f"⛔ Daily image limit reached: **{state['used']}/{state['limit']}** in this 24h window "
        f"(shared with all image tools).\n"
        f"⏳ Reset in **{_seconds_human(state['reset_in'])}**."
    )


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
# UI - OWNER LOCKED
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
# UI - STARTER
# =================================================
def build_model_options() -> list[discord.SelectOption]:
    opts: list[discord.SelectOption] = []
    for i, mid in enumerate(MODEL_ORDER):
        opts.append(discord.SelectOption(
            label=MODELS[mid]["label"],
            value=mid,
            default=(i == 0),
        ))
    if not opts:
        opts.append(discord.SelectOption(label="No models available", value=NO_MODEL_VALUE))
    return opts


class StarterModelSelect(discord.ui.Select):
    def __init__(self, session_ref: Callable[[], Optional[aiohttp.ClientSession]], channel_id: int):
        self._session_ref = session_ref
        self.channel_id = channel_id
        super().__init__(
            placeholder="🎭 Choose a face-consistent model...",
            min_values=1,
            max_values=1,
            options=build_model_options(),
            custom_id=f"venice_face_model_select:{channel_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        # Role Gate
        if not user_has_required_role(interaction.user if isinstance(interaction.user, discord.Member) else None):
            await send_role_locked_message(interaction)
            return

        selected = self.values[0]
        if selected == NO_MODEL_VALUE:
            await send_ephemeral(interaction, "❌ No models available.")
            return
        if selected not in MODELS:
            await send_ephemeral(interaction, "❌ Unknown model.")
            return

        session = self._session_ref()
        if session is None or session.closed:
            await send_ephemeral(interaction, "❌ Backend session not ready. Try again in a moment.")
            return

        await interaction.response.send_modal(FacePromptModal(session, selected, interaction.user.id))


class StarterView(discord.ui.View):
    def __init__(self, session_ref: Callable[[], Optional[aiohttp.ClientSession]], channel_id: int):
        super().__init__(timeout=None)
        self.add_item(StarterModelSelect(session_ref, channel_id))


# =================================================
# UI - MODAL
# =================================================
class FacePromptModal(discord.ui.Modal):
    def __init__(self, session: aiohttp.ClientSession, model_id: str, owner_id: int):
        self.session = session
        self.model_id = model_id
        self.owner_id = owner_id
        cfg = MODELS[model_id]
        super().__init__(title=f"🎭 {get_model_label(model_id)}"[:45])
        modal_max = min(int(cfg["prompt_limit"]), 4000)
        self.prompt = discord.ui.TextInput(
            label="Describe the scene",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=modal_max,
            placeholder="Woman in a red dress on a rainy Tokyo street at night...",
        )
        self.add_item(self.prompt)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await send_ephemeral(interaction, "🚫 This modal does not belong to you.")
            return
        if not user_has_required_role(interaction.user if isinstance(interaction.user, discord.Member) else None):
            await send_role_locked_message(interaction)
            return
        user_prompt = (self.prompt.value or "").strip()
        if not user_prompt:
            await send_ephemeral(interaction, "❌ Empty prompt.")
            return
        await interaction.response.defer(ephemeral=True)
        await run_face_generation(
            interaction=interaction,
            session=self.session,
            model_id=self.model_id,
            user_prompt=user_prompt,
        )


# =================================================
# GENERATION FLOW
# =================================================
async def run_face_generation(
    interaction: discord.Interaction,
    session: aiohttp.ClientSession,
    model_id: str,
    user_prompt: str,
):
    if not interaction.guild:
        await send_ephemeral(interaction, "❌ Server-only.")
        return
    if not interaction.channel:
        await send_ephemeral(interaction, "❌ Channel unavailable.")
        return

    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if not user_has_required_role(member):
        await send_role_locked_message(interaction)
        return

    progress_msg: Optional[discord.Message] = None
    quota_success = False
    token_quota: Optional[dict[str, int]] = None

    try:
        # Quota reservieren (SHARED mit Image-Cog)
        image_limit = get_image_limit_for_member(member)
        ok, state, token_quota = await image_quota.reserve(
            interaction.guild.id, interaction.user.id, image_limit, 1
        )
        if not ok:
            await send_quota_exhausted_message(interaction, state)
            return

        # Referenzbild laden
        image_b64 = await face_ref_cache.get_base64(session)
        if not image_b64:
            await send_ephemeral(
                interaction,
                "❌ Could not load face reference image. Check `FACE_REFERENCE_URL` in code.",
            )
            return

        # Prompt bauen
        full_prompt = (
            f"{user_prompt.strip()}"
            f"{PROMPT_HIDDEN_SUFFIX}"
            f"{FACE_INSTRUCTION_SUFFIX}"
        ).strip()

        model_label = get_model_label(model_id)
        est_time = 35.0

        progress_embed = _build_progress_embed(
            user=interaction.user,
            prompt=user_prompt,
            model_label=model_label,
            percent=0,
            eta_sec=est_time,
            stage="Sending request to Venice...",
            quota_used=int(state["used"]),
            quota_limit=int(state["limit"]),
            quota_remaining=int(state["remaining"]),
        )
        progress_msg = await send_ephemeral(interaction, embed=progress_embed)

        gen_task = asyncio.create_task(venice_edit(session, model_id, full_prompt, image_b64))

        def gen_embed(percent: int, eta: float) -> discord.Embed:
            return _build_progress_embed(
                user=interaction.user,
                prompt=user_prompt,
                model_label=model_label,
                percent=percent,
                eta_sec=eta,
                stage="Generating image with face reference...",
                quota_used=int(state["used"]),
                quota_limit=int(state["limit"]),
                quota_remaining=int(state["remaining"]),
            )

        await run_with_progress(gen_task, progress_msg, est_time, 0, 97, gen_embed, min_est=6.0)
        image_bytes, err = await gen_task

        if not image_bytes:
            await send_ephemeral(
                interaction,
                f"❌ Generation failed.\n```{_codeblock_safe(_trim(err or 'unknown error', 900))}```",
            )
            return

        if progress_msg:
            with contextlib.suppress(Exception):
                await progress_msg.edit(
                    content=None,
                    embed=_build_progress_embed(
                        user=interaction.user,
                        prompt=user_prompt,
                        model_label=model_label,
                        percent=100,
                        eta_sec=0.0,
                        stage="Uploading...",
                        quota_used=int(state["used"]),
                        quota_limit=int(state["limit"]),
                        quota_remaining=int(state["remaining"]),
                    ),
                )

        # Public Embed
        embed = discord.Embed(
            title="🎭 Face Image",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(
            name=f"{interaction.user.display_name} • {datetime.now().strftime('%Y-%m-%d')}",
            icon_url=interaction.user.display_avatar.url,
        )
        embed.add_field(
            name="Prompt",
            value=f"```{_codeblock_safe(_trim(user_prompt, 1600))}```",
            inline=False,
        )
        guild_icon = interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        embed.set_footer(
            text=f"{model_label} • {FACE_ASPECT_RATIO} • {FACE_RESOLUTION}",
            icon_url=guild_icon,
        )

        # Upload mit Kompressions-Fallback
        upload_limit = _discord_upload_limit_bytes(interaction)
        posted: Optional[discord.Message] = None

        for s in (1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.22, 0.18):
            target = max(256 * 1024, int(upload_limit * s))
            cand_bytes, cand_ext = _fit_image_for_discord(image_bytes, target)
            fp = io.BytesIO(cand_bytes)
            fp.seek(0)
            fname = make_safe_filename(user_prompt, ext=cand_ext)
            candidate_file = discord.File(fp, filename=fname)
            embed.set_image(url=f"attachment://{fname}")

            try:
                posted = await interaction.channel.send(
                    content=f"{SERVER_ANIM_ICON} 🎭 **Face Image** • {interaction.user.mention}",
                    embed=embed,
                    file=candidate_file,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
                break
            except discord.HTTPException as e:
                if e.status == 413 or getattr(e, "code", None) == 40005:
                    continue
                raise

        if posted is None:
            await send_ephemeral(interaction, "❌ Upload failed after compression retries.")
            return

        quota_success = True

        # Aktuelle Quota abfragen für ephemeral Info
        quota_now = await image_quota.peek(
            interaction.guild.id, interaction.user.id, image_limit
        )

        # Ephemeral mit Animate-Button
        await send_ephemeral(
            interaction,
            content=(
                f"✅ Face image created.\n"
                f"Remaining in 24h (shared): **{quota_now['remaining']} / {quota_now['limit']}** "
                f"(reset in **{_seconds_human(quota_now['reset_in'])}**).\n"
                f"Use the button below if you want to animate this image."
            ),
            view=AnimateEphemeralView(
                owner_id=interaction.user.id,
                source_channel_id=interaction.channel.id,
                source_message_id=posted.id,
                prompt_text=user_prompt,
                ratio="16:9",
            ),
        )

    finally:
        if progress_msg:
            with contextlib.suppress(Exception):
                await progress_msg.delete()

        if not quota_success:
            await image_quota.rollback(token_quota)

        # Starter-Message immer als neueste im Channel halten
        if isinstance(interaction.channel, discord.TextChannel):
            with contextlib.suppress(Exception):
                await VeniceFaceCog.ensure_starter_message_static(
                    interaction.channel,
                    bot_user_id=(interaction.client.user.id if interaction.client.user else None),
                    session_ref=lambda: session,
                )


# =================================================
# ANIMATE INTEGRATION (ruft VeniceVideoCog auf, wie im Original)
# =================================================
class AnimatePromptModal(discord.ui.Modal):
    def __init__(self, owner_id: int, source_channel_id: int, source_message_id: int, base_prompt: str, ratio: str):
        self.owner_id = owner_id
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id
        self.base_prompt = (base_prompt or "").strip()
        self.ratio = ratio
        super().__init__(title="🎬 Animate Image • Video Prompt")
        self.video_prompt = discord.ui.TextInput(
            label="Video prompt",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=2000,
            default=(self.base_prompt[:2000] if self.base_prompt else ""),
            placeholder="Describe motion, camera movement, atmosphere...",
        )
        self.add_item(self.video_prompt)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await send_ephemeral(interaction, "🚫 This modal does not belong to you.")
            return
        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            await send_ephemeral(interaction, "❌ Server-only.")
            return

        final_prompt = (self.video_prompt.value or "").strip() or self.base_prompt or "Animate this image with natural motion."

        video_cog = interaction.client.get_cog("VeniceVideoCog")
        if not video_cog:
            await send_ephemeral(interaction, "❌ VeniceVideoCog is not loaded.")
            return

        try:
            info = await video_cog.get_remaining_info(interaction.guild.id, interaction.user)
            budget = int(info["limit"])
            used = int(info["used"])
            remaining = int(info["remaining"])
            reset_in = int(info["reset_in"])
            tier = int(info["tier"])
        except Exception:
            await send_ephemeral(interaction, "❌ Could not load your video quota right now.")
            return

        if budget <= 0:
            await send_ephemeral(interaction, "🎬 Video rendering is locked for members without a Tier role.")
            return

        max_now = min(MAX_VIDEO_RENDER_SECONDS, remaining)
        allowed = [s for s in VIDEO_DURATION_CHOICES if s <= max_now]
        if not allowed:
            await send_ephemeral(
                interaction,
                f"⛔ Video seconds exhausted for this 24h window: **{used}/{budget}s**.\n"
                f"⏳ Reset in **{_seconds_human(reset_in)}**.\n"
                f"Current tier: **T{tier}**.",
            )
            return

        await send_ephemeral(
            interaction,
            content=f"✅ Video prompt set.\n⏱ Choose length (remaining today: **{remaining}s**, max per render: **15s**):",
            view=AnimateDurationView(
                owner_id=self.owner_id,
                source_channel_id=self.source_channel_id,
                source_message_id=self.source_message_id,
                prompt_text=final_prompt,
                ratio=self.ratio,
                allowed_durations=allowed,
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
    ):
        super().__init__(owner_id=owner_id, timeout=300)
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id
        self.prompt_text = prompt_text
        self.ratio = ratio
        self.allowed_durations = sorted(set(d for d in allowed_durations if d > 0))

        if not self.allowed_durations:
            self.add_item(discord.ui.Button(label="No duration available", disabled=True, style=discord.ButtonStyle.secondary))
            return

        for idx, sec in enumerate(self.allowed_durations):
            style = (
                discord.ButtonStyle.success if sec <= 10
                else discord.ButtonStyle.danger if sec == 15
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
            await send_ephemeral(interaction, "❌ Server-only.")
            return

        video_cog = interaction.client.get_cog("VeniceVideoCog")
        if not video_cog:
            await send_ephemeral(interaction, "❌ VeniceVideoCog is not loaded.")
            return

        if seconds > MAX_VIDEO_RENDER_SECONDS:
            await send_ephemeral(interaction, "❌ Max duration per render is 15 seconds.")
            return

        with contextlib.suppress(Exception):
            rem = await video_cog.get_remaining_info(interaction.guild.id, interaction.user)
            if int(rem.get("remaining", 0)) < seconds:
                await send_ephemeral(
                    interaction,
                    f"⛔ Not enough video seconds left.\nRemaining: **{rem.get('remaining', 0)}s** of **{rem.get('limit', 0)}s**.",
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

        source_image_url = _extract_source_image_url(source_message)

        image_cog = interaction.client.get_cog("VeniceImageCog")
        face_cog = interaction.client.get_cog("VeniceFaceCog")
        shared_session = (
            getattr(face_cog, "session", None)
            or getattr(image_cog, "session", None)
        )
        image_bytes = await _extract_image_bytes_from_message(source_message, shared_session)

        if not source_image_url and not image_bytes:
            await send_ephemeral(interaction, "❌ No usable source image found.")
            return

        aspect = self.ratio if self.ratio in VIDEO_ALLOWED_ASPECTS else "16:9"
        prompt = (self.prompt_text or "").strip() or "Animate this image with natural motion."

        await video_cog.animate_image_to_video(
            interaction=interaction,
            image_url=source_image_url or "",
            image_bytes=image_bytes,
            prompt=prompt,
            aspect=aspect,
            seconds=seconds,
            target_channel=channel,
        )
        await cleanup_user_ephemerals(interaction)


class AnimateEphemeralView(OwnerLockedView):
    def __init__(self, owner_id: int, source_channel_id: int, source_message_id: int, prompt_text: str, ratio: str):
        super().__init__(owner_id=owner_id, timeout=1800)
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id
        self.prompt_text = prompt_text
        self.ratio = ratio

    @discord.ui.button(label="🎬 Animate this image", style=discord.ButtonStyle.primary)
    async def animate(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            await send_ephemeral(interaction, "❌ Server-only.")
            return
        await interaction.response.send_modal(
            AnimatePromptModal(
                owner_id=self.owner_id,
                source_channel_id=self.source_channel_id,
                source_message_id=self.source_message_id,
                base_prompt=self.prompt_text,
                ratio=self.ratio,
            )
        )


# =================================================
# COG
# =================================================
class VeniceFaceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self._ready_bootstrap_done = False
        self._ready_lock = asyncio.Lock()

    def _session_ref(self) -> Optional[aiohttp.ClientSession]:
        return self.session

    async def _ensure_session(self):
        if self.session and not self.session.closed:
            return
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=300),
            connector=aiohttp.TCPConnector(limit=30, ttl_dns_cache=300),
        )

    async def cog_load(self):
        await self._ensure_session()
        # persistente View für den Face-Channel
        self.bot.add_view(StarterView(self._session_ref, FACE_CHANNEL_ID))

    def cog_unload(self):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    @staticmethod
    async def _delete_recent_starter_posts_unlocked(
        channel: discord.TextChannel, bot_user_id: Optional[int], limit: int
    ) -> int:
        deleted = 0
        async for msg in channel.history(limit=limit):
            if bot_user_id is not None and msg.author.id != bot_user_id:
                continue
            if is_starter_message(msg):
                with contextlib.suppress(Exception):
                    await msg.delete()
                    deleted += 1
        return deleted

    async def ensure_starter_message(self, channel: discord.TextChannel):
        async with get_channel_lock(channel.id):
            try:
                await VeniceFaceCog._delete_recent_starter_posts_unlocked(
                    channel,
                    bot_user_id=(self.bot.user.id if self.bot.user else None),
                    limit=RECENT_SCAN_LIMIT,
                )
                await channel.send(
                    BUTTON_MESSAGE_TEXT,
                    view=StarterView(self._session_ref, channel.id),
                )
            except Exception as e:
                logger.warning("ensure_starter_message failed in channel %s: %s", channel.id, e)

    @staticmethod
    async def ensure_starter_message_static(
        channel: discord.TextChannel,
        bot_user_id: Optional[int],
        session_ref: Callable[[], Optional[aiohttp.ClientSession]],
    ):
        async with get_channel_lock(channel.id):
            try:
                await VeniceFaceCog._delete_recent_starter_posts_unlocked(
                    channel, bot_user_id=bot_user_id, limit=RECENT_SCAN_LIMIT,
                )
                await channel.send(
                    BUTTON_MESSAGE_TEXT,
                    view=StarterView(session_ref, channel.id),
                )
            except Exception:
                pass

    @commands.command(name="face_reload")
    @commands.has_permissions(administrator=True)
    async def face_reload(self, ctx: commands.Context):
        await self._ensure_session()
        # Referenzbild neu laden
        await face_ref_cache.get_base64(self.session, force=True)

        reposted = 0
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.id == FACE_CHANNEL_ID:
                    await self.ensure_starter_message(channel)
                    reposted += 1

        await ctx.send(
            f"✅ Face-Cog reloaded. Reference cached, reposted {reposted} starter message(s)."
        )

    @commands.Cog.listener()
    async def on_ready(self):
        async with self._ready_lock:
            if self._ready_bootstrap_done:
                return
            self._ready_bootstrap_done = True
            await self._ensure_session()
            # Referenzbild vorab laden
            with contextlib.suppress(Exception):
                await face_ref_cache.get_base64(self.session)
            for guild in self.bot.guilds:
                for channel in guild.text_channels:
                    if channel.id == FACE_CHANNEL_ID:
                        await self.ensure_starter_message(channel)


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceFaceCog(bot))