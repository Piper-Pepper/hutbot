import asyncio
import base64
import io
import logging
import os
import random
import re
import time
import uuid
from datetime import datetime
from typing import Optional, Any

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

# =================================================
# ENV / API
# =================================================
load_dotenv()

VENICE_API_KEY = os.getenv("VENICE_API_KEY")
if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env!")

VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"
VENICE_UPSCALE_URL = os.getenv("VENICE_UPSCALE_URL", "https://api.venice.ai/api/v1/image/upscale")

BUTTON_MESSAGE_TEXT = "💡 Choose Model for 🖼️ NEW image!"
LEGACY_STARTER_TEXTS = {
    "💡 Choose Model for 🖼️ NEW image!",
    "💡 Choose a model for a new image!",
}
RECENT_SCAN_LIMIT = 10

SURPRISE_VALUE = "__surprise_me__"
SURPRISE_LABEL = "🎲 Surprise me"

logger = logging.getLogger("venice_picture_bot")

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

RESOLUTION_ROLE_REQUIREMENTS = {
    "2K": LEVEL4_ROLE_ID,
    "4K": LEVEL11_ROLE_ID,
}
ROLE_LEVEL_NAMES = {
    LEVEL4_ROLE_ID: "Level 4",
    LEVEL11_ROLE_ID: "Level 11",
}

# =================================================
# PROMPT / UI
# =================================================
DEFAULT_NEGATIVE_PROMPT = "disfigured, missing fingers, extra limbs, watermark, underage"
NSFW_PROMPT_SUFFIX = " "
SFW_PROMPT_SUFFIX = " "

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

# Fallback for models with no explicit aspectRatios
FALLBACK_ASPECTS = ["1:1", "16:9", "9:16"]

# =================================================
# MODEL CONFIG (JSON-aligned)
# removed as previously requested:
# - venice-sd35
# - flux-2-pro
# - lustify-sdxl
# - bria-bg-remover
# =================================================
COMMON_RATIOS = ["1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"]
GROK_RATIOS = ["1:1", "16:9", "9:16", "3:4", "3:2", "2:3"]
GPT15_RATIOS = ["1:1", "3:2", "2:3"]

