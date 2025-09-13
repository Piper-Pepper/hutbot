import discord
from discord.ext import commands
import aiohttp
import io
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env!")

VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"

# Channels
NSFW_CHANNEL_ID = 1415769909874524262
SFW_CHANNEL_ID = 1415769966573260970

DEFAULT_NEGATIVE_PROMPT = "blurry, bad anatomy, missing fingers, extra limbs, text, watermark"

# CFG reference for user guidance
CFG_REFERENCE = {
    "lustify-sdxl": {"low": 3.5, "normal": 4.5, "high": 5.5},
    "flux-dev-uncensored": {"low": 3.5, "normal": 4.5, "high": 5.5},
    "pony-realism": {"low": 4.0, "normal": 5.0, "high": 6.0},
    "qwen-image": {"low": 3.0, "normal": 3.5, "high": 4.5},
    "flux-dev": {"low": 4.0, "normal": 5.0, "high": 6.0},
    "stable-diffusion-3.5": {"low": 3.0, "normal": 4.0, "high": 5.0},
    "hidream": {"low": 3.0, "normal": 4.0, "high": 5.0},
    "venice-sd35": {"low": 3.0, "normal": 4.0, "high": 5.0},
}

# NSFW/SFW prompt suffixes (edit here)
NSFW_PROMPT_SUFFIX = " NSFW, show explicit details"
SFW_PROMPT_SUFFIX = " SFW, no explicit content"

# 4 Buttons per channel
VARIANT_MAP = {
    # NSFW
    ">": {"label": "Lustify", "model": "lustify-sdxl", "cfg_scale": 4.5, "steps": 30, "channel": NSFW_CHANNEL_ID},
    "!!": {"label": "Pony", "model": "pony-realism", "cfg_scale": 5.0, "steps": 20, "channel": NSFW_CHANNEL_ID},
    "##": {"label": "FluxUnc", "model": "flux-dev-uncensored", "cfg_scale": 4.5, "steps": 30, "channel": NSFW_CHANNEL_ID},
    "**": {"label": "FluxDev", "model": "flux-dev", "cfg_scale": 5.0, "steps": 30, "channel": NSFW_CHANNEL_ID},

    # SFW
    "?": {"label": "SD3.5", "model": "stable-diffusion-3.5", "cfg_scale": 4.0, "steps": 8, "channel": SFW_CHANNEL_ID},
    "&": {"label": "Flux", "model": "flux-dev", "cfg_scale": 5.0, "steps": 30, "channel": SFW_CHANNEL_ID},
    "~": {"label": "Qwen", "model": "qwen-image", "cfg_scale": 3.5, "steps": 8, "channel": SFW_CHANNEL_ID},
    "$$": {"label": "HiDream", "model": "hidream", "cfg_scale": 4.0, "steps": 20, "channel": SFW_CHANNEL_ID},
}

# --- Venice API call ---
async def venice_generate(session: aiohttp.ClientSession, prompt: str, variant: dict) -> bytes | None:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": variant["model"],
        "prompt": prompt,
        "width": variant.get("width", 1024),
        "height": variant.get("height", 1024),
        "steps": variant["steps"],
        "cfg_scale": variant["cfg_scale"],
        "negative_prompt": variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT),
        "safe_mode": False,
        "hide_watermark": True,
        "return_binary": True
    }
    try:
        async with session.post(VENICE_IMAGE_URL, headers=headers, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"Venice API Error {resp.status}: {text}")
                return None
            return await resp.read()
    except Exception as e:
        print(f"Exception calling Venice API: {e}")
        return None

# --- Aspect Ratio View ---
class AspectRatioView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, hidden_suffix):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix

    async def generate_image(self, interaction: discord.Interaction, width: int, height: int):
        payload_variant = {**self.variant, "width": width, "height": height}
        img_bytes = await venice_generate(self.session, self.prompt_text + self.hidden_suffix, payload_variant)
        if not img_bytes:
            await interaction.response.send_message("❌ Generation failed!", ephemeral=True)
            return
        fp = io.BytesIO(img_bytes)
        file = discord.File(fp, filename="image.png")
        content = (
            f"**Prompt:** {self.prompt_text}\n"
            f"||**Weitere Infos:**\n"
            f"Model: {payload_variant['model']}\n"
            f"CFG: {payload_variant['cfg_scale']}\n"
            f"Steps: {payload_variant['steps']}\n"
            f"Negative Prompt: {payload_variant['negative_prompt']}\n"
            f"Hidden Prompt Zusatz: {self.hidden_suffix}||"
        )
        await interaction.response.send_message(content=content, file=file)
        self.stop()

    @discord.ui.button(label="1:1", style=discord.ButtonStyle.blurple)
    async def ratio_1_1(self, button, interaction):
        await self.generate_image(interaction, 1024, 1024)

    @discord.ui.button(label="16:9", style=discord.ButtonStyle.blurple)
    async def ratio_16_9(self, button, interaction):
        await self.generate_image(interaction, 1024, 576)

    @discord.ui.button(label="9:16", style=discord.ButtonStyle.blurple)
    async def ratio_9_16(self, button, interaction):
        await self.generate_image(interaction, 576, 1024)

