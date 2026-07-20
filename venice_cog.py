import asyncio
import base64
import binascii
import io
import json
import logging
import os
import random
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, Any, Callable

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

try:
    from PIL import Image
except Exception:
    Image = None

# =================================================
# ENV / API
# =================================================
load_dotenv()

VENICE_API_KEY = os.getenv("MORDIEM_API")
if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env!")

VENICE_IMAGE_URL = os.getenv("MORDIEM_IMAGE_URL")
VENICE_UPSCALE_URL = os.getenv("MORDIEM_UPSCALE_URL")
VENICE_MODELS_URL = os.getenv("MORDIEM_MODELS_URL")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


# Upload tuning
DISCORD_UPLOAD_LIMIT_FORCE_MB = _env_int("DISCORD_UPLOAD_LIMIT_FORCE_MB", 0)
DISCORD_UPLOAD_LIMIT_FALLBACK_MB = _env_int("DISCORD_UPLOAD_LIMIT_FALLBACK_MB", 50)
DISCORD_UPLOAD_SAFETY_BYTES = _env_int("DISCORD_UPLOAD_SAFETY_BYTES", 512 * 1024)

BUTTON_MESSAGE_TEXT = "💡 Choose Model for 🖼️ NEW image!"
LEGACY_STARTER_TEXTS = {
    "💡 Choose Model for 🖼️ NEW image!",
    "💡 Choose a model for a new image!",
}
RECENT_SCAN_LIMIT = 10

EASY_MODE_VALUE = "__easy_mode__"
EASY_MODE_ICON = "🔞"
EASY_MODE_LABEL = f"👉Easy Mode {EASY_MODE_ICON}👈"
NO_MODEL_VALUE = "__no_models__"

KEEP_NON_EASY_REUSE_EPHEMERAL = True

logger = logging.getLogger("venice_picture_bot")

if Image is None:
    logger.warning("Pillow (PIL) not available. Upload downscaling/compression is limited.")

# =================================================
# CHANNELS / ROLES
# =================================================
NSFW_CHANNELS = [
    1415769909874524262,
    1415769966573260970,
    1416267309399670917,
    1416267383160442901,
    1416468498305126522,
    1346843244067160074,
    1477717109873049822,
]
SFW_CHANNEL = 1461752750550552741
ALLOWED_CHANNEL_IDS = set(NSFW_CHANNELS + [SFW_CHANNEL])

LEVEL4_ROLE_ID = 1377051179615522926
LEVEL11_ROLE_ID = 1375147276413964408

RESOLUTION_ROLE_REQUIREMENTS = {"2K": LEVEL4_ROLE_ID, "4K": LEVEL11_ROLE_ID}
RESOLUTION_LEVEL_REQUIREMENTS = {"2K": 4, "4K": 11}
ROLE_LEVEL_NAMES = {LEVEL4_ROLE_ID: "Level 4", LEVEL11_ROLE_ID: "Level 11"}

# =================================================
# PROMPT / UI
# =================================================
DEFAULT_NEGATIVE_PROMPT = "disfigured, missing fingers, extra limbs, watermark, underage"
PROMPT_SUFFIX = " "  # NSFW and SFW currently identical; split later if needed

pepper = "<a:01pepper_icon:1377636862847619213>"
REACTIONS = ["1️⃣", "2️⃣", "3️⃣", "<:011:1346549711817146400>", "<:011pump:1346549688836296787>"]

ASPECT_LABELS = {
    "auto": "⚙️ Auto",
    "1:1": "🟦 1:1",
    "16:9": "📺 16:9",
    "9:16": "📱 9:16",
    "21:9": "🎬 21:9",
    "3:2": "🖼️ 3:2",
    "2:3": "📷 2:3",
    "3:4": "🖼️ 3:4",
    "4:5": "🖼️ 4:5",
}
RESOLUTION_TIERS = ["1K", "2K", "4K"]
FALLBACK_ASPECTS = ["1:1", "16:9", "9:16"]

# =================================================
# CONTENT RATINGS
# =================================================
# "explicit" = full NSFW/porn, "nudity" = nudity ok/no hardcore, "sfw" = censored
RATING_EXPLICIT = "explicit"
RATING_NUDITY = "nudity"
RATING_SFW = "sfw"

# Ratings that count as "open" -> included in Easy Mode pool and flagged with 🔞
OPEN_RATINGS = {RATING_EXPLICIT, RATING_NUDITY}

# =================================================
# MODEL CONFIG (single source of truth)
# =================================================
DEFAULT_MODEL_ROW = {
    "prompt_limit": 1500,
    "default_steps": 20,
    "max_steps": 50,
    "cfg_default": 5.2,
    "aspect_ratios": None,
    "default_aspect_ratio": "1:1",
    "width_height_divisor": 8,
    "resolutions": [],
    "default_resolution": "1K",
    "speed_factor": 1.0,
}

_FULL_ASPECTS = ["1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"]