MODEL_CONFIG: dict[str, dict[str, Any]] = {
    "hidream": {
        "label": "🌙 HiDream",
        "prompt_limit": 1500, "default_steps": 20, "max_steps": 50, "cfg_default": 6.5,
        "aspect_ratios": None, "width_height_divisor": 8, "resolutions": [],
    },
    "flux-2-max": {
        "label": "🌌 Flux 2 Max",
        "prompt_limit": 3000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": ["auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"],
        "width_height_divisor": 1, "resolutions": [],
    },
    "gpt-image-2": {
        "label": "🧠 GPT Image 2",
        "prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": ["1K", "2K", "4K"],
    },
    "gpt-image-1-5": {
        "label": "🪄 GPT Image 1.5",
        "prompt_limit": 5000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": GPT15_RATIOS, "width_height_divisor": 1, "resolutions": [],
    },
    "hunyuan-image-v3": {
        "label": "🐉 Hunyuan Image 3.0",
        "prompt_limit": 3000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": [],
    },
    "imagineart-1.5-pro": {
        "label": "🎨 ImagineArt 1.5 Pro",
        "prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": ["1:1", "3:2", "16:9", "9:16", "2:3", "3:4", "4:5"],
        "width_height_divisor": 1, "resolutions": [],
    },
    "nano-banana-2": {
        "label": "🐵 Nano Banana 2",
        "prompt_limit": 32768, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": ["1K", "2K", "4K"],
    },
    "nano-banana-pro": {
        "label": "🍌 Nano Banana Pro",
        "prompt_limit": 32768, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": ["1K", "2K", "4K"],
    },
    "recraft-v4": {
        "label": "🧱 Recraft V4",
        "prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": [],
    },
    "recraft-v4-pro": {
        "label": "🏗️ Recraft V4 Pro",
        "prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": [],
    },
    "seedream-v4": {
        "label": "🌊 Seedream V4.5",
        "prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": [],
    },
    "seedream-v5-lite": {
        "label": "💧 Seedream V5 Lite",
        "prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": [],
    },
    "qwen-image-2": {
        "label": "🔷 Qwen Image 2",
        "prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": [],
    },
    "qwen-image-2-pro": {
        "label": "🧩 Qwen Image 2 Pro",
        "prompt_limit": 10000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": [],
    },
    "wan-2-7-text-to-image": {
        "label": "🐋 Wan 2.7",
        "prompt_limit": 3000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": [],
    },
    "wan-2-7-pro-text-to-image": {
        "label": "🦈 Wan 2.7 Pro",
        "prompt_limit": 3000, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": COMMON_RATIOS, "width_height_divisor": 1, "resolutions": [],
    },
    "grok-imagine-image": {
        "label": "🧠 Grok Imagine",
        "prompt_limit": 7500, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": GROK_RATIOS, "width_height_divisor": 1, "resolutions": ["1K", "2K"],
    },
    "grok-imagine-image-pro": {
        "label": "🚀 Grok Imagine Pro",
        "prompt_limit": 7500, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": GROK_RATIOS, "width_height_divisor": 1, "resolutions": ["1K", "2K"],
    },
    "lustify-v7": {
        "label": "🥵 Lustify v7",
        "prompt_limit": 1500, "default_steps": 20, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": None, "width_height_divisor": 8, "resolutions": [],
    },
    "lustify-v8": {
        "label": "🔥 Lustify v8",
        "prompt_limit": 1500, "default_steps": 30, "max_steps": 50, "cfg_default": 5.0,
        "aspect_ratios": None, "width_height_divisor": 8, "resolutions": [],
    },
    "qwen-image": {
        "label": "🐼 Qwen Image",
        "prompt_limit": 1500, "default_steps": 8, "max_steps": 8, "cfg_default": 6.0,
        "aspect_ratios": None, "width_height_divisor": 8, "resolutions": [],
    },
    "wai-Illustrious": {
        "label": "🎌 Anime (WAI)",
        "prompt_limit": 1500, "default_steps": 25, "max_steps": 30, "cfg_default": 7.0,
        "aspect_ratios": None, "width_height_divisor": 16, "resolutions": [],
    },
    "z-image-turbo": {
        "label": "⚡ Z-Image Turbo",
        "prompt_limit": 7500, "default_steps": 8, "max_steps": 8, "cfg_default": 6.0,
        "aspect_ratios": None, "width_height_divisor": 8, "resolutions": [],
    },
    "chroma": {
        "label": "🌈 Chroma",
        "prompt_limit": 7500, "default_steps": 10, "max_steps": 10, "cfg_default": 6.0,
        "aspect_ratios": None, "width_height_divisor": 8, "resolutions": [],
    },
}

UNCENSORED_MODELS = {
    "lustify-v7",
    "lustify-v8",
    "grok-imagine-image",
    "grok-imagine-image-pro",
    "z-image-turbo",
    "seedream-v4",
    "seedream-v5-lite",
    "wan-2-7-text-to-image",
    "wan-2-7-pro-text-to-image",
}

MODEL_ORDER = list(MODEL_CONFIG.keys())

# =================================================
# LOCKS
# =================================================
_channel_locks: dict[int, asyncio.Lock] = {}


def get_channel_lock(channel_id: int) -> asyncio.Lock:
    lock = _channel_locks.get(channel_id)
    if lock is None:
        lock = asyncio.Lock()
        _channel_locks[channel_id] = lock
    return lock


# =================================================
# HELPERS
# =================================================
def get_model_label(model_id: str) -> str:
    base = MODEL_CONFIG[model_id]["label"]
    return f"{base} 🔞" if model_id in UNCENSORED_MODELS else base


def get_model_ratios(model_id: str) -> list[str]:
    ratios = MODEL_CONFIG[model_id]["aspect_ratios"]
    return ratios if ratios else FALLBACK_ASPECTS


def make_safe_filename(prompt: str) -> str:
    base = "_".join((prompt or "").split()[:5]) or "image"
    base = re.sub(r"[^a-zA-Z0-9_]", "_", base)
    return f"{base}_{int(time.time_ns())}_{uuid.uuid4().hex[:8]}.png"


