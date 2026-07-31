# venice_cog.py
import asyncio
import contextlib
import io
import logging
import os
import random
import re
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

try:
    from PIL import Image
except Exception:
    Image = None

try:
    import pytesseract
except Exception:
    pytesseract = None

from venice_shared import (
    SERVER_ANIM_ICON,
    AnimateEphemeralView,
    OwnerLockedView,
    add_rating_reactions,
    build_progress_embed,
    bytes_to_b64,
    codeblock_safe,
    eta_text,
    extract_image_from_response,
    get_image_limit_for_member,
    get_member_tier,
    get_quota_store,
    looks_like_image,
    refresh_starter_message,
    register_starter_reposter,
    repost_starter_for_channel,
    run_with_progress,
    seconds_human,
    send_ephemeral,
    send_image_quota_message,
    send_image_with_compression,
    send_resolution_lock_message,
    trim,
)

load_dotenv()
logger = logging.getLogger("venice_image_cog")

# =================================================
# ENV
# =================================================
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
VENICE_IMAGE_URL = os.getenv("VENICE_IMAGE_URL")
VENICE_UPSCALE_URL = os.getenv("VENICE_UPSCALE_URL")

if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env")
if not VENICE_IMAGE_URL:
    raise RuntimeError("VENICE_IMAGE_URL not set in .env")
if not VENICE_UPSCALE_URL:
    raise RuntimeError("VENICE_UPSCALE_URL not set in .env")

# =================================================
# CHANNELS
# =================================================
NSFW_CHANNELS = [
    1415769909874524262,
    1415769966573260970,
    1416267309399670917,
    1416267383160442901,
    1346843244067160074,
    1477717109873049822,
]
SFW_CHANNEL = 1461752750550552741
ALLOWED_CHANNEL_IDS = set(NSFW_CHANNELS + [SFW_CHANNEL])

BUTTON_MESSAGE_TEXT = "💡 Choose Model for 🖼️ NEW image!"
LEGACY_STARTER_TEXTS = {
    "💡 Choose Model for 🖼️ NEW image!",
    "💡 Choose a model for a new image!",
}
RECENT_SCAN_LIMIT = 12

# =================================================
# QUOTA (geteilt mit Face-Cog über die Registry)
# =================================================
IMAGE_QUOTA_FILE = os.getenv("IMAGE_QUOTA_FILE", "goonhut_image_quota.json")
image_quota = get_quota_store(IMAGE_QUOTA_FILE)

RESOLUTION_MIN_TIER = {"1K": 0, "2K": 1, "4K": 2}

# =================================================
# MODELS / UI
# =================================================
DEFAULT_NEGATIVE_PROMPT = "disfigured, missing fingers, extra limbs, watermark, underage"
PROMPT_SUFFIX = " "

EASY_MODE_VALUE = "__easy_mode__"
EASY_MODE_ICON = "🔞"
EASY_MODE_LABEL = f"👉Easy Mode {EASY_MODE_ICON}👈"
NO_MODEL_VALUE = "__no_models__"

AUTO_FILTER_EPHEMERAL_TEXT = (
    "⚠️ Your prompt triggered this model's automatic safety filter, so no image was posted.\n"
    "Please try a different prompt, or use another image model that is less sensitive."
)
VENICE_FILTER_OCR_KEYWORDS = (
    "detected content",
    "violates our terms",
    "terms of service",
    "support@venice.ai",
    "please try changing your prompt",
    "try changing your prompt",
)

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

RATING_EXPLICIT = "explicit"
RATING_NUDITY = "nudity"
RATING_SFW = "sfw"
OPEN_RATINGS = {RATING_EXPLICIT, RATING_NUDITY}

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