# label, rating, baseline caps (API sync overrides caps at runtime)
MODELS: dict[str, dict[str, Any]] = {
    "hidream": {"label": "🌙 HiDream", "rating": RATING_NUDITY,
        "caps": {"prompt_limit": 1500, "default_steps": 20, "max_steps": 50, "cfg_default": 6.5, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
    "flux-2-max": {"label": "🌌 Flux 2 Max", "rating": RATING_SFW,
        "caps": {"prompt_limit": 3000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": ["auto", *_FULL_ASPECTS], "default_aspect_ratio": "auto", "width_height_divisor": 1, "resolutions": []}},
    "gpt-image-2": {"label": "🧠 GPT Image 2", "rating": RATING_SFW,
        "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": ["1K", "2K", "4K"]}},
    "gpt-image-1-5": {"label": "🪄 GPT Image 1.5", "rating": RATING_SFW,
        "caps": {"prompt_limit": 5000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": ["1:1", "3:2", "2:3"], "width_height_divisor": 1, "resolutions": []}},
    "hunyuan-image-v3": {"label": "🐉 Hunyuan Image 3.0", "rating": RATING_NUDITY,
        "caps": {"prompt_limit": 3000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": []}},
    "imagineart-1.5-pro": {"label": "🎨 ImagineArt 1.5 Pro", "rating": RATING_SFW,
        "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": ["1:1", "3:2", "16:9", "9:16", "2:3", "3:4", "4:5"], "width_height_divisor": 1, "resolutions": []}},
    "ideogram-v4": {"label": "🔤 Ideogram V4 (Text)", "rating": RATING_SFW,
        "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": []}},
    "nano-banana-2": {"label": "🐵 Nano Banana 2", "rating": RATING_SFW,
        "caps": {"prompt_limit": 32768, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": ["1K", "2K", "4K"]}},
    "nano-banana-pro": {"label": "🍌 Nano Banana Pro", "rating": RATING_SFW,
        "caps": {"prompt_limit": 32768, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": ["1K", "2K", "4K"]}},
    "recraft-v4-pro": {"label": "🏗️ Recraft V4 Pro", "rating": RATING_SFW,
        "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": []}},
    "seedream-v5-pro": {"label": "🌊 Seedream V5 Pro", "rating": RATING_NUDITY,
        "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": ["1:1", "3:2", "16:9", "9:16", "2:3", "3:4"], "width_height_divisor": 1, "resolutions": ["1K", "2K"], "default_resolution": "2K"}},
    "krea-2-turbo": {"label": "🎇 Krea 2 Turbo", "rating": RATING_NUDITY,
        "caps": {"prompt_limit": 5000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": ["1K", "2K"]}},
    "qwen-image-2-pro": {"label": "🧩 Qwen Image 2 Pro", "rating": RATING_SFW,
        "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": []}},
    "wan-2-7-pro-text-to-image": {"label": "🦈 Wan 2.7 Pro", "rating": RATING_SFW,
        "caps": {"prompt_limit": 3000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": []}},
    "grok-imagine-image-quality": {"label": "🚀 Grok Imagine HQ", "rating": RATING_SFW,
        "caps": {"prompt_limit": 7500, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": ["1:1", "16:9", "9:16", "3:4", "3:2", "2:3"], "width_height_divisor": 1, "resolutions": ["1K", "2K"]}},
    "lustify-sdxl": {"label": "💋 Lustify SDXL (Legacy)", "rating": RATING_EXPLICIT,
        "caps": {"prompt_limit": 1500, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
    "lustify-v7": {"label": "🥵 Lustify v7", "rating": RATING_EXPLICIT,
        "caps": {"prompt_limit": 1500, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
    "lustify-v8": {"label": "🔥 Lustify v8", "rating": RATING_EXPLICIT,
        "caps": {"prompt_limit": 1500, "default_steps": 30, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
    "wai-Illustrious": {"label": "🎌 Anime (WAI)", "rating": RATING_SFW,
        "caps": {"prompt_limit": 1500, "default_steps": 25, "max_steps": 30, "cfg_default": 7.0, "aspect_ratios": None, "width_height_divisor": 16, "resolutions": []}},
    "z-image-turbo": {"label": "⚡ Z-Image Turbo", "rating": RATING_EXPLICIT,
        "caps": {"prompt_limit": 7500, "default_steps": 8, "max_steps": 8, "cfg_default": 6.0, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
    "chroma": {"label": "🌈 Chroma", "rating": RATING_NUDITY,
        "caps": {"prompt_limit": 7500, "default_steps": 10, "max_steps": 10, "cfg_default": 6.0, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
}

MODEL_CONFIG: dict[str, dict[str, Any]] = {
    mid: {"label": m["label"], "rating": m["rating"], **DEFAULT_MODEL_ROW, **m["caps"]}
    for mid, m in MODELS.items()
}
MODEL_ORDER = list(MODELS.keys())
MODEL_RATINGS = {mid: m["rating"] for mid, m in MODELS.items()}
UNCENSORED_MODELS = {mid for mid, r in MODEL_RATINGS.items() if r in OPEN_RATINGS}
DISABLED_MODELS: set[str] = set()
EXCLUDED_IMAGE_MODELS = {"venice-sd35", "flux-2-pro", "bria-bg-remover"}

# =================================================
# TIMING
# =================================================
NATIVE_RES_TIME_FACTOR = {"1K": 1.00, "2K": 1.30, "4K": 1.70}
UPSCALE_BASE_SECONDS = {2: 10.0, 4: 22.0}
UPSCALE_TARGET_FACTOR = {"2K": 1.10, "4K": 1.35}

TIMING_EWMA: dict[str, float] = defaultdict(float)
TIMING_N: dict[str, int] = defaultdict(int)
TIMING_CACHE_FILE = os.getenv("VENICE_TIMING_CACHE_FILE", "venice_timing_cache.json")
_TIMING_DIRTY_COUNT = 0


def _timing_key(model_id: str, target_res: str, upscale_factor: Optional[int]) -> str:
    return f"{model_id}|{target_res}|up{upscale_factor or 1}"


def load_timing_cache():
    global _TIMING_DIRTY_COUNT
    try:
        if not os.path.exists(TIMING_CACHE_FILE):
            return
        with open(TIMING_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        TIMING_EWMA.clear()
        TIMING_N.clear()
        for k, v in (data.get("ewma") or {}).items():
            TIMING_EWMA[k] = float(v)
        for k, v in (data.get("n") or {}).items():
            TIMING_N[k] = int(v)
        _TIMING_DIRTY_COUNT = 0
        logger.info("Timing cache loaded (%s keys)", len(TIMING_EWMA))
    except Exception as e:
        logger.warning("Failed to load timing cache: %s", e)


def save_timing_cache():
    global _TIMING_DIRTY_COUNT
    try:
        payload = {
            "ewma": dict(TIMING_EWMA),
            "n": dict(TIMING_N),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(TIMING_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        _TIMING_DIRTY_COUNT = 0
    except Exception as e:
        logger.warning("Failed to save timing cache: %s", e)


def timing_get_estimate(model_id: str, target_res: str, upscale_factor: Optional[int], fallback: float) -> float:
    k = _timing_key(model_id, target_res, upscale_factor)
    if TIMING_N[k] >= 3 and TIMING_EWMA[k] > 0:
        return TIMING_EWMA[k]
    return fallback


def timing_update(model_id: str, target_res: str, upscale_factor: Optional[int], measured_seconds: float, alpha: float = 0.25):
    global _TIMING_DIRTY_COUNT
    k = _timing_key(model_id, target_res, upscale_factor)
    old = TIMING_EWMA[k]
    TIMING_EWMA[k] = measured_seconds if old <= 0 else (alpha * measured_seconds + (1 - alpha) * old)
    TIMING_N[k] += 1
    _TIMING_DIRTY_COUNT += 1
    if _TIMING_DIRTY_COUNT >= 10:
        save_timing_cache()


# =================================================
# LOCKS / EPHEMERALS
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
    return (guild_id, interaction.user.id)


async def track_ephemeral_message(interaction: discord.Interaction, msg: Optional[discord.Message]):
    if not msg:
        return
    _ephemeral_messages.setdefault(_ephemeral_key(interaction), []).append(msg)


async def cleanup_user_ephemerals(interaction: discord.Interaction):
    for m in _ephemeral_messages.pop(_ephemeral_key(interaction), []):
        try:
            await m.delete()
        except Exception:
            pass


# =================================================
# GENERIC HELPERS
# =================================================
def _safe_float(v: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _resolution_sort_key(x: str) -> int:
    return RESOLUTION_TIERS.index(x) if x in RESOLUTION_TIERS else 999


def _eta_text(seconds_float: float) -> str:
    s = max(0, int(round(seconds_float)))
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


def get_active_model_ids() -> list[str]:
    return [m for m in MODEL_ORDER if m not in DISABLED_MODELS]


def get_model_rating(model_id: str) -> str:
    return MODEL_RATINGS.get(model_id, RATING_SFW)


def is_uncensored_model(model_id: str) -> bool:
    return get_model_rating(model_id) in OPEN_RATINGS


def get_easy_mode_candidates() -> list[str]:
    active = set(get_active_model_ids())
    return [m for m in MODEL_ORDER if m in active and is_uncensored_model(m)]


def get_model_label(model_id: str) -> str:
    base = (MODEL_CONFIG.get(model_id) or {}).get("label", model_id)
    return f"{base} {EASY_MODE_ICON}" if is_uncensored_model(model_id) else base


def get_model_ratios(model_id: str) -> list[str]:
    ratios = MODEL_CONFIG[model_id].get("aspect_ratios")
    return ratios if ratios else FALLBACK_ASPECTS


def make_safe_filename(prompt: str, ext: str = "png") -> str:
    base = "_".join((prompt or "").split()[:5]) or "image"
    base = re.sub(r"[^a-zA-Z0-9_]", "_", base)
    ext = (ext or "png").lower().strip(".")
    return f"{base}_{int(time.time_ns())}_{uuid.uuid4().hex[:8]}.{ext}"


def snap_to_divisor(value: int, divisor: int) -> int:
    if divisor <= 1:
        return max(1, int(value))
    return max(divisor, int(round(value / divisor) * divisor))


def dimensions_for_ratio(ratio: str, divisor: int, base_long_side: int = 1024) -> tuple[int, int]:
    m = re.match(r"^(\d+):(\d+)$", ratio) if ratio != "auto" else None
    if not m:
        side = snap_to_divisor(base_long_side, divisor)
        return side, side

    rw, rh = int(m.group(1)), int(m.group(2))
    if rw <= 0 or rh <= 0:
        side = snap_to_divisor(base_long_side, divisor)
        return side, side

    if rw >= rh:
        w, h = base_long_side, int(round(base_long_side * (rh / rw)))
    else:
        h, w = base_long_side, int(round(base_long_side * (rw / rh)))
    return snap_to_divisor(w, divisor), snap_to_divisor(h, divisor)


def build_model_options(channel_id: int, include_easy: bool = True) -> list[discord.SelectOption]:
    if channel_id not in ALLOWED_CHANNEL_IDS:
        return [discord.SelectOption(label="No models in this channel", value=NO_MODEL_VALUE)]

    options: list[discord.SelectOption] = []
    if include_easy and get_easy_mode_candidates():
        options.append(discord.SelectOption(label=EASY_MODE_LABEL, value=EASY_MODE_VALUE))
    for model_id in get_active_model_ids():
        options.append(discord.SelectOption(label=get_model_label(model_id), value=model_id))
    if not options:
        options.append(discord.SelectOption(label="No models available", value=NO_MODEL_VALUE))
    return options[:25]


def build_easy_embed(model_id: str, ratio: str) -> discord.Embed:
    return discord.Embed(
        title=f"⚡ Easy Mode {EASY_MODE_ICON}",
        description=f"**Model:** {get_model_label(model_id)}\n**Aspect Ratio:** {ASPECT_LABELS.get(ratio, ratio)}",
        color=discord.Color.gold(),
    )


def is_model_dropdown_message(msg: discord.Message) -> bool:
    if not msg.components or msg.embeds or msg.attachments:
        return False
    for row in msg.components:
        for child in row.children:
            cid = getattr(child, "custom_id", None)
            if isinstance(cid, str) and (cid.startswith("venice_model_select:") or cid.startswith("venice_model_select_")):
                return True
    return (msg.content or "").strip() in LEGACY_STARTER_TEXTS


def required_role_for_resolution(resolution: Optional[str]) -> Optional[int]:
    return RESOLUTION_ROLE_REQUIREMENTS.get(resolution) if resolution else None


def required_level_for_resolution(resolution: Optional[str]) -> Optional[int]:
    return RESOLUTION_LEVEL_REQUIREMENTS.get(resolution) if resolution else None


def has_role(member: discord.Member, role_id: int) -> bool:
    return any(r.id == role_id for r in member.roles)


def channel_suffix(channel_id: Optional[int]) -> str:
    return PROMPT_SUFFIX


def generation_plan(model_id: str, wanted_resolution: str) -> tuple[Optional[str], Optional[int]]:
    native = set(MODEL_CONFIG[model_id]["resolutions"])
    if wanted_resolution in native:
        return wanted_resolution, None
    if wanted_resolution == "1K":
        return None, None
    if wanted_resolution == "2K":
        return ("1K", 2) if "1K" in native else (None, 2)
    if wanted_resolution == "4K":
        if "2K" in native:
            return "2K", 2
        if "1K" in native:
            return "1K", 4
        return None, 4
    return None, None


def build_generate_payload(model_id, ratio, generation_resolution, prompt, negative_prompt, cfg_scale, steps) -> dict[str, Any]:
    cfg = MODEL_CONFIG[model_id]
    payload: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "steps": steps,
        "cfg_scale": cfg_scale,
        "negative_prompt": negative_prompt,
        "safe_mode": False,
        "hide_watermark": True,
        "return_binary": True,
    }

    native_ratios = cfg.get("aspect_ratios")
    if native_ratios:
        default_ratio = cfg.get("default_aspect_ratio") or native_ratios[0]
        payload["aspect_ratio"] = ratio if ratio in native_ratios else default_ratio
    else:
        w, h = dimensions_for_ratio(ratio, cfg.get("width_height_divisor", 8), base_long_side=1024)
        payload["width"], payload["height"] = w, h

    if generation_resolution and generation_resolution in set(cfg.get("resolutions", [])):
        payload["resolution"] = generation_resolution
    return payload


def build_resolution_hint(model_id: str) -> str:
    native = set(MODEL_CONFIG[model_id]["resolutions"])
    if not native:
        return "1K is native for this model. 2K/4K via upscale."
    parts = [f"Native: {', '.join(sorted(native, key=_resolution_sort_key))}"]
    parts.append("2K" if "2K" in native else "2K via upscale")
    parts.append("4K" if "4K" in native else "4K via upscale")
    parts.append("📈")
    return " • ".join(parts)


def estimate_generation_seconds(model_id, steps, cfg_scale, prompt_len, generation_resolution) -> float:
    cfg = MODEL_CONFIG[model_id]
    base = 8.5
    model_f = float(cfg.get("speed_factor", 1.0))
    default_steps = max(1, int(cfg.get("default_steps", 20)))
    steps_f = max(0.55, steps / default_steps)
    prompt_f = 1.0 + min(prompt_len, 4000) / 8000.0
    cfg_f = 1.0 + max(0.0, cfg_scale - 5.0) * 0.02
    res_f = NATIVE_RES_TIME_FACTOR.get(generation_resolution or "1K", 1.0)
    return max(6.0, min(base * model_f * steps_f * prompt_f * cfg_f * res_f, 240.0))


def estimate_upscale_seconds(scale: Optional[int], target_resolution: str) -> float:
    if scale not in (2, 4):
        return 0.0
    base = UPSCALE_BASE_SECONDS.get(scale, 10.0)
    return max(4.0, min(base * UPSCALE_TARGET_FACTOR.get(target_resolution, 1.0), 180.0))


# =================================================
# IMAGE BYTES HELPERS
# =================================================
def _looks_like_image(b: bytes) -> bool:
    if not b or len(b) < 12:
        return False
    return (
        b.startswith(b"\x89PNG\r\n\x1a\n")
        or b.startswith(b"\xff\xd8\xff")
        or (b[:4] == b"RIFF" and b[8:12] == b"WEBP")
        or b.startswith((b"GIF87a", b"GIF89a"))
    )


def _infer_image_ext(b: bytes) -> str:
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if b.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return "png"


def _discord_upload_limit_bytes(interaction: discord.Interaction) -> int:
    if DISCORD_UPLOAD_LIMIT_FORCE_MB > 0:
        forced = DISCORD_UPLOAD_LIMIT_FORCE_MB * 1024 * 1024
        logger.info("Upload limit forced via env: %s bytes", forced)
        return forced

    inter_limit = getattr(interaction, "filesize_limit", None)
    guild_limit = getattr(interaction.guild, "filesize_limit", None) if interaction.guild else None
    candidates = [v for v in (inter_limit, guild_limit) if isinstance(v, int) and v > 0]
    chosen = max(candidates) if candidates else DISCORD_UPLOAD_LIMIT_FALLBACK_MB * 1024 * 1024
    logger.info("Upload limit detected: interaction=%s guild=%s chosen=%s", inter_limit, guild_limit, chosen)
    return chosen


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
            try:
                buf = io.BytesIO()
                work.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
                data = buf.getvalue()
                remember(data, "jpg")
                if len(data) <= target:
                    return data, "jpg"
            except Exception:
                continue

    for max_side in (3072, 2560, 2048, 1536, 1280, 1024, 896, 768, 640, 512):
        work = img.copy()
        work.thumbnail((max_side, max_side), resample)
        for q in (90, 80, 70, 60, 50, 40, 30, 24):
            try:
                buf = io.BytesIO()
                work.save(buf, format="WEBP", quality=q, method=6)
                data = buf.getvalue()
                remember(data, "webp")
                if len(data) <= target:
                    return data, "webp"
            except Exception:
                continue

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
        for key in ("image", "image_base64", "imageBase64", "b64_json", "base64", "upscaled_image"):
            val = obj.get(key)
            if isinstance(val, str):
                out = _b64_to_bytes(val)
                if out and _looks_like_image(out):
                    return out
        for _, val in list(obj.items())[:16]:
            out = _extract_image_from_json_obj(val)
            if out:
                return out
    elif isinstance(obj, list):
        for item in obj[:16]:
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


# =================================================
# API MODEL SYNC
# =================================================
def _is_deprecated(model_obj: dict[str, Any]) -> bool:
    dep = (model_obj.get("model_spec") or {}).get("deprecation") or {}
    d = dep.get("date")
    if not d:
        return False
    try:
        return datetime.fromisoformat(d.replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    except Exception:
        return False


def _extract_price_usd(model_obj: dict[str, Any]) -> Optional[float]:
    pricing = (model_obj.get("model_spec") or {}).get("pricing") or {}
    gen = pricing.get("generation")
    if isinstance(gen, dict) and "usd" in gen:
        return _safe_float(gen["usd"], None)
    res = pricing.get("resolutions")
    if isinstance(res, dict) and res:
        if isinstance(res.get("1K"), dict):
            return _safe_float(res["1K"].get("usd"), None)
        vals = [_safe_float(row.get("usd"), None) for row in res.values() if isinstance(row, dict) and "usd" in row]
        vals = [v for v in vals if v is not None]
        if vals:
            return min(vals)
    return None


def _calc_speed_factor_from_price(usd: Optional[float]) -> float:
    if usd is None or usd <= 0:
        return 1.0
    return _clamp((usd / 0.05) ** 0.20, 0.70, 1.55)


def _auto_cfg_default(model_id: str, default_steps: int, width_div: int) -> float:
    mid = model_id.lower()
    if "wai-illustrious" in mid or "anime" in mid:
        return 7.0
    if model_id == "qwen-image":
        return 6.0
    if default_steps <= 10:
        return 6.0
    if width_div >= 16:
        return 6.8
    if any(x in mid for x in ["gpt-image", "recraft", "seedream", "krea", "flux", "wan-2-7",
                              "grok-imagine", "nano-banana", "hunyuan", "imagineart", "ideogram", "qwen-image-2"]):
        return 5.0
    return 5.4


def _extract_image_caps(model_obj: dict[str, Any]) -> dict[str, Any]:
    spec = model_obj.get("model_spec") or {}
    cons = spec.get("constraints") or {}
    steps = cons.get("steps") or {}

    prompt_limit = _to_int(cons.get("promptCharacterLimit"), 1500)
    default_steps = _to_int(steps.get("default"), 20)
    max_steps = _to_int(steps.get("max"), max(default_steps, 20))
    aspect_ratios = cons.get("aspectRatios") or None
    default_aspect = cons.get("defaultAspectRatio") or (aspect_ratios[0] if aspect_ratios else "1:1")
    width_div = _to_int(cons.get("widthHeightDivisor"), 8)
    resolutions = cons.get("resolutions") or []
    default_resolution = cons.get("defaultResolution") or ("1K" if "1K" in resolutions else (resolutions[0] if resolutions else "1K"))

    usd = _extract_price_usd(model_obj)
    return {
        "prompt_limit": prompt_limit,
        "default_steps": default_steps,
        "max_steps": max_steps,
        "aspect_ratios": aspect_ratios,
        "default_aspect_ratio": default_aspect,
        "width_height_divisor": width_div,
        "resolutions": resolutions,
        "default_resolution": default_resolution,
        "cfg_default": _auto_cfg_default(model_obj.get("id", ""), default_steps, width_div),
        "speed_factor": _calc_speed_factor_from_price(usd),
    }


async def sync_model_caps_from_api(session: aiohttp.ClientSession):
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}"}
    async with session.get(VENICE_MODELS_URL, headers=headers) as resp:
        if resp.status != 200:
            logger.warning("Model sync failed %s: %s", resp.status, await resp.text())
            return
        payload = await resp.json(content_type=None)

    DISABLED_MODELS.clear()
    api_models = {m["id"]: m for m in payload.get("data", []) if m.get("type") == "image" and m.get("id")}

    for mid in MODEL_ORDER:
        if mid in EXCLUDED_IMAGE_MODELS:
            DISABLED_MODELS.add(mid)
            continue
        m = api_models.get(mid)
        if not m:
            logger.warning("Model %s not found in API; using baseline fallback.", mid)
            continue
        if _is_deprecated(m) and mid != "lustify-sdxl":
            DISABLED_MODELS.add(mid)
            logger.info("Model disabled (deprecated): %s", mid)
            continue
        MODEL_CONFIG[mid].update(_extract_image_caps(m))

    if not get_active_model_ids():
        logger.warning("No active models after sync; re-enabling all curated fallback models.")
        DISABLED_MODELS.clear()


# =================================================
# EPHEMERAL / LEVEL HELPERS
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


async def resolve_member_level(interaction: discord.Interaction, member: discord.Member) -> Optional[int]:
    bot = interaction.client
    guild_id = interaction.guild.id if interaction.guild else None
    user_id = member.id

    for fn_name in ("get_user_level", "get_level", "xp_get_level", "fetch_user_level", "level_for_user"):
        fn = getattr(bot, fn_name, None)
        if not callable(fn):
            continue
        try:
            try:
                res = fn(guild_id, user_id)
            except TypeError:
                try:
                    res = fn(user_id, guild_id)
                except TypeError:
                    res = fn(member)
            if asyncio.iscoroutine(res):
                res = await res
            if res is not None:
                return int(res)
        except Exception:
            continue

    for attr in ("level", "lvl", "xp_level"):
        try:
            val = getattr(member, attr, None)
            if val is not None:
                return int(val)
        except Exception:
            pass

    max_from_roles: Optional[int] = None
    for r in member.roles:
        m = re.search(r"(?:lvl|level)\s*(\d+)", r.name or "", flags=re.IGNORECASE)
        if m:
            lv = int(m.group(1))
            max_from_roles = lv if max_from_roles is None else max(max_from_roles, lv)
    return max_from_roles


async def send_resolution_lock_message(interaction, resolution, role_id, level_required=None, current_level=None):
    level_name = ROLE_LEVEL_NAMES.get(role_id, "Required level")
    level_txt = f"Level {level_required}+" if level_required else level_name
    role_txt = f"<@&{role_id}> ({level_name})" if role_id else "`required role`"
    current_txt = f"\nYour current level: **{current_level}**" if current_level is not None else ""
    await send_ephemeral(
        interaction,
        f"🔒 **{resolution}** is locked.\n"
        f"You need **{level_txt}** and role {role_txt}.{current_txt}\n"
        f"💡 Earn XP by being active and generating images.\n"
        f"🗳️ Voting on images also gives XP."
    )


# =================================================
# API CALLS
# =================================================
async def venice_generate(session: aiohttp.ClientSession, payload: dict[str, Any], retries: int = 2) -> Optional[bytes]:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}"}
    for attempt in range(retries + 1):
        try:
            async with session.post(VENICE_IMAGE_URL, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    img = await _extract_image_from_response(resp)
                    if img and _looks_like_image(img):
                        return img
                    logger.warning("Generate 200 but no valid image payload")
                    return None
                body = await resp.text()
                logger.warning("Venice generate error %s: %s", resp.status, body)
                if resp.status in (429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Venice generate request failed (attempt %s): %s", attempt + 1, e)
            if attempt < retries:
                await asyncio.sleep(1.2 * (attempt + 1))
                continue
            return None
        except Exception as e:
            logger.exception("Unexpected error in venice_generate: %s", e)
            return None
    return None


async def _upscale_once(session: aiohttp.ClientSession, image_bytes: bytes, scale: int, retries: int = 2) -> Optional[bytes]:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}"}
    if not _looks_like_image(image_bytes):
        logger.warning("Upscale input is not a valid image")
        return None

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    payloads = [
        {"image": b64, "scale": scale},
        {"image": f"data:image/png;base64,{b64}", "scale": scale},
    ]

    for attempt in range(retries + 1):
        for payload in payloads:
            try:
                async with session.post(VENICE_UPSCALE_URL, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        out = await _extract_image_from_response(resp)
                        if out and _looks_like_image(out):
                            return out
                    else:
                        logger.warning("Venice upscale json error %s: %s", resp.status, await resp.text())
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning("Venice upscale request failed (attempt %s): %s", attempt + 1, e)
            except Exception as e:
                logger.exception("Unexpected error in _upscale_once: %s", e)
        if attempt < retries:
            await asyncio.sleep(1.2 * (attempt + 1))
    return None


async def venice_upscale(session: aiohttp.ClientSession, image_bytes: bytes, scale: int, retries: int = 2) -> Optional[bytes]:
    if scale == 4:
        first = await _upscale_once(session, image_bytes, 2, retries=retries)
        if not first:
            return None
        return await _upscale_once(session, first, 2, retries=retries)
    return await _upscale_once(session, image_bytes, scale, retries=retries)


# =================================================
# PROGRESS LOOP (shared)
# =================================================
async def run_with_progress(
    task: asyncio.Task,
    progress_msg: Optional[discord.Message],
    est: float,
    start_percent: int,
    end_percent: int,
    make_content: Callable[[int, float], str],
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
                try:
                    await progress_msg.edit(content=make_content(percent, eta))
                except Exception:
                    pass
        await asyncio.sleep(0.8)

    return time.monotonic() - started


# =================================================
# GENERATION DATA FACTORY
# =================================================
def build_generation_data(*, model_id, ratio, prompt_text, negative_prompt, cfg_scale, steps,
                          hidden_suffix, owner_id, channel_id, is_easy_mode, previous_inputs) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "ratio": ratio,
        "prompt_text": prompt_text,
        "negative_prompt": negative_prompt,
        "cfg_scale": cfg_scale,
        "steps": steps,
        "hidden_suffix": hidden_suffix,
        "owner_id": owner_id,
        "channel_id": channel_id,
        "is_easy_mode": is_easy_mode,
        "previous_inputs": previous_inputs,
    }


# =================================================
# SHARED MODEL SELECTION HANDLER
# =================================================
async def handle_model_selection(
    interaction: discord.Interaction,
    session: aiohttp.ClientSession,
    selected: str,
    hidden_suffix: str,
    owner_id: int,
    channel_id: int,
    previous_inputs: Optional[dict[str, Any]] = None,
):
    if selected == NO_MODEL_VALUE:
        await send_ephemeral(interaction, "❌ No models available right now.")
        return

    if selected == EASY_MODE_VALUE:
        candidates = get_easy_mode_candidates()
        if not candidates:
            await send_ephemeral(interaction, "❌ No Easy Mode models available.")
            return
        model_id = random.choice(candidates)
        ratio = random.choice(get_model_ratios(model_id))
        await interaction.response.send_modal(
            EasyModeModal(session, model_id, ratio, hidden_suffix, owner_id)
        )
        await send_ephemeral(interaction, embed=build_easy_embed(model_id, ratio))
        return

    if selected in DISABLED_MODELS:
        await send_ephemeral(interaction, "❌ This model is disabled.")
        return

    await send_ephemeral(
        interaction,
        content=f"{get_model_label(selected)} selected. Now choose an aspect ratio:",
        view=AspectRatioSelectView(session, selected, hidden_suffix, owner_id, previous_inputs),
    )


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
# ASPECT RATIO
# =================================================
class AspectRatioSelect(discord.ui.Select):
    def __init__(self, session, model_id, hidden_suffix, owner_id, previous_inputs=None):
        self.session = session
        self.model_id = model_id
        self.hidden_suffix = hidden_suffix
        self.owner_id = owner_id
        self.previous_inputs = previous_inputs or {}
        options = [discord.SelectOption(label=ASPECT_LABELS.get(r, r), value=r) for r in get_model_ratios(model_id)][:25]
        super().__init__(placeholder="📐 Choose aspect ratio...", min_values=1, max_values=1,
                         options=options, custom_id=f"venice_aspect_select:{model_id}")

    async def callback(self, interaction: discord.Interaction):
        if self.model_id in DISABLED_MODELS:
            await send_ephemeral(interaction, "❌ This model is disabled.")
            return
        source_msg = interaction.message
        await interaction.response.send_modal(
            GenerationModal(self.session, self.model_id, self.values[0], self.hidden_suffix, self.owner_id, self.previous_inputs)
        )
        if source_msg:
            try:
                await source_msg.edit(view=None, content="✅ Aspect ratio selected.")
            except Exception:
                pass


class AspectRatioSelectView(OwnerLockedView):
    def __init__(self, session, model_id, hidden_suffix, owner_id, previous_inputs=None):
        super().__init__(owner_id=owner_id, timeout=600)
        self.add_item(AspectRatioSelect(session, model_id, hidden_suffix, owner_id, previous_inputs))


# =================================================
# MODALS
# =================================================
class EasyModeModal(discord.ui.Modal):
    def __init__(self, session, model_id, ratio, hidden_suffix, owner_id):
        self.session = session
        self.model_id = model_id
        self.ratio = ratio
        self.hidden_suffix_value = hidden_suffix
        self.owner_id = owner_id
        cfg = MODEL_CONFIG[model_id]
        super().__init__(title=f"Easy Mode {EASY_MODE_ICON} • {get_model_label(model_id)} • {ASPECT_LABELS.get(ratio, ratio)}")
        self.prompt = discord.ui.TextInput(
            label="Describe what you want to see",
            style=discord.TextStyle.paragraph, required=True,
            max_length=min(int(cfg["prompt_limit"]), 4000),
        )
        self.add_item(self.prompt)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await send_ephemeral(interaction, "🚫 This modal does not belong to you.")
            return
        if self.model_id in DISABLED_MODELS:
            await send_ephemeral(interaction, "❌ This model is disabled.")
            return

        cfg = MODEL_CONFIG[self.model_id]
        generation_data = build_generation_data(
            model_id=self.model_id, ratio=self.ratio, prompt_text=self.prompt.value,
            negative_prompt=DEFAULT_NEGATIVE_PROMPT, cfg_scale=float(cfg["cfg_default"]),
            steps=int(cfg["default_steps"]), hidden_suffix=self.hidden_suffix_value,
            owner_id=self.owner_id, channel_id=interaction.channel.id if interaction.channel else None,
            is_easy_mode=True,
            previous_inputs={
                "prompt": self.prompt.value, "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
                "cfg_value": "", "steps": None, "hidden_suffix": self.hidden_suffix_value,
            },
        )
        await send_ephemeral(
            interaction,
            content=(
                f"✅ Easy Mode {EASY_MODE_ICON}: {get_model_label(self.model_id)} • {ASPECT_LABELS.get(self.ratio, self.ratio)}\n"
                f"{build_resolution_hint(self.model_id)}\nChoose resolution:"
            ),
            view=ResolutionSelectView(self.session, generation_data),
        )


class GenerationModal(discord.ui.Modal):
    def __init__(self, session, model_id, ratio, hidden_suffix, owner_id, previous_inputs=None):
        self.session = session
        self.model_id = model_id
        self.ratio = ratio
        self.hidden_suffix_value = hidden_suffix
        self.owner_id = owner_id
        self.previous_inputs = previous_inputs or {}

        cfg = MODEL_CONFIG[model_id]
        fixed_steps = cfg["default_steps"] == cfg["max_steps"]
        super().__init__(title=f"{get_model_label(model_id)} • {ASPECT_LABELS.get(ratio, ratio)}")

        self.prompt = discord.ui.TextInput(
            label="Describe your image", style=discord.TextStyle.paragraph, required=True,
            max_length=min(int(cfg["prompt_limit"]), 4000),
            default=self.previous_inputs.get("prompt", ""),
        )
        self.negative_prompt = discord.ui.TextInput(
            label="Negative prompt (optional)", style=discord.TextStyle.short, required=False,
            max_length=800, default=self.previous_inputs.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT,
        )
        self.cfg_value = discord.ui.TextInput(
            label="CFG scale", style=discord.TextStyle.short, required=False, max_length=8,
            placeholder=str(cfg["cfg_default"]), default=self.previous_inputs.get("cfg_value", ""),
        )
        self.steps_value = discord.ui.TextInput(
            label=f"Steps (fixed: {cfg['default_steps']})" if fixed_steps else f"Steps (1-{cfg['max_steps']})",
            style=discord.TextStyle.short, required=False, max_length=3,
            placeholder=str(cfg["default_steps"]),
            default=str(self.previous_inputs.get("steps")) if self.previous_inputs.get("steps") else "",
        )
        self.hidden_suffix = discord.ui.TextInput(
            label="Hidden suffix", style=discord.TextStyle.paragraph, required=False, max_length=1200,
            placeholder=(hidden_suffix[:100] if hidden_suffix else ""),
            default=self.previous_inputs.get("hidden_suffix", ""),
        )
        for item in (self.prompt, self.negative_prompt, self.cfg_value, self.steps_value, self.hidden_suffix):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await send_ephemeral(interaction, "🚫 This modal does not belong to you.")
            return
        if self.model_id in DISABLED_MODELS:
            await send_ephemeral(interaction, "❌ This model is disabled.")
            return

        cfg = MODEL_CONFIG[self.model_id]
        fixed_steps = cfg["default_steps"] == cfg["max_steps"]

        try:
            cfg_val = float(self.cfg_value.value)
        except Exception:
            cfg_val = float(cfg["cfg_default"])

        if fixed_steps:
            steps_val = int(cfg["default_steps"])
        else:
            try:
                steps_val = max(1, min(int(self.steps_value.value), int(cfg["max_steps"])))
            except Exception:
                steps_val = int(cfg["default_steps"])

        negative_prompt = (self.negative_prompt.value or "").strip() or DEFAULT_NEGATIVE_PROMPT
        hidden_suffix = (self.hidden_suffix.value or "").strip() or self.hidden_suffix_value

        generation_data = build_generation_data(
            model_id=self.model_id, ratio=self.ratio, prompt_text=self.prompt.value,
            negative_prompt=negative_prompt, cfg_scale=cfg_val, steps=steps_val,
            hidden_suffix=hidden_suffix, owner_id=self.owner_id,
            channel_id=interaction.channel.id if interaction.channel else None, is_easy_mode=False,
            previous_inputs={
                "prompt": self.prompt.value, "negative_prompt": negative_prompt,
                "cfg_value": self.cfg_value.value,
                "steps": steps_val if steps_val != cfg["default_steps"] else None,
                "hidden_suffix": hidden_suffix,
            },
        )
        await send_ephemeral(
            interaction,
            content=(
                f"✅ {get_model_label(self.model_id)} • {ASPECT_LABELS.get(self.ratio, self.ratio)}\n"
                f"{build_resolution_hint(self.model_id)}\nChoose resolution:"
            ),
            view=ResolutionSelectView(self.session, generation_data),
        )


# =================================================
# STARTER
# =================================================
class StarterModelSelect(discord.ui.Select):
    def __init__(self, session, channel_id):
        self.session = session
        self.channel_id = channel_id
        super().__init__(placeholder="🎨 Choose your model...", min_values=1, max_values=1,
                         options=build_model_options(channel_id, include_easy=True),
                         custom_id=f"venice_model_select:{channel_id}")

    async def callback(self, interaction: discord.Interaction):
        cid = interaction.channel.id if interaction.channel else self.channel_id
        await handle_model_selection(
            interaction, self.session, self.values[0],
            hidden_suffix=channel_suffix(cid), owner_id=interaction.user.id, channel_id=cid,
        )


class StarterView(discord.ui.View):
    def __init__(self, session, channel_id):
        super().__init__(timeout=None)
        self.add_item(StarterModelSelect(session, channel_id))


# =================================================
# RESOLUTION + GENERATION
# =================================================
class ResolutionSelectView(OwnerLockedView):
    def __init__(self, session, generation_data):
        super().__init__(owner_id=generation_data["owner_id"], timeout=900)
        self.session = session
        self.generation_data = generation_data

        model_id = generation_data["model_id"]
        native = set(MODEL_CONFIG[model_id]["resolutions"])
        for res in RESOLUTION_TIERS:
            label = f"{res} ↗" if (res not in native and res in ("2K", "4K")) else res
            style = (discord.ButtonStyle.success if res == "1K"
                     else discord.ButtonStyle.primary if res == "2K"
                     else discord.ButtonStyle.danger)
            btn = discord.ui.Button(label=label, style=style, custom_id=f"venice_res:{res}:{model_id}")
            btn.callback = self._make_resolution_callback(res)
            self.add_item(btn)

    def _make_resolution_callback(self, resolution: str):
        async def callback(interaction: discord.Interaction):
            if not isinstance(interaction.user, discord.Member):
                await send_ephemeral(interaction, "❌ This action can only be used in a server.")
                return

            role_needed = required_role_for_resolution(resolution)
            level_needed = required_level_for_resolution(resolution)
            current_level = await resolve_member_level(interaction, interaction.user)

            missing_role = bool(role_needed and not has_role(interaction.user, role_needed))
            missing_level = bool(level_needed and current_level is not None and current_level < level_needed)

            if missing_role or missing_level:
                await send_resolution_lock_message(interaction, resolution, role_needed or 0, level_needed, current_level)
                return

            await interaction.response.defer(ephemeral=True)
            if interaction.message:
                try:
                    await interaction.message.edit(view=None, content="✅ Resolution selected.")
                except Exception:
                    pass
            await self.generate_image(interaction, resolution)

        return callback

    async def generate_image(self, interaction: discord.Interaction, resolution: str):
        gd = self.generation_data
        model_id = gd["model_id"]
        ratio = gd["ratio"]
        prompt_text = gd["prompt_text"]
        hidden_suffix = gd["hidden_suffix"]
        negative_prompt = gd["negative_prompt"]
        cfg_val = float(gd["cfg_scale"])
        steps = int(gd["steps"])
        previous_inputs = gd["previous_inputs"]
        channel_id = gd["channel_id"]
        is_easy_mode = bool(gd.get("is_easy_mode", False))

        if model_id in DISABLED_MODELS:
            await interaction.followup.send("❌ This model is disabled.", ephemeral=True)
            self.stop()
            return

        full_prompt = f"{(prompt_text or '').strip()} {(hidden_suffix or '').strip()}".strip()
        gen_res, upscale_factor = generation_plan(model_id, resolution)

        logger.info("PLAN model=%s target=%s native=%s => gen_res=%s upscale=%s",
                    model_id, resolution, MODEL_CONFIG[model_id]["resolutions"], gen_res, upscale_factor)

        payload = build_generate_payload(model_id, ratio, gen_res, full_prompt, negative_prompt, cfg_val, steps)
        effective_gen_res = gen_res or "1K"

        est_gen = timing_get_estimate(
            model_id, effective_gen_res, None,
            estimate_generation_seconds(model_id, steps, cfg_val, len(prompt_text or ""), effective_gen_res),
        )
        gen_cap = 82 if upscale_factor in (2, 4) else 97

        progress_msg = await interaction.followup.send(f"{pepper} Generating image...", ephemeral=True, wait=True)
        await track_ephemeral_message(interaction, progress_msg)

        gen_task = asyncio.create_task(venice_generate(self.session, payload))
        display_name = interaction.user.display_name

        def gen_content(percent: int, eta: float) -> str:
            return f"{pepper} Generating image for **{display_name}**... {percent}% (ETA ~{_eta_text(eta)})"

        gen_measured = await run_with_progress(gen_task, progress_msg, est_gen, 0, gen_cap, gen_content, min_est=6.0)
        image_bytes = await gen_task

        if not image_bytes:
            await interaction.followup.send("❌ Generation failed.", ephemeral=True)
            self.stop()
            return

        timing_update(model_id, effective_gen_res, None, gen_measured)

        upscaled_success = False
        if upscale_factor in (2, 4):
            est_up = timing_get_estimate(model_id, resolution, upscale_factor,
                                         estimate_upscale_seconds(upscale_factor, resolution))
            up_task = asyncio.create_task(venice_upscale(self.session, image_bytes, upscale_factor))

            def up_content(percent: int, eta: float) -> str:
                return f"{pepper} Upscaling ({upscale_factor}x) for **{display_name}**... {percent}% (ETA ~{_eta_text(eta)})"

            up_measured = await run_with_progress(up_task, progress_msg, est_up, gen_cap, 99, up_content, min_est=4.0)
            upscaled = await up_task
            if upscaled:
                image_bytes = upscaled
                upscaled_success = True
                timing_update(model_id, resolution, upscale_factor, up_measured)

        try:
            await progress_msg.edit(content=f"{pepper} Finalizing... 100%")
        except Exception:
            pass

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(
            name=f"{display_name} ({datetime.now().strftime('%Y-%m-%d')})",
            icon_url=interaction.user.display_avatar.url,
        )

        prompt_preview = (prompt_text or "").replace("\n\n", "\n")
        if len(prompt_preview) > 600:
            prompt_preview = prompt_preview[:600] + " [...]"
        embed.description = f"🔮 Prompt:\n{prompt_preview}"

        default_hidden = channel_suffix(channel_id)
        used_hidden = previous_inputs.get("hidden_suffix")
        if isinstance(used_hidden, str) and used_hidden and used_hidden != default_hidden:
            embed.description += "\n\n🔒 Hidden prompt used"

        if negative_prompt and negative_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.description += f"\n\n🚫 Negative prompt:\n{negative_prompt}"

        guild_icon = interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        upscale_flag = " 📈" if upscaled_success else ""
        embed.set_footer(
            text=(f"{get_model_label(model_id)} | {ASPECT_LABELS.get(ratio, ratio)} | "
                  f"🧱 {resolution}{upscale_flag} | 🤖 {cfg_val} | 🪜 {steps}"),
            icon_url=guild_icon,
        )

        if not interaction.channel:
            await interaction.followup.send("❌ Channel is unavailable.", ephemeral=True)
            self.stop()
            return

        upload_limit = _discord_upload_limit_bytes(interaction)
        detected_interaction_limit = getattr(interaction, "filesize_limit", None)
        detected_guild_limit = getattr(interaction.guild, "filesize_limit", None) if interaction.guild else None

        def _mib(n: int) -> float:
            return n / (1024 * 1024)

        posted: Optional[discord.Message] = None
        last_attempt_size = 0

        for s in (1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.22, 0.18):
            target = max(256 * 1024, int(upload_limit * s))
            candidate_bytes, candidate_ext = _fit_image_for_discord(image_bytes, target)
            last_attempt_size = len(candidate_bytes)

            fp = io.BytesIO(candidate_bytes)
            fp.seek(0)
            candidate_file = discord.File(fp, filename=make_safe_filename(prompt_text, ext=candidate_ext))
            embed.set_image(url=f"attachment://{candidate_file.filename}")

            try:
                posted = await interaction.channel.send(
                    content=interaction.user.mention, embed=embed, file=candidate_file,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
                break
            except discord.HTTPException as e:
                if e.status == 413 or getattr(e, "code", None) == 40005:
                    logger.warning("Upload too large (scale=%s): size=%.2f MiB, limit=%.2f MiB, err=%s",
                                   s, _mib(len(candidate_bytes)), _mib(upload_limit), e)
                    continue
                raise

        if posted is None:
            await interaction.followup.send(
                "❌ Upload failed after multiple compression retries.\n"
                f"Last attempt: {_mib(last_attempt_size):.2f} MiB\n"
                f"Detected limits -> interaction: {detected_interaction_limit}, "
                f"guild: {detected_guild_limit}, chosen: {upload_limit}",
                ephemeral=True,
            )
            self.stop()
            return

        for emo in REACTIONS:
            try:
                await posted.add_reaction(emo)
            except Exception:
                pass

        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.ensure_starter_message_static(
                interaction.channel, self.session,
                bot_user_id=(interaction.client.user.id if interaction.client.user else None),
            )

        await cleanup_user_ephemerals(interaction)

        if (not is_easy_mode) and KEEP_NON_EASY_REUSE_EPHEMERAL:
            await send_ephemeral(
                interaction,
                content=f"🚨 {interaction.user.mention}, re-use and edit your prompt?",
                view=PostGenerationView(
                    session=self.session, author_id=interaction.user.id, source_message=posted,
                    channel_id=(interaction.channel.id if interaction.channel else 0),
                    previous_inputs=previous_inputs, hidden_suffix=hidden_suffix,
                ),
            )

        self.stop()


# =================================================
# REUSE FLOW
# =================================================
class ReuseModelSelect(discord.ui.Select):
    def __init__(self, session, channel_id, owner_id, previous_inputs, hidden_suffix):
        self.session = session
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.previous_inputs = previous_inputs
        self.hidden_suffix = hidden_suffix
        super().__init__(placeholder="♻️ Re-use with model...", min_values=1, max_values=1,
                         options=build_model_options(channel_id, include_easy=True))

    async def callback(self, interaction: discord.Interaction):
        await handle_model_selection(
            interaction, self.session, self.values[0],
            hidden_suffix=self.hidden_suffix, owner_id=self.owner_id,
            channel_id=self.channel_id, previous_inputs=self.previous_inputs,
        )


class ReuseModelSelectView(OwnerLockedView):
    def __init__(self, session, channel_id, owner_id, previous_inputs, hidden_suffix):
        super().__init__(owner_id=owner_id, timeout=300)
        self.add_item(ReuseModelSelect(session, channel_id, owner_id, previous_inputs, hidden_suffix))


class PostGenerationView(OwnerLockedView):
    def __init__(self, session, author_id, source_message, channel_id, previous_inputs, hidden_suffix):
        super().__init__(owner_id=author_id, timeout=1200)
        self.session = session
        self.source_message = source_message
        self.channel_id = channel_id
        self.previous_inputs = previous_inputs
        self.hidden_suffix = hidden_suffix

        reuse_btn = discord.ui.Button(label="♻️ Re-use Prompt", style=discord.ButtonStyle.success)
        reuse_btn.callback = self.reuse_callback
        self.add_item(reuse_btn)

        delete_btn = discord.ui.Button(label="🗑️ Delete", style=discord.ButtonStyle.danger)
        delete_btn.callback = self.delete_callback
        self.add_item(delete_btn)

        delete_reuse_btn = discord.ui.Button(label="🧹 Delete & Re-use", style=discord.ButtonStyle.danger)
        delete_reuse_btn.callback = self.delete_reuse_callback
        self.add_item(delete_reuse_btn)

    async def reuse_callback(self, interaction: discord.Interaction):
        await send_ephemeral(
            interaction, "♻️ Choose a model to re-use your prompt:",
            view=ReuseModelSelectView(self.session, self.channel_id, self.owner_id, self.previous_inputs, self.hidden_suffix),
        )

    async def delete_callback(self, interaction: discord.Interaction):
        try:
            await self.source_message.delete()
        except Exception:
            pass
        await send_ephemeral(interaction, "✅ Post deleted.")

    async def delete_reuse_callback(self, interaction: discord.Interaction):
        try:
            await self.source_message.delete()
        except Exception:
            pass
        await self.reuse_callback(interaction)


# =================================================
# COG
# =================================================
class VeniceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self._ready_bootstrap_done = False
        self._ready_lock = asyncio.Lock()

    async def _ensure_session(self):
        if self.session and not self.session.closed:
            return
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=300),
            connector=aiohttp.TCPConnector(limit=60, ttl_dns_cache=300),
        )

    async def cog_load(self):
        await self._ensure_session()
        load_timing_cache()
        try:
            await sync_model_caps_from_api(self.session)
            logger.info("Model sync done. Active=%s Disabled=%s",
                        len(get_active_model_ids()), len(DISABLED_MODELS))
        except Exception as e:
            logger.warning("Model sync failed in cog_load: %s", e)

        for channel_id in ALLOWED_CHANNEL_IDS:
            self.bot.add_view(StarterView(self.session, channel_id))

    def cog_unload(self):
        save_timing_cache()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    @staticmethod
    async def _delete_recent_model_dropdown_posts_unlocked(channel, bot_user_id, limit):
        deleted = 0
        async for msg in channel.history(limit=limit):
            if bot_user_id is not None and msg.author.id != bot_user_id:
                continue
            if is_model_dropdown_message(msg):
                try:
                    await msg.delete()
                    deleted += 1
                except Exception:
                    pass
        return deleted

    @staticmethod
    async def delete_recent_model_dropdown_posts(channel, bot_user_id, limit=RECENT_SCAN_LIMIT):
        async with get_channel_lock(channel.id):
            return await VeniceCog._delete_recent_model_dropdown_posts_unlocked(channel, bot_user_id, limit)

    async def ensure_starter_message(self, channel: discord.TextChannel):
        async with get_channel_lock(channel.id):
            try:
                await VeniceCog._delete_recent_model_dropdown_posts_unlocked(
                    channel, bot_user_id=(self.bot.user.id if self.bot.user else None), limit=RECENT_SCAN_LIMIT)
                await channel.send(BUTTON_MESSAGE_TEXT, view=StarterView(self.session, channel.id))
            except discord.Forbidden:
                logger.warning("Missing permissions in channel %s", channel.id)
            except Exception as e:
                logger.warning("ensure_starter_message failed in channel %s: %s", channel.id, e)

    @staticmethod
    async def ensure_starter_message_static(channel, session, bot_user_id):
        async with get_channel_lock(channel.id):
            try:
                await VeniceCog._delete_recent_model_dropdown_posts_unlocked(
                    channel, bot_user_id=bot_user_id, limit=RECENT_SCAN_LIMIT)
                await channel.send(BUTTON_MESSAGE_TEXT, view=StarterView(session, channel.id))
            except Exception:
                pass

    @commands.command(name="venice_reload")
    @commands.has_permissions(administrator=True)
    async def venice_reload(self, ctx: commands.Context):
        await self._ensure_session()
        try:
            await sync_model_caps_from_api(self.session)
        except Exception as e:
            await ctx.send(f"⚠️ Model sync failed: {e}")
            return

        reposted = 0
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.id in ALLOWED_CHANNEL_IDS:
                    await self.ensure_starter_message(channel)
                    reposted += 1

        await ctx.send(
            f"✅ Reloaded. Active={len(get_active_model_ids())}, "
            f"Disabled={len(DISABLED_MODELS)}, reposted {reposted} starter message(s)."
        )

    @commands.Cog.listener()
    async def on_ready(self):
        async with self._ready_lock:
            if self._ready_bootstrap_done:
                return
            self._ready_bootstrap_done = True
            await self._ensure_session()
            for guild in self.bot.guilds:
                for channel in guild.text_channels:
                    if channel.id in ALLOWED_CHANNEL_IDS:
                        await self.ensure_starter_message(channel)


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))