def snap_to_divisor(value: int, divisor: int) -> int:
    if divisor <= 1:
        return max(1, int(value))
    snapped = int(round(value / divisor) * divisor)
    return max(divisor, snapped)


def dimensions_for_ratio(ratio: str, divisor: int, base_long_side: int = 1024) -> tuple[int, int]:
    if ratio == "auto":
        side = snap_to_divisor(base_long_side, divisor)
        return side, side

    m = re.match(r"^(\d+):(\d+)$", ratio)
    if not m:
        side = snap_to_divisor(base_long_side, divisor)
        return side, side

    rw = int(m.group(1))
    rh = int(m.group(2))
    if rw <= 0 or rh <= 0:
        side = snap_to_divisor(base_long_side, divisor)
        return side, side

    if rw >= rh:
        w = base_long_side
        h = int(round(base_long_side * (rh / rw)))
    else:
        h = base_long_side
        w = int(round(base_long_side * (rw / rh)))

    return snap_to_divisor(w, divisor), snap_to_divisor(h, divisor)


def build_model_options(channel_id: int, include_surprise: bool = True) -> list[discord.SelectOption]:
    if channel_id not in ALLOWED_CHANNEL_IDS:
        return []

    options: list[discord.SelectOption] = []
    if include_surprise:
        options.append(discord.SelectOption(label=SURPRISE_LABEL, value=SURPRISE_VALUE))

    for model_id in MODEL_ORDER:
        options.append(discord.SelectOption(label=get_model_label(model_id), value=model_id))

    return options[:25]


def is_model_dropdown_message(msg: discord.Message) -> bool:
    if not msg.components or msg.embeds or msg.attachments:
        return False

    for row in msg.components:
        for child in row.children:
            cid = getattr(child, "custom_id", None)
            if isinstance(cid, str) and (
                cid.startswith("venice_model_select:") or cid.startswith("venice_model_select_")
            ):
                return True

    return (msg.content or "").strip() in LEGACY_STARTER_TEXTS


def required_role_for_resolution(resolution: Optional[str]) -> Optional[int]:
    return RESOLUTION_ROLE_REQUIREMENTS.get(resolution) if resolution else None


def has_role(member: discord.Member, role_id: int) -> bool:
    return any(r.id == role_id for r in member.roles)


def resolution_native_supported(model_id: str, resolution: str) -> bool:
    return resolution in set(MODEL_CONFIG[model_id]["resolutions"])


def generation_plan(model_id: str, wanted_resolution: str) -> tuple[Optional[str], Optional[int]]:
    """
    Returns (generation_resolution, upscale_factor).
    generation_resolution is native resolution to pass if supported by model.
    upscale_factor is 2 or 4 when post-upscale is needed.
    """
    native = set(MODEL_CONFIG[model_id]["resolutions"])

    if wanted_resolution == "1K":
        if "1K" in native:
            return "1K", None
        return None, None

    if wanted_resolution == "2K":
        if "2K" in native:
            return "2K", None
        if "1K" in native:
            return "1K", 2
        return None, 2

    if wanted_resolution == "4K":
        if "4K" in native:
            return "4K", None
        if "2K" in native:
            return "2K", 2
        if "1K" in native:
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

    native_ratios = cfg["aspect_ratios"]
    if native_ratios:
        payload["aspect_ratio"] = ratio if ratio in native_ratios else native_ratios[0]
    else:
        w, h = dimensions_for_ratio(ratio, cfg["width_height_divisor"], base_long_side=1024)
        payload["width"] = w
        payload["height"] = h

    if generation_resolution and generation_resolution in cfg["resolutions"]:
        payload["resolution"] = generation_resolution

    return payload


def build_resolution_hint(model_id: str) -> str:
    native = set(MODEL_CONFIG[model_id]["resolutions"])

    if not native:
        return "1K is native for this model. 2K/4K are delivered via upscale."

    parts = [f"Native: {', '.join(sorted(native, key=lambda x: RESOLUTION_TIERS.index(x)))}"]

    if "2K" in native:
        parts.append(f"2K requires <@&{LEVEL4_ROLE_ID}>")
    else:
        parts.append("2K via upscale")

    if "4K" in native:
        parts.append(f"4K requires <@&{LEVEL11_ROLE_ID}>")
    else:
        parts.append("4K via upscale")

    parts.append("Earn XP in the server to unlock higher tiers.")
    return " • ".join(parts)