# --- Modal für Bildgenerierung ---
class VeniceModal(discord.ui.Modal):
    def __init__(self, session: aiohttp.ClientSession, variant: dict, channel_id: int):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
        self.channel_id = channel_id

        self.prompt = discord.ui.TextInput(
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            placeholder="Describe your character or scene",
            required=True,
            max_length=500
        )
        self.negative_prompt = discord.ui.TextInput(
            label="Negative Prompt (optional)",
            style=discord.TextStyle.paragraph,
            placeholder="Things to avoid",
            required=False,
            max_length=300
        )
        ref = CFG_REFERENCE[variant['model']]
        self.cfg_value = discord.ui.TextInput(
            label="CFG Value (optional, number overrides normal)",
            style=discord.TextStyle.short,
            placeholder=f"{variant['cfg_scale']} (approx Low: {ref['low']}, Normal: {ref['normal']}, High: {ref['high']})",
            required=False,
            max_length=5
        )

        self.add_item(self.prompt)
        self.add_item(self.negative_prompt)
        self.add_item(self.cfg_value)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cfg_value = float(self.cfg_value.value)
        except (ValueError, TypeError):
            cfg_value = self.variant['cfg_scale']

        hidden_suffix = NSFW_PROMPT_SUFFIX if self.channel_id == NSFW_CHANNEL_ID else SFW_PROMPT_SUFFIX

        variant = {
            **self.variant,
            "cfg_scale": cfg_value,
            "negative_prompt": self.negative_prompt.value or DEFAULT_NEGATIVE_PROMPT
        }

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(self.session, variant, self.prompt.value, hidden_suffix),
            ephemeral=True
        )

# --- Buttons / View ---
class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel_id: int):
        super().__init__(timeout=None)
        self.session = session
        self.channel_id = channel_id

        added = 0
        for prefix, variant in VARIANT_MAP.items():
            if variant['channel'] == channel_id and added < 4:
                style = discord.ButtonStyle.red if channel_id == NSFW_CHANNEL_ID else discord.ButtonStyle.blurple
                btn = discord.ui.Button(label=variant['label'], style=style, custom_id=prefix)
                async def _cb(interaction: discord.Interaction, pref=prefix):
                    await interaction.response.send_modal(VeniceModal(self.session, VARIANT_MAP[pref], self.channel_id))
                btn.callback = _cb
                self.add_item(btn)
                added += 1

# --- Cog ---
class VeniceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    @commands.Cog.listener()
    async def on_ready(self):
        for channel_id in [NSFW_CHANNEL_ID, SFW_CHANNEL_ID]:
            channel = self.bot.get_channel(channel_id)
            if channel:
                async for msg in channel.history(limit=10):
                    if msg.components:
                        try: await msg.delete()
                        except: pass
                await channel.send("💡 Click a button to start generating images!", view=VeniceView(self.session, channel_id))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        content = message.content.strip()
        sorted_prefixes = sorted(VARIANT_MAP.keys(), key=len, reverse=True)
        prefix = next((p for p in sorted_prefixes if content.startswith(p)), None)
        if not prefix:
            return
        variant = VARIANT_MAP[prefix]
        if message.channel.id != variant['channel']:
            return
        parts = content[len(prefix):].split("||", 1)
        prompt_text = parts[0].strip()
        neg_text = parts[1].strip() if len(parts) > 1 else DEFAULT_NEGATIVE_PROMPT
        variant = {**variant, "negative_prompt": neg_text}
        img_bytes = await venice_generate(self.session, prompt_text, variant)
        if not img_bytes:
            await message.reply("❌ Generation failed!")
            return
        fp = io.BytesIO(img_bytes)
        file = discord.File(fp, filename="image.png")
        await message.reply(content=f"Generated with {variant['label']} | CFG: {variant['cfg_scale']} | Steps: {variant['steps']}", file=file)
        async for msg in message.channel.history(limit=10):
            if msg.components:
                try: await msg.delete()
                except: pass
        await message.channel.send("💡 Choose the next generation:", view=VeniceView(self.session, message.channel.id))

async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