MODELS: dict[str, dict[str, Any]] = {
    "hidream": {"label": "🌙 HiDream", "rating": RATING_SFW, "caps": {"prompt_limit": 1500, "default_steps": 20, "max_steps": 50, "cfg_default": 6.5, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
    "flux-2-max": {"label": "🌌 Flux 2 Max", "rating": RATING_SFW, "caps": {"prompt_limit": 3000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": ["auto", *_FULL_ASPECTS], "default_aspect_ratio": "auto", "width_height_divisor": 1, "resolutions": []}},
    "gpt-image-2": {"label": "🧠 GPT Image 2", "rating": RATING_SFW, "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": ["1K", "2K", "4K"]}},
    "gpt-image-1-5": {"label": "🪄 GPT Image 1.5", "rating": RATING_SFW, "caps": {"prompt_limit": 5000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": ["1:1", "3:2", "2:3"], "width_height_divisor": 1, "resolutions": []}},
    "hunyuan-image-v3": {"label": "🐉 Hunyuan Image 3.0", "rating": RATING_NUDITY, "caps": {"prompt_limit": 3000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": []}},
    "imagineart-1.5-pro": {"label": "🎨 ImagineArt 1.5 Pro", "rating": RATING_SFW, "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": ["1:1", "3:2", "16:9", "9:16", "2:3", "3:4", "4:5"], "width_height_divisor": 1, "resolutions": []}},
    "ideogram-v4": {"label": "🔤 Ideogram V4 (Text)", "rating": RATING_SFW, "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": []}},
    "nano-banana-2": {"label": "🐵 Nano Banana 2", "rating": RATING_SFW, "caps": {"prompt_limit": 32768, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": ["1K", "2K", "4K"]}},
    "nano-banana-pro": {"label": "🍌 Nano Banana Pro", "rating": RATING_SFW, "caps": {"prompt_limit": 32768, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": ["1K", "2K", "4K"]}},
    "recraft-v4-pro": {"label": "🏗️ Recraft V4 Pro", "rating": RATING_SFW, "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": []}},
    "seedream-v5-pro": {"label": "🌊 Seedream V5 Pro", "rating": RATING_NUDITY, "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": ["1:1", "3:2", "16:9", "9:16", "2:3", "3:4"], "width_height_divisor": 1, "resolutions": ["1K", "2K"], "default_resolution": "2K"}},
    "krea-2-turbo": {"label": "🎇 Krea 2 Turbo", "rating": RATING_EXPLICIT, "caps": {"prompt_limit": 5000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": ["1K", "2K"]}},
    "qwen-image-2-pro": {"label": "🧩 Qwen Image 2 Pro", "rating": RATING_SFW, "caps": {"prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": []}},
    "wan-2-7-pro-text-to-image": {"label": "🦈 Wan 2.7 Pro", "rating": RATING_SFW, "caps": {"prompt_limit": 3000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": _FULL_ASPECTS, "width_height_divisor": 1, "resolutions": []}},
    "grok-imagine-image-quality": {"label": "🚀 Grok Imagine HQ", "rating": RATING_SFW, "caps": {"prompt_limit": 7500, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": ["1:1", "16:9", "9:16", "3:4", "3:2", "2:3"], "width_height_divisor": 1, "resolutions": ["1K", "2K"]}},
    "lustify-sdxl": {"label": "💋 Lustify SDXL (Legacy)", "rating": RATING_EXPLICIT, "caps": {"prompt_limit": 1500, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
    "lustify-v7": {"label": "🥵 Lustify v7", "rating": RATING_EXPLICIT, "caps": {"prompt_limit": 1500, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
    "lustify-v8": {"label": "🔥 Lustify v8", "rating": RATING_EXPLICIT, "caps": {"prompt_limit": 1500, "default_steps": 30, "max_steps": 50, "cfg_default": 5.0, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
    "wai-Illustrious": {"label": "🎌 Anime (WAI)", "rating": RATING_SFW, "caps": {"prompt_limit": 1500, "default_steps": 25, "max_steps": 30, "cfg_default": 7.0, "aspect_ratios": None, "width_height_divisor": 16, "resolutions": []}},
    "z-image-turbo": {"label": "⚡ Z-Image Turbo", "rating": RATING_EXPLICIT, "caps": {"prompt_limit": 7500, "default_steps": 8, "max_steps": 8, "cfg_default": 6.0, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
    "chroma": {"label": "🌈 Chroma", "rating": RATING_NUDITY, "caps": {"prompt_limit": 7500, "default_steps": 10, "max_steps": 10, "cfg_default": 6.0, "aspect_ratios": None, "width_height_divisor": 8, "resolutions": []}},
}

MODEL_CONFIG: dict[str, dict[str, Any]] = {
    mid: {"label": m["label"], "rating": m["rating"], **DEFAULT_MODEL_ROW, **m["caps"]}
    for mid, m in MODELS.items()
}
MODEL_ORDER = list(MODELS.keys())
MODEL_RATINGS = {mid: m["rating"] for mid, m in MODELS.items()}
DISABLED_MODELS: set[str] = set()

NATIVE_RES_TIME_FACTOR = {"1K": 1.00, "2K": 1.30, "4K": 1.70}
UPSCALE_BASE_SECONDS = {2: 10.0, 4: 22.0}
UPSCALE_TARGET_FACTOR = {"2K": 1.10, "4K": 1.35}


# =================================================
# FILTER PLACEHOLDER DETECTION
# =================================================
def _contains_filter_keywords(text: str) -> bool:
    t = (text or "").lower()
    return sum(1 for k in VENICE_FILTER_OCR_KEYWORDS if k in t) >= 2


def _is_venice_filter_placeholder(image_bytes: bytes) -> bool:
    if not image_bytes or Image is None:
        return False
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return False

    gray = img.convert("L")
    w, h = gray.size

    if pytesseract is not None:
        with contextlib.suppress(Exception):
            if _contains_filter_keywords(pytesseract.image_to_string(gray)):
                return True

    hist = gray.histogram()
    total = max(1, w * h)
    dark_ratio = sum(hist[:28]) / total
    bright_ratio = sum(hist[220:]) / total
    mid_ratio = sum(hist[80:180]) / total

    mean = sum(i * c for i, c in enumerate(hist)) / total
    var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / total
    std = var ** 0.5

    squareish = abs(w - h) <= max(24, int(0.06 * max(w, h)))
    common_sizes = {512, 768, 896, 1024, 1280, 1536, 1792, 2048}
    likely_size = (w in common_sizes and h in common_sizes)

    return (
        squareish and likely_size
        and dark_ratio > 0.70
        and 0.006 < bright_ratio < 0.16
        and mid_ratio < 0.34
        and mean < 75
        and 16 < std < 78
    )


# =================================================
# MODEL HELPERS
# =================================================
def get_active_model_ids() -> list[str]:
    return [m for m in MODEL_ORDER if m not in DISABLED_MODELS]


def get_model_rating(model_id: str) -> str:
    return MODEL_RATINGS.get(model_id, RATING_SFW)


def is_open_model(model_id: str) -> bool:
    return get_model_rating(model_id) in OPEN_RATINGS


def get_easy_mode_candidates() -> list[str]:
    active = set(get_active_model_ids())
    return [m for m in MODEL_ORDER if m in active and is_open_model(m)]


def get_model_label(model_id: str) -> str:
    base = (MODEL_CONFIG.get(model_id) or {}).get("label", model_id)
    return f"{base} {EASY_MODE_ICON}" if is_open_model(model_id) else base


def get_model_ratios(model_id: str) -> list[str]:
    return MODEL_CONFIG[model_id].get("aspect_ratios") or FALLBACK_ASPECTS


def required_tier_for_resolution(resolution: Optional[str]) -> int:
    return int(RESOLUTION_MIN_TIER.get(resolution or "1K", 0))


def snap_to_divisor(value: int, divisor: int) -> int:
    if divisor <= 1:
        return max(1, int(value))
    return max(divisor, int(round(value / divisor) * divisor))


def dimensions_for_ratio(ratio: str, divisor: int, base_long_side: int = 1024) -> tuple[int, int]:
    # FIX: war r"^(\d+):(\d+)\$" - das \$ matchte ein Dollarzeichen statt
    # Zeilenende, also griff NIE ein Ratio und alles wurde 1024x1024.
    m = re.match(r"^(\d+):(\d+)\$", ratio) if ratio != "auto" else None
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


def channel_suffix(channel_id: Optional[int]) -> str:
    return PROMPT_SUFFIX


def generation_plan(model_id: str, wanted_resolution: str) -> tuple[Optional[str], Optional[int]]:
    native = set(MODEL_CONFIG[model_id]["resolutions"])
    if wanted_resolution in native:
        return wanted_resolution, None
    if wanted_resolution == "1K":
        return None, None
    if wanted_resolution == "2K":
        return ("1K", 2) if "1K" in native or not native else (None, 2)
    if wanted_resolution == "4K":
        if "2K" in native:
            return "2K", 2
        if "1K" in native or not native:
            return "1K", 4
        return None, 4
    return None, None


def build_generate_payload(
    model_id: str,
    ratio: str,
    generation_resolution: Optional[str],
    prompt: str,
    negative_prompt: str,
    cfg_scale: float,
    steps: int,
) -> dict[str, Any]:
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
        w, h = dimensions_for_ratio(ratio, cfg.get("width_height_divisor", 8), 1024)
        payload["width"], payload["height"] = w, h

    if generation_resolution and generation_resolution in set(cfg.get("resolutions", [])):
        payload["resolution"] = generation_resolution

    return payload


def build_resolution_hint(model_id: str) -> str:
    native = set(MODEL_CONFIG[model_id]["resolutions"])
    if not native:
        return "1K is native. 2K/4K via upscale."
    ordered = sorted(
        native, key=lambda x: RESOLUTION_TIERS.index(x) if x in RESOLUTION_TIERS else 999
    )
    return f"Native: {', '.join(ordered)} • 2K/4K may use upscale"


def estimate_generation_seconds(
    model_id: str, steps: int, cfg_scale: float, prompt_len: int, generation_resolution: str
) -> float:
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
        description=(
            f"**Model:** {get_model_label(model_id)}\n"
            f"**Aspect Ratio:** {ASPECT_LABELS.get(ratio, ratio)}"
        ),
        color=discord.Color.gold(),
    )


def _image_progress_embed(
    user: discord.abc.User,
    prompt: str,
    model_label: str,
    ratio: str,
    resolution: str,
    percent: int,
    eta_sec: float,
    stage: str,
    quota: dict[str, int],
) -> discord.Embed:
    return build_progress_embed(
        title="🖼️ IMAGE RENDER",
        color=discord.Color.blurple(),
        user=user,
        prompt=prompt,
        percent=percent,
        status_lines=[stage, f"ETA: `{eta_text(eta_sec)}`"],
        quota_name="Quota (24h, shared)",
        quota_used=int(quota["used"]),
        quota_limit=int(quota["limit"]),
        quota_remaining=int(quota["remaining"]),
        footer=f"{model_label} • {ASPECT_LABELS.get(ratio, ratio)} • {resolution}",
    )


def is_model_dropdown_message(msg: discord.Message) -> bool:
    if msg.embeds or msg.attachments:
        return False
    if msg.components:
        for row in msg.components:
            for child in row.children:
                cid = getattr(child, "custom_id", None)
                if isinstance(cid, str) and cid.startswith("venice_model_select"):
                    return True
    return (msg.content or "").strip() in LEGACY_STARTER_TEXTS


# =================================================
# API CALLS
# =================================================
async def venice_generate(
    session: aiohttp.ClientSession, payload: dict[str, Any], retries: int = 2
) -> Optional[bytes]:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}"}
    req_id = os.urandom(4).hex()
    logger.info("[IMG %s] -> POST generate model=%s", req_id, payload.get("model"))
    timeout = aiohttp.ClientTimeout(total=120, connect=20, sock_read=90)

    for attempt in range(retries + 1):
        try:
            async with session.post(
                VENICE_IMAGE_URL, headers=headers, json=payload, timeout=timeout
            ) as resp:
                logger.info("[IMG %s] <- status=%s attempt=%s", req_id, resp.status, attempt + 1)
                if resp.status == 200:
                    img = await extract_image_from_response(resp)
                    return img if img and looks_like_image(img) else None

                if resp.status in (429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue

                with contextlib.suppress(Exception):
                    logger.error("[IMG %s] error body: %s", req_id, (await resp.text())[:500])
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt < retries:
                await asyncio.sleep(1.2 * (attempt + 1))
                continue
            return None
        except Exception:
            logger.exception("[IMG %s] unexpected", req_id)
            return None
    return None


async def _upscale_once(
    session: aiohttp.ClientSession, image_bytes: bytes, scale: int, retries: int = 2
) -> Optional[bytes]:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}"}
    if not looks_like_image(image_bytes):
        return None

    b64 = bytes_to_b64(image_bytes)
    payloads = [
        {"image": b64, "scale": scale},
        {"image": f"data:image/png;base64,{b64}", "scale": scale},
    ]
    timeout = aiohttp.ClientTimeout(total=120, connect=20, sock_read=90)

    for attempt in range(retries + 1):
        for payload in payloads:
            try:
                async with session.post(
                    VENICE_UPSCALE_URL, headers=headers, json=payload, timeout=timeout
                ) as resp:
                    if resp.status == 200:
                        out = await extract_image_from_response(resp)
                        if out and looks_like_image(out):
                            return out
            except Exception:
                continue
        if attempt < retries:
            await asyncio.sleep(1.2 * (attempt + 1))
    return None


async def venice_upscale(
    session: aiohttp.ClientSession, image_bytes: bytes, scale: int, retries: int = 2
) -> Optional[bytes]:
    if scale == 4:
        first = await _upscale_once(session, image_bytes, 2, retries=retries)
        if not first:
            return None
        return await _upscale_once(session, first, 2, retries=retries)
    return await _upscale_once(session, image_bytes, scale, retries=retries)


# =================================================
# UI FLOW
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
# UI CLASSES
# =================================================
class StarterModelSelect(discord.ui.Select):
    def __init__(self, session: aiohttp.ClientSession, channel_id: int):
        self.session = session
        self.channel_id = channel_id
        super().__init__(
            placeholder="🎨 Choose your model...",
            min_values=1,
            max_values=1,
            options=build_model_options(channel_id, include_easy=True),
            custom_id=f"venice_model_select:{channel_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        cid = interaction.channel.id if interaction.channel else self.channel_id
        await handle_model_selection(
            interaction=interaction,
            session=self.session,
            selected=self.values[0],
            hidden_suffix=channel_suffix(cid),
            owner_id=interaction.user.id,
            channel_id=cid,
        )


class StarterView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel_id: int):
        super().__init__(timeout=None)
        self.add_item(StarterModelSelect(session, channel_id))


class AspectRatioSelect(discord.ui.Select):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        model_id: str,
        hidden_suffix: str,
        owner_id: int,
        previous_inputs=None,
    ):
        self.session = session
        self.model_id = model_id
        self.hidden_suffix = hidden_suffix
        self.owner_id = owner_id
        self.previous_inputs = previous_inputs or {}

        options = [
            discord.SelectOption(label=ASPECT_LABELS.get(r, r), value=r)
            for r in get_model_ratios(model_id)
        ][:25]
        super().__init__(
            placeholder="📐 Choose aspect ratio...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"venice_aspect_select:{model_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.model_id in DISABLED_MODELS:
            await send_ephemeral(interaction, "❌ This model is disabled.")
            return

        src = interaction.message
        await interaction.response.send_modal(
            GenerationModal(
                self.session, self.model_id, self.values[0],
                self.hidden_suffix, self.owner_id, self.previous_inputs,
            )
        )
        if src:
            with contextlib.suppress(Exception):
                await src.edit(view=None, content="✅ Aspect ratio selected.")


class AspectRatioSelectView(OwnerLockedView):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        model_id: str,
        hidden_suffix: str,
        owner_id: int,
        previous_inputs=None,
    ):
        super().__init__(owner_id=owner_id, timeout=600)
        self.add_item(
            AspectRatioSelect(session, model_id, hidden_suffix, owner_id, previous_inputs)
        )


class EasyModeModal(discord.ui.Modal):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        model_id: str,
        ratio: str,
        hidden_suffix: str,
        owner_id: int,
    ):
        self.session = session
        self.model_id = model_id
        self.ratio = ratio
        self.hidden_suffix_value = hidden_suffix
        self.owner_id = owner_id
        cfg = MODEL_CONFIG[model_id]

        super().__init__(
            title=f"Easy Mode {EASY_MODE_ICON} • {get_model_label(model_id)} "
                  f"• {ASPECT_LABELS.get(ratio, ratio)}"[:45]
        )
        self.prompt = discord.ui.TextInput(
            label="Describe what you want to see",
            style=discord.TextStyle.paragraph,
            required=True,
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
        generation_data = {
            "model_id": self.model_id,
            "ratio": self.ratio,
            "prompt_text": self.prompt.value,
            "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
            "cfg_scale": float(cfg["cfg_default"]),
            "steps": int(cfg["default_steps"]),
            "hidden_suffix": self.hidden_suffix_value,
            "owner_id": self.owner_id,
            "channel_id": interaction.channel.id if interaction.channel else None,
            "is_easy_mode": True,
            "previous_inputs": {
                "prompt": self.prompt.value,
                "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
                "cfg_value": "",
                "steps": None,
                "hidden_suffix": self.hidden_suffix_value,
            },
        }

        await send_ephemeral(
            interaction,
            content=(
                f"✅ Easy Mode: {get_model_label(self.model_id)} "
                f"• {ASPECT_LABELS.get(self.ratio, self.ratio)}\n"
                f"{build_resolution_hint(self.model_id)}\nChoose resolution:"
            ),
            view=ResolutionSelectView(self.session, generation_data),
        )


class GenerationModal(discord.ui.Modal):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        model_id: str,
        ratio: str,
        hidden_suffix: str,
        owner_id: int,
        previous_inputs=None,
    ):
        self.session = session
        self.model_id = model_id
        self.ratio = ratio
        self.hidden_suffix_value = hidden_suffix
        self.owner_id = owner_id
        self.previous_inputs = previous_inputs or {}

        cfg = MODEL_CONFIG[model_id]
        fixed_steps = cfg["default_steps"] == cfg["max_steps"]
        super().__init__(
            title=f"{get_model_label(model_id)} • {ASPECT_LABELS.get(ratio, ratio)}"[:45]
        )

        self.prompt = discord.ui.TextInput(
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=min(int(cfg["prompt_limit"]), 4000),
            default=self.previous_inputs.get("prompt", ""),
        )
        self.negative_prompt = discord.ui.TextInput(
            label="Negative prompt (optional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=800,
            default=self.previous_inputs.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT,
        )
        self.cfg_value = discord.ui.TextInput(
            label="CFG scale",
            style=discord.TextStyle.short,
            required=False,
            max_length=8,
            placeholder=str(cfg["cfg_default"]),
            default=self.previous_inputs.get("cfg_value", ""),
        )
        self.steps_value = discord.ui.TextInput(
            label=(f"Steps (fixed: {cfg['default_steps']})" if fixed_steps
                   else f"Steps (1-{cfg['max_steps']})"),
            style=discord.TextStyle.short,
            required=False,
            max_length=3,
            placeholder=str(cfg["default_steps"]),
            default=(str(self.previous_inputs.get("steps"))
                     if self.previous_inputs.get("steps") else ""),
        )
        self.hidden_suffix = discord.ui.TextInput(
            label="Hidden suffix",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1200,
            placeholder=(hidden_suffix[:100] if hidden_suffix else ""),
            default=self.previous_inputs.get("hidden_suffix", ""),
        )

        for item in (
            self.prompt, self.negative_prompt, self.cfg_value,
            self.steps_value, self.hidden_suffix,
        ):
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

        generation_data = {
            "model_id": self.model_id,
            "ratio": self.ratio,
            "prompt_text": self.prompt.value,
            "negative_prompt": negative_prompt,
            "cfg_scale": cfg_val,
            "steps": steps_val,
            "hidden_suffix": hidden_suffix,
            "owner_id": self.owner_id,
            "channel_id": interaction.channel.id if interaction.channel else None,
            "is_easy_mode": False,
            "previous_inputs": {
                "prompt": self.prompt.value,
                "negative_prompt": negative_prompt,
                "cfg_value": self.cfg_value.value,
                "steps": steps_val if steps_val != cfg["default_steps"] else None,
                "hidden_suffix": hidden_suffix,
            },
        }

        await send_ephemeral(
            interaction,
            content=(
                f"✅ {get_model_label(self.model_id)} "
                f"• {ASPECT_LABELS.get(self.ratio, self.ratio)}\n"
                f"{build_resolution_hint(self.model_id)}\nChoose resolution:"
            ),
            view=ResolutionSelectView(self.session, generation_data),
        )


class ResolutionSelectView(OwnerLockedView):
    def __init__(self, session: aiohttp.ClientSession, generation_data: dict[str, Any]):
        super().__init__(owner_id=generation_data["owner_id"], timeout=900)
        self.session = session
        self.generation_data = generation_data

        model_id = generation_data["model_id"]
        native = set(MODEL_CONFIG[model_id]["resolutions"])

        for res in RESOLUTION_TIERS:
            label = f"{res} ↗" if (res not in native and res in ("2K", "4K")) else res
            style = (
                discord.ButtonStyle.success if res == "1K"
                else discord.ButtonStyle.primary if res == "2K"
                else discord.ButtonStyle.danger
            )
            btn = discord.ui.Button(
                label=label, style=style, custom_id=f"venice_res:{res}:{model_id}"
            )
            btn.callback = self._make_resolution_callback(res)
            self.add_item(btn)

    def _make_resolution_callback(self, resolution: str):
        async def callback(interaction: discord.Interaction):
            if not isinstance(interaction.user, discord.Member):
                await send_ephemeral(interaction, "❌ This action can only be used in a server.")
                return

            current_tier = get_member_tier(interaction.user)
            needed_tier = required_tier_for_resolution(resolution)
            if current_tier < needed_tier:
                await send_resolution_lock_message(
                    interaction, resolution, needed_tier, current_tier
                )
                return

            await interaction.response.defer(ephemeral=True)
            if interaction.message:
                with contextlib.suppress(Exception):
                    await interaction.message.edit(view=None, content="✅ Resolution selected.")
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

        progress_msg: Optional[discord.Message] = None
        quota_success = False
        token_quota: Optional[dict[str, int]] = None

        try:
            if model_id in DISABLED_MODELS:
                await send_ephemeral(interaction, "❌ This model is disabled.")
                return
            if not interaction.guild:
                await send_ephemeral(interaction, "❌ This action is server-only.")
                return

            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            image_limit = get_image_limit_for_member(member)

            ok_quota, state, token_quota = await image_quota.reserve(
                interaction.guild.id, interaction.user.id, image_limit, 1
            )
            if not ok_quota:
                await send_image_quota_message(interaction, member, state)
                return

            full_prompt = f"{(prompt_text or '').strip()} {(hidden_suffix or '').strip()}".strip()
            gen_res, upscale_factor = generation_plan(model_id, resolution)
            payload = build_generate_payload(
                model_id, ratio, gen_res, full_prompt, negative_prompt, cfg_val, steps
            )
            effective_gen_res = gen_res or "1K"

            est_gen = estimate_generation_seconds(
                model_id, steps, cfg_val, len(prompt_text or ""), effective_gen_res
            )
            gen_cap = 82 if upscale_factor in (2, 4) else 97

            progress_msg = await send_ephemeral(
                interaction,
                embed=_image_progress_embed(
                    interaction.user, prompt_text, get_model_label(model_id),
                    ratio, resolution, 0, est_gen, "Initializing request...", state,
                ),
            )

            gen_task = asyncio.create_task(venice_generate(self.session, payload))

            def gen_embed(percent: int, eta: float) -> discord.Embed:
                return _image_progress_embed(
                    interaction.user, prompt_text, get_model_label(model_id),
                    ratio, resolution, percent, eta, "Generating image...", state,
                )

            await run_with_progress(gen_task, progress_msg, est_gen, 0, gen_cap, gen_embed, 6.0)
            image_bytes = await gen_task
            if not image_bytes:
                await send_ephemeral(interaction, "❌ Image generation failed.")
                return

            upscaled_success = False
            if upscale_factor in (2, 4):
                est_up = estimate_upscale_seconds(upscale_factor, resolution)
                up_task = asyncio.create_task(
                    venice_upscale(self.session, image_bytes, upscale_factor)
                )

                def up_embed(percent: int, eta: float) -> discord.Embed:
                    return _image_progress_embed(
                        interaction.user, prompt_text, get_model_label(model_id),
                        ratio, resolution, percent, eta,
                        f"Upscaling {upscale_factor}x...", state,
                    )

                await run_with_progress(up_task, progress_msg, est_up, gen_cap, 99, up_embed, 4.0)
                upscaled = await up_task
                if upscaled:
                    image_bytes = upscaled
                    upscaled_success = True

            if _is_venice_filter_placeholder(image_bytes):
                await send_ephemeral(interaction, AUTO_FILTER_EPHEMERAL_TEXT)
                return

            if progress_msg:
                with contextlib.suppress(Exception):
                    await progress_msg.edit(
                        content=None,
                        embed=_image_progress_embed(
                            interaction.user, prompt_text, get_model_label(model_id),
                            ratio, resolution, 100, 0.0, "Finalizing upload...", state,
                        ),
                    )

            if not interaction.channel:
                await send_ephemeral(interaction, "❌ Channel unavailable.")
                return

            embed = discord.Embed(
                title="🖼️ Image",
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_author(
                name=f"{interaction.user.display_name} • {datetime.now().strftime('%Y-%m-%d')}",
                icon_url=interaction.user.display_avatar.url,
            )
            prompt_preview = codeblock_safe(trim((prompt_text or "").replace("\n\n", "\n"), 1600))
                    embed.add_field(
                        name="Prompt",
                        value=f"```{prompt_preview}```",
                        inline=False,
                    )

            default_hidden = channel_suffix(channel_id)
            used_hidden = previous_inputs.get("hidden_suffix")
            if isinstance(used_hidden, str) and used_hidden and used_hidden != default_hidden:
                embed.add_field(name="Hidden Prompt", value="✅", inline=False)

            if negative_prompt and negative_prompt != DEFAULT_NEGATIVE_PROMPT:
                embed.add_field(
                    name="Negative Prompt",
                    value=f"```{codeblock_safe(trim(negative_prompt, 1000))}```",
                    inline=False,
                )

            guild_icon = (
                interaction.guild.icon.url
                if interaction.guild and interaction.guild.icon else None
            )
            upflag = "📈" if upscaled_success else ""
            embed.set_footer(
                text=(
                    f"{get_model_label(model_id)} • {ASPECT_LABELS.get(ratio, ratio)} "
                    f"• 🧱 {resolution}{upflag} • 🤖 {cfg_val} • 🪜 {steps}"
                ),
                icon_url=guild_icon,
            )

            posted = await send_image_with_compression(
                channel=interaction.channel,
                interaction=interaction,
                image_bytes=image_bytes,
                embed=embed,
                content=f"{SERVER_ANIM_ICON} 🖼️ **Image** • {interaction.user.mention}",
                filename_prompt=prompt_text,
                filename_fallback="image",
            )
            if posted is None:
                await send_ephemeral(interaction, "❌ Upload failed after compression retries.")
                return

            quota_success = True
            await add_rating_reactions(posted)

            quota_now = await image_quota.peek(
                interaction.guild.id, interaction.user.id, image_limit
            )
            await send_ephemeral(
                interaction,
                content=(
                    f"✅ Image created.\n"
                    f"Remaining in 24h (shared): "
                    f"**{quota_now['remaining']} / {quota_now['limit']}** "
                    f"(reset in **{seconds_human(int(quota_now['reset_in']))}**).\n"
                    f"Use the button below if you want to animate this image."
                ),
                view=AnimateEphemeralView(
                    owner_id=interaction.user.id,
                    source_channel_id=interaction.channel.id,
                    source_message_id=posted.id,
                    prompt_text=prompt_text,
                    ratio=ratio,
                ),
            )

        finally:
            if progress_msg:
                with contextlib.suppress(Exception):
                    await progress_msg.delete()

            if not quota_success:
                await image_quota.rollback(token_quota)

            with contextlib.suppress(Exception):
                await repost_starter_for_channel(interaction.channel)

            self.stop()


# =================================================
# COG
# =================================================
class VeniceImageCog(commands.Cog):
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
        for channel_id in ALLOWED_CHANNEL_IDS:
            self.bot.add_view(StarterView(self.session, channel_id))
            register_starter_reposter(channel_id, self.ensure_starter_message)

    def cog_unload(self):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    async def ensure_starter_message(self, channel: discord.TextChannel):
        await self._ensure_session()
        await refresh_starter_message(
            channel=channel,
            bot_user_id=(self.bot.user.id if self.bot.user else None),
            content=BUTTON_MESSAGE_TEXT,
            view_factory=lambda: StarterView(self.session, channel.id),
            matcher=is_model_dropdown_message,
            scan_limit=RECENT_SCAN_LIMIT,
        )

    @commands.command(name="venice_reload")
    @commands.has_permissions(administrator=True)
    async def venice_reload(self, ctx: commands.Context):
        await self._ensure_session()

        reposted = 0
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.id in ALLOWED_CHANNEL_IDS:
                    await self.ensure_starter_message(channel)
                    reposted += 1

        pruned = await image_quota.prune()
        await ctx.send(
            f"✅ Reloaded. Active={len(get_active_model_ids())}, "
            f"Disabled={len(DISABLED_MODELS)}, reposted {reposted} starter message(s), "
            f"pruned {pruned} expired quota entr(ies)."
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
    await bot.add_cog(VeniceImageCog(bot))