def build_surprise_embed(model_id: str, ratio: str) -> discord.Embed:
    emb = discord.Embed(
        title="🎲 Surprise pick!",
        description=f"**Model:** {get_model_label(model_id)}\n**Aspect Ratio:** {ASPECT_LABELS.get(ratio, ratio)}",
        color=discord.Color.purple(),
    )
    return emb


async def send_ephemeral(interaction: discord.Interaction, content: str):
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)


async def send_resolution_lock_message(interaction: discord.Interaction, resolution: str, role_id: int):
    level_name = ROLE_LEVEL_NAMES.get(role_id, "Required level")
    await send_ephemeral(
        interaction,
        f"🔒 **{resolution}** is locked.\n"
        f"You need <@&{role_id}> (**{level_name}**) to use this quality tier.\n"
        f"💡 Earn XP in the server to unlock this role."
    )


async def venice_generate(session: aiohttp.ClientSession, payload: dict[str, Any], retries: int = 2) -> Optional[bytes]:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}"}
    for attempt in range(retries + 1):
        try:
            async with session.post(VENICE_IMAGE_URL, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    return await resp.read()

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


async def venice_upscale(
    session: aiohttp.ClientSession,
    image_bytes: bytes,
    scale: int,
    retries: int = 2
) -> Optional[bytes]:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}"}

    for attempt in range(retries + 1):
        try:
            # Try multipart first
            form = aiohttp.FormData()
            form.add_field("file", image_bytes, filename="image.png", content_type="image/png")
            form.add_field("scale", str(scale))
            form.add_field("return_binary", "true")

            async with session.post(VENICE_UPSCALE_URL, headers=headers, data=form) as resp:
                if resp.status == 200:
                    return await resp.read()

                body = await resp.text()
                logger.warning("Venice upscale multipart error %s: %s", resp.status, body)

            # Fallback: JSON base64
            payload = {
                "image": base64.b64encode(image_bytes).decode("utf-8"),
                "scale": scale,
                "return_binary": True,
            }
            async with session.post(VENICE_UPSCALE_URL, headers=headers, json=payload) as resp2:
                if resp2.status == 200:
                    return await resp2.read()

                body2 = await resp2.text()
                logger.warning("Venice upscale json error %s: %s", resp2.status, body2)

            if attempt < retries:
                await asyncio.sleep(1.2 * (attempt + 1))
                continue

            return None

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Venice upscale request failed (attempt %s): %s", attempt + 1, e)
            if attempt < retries:
                await asyncio.sleep(1.2 * (attempt + 1))
                continue
            return None
        except Exception as e:
            logger.exception("Unexpected error in venice_upscale: %s", e)
            return None

    return None


# =================================================
# OWNER LOCKED BASE VIEW
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
# FLOW: MODEL -> ASPECT -> MODAL -> RESOLUTION
# =================================================
class AspectRatioSelect(discord.ui.Select):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        model_id: str,
        hidden_suffix: str,
        owner_id: int,
        previous_inputs: Optional[dict[str, Any]] = None,
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
        ratio = self.values[0]
        source_msg = interaction.message

        await interaction.response.send_modal(
            GenerationModal(
                session=self.session,
                model_id=self.model_id,
                ratio=ratio,
                hidden_suffix=self.hidden_suffix,
                owner_id=self.owner_id,
                previous_inputs=self.previous_inputs,
            )
        )

        # Remove aspect dropdown post once clicked
        if source_msg:
            try:
                await source_msg.edit(view=None, content="✅ Aspect ratio selected.")
            except Exception:
                pass


class AspectRatioSelectView(OwnerLockedView):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        model_id: str,
        hidden_suffix: str,
        owner_id: int,
        previous_inputs: Optional[dict[str, Any]] = None,
    ):
        super().__init__(owner_id=owner_id, timeout=600)
        self.add_item(
            AspectRatioSelect(
                session=session,
                model_id=model_id,
                hidden_suffix=hidden_suffix,
                owner_id=owner_id,
                previous_inputs=previous_inputs
            )
        )


