import asyncio
import io
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Optional, Any

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

# -------------------------------------------------
# ENV / API
# -------------------------------------------------
load_dotenv()

VENICE_API_KEY = os.getenv("VENICE_API_KEY")
if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env!")

VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"
BUTTON_MESSAGE_TEXT = "💡 Choose a model for a new image!"

logger = logging.getLogger("venice_picture_bot")

# -------------------------------------------------
# CHANNELS / ROLES
# -------------------------------------------------
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

# -------------------------------------------------
# PROMPT CONFIG
# -------------------------------------------------
DEFAULT_NEGATIVE_PROMPT = "disfigured, missing fingers, extra limbs, watermark, underage"
NSFW_PROMPT_SUFFIX = " "
SFW_PROMPT_SUFFIX = " "

pepper = "<a:01pepper_icon:1377636862847619213>"

# -------------------------------------------------
# ASPECT RATIO LABELS
# -------------------------------------------------
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

def ratio_to_dimensions(ratio: str, base: int = 1024) -> tuple[int, int]:
    if ratio == "16:9":
        return 1280, 720
    if ratio == "9:16":
        return 720, 1280
    if ratio == "21:9":
        return 1440, 640
    if ratio == "3:2":
        return 1200, 800
    if ratio == "2:3":
        return 800, 1200
    if ratio == "3:4":
        return 960, 1280
    if ratio == "4:5":
        return 1024, 1280
    return base, base

# -------------------------------------------------
# MODEL CONFIG (API-aligned)
# Removed intentionally:
# - venice-sd35
# - lustify-sdxl
# - lustify-v7
# - bria-bg-remover (not classic text-to-image flow)
# -------------------------------------------------
COMMON_RATIOS = ["1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"]
GROK_RATIOS = ["1:1", "16:9", "9:16", "3:4", "3:2", "2:3"]
GPT15_RATIOS = ["1:1", "3:2", "2:3"]
FALLBACK_RATIOS = ["1:1", "16:9", "9:16"]

