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

# Image variants
VARIANT_MAP = {
    # NSFW
    ">": {"model": "lustify-sdxl", "cfg_scale": 4.0, "steps": 30, "channel": NSFW_CHANNEL_ID},
    "!!": {"model": "pony-realism", "cfg_scale": 5.0, "steps": 8, "channel": NSFW_CHANNEL_ID},  # angepasste steps max
    "##": {"model": "flux-dev-uncensored", "cfg_scale": 4.5, "steps": 30, "channel": NSFW_CHANNEL_ID},
    # SFW
    "?": {"model": "stable-diffusion-3.5", "cfg_scale": 4.0, "steps": 8, "channel": SFW_CHANNEL_ID},
    "&": {"model": "flux-dev", "cfg_scale": 5.0, "steps": 30, "channel": SFW_CHANNEL_ID},
    "~": {"model": "qwen-image", "cfg_scale": 3.5, "steps": 8, "channel": SFW_CHANNEL_ID},
}

DEFAULT_NEGATIVE_PROMPT = "blurry, bad anatomy, missing fingers, extra limbs, text, watermark"

# ----- Venice Image Generation -----
async def venice_generate(session: aiohttp.ClientSession, prompt: str, variant: dict) -> bytes | None:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": variant["model"],
        "prompt": prompt,
        "width": 1024,
        "height": 1024,
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

# ----- Modal -----
class VeniceModal(discord.ui.Modal, title="Generate Image"):
    prompt = discord.ui.TextInput(
        label="Describe your image",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your character or scene",
        required=True,
        max_length=500
    )

    negative_prompt = discord.ui.TextInput(
        label="Negative Prompt (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Optional: describe things you DON'T want in the image (leave empty to use default)",
        required=False,
        max_length=300
    )

    def __init__(self, session: aiohttp.ClientSession, variant: dict, channel_id: int):
        super().__init__()
        self.session = session
        self.variant = variant
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("🎨 Generating your image...", ephemeral=True)
        
        # Use default negative prompt if field is empty
        neg_prompt = self.negative_prompt.value.strip() or DEFAULT_NEGATIVE_PROMPT

        img_bytes = await venice_generate(self.session, self.prompt.value, {**self.variant, "negative_prompt": neg_prompt})
        if not img_bytes:
            await interaction.followup.send("❌ Sorry, generation failed.", ephemeral=True)
            return

        fp = io.BytesIO(img_bytes)
        file = discord.File(fp, filename="image.png")
        channel = interaction.client.get_channel(self.channel_id)
        if channel:
            await channel.send(
                content=f"{interaction.user.mention} generated an image:\nPrompt: `{self.prompt.value}`\nNegative Prompt: `{neg_prompt}`",
                file=file
            )
            await channel.send(
                "💡 Choose the next generation:",
                view=VeniceView(self.session, self.channel_id)
            )

# ----- Button View -----
class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel_id: int):
        super().__init__(timeout=None)
        self.session = session
        self.channel_id = channel_id

    async def _send_modal(self, interaction: discord.Interaction, prefix: str):
        variant = VARIANT_MAP[prefix]
        await interaction.response.send_modal(VeniceModal(self.session, variant, self.channel_id))

    @discord.ui.button(label="Lustify", style=discord.ButtonStyle.red)
    async def button1(self, interaction: discord.Interaction, button: discord.ui.Button):
        prefix = ">" if self.channel_id == NSFW_CHANNEL_ID else "?"
        await self._send_modal(interaction, prefix)

    @discord.ui.button(label="Pony", style=discord.ButtonStyle.red)
    async def button2(self, interaction: discord.Interaction, button: discord.ui.Button):
        prefix = "!!" if self.channel_id == NSFW_CHANNEL_ID else "&"
        await self._send_modal(interaction, prefix)

    @discord.ui.button(label="FluxUnc", style=discord.ButtonStyle.red)
    async def button3(self, interaction: discord.Interaction, button: discord.ui.Button):
        prefix = "##" if self.channel_id == NSFW_CHANNEL_ID else "~"
        await self._send_modal(interaction, prefix)

# ----- Cog -----
class VeniceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.session.bot = bot

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    @commands.Cog.listener()
    async def on_ready(self):
        for channel_id in [NSFW_CHANNEL_ID, SFW_CHANNEL_ID]:
            channel = self.bot.get_channel(channel_id)
            if channel:
                await channel.send(
                    "💡 Use a prefix or click a button to generate an image!\nYou can also specify a negative prompt (optional).",
                    view=VeniceView(self.session, channel_id)
                )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        allowed_channels = [v["channel"] for v in VARIANT_MAP.values()]
        if message.channel.id not in allowed_channels:
            return

        content = message.content.strip()
        prefix = next((p for p in VARIANT_MAP if content.startswith(p)), None)
        if not prefix:
            return

        variant = VARIANT_MAP[prefix]
        if message.channel.id != variant["channel"]:
            return

        # Split prompt and optional negative prompt if user writes "prompt || negative"
        parts = content[len(prefix):].split("||", 1)
        prompt_text = parts[0].strip()
        neg_text = parts[1].strip() if len(parts) > 1 else DEFAULT_NEGATIVE_PROMPT

        if not prompt_text:
            return

        async with message.channel.typing():
            img_bytes = await venice_generate(self.session, prompt_text, {**variant, "negative_prompt": neg_text})
            if not img_bytes:
                await message.reply("❌ Generation failed!")
                return

            fp = io.BytesIO(img_bytes)
            file = discord.File(fp, filename="image.png")
            await message.reply(
                content=f"Generated (`{prefix}` variant) using model `{variant['model']}`:\nPrompt: `{prompt_text}`\nNegative Prompt: `{neg_text}`",
                file=file
            )
            await message.channel.send(
                "💡 Choose the next generation:",
                view=VeniceView(self.session, message.channel.id)
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