class StarterModelSelect(discord.ui.Select):
    def __init__(self, session: aiohttp.ClientSession, channel_id: int):
        self.session = session
        self.channel_id = channel_id

        super().__init__(
            placeholder="🎨 Choose your model...",
            min_values=1,
            max_values=1,
            options=build_model_options(channel_id, include_surprise=True),
            custom_id=f"venice_model_select:{channel_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        hidden_suffix = NSFW_PROMPT_SUFFIX if interaction.channel and interaction.channel.id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX

        if selected == SURPRISE_VALUE:
            model_id = random.choice(MODEL_ORDER)
            ratio = random.choice(get_model_ratios(model_id))

            await interaction.response.send_modal(
                GenerationModal(
                    session=self.session,
                    model_id=model_id,
                    ratio=ratio,
                    hidden_suffix=hidden_suffix,
                    owner_id=interaction.user.id,
                    previous_inputs=None
                )
            )

            try:
                await interaction.followup.send(embed=build_surprise_embed(model_id, ratio), ephemeral=True)
            except Exception:
                pass

            # requested: when dropdown action happens, clean model dropdown in recent 10
            if isinstance(interaction.channel, discord.TextChannel):
                await VeniceCog.delete_recent_model_dropdown_posts(
                    interaction.channel,
                    bot_user_id=(interaction.client.user.id if interaction.client.user else None),
                    limit=RECENT_SCAN_LIMIT
                )
            return

        model_id = selected
        await interaction.response.send_message(
            content=f"{get_model_label(model_id)} selected. Now choose an aspect ratio:",
            view=AspectRatioSelectView(
                session=self.session,
                model_id=model_id,
                hidden_suffix=hidden_suffix,
                owner_id=interaction.user.id,
            ),
            ephemeral=True
        )

        # requested logic: after aspect dropdown post, remove model dropdown in recent 10
        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.delete_recent_model_dropdown_posts(
                interaction.channel,
                bot_user_id=(interaction.client.user.id if interaction.client.user else None),
                limit=RECENT_SCAN_LIMIT
            )


class StarterView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel_id: int):
        super().__init__(timeout=None)
        self.add_item(StarterModelSelect(session, channel_id))


class GenerationModal(discord.ui.Modal):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        model_id: str,
        ratio: str,
        hidden_suffix: str,
        owner_id: int,
        previous_inputs: Optional[dict[str, Any]] = None,
    ):
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
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=min(cfg["prompt_limit"], 4000),
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
            label=f"Steps (fixed: {cfg['default_steps']})" if fixed_steps else f"Steps (1-{cfg['max_steps']})",
            style=discord.TextStyle.short,
            required=False,
            max_length=3,
            placeholder=str(cfg["default_steps"]),
            default=str(self.previous_inputs.get("steps")) if self.previous_inputs.get("steps") else "",
        )
        self.hidden_suffix = discord.ui.TextInput(
            label="Hidden suffix",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1200,
            placeholder=(hidden_suffix[:100] if hidden_suffix else ""),
            default=self.previous_inputs.get("hidden_suffix", ""),
        )

        for item in [self.prompt, self.negative_prompt, self.cfg_value, self.steps_value, self.hidden_suffix]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await send_ephemeral(interaction, "🚫 This modal does not belong to you.")
            return

        cfg = MODEL_CONFIG[self.model_id]
        fixed_steps = cfg["default_steps"] == cfg["max_steps"]

        try:
            cfg_val = float(self.cfg_value.value)
        except Exception:
            cfg_val = cfg["cfg_default"]

        if fixed_steps:
            steps_val = cfg["default_steps"]
        else:
            try:
                steps_val = int(self.steps_value.value)
                steps_val = max(1, min(steps_val, cfg["max_steps"]))
            except Exception:
                steps_val = cfg["default_steps"]

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
            "previous_inputs": {
                "prompt": self.prompt.value,
                "negative_prompt": negative_prompt,
                "cfg_value": self.cfg_value.value,
                "steps": steps_val if steps_val != cfg["default_steps"] else None,
                "hidden_suffix": hidden_suffix
            }
        }

        await interaction.response.send_message(
            content=(
                f"✅ {get_model_label(self.model_id)} • {ASPECT_LABELS.get(self.ratio, self.ratio)}\n"
                f"{build_resolution_hint(self.model_id)}\n"
                "Choose resolution:"
            ),
            view=ResolutionSelectView(self.session, generation_data),
            ephemeral=True
        )


