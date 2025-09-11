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
IMAGE_CHANNEL_ID = 1346843244067160074  # ⬅️ Deinen NSFW-Channel hier eintragen

VARIANT_MAP = {
    ">": {  # Safe Normal
        "model": "stable-diffusion-3.5",
        "cfg_scale": 4.0,
        "steps": 30,
    },
    "??": {  # Safe Stylized
        "model": "hidream",
        "cfg_scale": 3.5,
        "steps": 25,
    },
    "!!": {  # Extreme NSFW
        "model": "lustify-sdxl",
        "cfg_scale": 6.0,
        "steps": 40,
    },
    "~": {  # NSFW-focused
        "model": "pony-realism",
        "cfg_scale": 5.0,
        "steps": 50,
    },
}

NEGATIVE_PROMPT = "blurry, bad anatomy, missing fingers, extra limbs, text, watermark"

async def venice_generate(session: aiohttp.ClientSession, prompt: str, variant: dict) -> bytes | None:
    headers = {
        "Authorization": f"Bearer {VENICE_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": variant["model"],
        "prompt": prompt,
        "width": 1024,
        "height": 1024,
        "steps": variant["steps"],
        "safe_mode": False,
        "hide_watermark": True,
        "cfg_scale": variant["cfg_scale"],
        "negative_prompt": NEGATIVE_PROMPT,
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

# ===== Modal & View =====
class VeniceModal(discord.ui.Modal, title="Generate Image"):
    prompt = discord.ui.TextInput(
        label="Describe your image",
        style=discord.TextStyle.paragraph,
        placeholder="A beautiful forest scene",
        required=True,
        max_length=500
    )

    def __init__(self, session: aiohttp.ClientSession, variant: dict):
        super().__init__()
        self.session = session
        self.variant = variant

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("🎨 Generating your image...", ephemeral=True)
        img_bytes = await venice_generate(self.session, self.prompt.value, self.variant)
        if not img_bytes:
            await interaction.followup.send("❌ Sorry, generation failed.", ephemeral=True)
            return

        fp = io.BytesIO(img_bytes)
        file = discord.File(fp, filename="venice.png")
        await interaction.followup.send(
            content=f"Here’s your image, {interaction.user.mention}!\nPrompt: `{self.prompt.value}`",
            file=file
        )

class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession):
        super().__init__(timeout=None)
        self.session = session

    @discord.ui.button(label="🎨 Normal Safe", style=discord.ButtonStyle.green)
    async def normal_safe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VeniceModal(self.session, VARIANT_MAP[">"]))

    @discord.ui.button(label="🎭 Stylized Safe", style=discord.ButtonStyle.blurple)
    async def stylized_safe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VeniceModal(self.session, VARIANT_MAP["??"]))

    @discord.ui.button(label="🔥 Extreme NSFW", style=discord.ButtonStyle.red)
    async def extreme_nsfw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VeniceModal(self.session, VARIANT_MAP["!!"]))

    @discord.ui.button(label="🔞 NSFW", style=discord.ButtonStyle.gray)
    async def nsfw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VeniceModal(self.session, VARIANT_MAP["~"]))

# ===== Cog =====
class VeniceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    @commands.Cog.listener()
    async def on_ready(self):
        channel = self.bot.get_channel(IMAGE_CHANNEL_ID)
        if channel:
            await channel.send(
                "💡 **Tippe `> dein prompt` oder klicke einen Button, um ein Bild zu generieren.**",
                view=VeniceView(self.session)
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id != IMAGE_CHANNEL_ID:
            return

        content = message.content.strip()
        prefix = next((p for p in VARIANT_MAP if content.startswith(p)), None)
        if not prefix:
            return

        prompt = content[len(prefix):].strip()
        if not prompt:
            return

        async with message.channel.typing():
            img_bytes = await venice_generate(self.session, prompt, VARIANT_MAP[prefix])
            if not img_bytes:
                await message.reply("❌ Generation failed!")
                return

            fp = io.BytesIO(img_bytes)
            file = discord.File(fp, filename="venice.png")
            await message.reply(
                content=f"Generated (`{prefix}` variant): `{prompt}`",
                file=file
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
