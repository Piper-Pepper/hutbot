import asyncio
import io
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Optional

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
# ASPECT RATIOS
# -------------------------------------------------
ASPECT_LABELS = {
    "auto": "⚙️Auto",
    "1:1": "🟦1:1",
    "16:9": "📺16:9",
    "9:16": "📱9:16",
    "21:9": "🎬21:9",
    "3:2": "🖼️3:2",
    "2:3": "📷2:3",
    "3:4": "🖼️3:4",
    "4:5": "🖼️4:5",
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
# MODEL CONFIG (aligned with your provided specs)
# SD3.5 and old Lustify removed on purpose
# -------------------------------------------------
MODEL_CONFIG = {
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
    "qwen-image-2-pro": {
        "label": "🧩 Qwen Image 2 Pro",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"],
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "grok-imagine-image": {
        "label": "🧠 Grok Imagine",
        "prompt_limit": 7500,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "16:9", "9:16", "3:4", "3:2", "2:3"],
        "use_aspect_ratio": True,
        "resolutions": ["1K", "2K"],
    },
    "grok-imagine-image-pro": {
        "label": "🚀 Grok Imagine Pro",
        "prompt_limit": 7500,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "16:9", "9:16", "3:4", "3:2", "2:3"],
        "use_aspect_ratio": True,
        "resolutions": ["1K", "2K"],
    },
    "nano-banana-2": {
        "label": "🐵 Nano Banana 2",
        "prompt_limit": 32768,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"],
        "use_aspect_ratio": True,
        "resolutions": ["1K", "2K", "4K"],
    },
    "nano-banana-pro": {
        "label": "🍌 Nano Banana Pro",
        "prompt_limit": 32768,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"],
        "use_aspect_ratio": True,
        "resolutions": ["1K", "2K", "4K"],
    },
    "recraft-v4-pro": {
        "label": "🧱 Recraft V4 Pro",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"],
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "imagineart-1.5-pro": {
        "label": "🖌️ ImagineArt 1.5 Pro",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "3:2", "16:9", "9:16", "2:3", "3:4", "4:5"],
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "seedream-v4": {
        "label": "🌊 Seedream V4.5",
        "prompt_limit": 10000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"],
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    "hunyuan-image-v3": {
        "label": "🐉 Hunyuan Image 3.0",
        "prompt_limit": 3000,
        "cfg_scale": 5.0,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"],
        "use_aspect_ratio": True,
        "resolutions": [],
    },
    # Models without explicit aspect ratio list in your JSON -> conservative fallback set
    "hidream": {
        "label": "🌙 HiDream",
        "prompt_limit": 1500,
        "cfg_scale": 6.5,
        "default_steps": 20,
        "max_steps": 50,
        "ratios": ["1:1", "16:9", "9:16"],
        "use_aspect_ratio": False,
        "resolutions": [],
    },
    "wai-Illustrious": {
        "label": "🎌 Anime WAI",
        "prompt_limit": 1500,
        "cfg_scale": 7.0,
        "default_steps": 25,
        "max_steps": 30,
        "ratios": ["1:1", "16:9", "9:16"],
        "use_aspect_ratio": False,
        "resolutions": [],
    },
    "z-image-turbo": {
        "label": "🌀 Z-Image Turbo",
        "prompt_limit": 7500,
        "cfg_scale": 6.0,
        "default_steps": 8,
        "max_steps": 8,
        "ratios": ["1:1", "16:9", "9:16"],
        "use_aspect_ratio": False,
        "resolutions": [],
    },
    "lustify-v8": {
        "label": "🔥 Lustify V8",
        "prompt_limit": 1500,
        "cfg_scale": 5.0,
        "default_steps": 30,
        "max_steps": 50,
        "ratios": ["1:1", "16:9", "9:16"],
        "use_aspect_ratio": False,
        "resolutions": [],
    },
}

MODEL_ORDER = [
    "flux-2-max",
    "qwen-image-2-pro",
    "grok-imagine-image",
    "grok-imagine-image-pro",
    "nano-banana-2",
    "nano-banana-pro",
    "recraft-v4-pro",
    "imagineart-1.5-pro",
    "seedream-v4",
    "hunyuan-image-v3",
    "hidream",
    "wai-Illustrious",
    "z-image-turbo",
    "lustify-v8",
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
        f"💡 You can earn XP on the server to unlock this role."
    )
    await send_ephemeral(interaction, content)

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
# MODAL
# -------------------------------------------------
class VeniceModal(discord.ui.Modal):
    def __init__(self, session: aiohttp.ClientSession, variant: dict, hidden_suffix: str, previous_inputs=None):
        model_id = variant["model"]
        model_cfg = MODEL_CONFIG[model_id]

        super().__init__(title=f"Generate with {get_model_label(model_id)}")
        self.session = session
        self.variant = variant
        self.hidden_suffix_value = hidden_suffix
        self.previous_inputs = previous_inputs or {}

        prompt_limit = min(model_cfg["prompt_limit"], 4000)

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

        prev_steps = self.previous_inputs.get("steps")
        self.steps_value = discord.ui.TextInput(
            label=f"Steps (1-{model_cfg['max_steps']})",
            style=discord.TextStyle.short,
            required=False,
            max_length=3,
            placeholder=str(model_cfg["default_steps"]),
            default=str(prev_steps) if prev_steps else "",
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
        model_id = self.variant["model"]
        model_cfg = MODEL_CONFIG[model_id]

        try:
            cfg_val = float(self.cfg_value.value)
        except Exception:
            cfg_val = model_cfg["cfg_scale"]

        try:
            steps_val = int(self.steps_value.value)
            steps_val = max(1, min(steps_val, model_cfg["max_steps"]))
        except Exception:
            steps_val = model_cfg["default_steps"]

        negative_prompt = (self.negative_prompt.value or "").strip() or DEFAULT_NEGATIVE_PROMPT
        hidden_suffix = (self.hidden_suffix.value or "").strip() or self.hidden_suffix_value

        variant = {
            **self.variant,
            "cfg_scale": cfg_val,
            "steps": steps_val,
            "negative_prompt": negative_prompt,
        }

        prev = {
            "prompt": self.prompt.value,
            "negative_prompt": negative_prompt,
            "cfg_value": self.cfg_value.value,
            "steps": steps_val if steps_val != model_cfg["default_steps"] else None,
            "hidden_suffix": hidden_suffix,
        }

        channel_id = interaction.channel.id if interaction.channel else None

        await interaction.response.send_message(
            f"{get_model_label(model_id)} is ready. Choose an aspect ratio:",
            view=AspectRatioView(
                self.session,
                variant,
                prompt_text=self.prompt.value,
                hidden_suffix=hidden_suffix,
                author=interaction.user,
                channel_id=channel_id,
                previous_inputs=prev,
            ),
            ephemeral=True,
        )

# -------------------------------------------------
# ASPECT RATIO VIEW
# -------------------------------------------------
class AspectRatioView(discord.ui.View):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        variant: dict,
        prompt_text: str,
        hidden_suffix: str,
        author: discord.abc.User,
        channel_id: Optional[int] = None,
        previous_inputs=None,
    ):
        super().__init__(timeout=900)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.channel_id = channel_id
        self.previous_inputs = previous_inputs or {}

        model_id = variant["model"]
        cfg = MODEL_CONFIG[model_id]

        ratios = cfg["ratios"]
        resolutions = cfg["resolutions"]  # [] or list like ["1K","2K","4K"]

        for ratio in ratios:
            # Keep max 25 components
            if len(self.children) >= 25:
                break

            if ratio not in ASPECT_LABELS:
                continue

            # Free (lowest) button
            if resolutions:
                self._add_button(ratio=ratio, resolution="1K", style=discord.ButtonStyle.success)
                if "2K" in resolutions and len(self.children) < 25:
                    self._add_button(ratio=ratio, resolution="2K", style=discord.ButtonStyle.primary)
                if "4K" in resolutions and len(self.children) < 25:
                    self._add_button(ratio=ratio, resolution="4K", style=discord.ButtonStyle.secondary)
            else:
                self._add_button(ratio=ratio, resolution=None, style=discord.ButtonStyle.success)

    def _add_button(self, ratio: str, resolution: Optional[str], style: discord.ButtonStyle):
        base_label = ASPECT_LABELS[ratio]
        if resolution and resolution != "1K":
            label = f"{base_label} {resolution}"
        else:
            label = base_label

        row = min(len(self.children) // 5, 4)
        btn = discord.ui.Button(label=label, style=style, row=row)

        async def callback(interaction: discord.Interaction):
            if not isinstance(interaction.user, discord.Member):
                await send_ephemeral(interaction, "❌ This action can only be used in a server.")
                return

            role_needed = required_role_for_resolution(resolution)
            if role_needed and not has_role(interaction.user, role_needed):
                await send_resolution_lock_message(interaction, resolution, role_needed)
                return

            await self.generate_image(interaction, ratio=ratio, resolution=resolution)

        btn.callback = callback
        self.add_item(btn)

    async def generate_image(self, interaction: discord.Interaction, ratio: str, resolution: Optional[str]):
        await interaction.response.defer(ephemeral=True)

        model_id = self.variant["model"]
        model_cfg = MODEL_CONFIG[model_id]

        cfg_val = float(self.variant["cfg_scale"])
        steps = int(self.variant.get("steps", model_cfg["default_steps"]))
        negative_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)

        full_prompt = f"{(self.prompt_text or '').strip()} {(self.hidden_suffix or '').strip()}".strip()

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

        # only apply resolution if model supports it
        if resolution and resolution in model_cfg["resolutions"]:
            payload["resolution"] = resolution

        progress_msg = await interaction.followup.send(f"{pepper} Generating image...", ephemeral=True)

        gen_task = asyncio.create_task(venice_generate(self.session, payload))
        started = time.monotonic()

        last_percent = -1
        while not gen_task.done():
            elapsed = time.monotonic() - started
            est_total = max(10.0, min(75.0, 8 + steps * 0.9 + cfg_val * 0.6 + len(self.prompt_text) / 220))
            percent = min(95, int((elapsed / est_total) * 95))

            if percent != last_percent:
                last_percent = percent
                try:
                    await progress_msg.edit(
                        content=f"{pepper} Generating image for **{self.author.display_name}**... {percent}%"
                    )
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
        dfile = discord.File(file_obj, filename=make_safe_filename(self.prompt_text))

        today = datetime.now().strftime("%Y-%m-%d")

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=f"{self.author.display_name} ({today})", icon_url=self.author.display_avatar.url)

        prompt_preview = (self.prompt_text or "").replace("\n\n", "\n")
        if len(prompt_preview) > 600:
            prompt_preview = prompt_preview[:600] + " [...]"

        embed.description = f"🔮 Prompt:\n{prompt_preview}"

        default_hidden = NSFW_PROMPT_SUFFIX if self.channel_id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX
        used_hidden = self.previous_inputs.get("hidden_suffix")
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
            content=self.author.mention,
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
                self.session,
                self.variant,
                prompt_text=self.prompt_text,
                hidden_suffix=self.hidden_suffix,
                author=self.author,
                message=posted,
                previous_inputs=self.previous_inputs,
            ),
            ephemeral=True,
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.ensure_button_message_static(interaction.channel, self.session)

        self.stop()