class ResolutionSelectView(OwnerLockedView):
    def __init__(self, session: aiohttp.ClientSession, generation_data: dict[str, Any]):
        super().__init__(owner_id=generation_data["owner_id"], timeout=900)
        self.session = session
        self.generation_data = generation_data

        model_id = generation_data["model_id"]
        native = set(MODEL_CONFIG[model_id]["resolutions"])

        for res in RESOLUTION_TIERS:
            label = res
            if res not in native and res in ("2K", "4K"):
                label = f"{res} ↗"  # indicates upscale path

            style = (
                discord.ButtonStyle.success if res == "1K"
                else discord.ButtonStyle.primary if res == "2K"
                else discord.ButtonStyle.danger  # 4K red
            )

            btn = discord.ui.Button(
                label=label,
                style=style,
                disabled=False,
                custom_id=f"venice_res:{res}:{model_id}"
            )
            btn.callback = self._make_resolution_callback(res)
            self.add_item(btn)

    def _make_resolution_callback(self, resolution: str):
        async def callback(interaction: discord.Interaction):
            if not isinstance(interaction.user, discord.Member):
                await send_ephemeral(interaction, "❌ This action can only be used in a server.")
                return

            role_needed = required_role_for_resolution(resolution)
            if role_needed and not has_role(interaction.user, role_needed):
                await send_resolution_lock_message(interaction, resolution, role_needed)
                return

            await interaction.response.defer(ephemeral=True)

            # remove resolution post after click
            if interaction.message:
                try:
                    await interaction.message.edit(view=None, content="✅ Resolution selected.")
                except Exception:
                    pass

            await self.generate_image(interaction, resolution=resolution)

        return callback

    async def generate_image(self, interaction: discord.Interaction, resolution: str):
        model_id = self.generation_data["model_id"]
        ratio = self.generation_data["ratio"]
        prompt_text = self.generation_data["prompt_text"]
        hidden_suffix = self.generation_data["hidden_suffix"]
        negative_prompt = self.generation_data["negative_prompt"]
        cfg_val = float(self.generation_data["cfg_scale"])
        steps = int(self.generation_data["steps"])
        previous_inputs = self.generation_data["previous_inputs"]
        channel_id = self.generation_data["channel_id"]

        full_prompt = f"{(prompt_text or '').strip()} {(hidden_suffix or '').strip()}".strip()

        gen_res, upscale_factor = generation_plan(model_id, resolution)

        payload = build_generate_payload(
            model_id=model_id,
            ratio=ratio,
            generation_resolution=gen_res,
            prompt=full_prompt,
            negative_prompt=negative_prompt,
            cfg_scale=cfg_val,
            steps=steps,
        )

        progress_msg = await interaction.followup.send(f"{pepper} Generating image...", ephemeral=True)

        gen_task = asyncio.create_task(venice_generate(self.session, payload))
        started = time.monotonic()
        last_percent = -1

        while not gen_task.done():
            elapsed = time.monotonic() - started
            est_total = max(10.0, min(75.0, 8 + steps * 0.9 + cfg_val * 0.6 + len(prompt_text) / 220))
            percent = min(95, int((elapsed / est_total) * 95))
            if percent != last_percent:
                last_percent = percent
                try:
                    await progress_msg.edit(content=f"{pepper} Generating image for **{interaction.user.display_name}**... {percent}%")
                except Exception:
                    pass
            await asyncio.sleep(1.2)

        image_bytes = await gen_task
        if not image_bytes:
            await interaction.followup.send("❌ Generation failed.", ephemeral=True)
            if isinstance(interaction.channel, discord.TextChannel):
                await VeniceCog.ensure_starter_message_static(
                    interaction.channel,
                    self.session,
                    bot_user_id=(interaction.client.user.id if interaction.client.user else None)
                )
            self.stop()
            return

        upscale_note = None
        if upscale_factor in (2, 4):
            try:
                await progress_msg.edit(content=f"{pepper} Upscaling {upscale_factor}x...")
            except Exception:
                pass

            upscaled = await venice_upscale(self.session, image_bytes, upscale_factor)
            if upscaled:
                image_bytes = upscaled
                upscale_note = f"Upscaled {upscale_factor}x"
            else:
                upscale_note = f"Upscale {upscale_factor}x failed (kept base output)"

        try:
            await progress_msg.edit(content=f"{pepper} Finalizing... 100%")
        except Exception:
            pass

        fp = io.BytesIO(image_bytes)
        fp.seek(0)
        dfile = discord.File(fp, filename=make_safe_filename(prompt_text))

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(
            name=f"{interaction.user.display_name} ({datetime.now().strftime('%Y-%m-%d')})",
            icon_url=interaction.user.display_avatar.url
        )

        prompt_preview = (prompt_text or "").replace("\n\n", "\n")
        if len(prompt_preview) > 600:
            prompt_preview = prompt_preview[:600] + " [...]"
        embed.description = f"🔮 Prompt:\n{prompt_preview}"

        default_hidden = NSFW_PROMPT_SUFFIX if channel_id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX
        used_hidden = previous_inputs.get("hidden_suffix")
        if isinstance(used_hidden, str) and used_hidden and used_hidden != default_hidden:
            embed.description += "\n\n🔒 Hidden prompt used"

        if negative_prompt and negative_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.description += f"\n\n🚫 Negative prompt:\n{negative_prompt}"

        if upscale_note:
            embed.description += f"\n\n🛠️ {upscale_note}"

        embed.set_image(url=f"attachment://{dfile.filename}")

        guild_icon = interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        embed.set_footer(
            text=(
                f"{get_model_label(model_id)} | "
                f"Ratio: {ASPECT_LABELS.get(ratio, ratio)} | "
                f"Target: {resolution} | CFG: {cfg_val} | Steps: {steps}"
            ),
            icon_url=guild_icon
        )

        if not interaction.channel:
            await interaction.followup.send("❌ Channel is unavailable.", ephemeral=True)
            self.stop()
            return

        posted = await interaction.channel.send(
            content=interaction.user.mention,
            embed=embed,
            file=dfile,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

        for emo in REACTIONS:
            try:
                await posted.add_reaction(emo)
            except Exception:
                pass

        await interaction.followup.send(
            content=f"🚨 {interaction.user.mention}, re-use and edit your prompt?",
            view=PostGenerationView(
                session=self.session,
                author_id=interaction.user.id,
                source_message=posted,
                channel_id=(interaction.channel.id if interaction.channel else 0),
                previous_inputs=previous_inputs,
                hidden_suffix=hidden_suffix
            ),
            ephemeral=True
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.ensure_starter_message_static(
                interaction.channel,
                self.session,
                bot_user_id=(interaction.client.user.id if interaction.client.user else None)
            )

        self.stop()


# =================================================
# REUSE FLOW
# =================================================
class ReuseModelSelect(discord.ui.Select):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        channel_id: int,
        owner_id: int,
        previous_inputs: dict[str, Any],
        hidden_suffix: str
    ):
        self.session = session
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.previous_inputs = previous_inputs
        self.hidden_suffix = hidden_suffix

        super().__init__(
            placeholder="♻️ Re-use with model...",
            min_values=1,
            max_values=1,
            options=build_model_options(channel_id, include_surprise=True)
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]

        if selected == SURPRISE_VALUE:
            model_id = random.choice(MODEL_ORDER)
            ratio = random.choice(get_model_ratios(model_id))

            await interaction.response.send_modal(
                GenerationModal(
                    session=self.session,
                    model_id=model_id,
                    ratio=ratio,
                    hidden_suffix=self.hidden_suffix,
                    owner_id=self.owner_id,
                    previous_inputs=self.previous_inputs
                )
            )

            try:
                await interaction.followup.send(embed=build_surprise_embed(model_id, ratio), ephemeral=True)
            except Exception:
                pass

            if isinstance(interaction.channel, discord.TextChannel):
                await VeniceCog.delete_recent_model_dropdown_posts(
                    interaction.channel,
                    bot_user_id=(interaction.client.user.id if interaction.client.user else None),
                    limit=RECENT_SCAN_LIMIT
                )
            return

        model_id = selected
        await interaction.response.send_message(
            content=f"{get_model_label(model_id)} selected. Now choose an aspect ratio:",
            view=AspectRatioSelectView(
                session=self.session,
                model_id=model_id,
                hidden_suffix=self.hidden_suffix,
                owner_id=self.owner_id,
                previous_inputs=self.previous_inputs
            ),
            ephemeral=True
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.delete_recent_model_dropdown_posts(
                interaction.channel,
                bot_user_id=(interaction.client.user.id if interaction.client.user else None),
                limit=RECENT_SCAN_LIMIT
            )


class ReuseModelSelectView(OwnerLockedView):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        channel_id: int,
        owner_id: int,
        previous_inputs: dict[str, Any],
        hidden_suffix: str
    ):
        super().__init__(owner_id=owner_id, timeout=300)
        self.add_item(
            ReuseModelSelect(
                session=session,
                channel_id=channel_id,
                owner_id=owner_id,
                previous_inputs=previous_inputs,
                hidden_suffix=hidden_suffix
            )
        )


class PostGenerationView(OwnerLockedView):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        author_id: int,
        source_message: discord.Message,
        channel_id: int,
        previous_inputs: dict[str, Any],
        hidden_suffix: str
    ):
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
        await interaction.response.send_message(
            "♻️ Choose a model to re-use your prompt:",
            view=ReuseModelSelectView(
                session=self.session,
                channel_id=self.channel_id,
                owner_id=self.owner_id,
                previous_inputs=self.previous_inputs,
                hidden_suffix=self.hidden_suffix
            ),
            ephemeral=True
        )

    async def delete_callback(self, interaction: discord.Interaction):
        try:
            await self.source_message.delete()
        except Exception:
            pass
        await interaction.response.send_message("✅ Post deleted.", ephemeral=True)

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
        timeout = aiohttp.ClientTimeout(total=240)
        connector = aiohttp.TCPConnector(limit=60, ttl_dns_cache=300)
        self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)

    async def cog_load(self):
        await self._ensure_session()

        # persistent starter dropdowns
        for channel_id in ALLOWED_CHANNEL_IDS:
            self.bot.add_view(StarterView(self.session, channel_id))

    def cog_unload(self):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    @staticmethod
    async def _delete_recent_model_dropdown_posts_unlocked(
        channel: discord.TextChannel,
        bot_user_id: Optional[int],
        limit: int
    ) -> int:
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
    async def delete_recent_model_dropdown_posts(
        channel: discord.TextChannel,
        bot_user_id: Optional[int],
        limit: int = RECENT_SCAN_LIMIT
    ) -> int:
        lock = get_channel_lock(channel.id)
        async with lock:
            return await VeniceCog._delete_recent_model_dropdown_posts_unlocked(channel, bot_user_id, limit)

    async def ensure_starter_message(self, channel: discord.TextChannel):
        lock = get_channel_lock(channel.id)
        async with lock:
            try:
                # requested: last 10 cleanup before repost
                await VeniceCog._delete_recent_model_dropdown_posts_unlocked(
                    channel,
                    bot_user_id=(self.bot.user.id if self.bot.user else None),
                    limit=RECENT_SCAN_LIMIT
                )
                await channel.send(BUTTON_MESSAGE_TEXT, view=StarterView(self.session, channel.id))
            except discord.Forbidden:
                logger.warning("Missing permissions in channel %s", channel.id)
            except Exception as e:
                logger.warning("ensure_starter_message failed in channel %s: %s", channel.id, e)

    @staticmethod
    async def ensure_starter_message_static(
        channel: discord.TextChannel,
        session: aiohttp.ClientSession,
        bot_user_id: Optional[int]
    ):
        lock = get_channel_lock(channel.id)
        async with lock:
            try:
                await VeniceCog._delete_recent_model_dropdown_posts_unlocked(
                    channel,
                    bot_user_id=bot_user_id,
                    limit=RECENT_SCAN_LIMIT
                )
                await channel.send(BUTTON_MESSAGE_TEXT, view=StarterView(session, channel.id))
            except Exception:
                pass

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