MODEL_CONFIG = {
    "flux-2-pro": {
        "label": "🛰️ Flux 2 Pro",
        "prompt_limit": 3000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "flux-2-max": {
        "label": "🌌 Flux 2 Max",
        "prompt_limit": 3000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"],
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "gpt-image-2": {
        "label": "🧠 GPT Image 2",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": ["1K", "2K", "4K"],
    },
    "gpt-image-1-5": {
        "label": "🪄 GPT Image 1.5",
        "prompt_limit": 5000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": GPT15_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "hunyuan-image-v3": {
        "label": "🐉 Hunyuan Image 3.0",
        "prompt_limit": 3000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "imagineart-1.5-pro": {
        "label": "🎨 ImagineArt 1.5 Pro",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "3:2", "16:9", "9:16", "2:3", "3:4", "4:5"],
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "nano-banana-2": {
        "label": "🐵 Nano Banana 2",
        "prompt_limit": 32768,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": ["1K", "2K", "4K"],
    },
    "nano-banana-pro": {
        "label": "🍌 Nano Banana Pro",
        "prompt_limit": 32768,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": ["1K", "2K", "4K"],
    },
    "recraft-v4": {
        "label": "🧱 Recraft V4",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "recraft-v4-pro": {
        "label": "🏗️ Recraft V4 Pro",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "seedream-v4": {
        "label": "🌊 Seedream V4.5",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "seedream-v5-lite": {
        "label": "💧 Seedream V5 Lite",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "qwen-image-2": {
        "label": "🔷 Qwen Image 2",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "qwen-image-2-pro": {
        "label": "🧩 Qwen Image 2 Pro",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "wan-2-7-text-to-image": {
        "label": "🐋 Wan 2.7",
        "prompt_limit": 3000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "wan-2-7-pro-text-to-image": {
        "label": "🦈 Wan 2.7 Pro",
        "prompt_limit": 3000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": COMMON_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "grok-imagine-image": {
        "label": "🧠 Grok Imagine",
        "prompt_limit": 7500,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": GROK_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": ["1K", "2K"],
    },
    "grok-imagine-image-pro": {
        "label": "🚀 Grok Imagine Pro",
        "prompt_limit": 7500,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": GROK_RATIOS,
        "use_aspect_ratio": True,
        "resolutions": ["1K", "2K"],
    },
    "lustify-v8": {
        "label": "🔥 Lustify V8",
        "prompt_limit": 1500,
        "cfg_scale": 5.0,
        "default_steps": 30,
        "max_steps": 50,
        "ratios": FALLBACK_RATIOS,
        "use_aspect_ratio": False,
        "resolutions": [],
    },
    "qwen-image": {
        "label": "🐼 Qwen Image",
        "prompt_limit": 1500,
        "cfg_scale": 6.0,
        "default_steps": 8,
        "max_steps": 8,
        "ratios": FALLBACK_RATIOS,
        "use_aspect_ratio": False,
        "resolutions": [],
    },
    "wai-Illustrious": {
        "label": "🎌 Anime (WAI)",
        "prompt_limit": 1500,
        "cfg_scale": 7.0,
        "default_steps": 25,
        "max_steps": 30,
        "ratios": FALLBACK_RATIOS,
        "use_aspect_ratio": False,
        "resolutions": [],
    },
    "z-image-turbo": {
        "label": "⚡ Z-Image Turbo",
        "prompt_limit": 7500,
        "cfg_scale": 6.0,
        "default_steps": 8,
        "max_steps": 8,
        "ratios": FALLBACK_RATIOS,
        "use_aspect_ratio": False,
        "resolutions": [],
    },
    "chroma": {
        "label": "🌈 Chroma",
        "prompt_limit": 7500,
        "cfg_scale": 6.0,
        "default_steps": 10,
        "max_steps": 10,
        "ratios": FALLBACK_RATIOS,
        "use_aspect_ratio": False,
        "resolutions": [],
    },
    "hidream": {
        "label": "🌙 HiDream",
        "prompt_limit": 1500,
        "cfg_scale": 6.5,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": FALLBACK_RATIOS,
        "use_aspect_ratio": False,
        "resolutions": [],
    },
}

MODEL_ORDER = [
    "flux-2-pro",
    "flux-2-max",
    "gpt-image-2",
    "gpt-image-1-5",
    "hunyuan-image-v3",
    "imagineart-1.5-pro",
    "nano-banana-2",
    "nano-banana-pro",
    "recraft-v4",
    "recraft-v4-pro",
    "seedream-v4",
    "seedream-v5-lite",
    "qwen-image-2",
    "qwen-image-2-pro",
    "wan-2-7-text-to-image",
    "wan-2-7-pro-text-to-image",
    "grok-imagine-image",
    "grok-imagine-image-pro",
    "lustify-v8",
    "qwen-image",
    "wai-Illustrious",
    "z-image-turbo",
    "chroma",
    "hidream",
]

VARIANT_MAP = {
    **{ch: [{"model": m} for m in MODEL_ORDER] for ch in NSFW_CHANNELS},
    SFW_CHANNEL: [{"model": m} for m in MODEL_ORDER],
}

# -------------------------------------------------
# HELPERS
# -------------------------------------------------
def get_model_label(model_id: str) -> str:
    return MODEL_CONFIG[model_id]["label"]

def make_safe_filename(prompt: str) -> str:
    base = "_".join((prompt or "").split()[:5]) or "image"
    base = re.sub(r"[^a-zA-Z0-9_]", "_", base)
    return f"{base}_{int(time.time_ns())}_{uuid.uuid4().hex[:8]}.png"

def required_role_for_resolution(resolution: Optional[str]) -> Optional[int]:
    return RESOLUTION_ROLE_REQUIREMENTS.get(resolution) if resolution else None

def has_role(member: discord.Member, role_id: int) -> bool:
    return any(r.id == role_id for r in member.roles)

async def send_ephemeral(interaction: discord.Interaction, content: str):
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)

async def send_resolution_lock_message(interaction: discord.Interaction, resolution: str, role_id: int):
    level_name = ROLE_LEVEL_NAMES.get(role_id, "Required level")
    content = (
        f"🔒 **{resolution}** is locked.\n"
        f"You need <@&{role_id}> (**{level_name}**) to use this quality tier.\n"
        f"💡 Earn XP in the server to unlock this role."
    )
    await send_ephemeral(interaction, content)

def build_resolution_hint(model_id: str) -> str:
    resolutions = MODEL_CONFIG[model_id]["resolutions"]
    if not resolutions:
        return "This model uses its default API resolution."
    parts = [f"Available: {', '.join(resolutions)}"]
    if "2K" in resolutions:
        parts.append(f"2K requires <@&{LEVEL4_ROLE_ID}>")
    if "4K" in resolutions:
        parts.append(f"4K requires <@&{LEVEL11_ROLE_ID}>")
    parts.append("Earn XP to unlock higher tiers.")
    return " • ".join(parts)

async def venice_generate(session: aiohttp.ClientSession, payload: dict, retries: int = 2) -> Optional[bytes]:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}"}

    for attempt in range(retries + 1):
        try:
            async with session.post(VENICE_IMAGE_URL, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    return await resp.read()

                body = await resp.text()
                logger.warning("Venice API error %s: %s", resp.status, body)

                if resp.status in (429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                return None

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Venice request failed (attempt %s): %s", attempt + 1, e)
            if attempt < retries:
                await asyncio.sleep(1.2 * (attempt + 1))
                continue
            return None
        except Exception as e:
            logger.exception("Unexpected error in venice_generate: %s", e)
            return None

    return None

# -------------------------------------------------
# OWNER-LOCKED BASE VIEW
# -------------------------------------------------
class OwnerLockedView(discord.ui.View):
    def __init__(self, owner_id: int, timeout: Optional[float] = 900):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await send_ephemeral(interaction, "🚫 This menu belongs to another user.")
            return False
        return True

# -------------------------------------------------
# FLOW 1: MODEL SELECT -> ASPECT SELECT
# -------------------------------------------------
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

        options = []
        for ratio in MODEL_CONFIG[model_id]["ratios"]:
            options.append(
                discord.SelectOption(
                    label=ASPECT_LABELS.get(ratio, ratio),
                    value=ratio
                )
            )

        super().__init__(
            placeholder="📐 Choose aspect ratio...",
            min_values=1,
            max_values=1,
            options=options[:25]
        )

    async def callback(self, interaction: discord.Interaction):
        ratio = self.values[0]
        await interaction.response.send_modal(
            VeniceModal(
                session=self.session,
                model_id=self.model_id,
                ratio=ratio,
                hidden_suffix=self.hidden_suffix,
                owner_id=self.owner_id,
                previous_inputs=self.previous_inputs,
            )
        )

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
                previous_inputs=previous_inputs,
            )
        )

class ModelSelect(discord.ui.Select):
    def __init__(self, session: aiohttp.ClientSession, channel_id: int):
        self.session = session
        self.channel_id = channel_id

        options = []
        for variant in VARIANT_MAP.get(channel_id, []):
            model_id = variant["model"]
            options.append(discord.SelectOption(label=get_model_label(model_id), value=model_id))

        super().__init__(
            placeholder="🎨 Choose your model...",
            min_values=1,
            max_values=1,
            options=options[:25],
            custom_id=f"venice_model_select_{channel_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        model_id = self.values[0]
        hidden_suffix = NSFW_PROMPT_SUFFIX if interaction.channel and interaction.channel.id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX

        await interaction.response.send_message(
            content=f"{get_model_label(model_id)} selected. Now choose an aspect ratio:",
            view=AspectRatioSelectView(
                session=self.session,
                model_id=model_id,
                hidden_suffix=hidden_suffix,
                owner_id=interaction.user.id,
                previous_inputs=None
            ),
            ephemeral=True
        )

class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.add_item(ModelSelect(session, channel.id))

# -------------------------------------------------
# FLOW 2: MODAL (prompt/cfg/steps)
# -------------------------------------------------
class VeniceModal(discord.ui.Modal):
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

        model_cfg = MODEL_CONFIG[model_id]
        ratio_label = ASPECT_LABELS.get(ratio, ratio)
        super().__init__(title=f"{get_model_label(model_id)} • {ratio_label}")

        prompt_limit = min(model_cfg["prompt_limit"], 4000)
        fixed_steps = model_cfg["default_steps"] == model_cfg["max_steps"]

        self.prompt = discord.ui.TextInput(
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=prompt_limit,
            default=self.previous_inputs.get("prompt", ""),
        )

        neg_default = self.previous_inputs.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT
        self.negative_prompt = discord.ui.TextInput(
            label="Negative prompt (optional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=800,
            default=neg_default,
        )

        self.cfg_value = discord.ui.TextInput(
            label="CFG scale",
            style=discord.TextStyle.short,
            required=False,
            max_length=8,
            placeholder=str(model_cfg["cfg_scale"]),
            default=self.previous_inputs.get("cfg_value", ""),
        )

        steps_label = f"Steps (fixed: {model_cfg['default_steps']})" if fixed_steps else f"Steps (1-{model_cfg['max_steps']})"
        self.steps_value = discord.ui.TextInput(
            label=steps_label,
            style=discord.TextStyle.short,
            required=False,
            max_length=3,
            placeholder=str(model_cfg["default_steps"]),
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

        model_cfg = MODEL_CONFIG[self.model_id]
        fixed_steps = model_cfg["default_steps"] == model_cfg["max_steps"]

        try:
            cfg_val = float(self.cfg_value.value)
        except Exception:
            cfg_val = model_cfg["cfg_scale"]

        if fixed_steps:
            steps_val = model_cfg["default_steps"]
        else:
            try:
                steps_val = int(self.steps_value.value)
                steps_val = max(1, min(steps_val, model_cfg["max_steps"]))
            except Exception:
                steps_val = model_cfg["default_steps"]

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
                "steps": steps_val if steps_val != model_cfg["default_steps"] else None,
                "hidden_suffix": hidden_suffix,
            }
        }

        hint = build_resolution_hint(self.model_id)
        ratio_label = ASPECT_LABELS.get(self.ratio, self.ratio)

        await interaction.response.send_message(
            content=f"✅ {get_model_label(self.model_id)} • {ratio_label}\n{hint}\nChoose resolution:",
            view=ResolutionSelectView(self.session, generation_data),
            ephemeral=True
        )

# -------------------------------------------------
# FLOW 3: RESOLUTION BUTTONS -> GENERATE
# -------------------------------------------------
class ResolutionSelectView(OwnerLockedView):
    def __init__(self, session: aiohttp.ClientSession, generation_data: dict[str, Any]):
        super().__init__(owner_id=generation_data["owner_id"], timeout=900)
        self.session = session
        self.generation_data = generation_data

        model_id = generation_data["model_id"]
        resolutions = MODEL_CONFIG[model_id]["resolutions"]

        if resolutions:
            for res in resolutions:
                style = discord.ButtonStyle.success if res == "1K" else (
                    discord.ButtonStyle.primary if res == "2K" else discord.ButtonStyle.secondary
                )
                btn = discord.ui.Button(label=res, style=style)
                btn.callback = self._make_resolution_callback(res)
                self.add_item(btn)
        else:
            btn = discord.ui.Button(label="Generate", style=discord.ButtonStyle.success)
            btn.callback = self._make_resolution_callback(None)
            self.add_item(btn)

    def _make_resolution_callback(self, resolution: Optional[str]):
        async def callback(interaction: discord.Interaction):
            if not isinstance(interaction.user, discord.Member):
                await send_ephemeral(interaction, "❌ This action can only be used in a server.")
                return

            role_needed = required_role_for_resolution(resolution)
            if role_needed and not has_role(interaction.user, role_needed):
                await send_resolution_lock_message(interaction, resolution, role_needed)
                return

            await self.generate_image(interaction, resolution=resolution)
        return callback

    async def generate_image(self, interaction: discord.Interaction, resolution: Optional[str]):
        await interaction.response.defer(ephemeral=True)

        model_id = self.generation_data["model_id"]
        model_cfg = MODEL_CONFIG[model_id]
        ratio = self.generation_data["ratio"]
        prompt_text = self.generation_data["prompt_text"]
        hidden_suffix = self.generation_data["hidden_suffix"]
        negative_prompt = self.generation_data["negative_prompt"]
        cfg_val = float(self.generation_data["cfg_scale"])
        steps = int(self.generation_data["steps"])
        previous_inputs = self.generation_data["previous_inputs"]
        channel_id = self.generation_data["channel_id"]

        full_prompt = f"{(prompt_text or '').strip()} {(hidden_suffix or '').strip()}".strip()

        payload = {
            "model": model_id,
            "prompt": full_prompt,
            "steps": steps,
            "cfg_scale": cfg_val,
            "negative_prompt": negative_prompt,
            "safe_mode": False,
            "hide_watermark": True,
            "return_binary": True,
        }

        if model_cfg["use_aspect_ratio"]:
            payload["aspect_ratio"] = ratio
        else:
            w, h = ratio_to_dimensions(ratio)
            payload["width"] = w
            payload["height"] = h

        if resolution and resolution in model_cfg["resolutions"]:
            payload["resolution"] = resolution

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
                await VeniceCog.ensure_button_message_static(interaction.channel, self.session)
            self.stop()
            return

        try:
            await progress_msg.edit(content=f"{pepper} Finalizing... 100%")
        except Exception:
            pass

        file_obj = io.BytesIO(image_bytes)
        file_obj.seek(0)
        dfile = discord.File(file_obj, filename=make_safe_filename(prompt_text))

        today = datetime.now().strftime("%Y-%m-%d")
        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=f"{interaction.user.display_name} ({today})", icon_url=interaction.user.display_avatar.url)

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

        embed.set_image(url=f"attachment://{dfile.filename}")

        guild_icon = interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        ratio_text = ASPECT_LABELS.get(ratio, ratio)
        res_text = f" | Res: {resolution}" if resolution else ""
        footer = f"{get_model_label(model_id)} | Ratio: {ratio_text}{res_text} | CFG: {cfg_val} | Steps: {steps}"
        embed.set_footer(text=footer, icon_url=guild_icon)

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

        for emo in ["1️⃣", "2️⃣", "3️⃣", "<:011:1346549711817146400>", "<:011pump:1346549688836296787>"]:
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
                hidden_suffix=hidden_suffix,
            ),
            ephemeral=True,
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.ensure_button_message_static(interaction.channel, self.session)

        self.stop()

# -------------------------------------------------
# REUSE FLOW
# -------------------------------------------------
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

        options = []
        for variant in VARIANT_MAP.get(channel_id, []):
            model_id = variant["model"]
            options.append(discord.SelectOption(label=get_model_label(model_id), value=model_id))

        super().__init__(
            placeholder="♻️ Re-use with model...",
            min_values=1,
            max_values=1,
            options=options[:25]
        )

    async def callback(self, interaction: discord.Interaction):
        model_id = self.values[0]
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

# -------------------------------------------------
# COG
# -------------------------------------------------
class VeniceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self._ready_bootstrap_done = False
        self._ready_lock = asyncio.Lock()

    async def cog_load(self):
        timeout = aiohttp.ClientTimeout(total=180)
        connector = aiohttp.TCPConnector(limit=60, ttl_dns_cache=300)
        self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)

    def cog_unload(self):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    def _is_starter_message(self, msg: discord.Message) -> bool:
        if msg.content != BUTTON_MESSAGE_TEXT:
            return False
        if not msg.components or msg.embeds or msg.attachments:
            return False
        if not self.bot.user or msg.author.id != self.bot.user.id:
            return False
        return True

    async def ensure_button_message(self, channel: discord.TextChannel):
        try:
            async for msg in channel.history(limit=40):
                if self._is_starter_message(msg):
                    try:
                        await msg.delete()
                    except Exception:
                        pass

            await channel.send(BUTTON_MESSAGE_TEXT, view=VeniceView(self.session, channel))
        except discord.Forbidden:
            logger.warning("Missing permissions in channel %s", channel.id)
        except Exception as e:
            logger.warning("ensure_button_message failed in channel %s: %s", channel.id, e)

    @staticmethod
    async def ensure_button_message_static(channel: discord.TextChannel, session: aiohttp.ClientSession):
        try:
            async for msg in channel.history(limit=40):
                if (
                    msg.content == BUTTON_MESSAGE_TEXT
                    and msg.components
                    and not msg.embeds
                    and not msg.attachments
                ):
                    try:
                        await msg.delete()
                    except Exception:
                        pass

            await channel.send(BUTTON_MESSAGE_TEXT, view=VeniceView(session, channel))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_ready(self):
        async with self._ready_lock:
            if self._ready_bootstrap_done:
                return
            self._ready_bootstrap_done = True

            if not self.session or self.session.closed:
                timeout = aiohttp.ClientTimeout(total=180)
                connector = aiohttp.TCPConnector(limit=60, ttl_dns_cache=300)
                self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)

            for guild in self.bot.guilds:
                for channel in guild.text_channels:
                    if channel.id in VARIANT_MAP:
                        await self.ensure_button_message(channel)

async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))