# -------------------------------------------------
# POST GENERATION VIEW
# -------------------------------------------------
class PostGenerationView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, hidden_suffix, author, message, previous_inputs=None):
        super().__init__(timeout=1200)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.message = message
        self.previous_inputs = previous_inputs or {}

        reuse_btn = discord.ui.Button(label="♻️ Re-use Prompt", style=discord.ButtonStyle.success)
        reuse_btn.callback = self.reuse_callback
        self.add_item(reuse_btn)

        delete_btn = discord.ui.Button(label="🗑️ Delete", style=discord.ButtonStyle.danger)
        delete_btn.callback = self.delete_callback
        self.add_item(delete_btn)

        delete_reuse_btn = discord.ui.Button(label="🧹 Delete & Re-use", style=discord.ButtonStyle.danger)
        delete_reuse_btn.callback = self.delete_reuse_callback
        self.add_item(delete_reuse_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author.id

    async def reuse_callback(self, interaction: discord.Interaction):
        await self.show_reuse_models(interaction)

    async def delete_callback(self, interaction: discord.Interaction):
        try:
            await self.message.delete()
        except Exception:
            pass
        await interaction.response.send_message("✅ Post deleted.", ephemeral=True)

    async def delete_reuse_callback(self, interaction: discord.Interaction):
        try:
            await self.message.delete()
        except Exception:
            pass
        await self.show_reuse_models(interaction)

    async def show_reuse_models(self, interaction: discord.Interaction):
        if not interaction.channel:
            await interaction.response.send_message("❌ Channel unavailable.", ephemeral=True)
            return

        class ReuseModelSelect(discord.ui.Select):
            def __init__(self, session: aiohttp.ClientSession, channel_id: int, prompt_text: str, hidden_suffix: str):
                self.session = session
                self.channel_id = channel_id
                self.prompt_text = prompt_text
                self.hidden_suffix = hidden_suffix

                options = []
                for variant in VARIANT_MAP.get(channel_id, []):
                    model_id = variant["model"]
                    options.append(
                        discord.SelectOption(
                            label=get_model_label(model_id),
                            value=model_id
                        )
                    )

                super().__init__(
                    placeholder="♻️ Re-use with model...",
                    min_values=1,
                    max_values=1,
                    options=options[:25]
                )

            async def callback(self, inner_interaction: discord.Interaction):
                model_id = self.values[0]
                await inner_interaction.response.send_modal(
                    VeniceModal(
                        self.session,
                        {"model": model_id},
                        self.hidden_suffix,
                        previous_inputs={
                            "prompt": self.prompt_text,
                            "hidden_suffix": self.hidden_suffix,
                        },
                    )
                )

        view = discord.ui.View(timeout=300)
        view.add_item(ReuseModelSelect(self.session, interaction.channel.id, self.prompt_text, self.hidden_suffix))
        await interaction.response.send_message("♻️ Choose a model to re-use your prompt:", view=view, ephemeral=True)

# -------------------------------------------------
# STARTER MODEL SELECT
# -------------------------------------------------
class ModelSelect(discord.ui.Select):
    def __init__(self, session: aiohttp.ClientSession, channel_id: int):
        self.session = session
        self.channel_id = channel_id

        options = []
        for variant in VARIANT_MAP.get(channel_id, []):
            model_id = variant["model"]
            options.append(discord.SelectOption(label=get_model_label(model_id), value=model_id))

        # custom_id helps identifying starter messages safely
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

        await interaction.response.send_modal(
            VeniceModal(self.session, {"model": model_id}, hidden_suffix)
        )

class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.add_item(ModelSelect(session, channel.id))

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

    async def cog_unload(self):
        if self.session and not self.session.closed:
            await self.session.close()

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