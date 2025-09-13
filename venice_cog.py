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

NSFW_CHANNEL_ID = 1415769909874524262
SFW_CHANNEL_ID = 1415769966573260970

DEFAULT_NEGATIVE_PROMPT = "blurry, bad anatomy, missing fingers, extra limbs, text, watermark"

# CFG normal values only for modal placeholders
CFG_REFERENCE = {
    "lustify-sdxl": 4.5,
    "flux-dev-uncensored": 4.5,
    "pony-realism": 5.0,
    "qwen-image": 3.5,
    "flux-dev": 5.0,
    "stable-diffusion-3.5": 4.0,
    "hidream": 4.0,
    "venice-sd35": 4.0,
}

NSFW_PROMPT_SUFFIX = " NSFW, show explicit details"
SFW_PROMPT_SUFFIX = " SFW, no explicit content"

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
async def venice_generate(session: aiohttp.ClientSession, prompt: str, variant: dict, width: int, height: int) -> bytes | None:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": variant["model"],
        "prompt": prompt,
        "width": width,
        "height": height,
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
    def __init__(self, session: aiohttp.ClientSession, variant: dict, prompt_text: str, hidden_suffix: str):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix

    async def generate_image(self, interaction: discord.Interaction, width: int, height: int):
        await interaction.response.defer(ephemeral=True)

        img_bytes = await venice_generate(self.session, self.prompt_text + self.hidden_suffix, self.variant, width, height)
        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            return

        fp = io.BytesIO(img_bytes)
        file = discord.File(fp, filename="image.png")
        content = (
            f"**Prompt:** {self.prompt_text}\n"
            f"||**Weitere Infos:**\n"
            f"Model: {self.variant['model']}\n"
            f"CFG: {self.variant['cfg_scale']}\n"
            f"Steps: {self.variant['steps']}\n"
            f"Negative Prompt: {self.variant.get('negative_prompt', DEFAULT_NEGATIVE_PROMPT)}\n"
            f"Hidden Prompt Zusatz: {self.hidden_suffix}||"
        )

        await interaction.followup.send(content=content, file=file, ephemeral=True)
        self.stop()

    @discord.ui.button(label="1:1", style=discord.ButtonStyle.blurple)
    async def ratio_1_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, 1024, 1024)

    @discord.ui.button(label="16:9", style=discord.ButtonStyle.blurple)
    async def ratio_16_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, 1024, 576)

    @discord.ui.button(label="9:16", style=discord.ButtonStyle.blurple)
    async def ratio_9_16(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, 576, 1024)

# --- Modal ---
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

        normal_cfg = CFG_REFERENCE[variant['model']]
        self.cfg_value = discord.ui.TextInput(
            label="CFG Value (optional)",
            style=discord.TextStyle.short,
            placeholder=f"{variant['cfg_scale']} (Normal: {normal_cfg})",
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
        variant = {**self.variant, "cfg_scale": cfg_value, "negative_prompt": self.negative_prompt.value or DEFAULT_NEGATIVE_PROMPT}

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(self.session, variant, self.prompt.value, hidden_suffix),
            ephemeral=True
        )

# --- Buttons View ---
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
                btn.callback = self.make_callback(variant, channel_id)
                self.add_item(btn)
                added += 1

    def make_callback(self, variant, channel_id):
        async def callback(interaction: discord.Interaction):
            await interaction.response.send_modal(VeniceModal(self.session, variant, channel_id))
        return callback

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
                        try: 
                            await msg.delete()
                        except: 
                            pass
                await channel.send("💡 Click a button to start generating images!", view=VeniceView(self.session, channel_id))

async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
