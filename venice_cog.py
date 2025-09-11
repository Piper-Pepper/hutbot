# venice_cog.py
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import io
import asyncio

# ===== CONFIG =====

if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env!")

VENICE_IMAGE_URL = "https://api.venice.ai/v1/images/generations"

# ==================

async def venice_generate(session: aiohttp.ClientSession, prompt: str) -> bytes | None:
    """
    Sends a prompt to Venice API and returns the generated image bytes
    """
    headers = {
        "Authorization": f"Bearer {VENICE_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "lustify-sdxl",
        "prompt": prompt,
        "width": 1024,
        "height": 1024,
        "steps": 30,
        "safe_mode": False,
        "hide_watermark": True,
        "cfg_scale": 4.0,
        "negative_prompt": "",
        "return_binary": True
    }

    try:
        async with session.post(VENICE_IMAGE_URL, headers=headers, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"Venice API Error {resp.status}: {text}")
                return None
            # Venice API returns raw image bytes directly when "return_binary": True
            return await resp.read()
    except Exception as e:
        print(f"Exception calling Venice API: {e}")
        return None

# ===== Modal =====
class VeniceModal(discord.ui.Modal, title="Generate an Image"):
    prompt = discord.ui.TextInput(
        label="Describe your image",
        style=discord.TextStyle.paragraph,
        placeholder="A cute cyberpunk cat drinking coffee",
        required=True,
        max_length=500
    )

    def __init__(self, session: aiohttp.ClientSession):
        super().__init__()
        self.session = session

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("🎨 Generating your image...", ephemeral=True)
        img_bytes = await venice_generate(self.session, self.prompt.value)
        if not img_bytes:
            await interaction.followup.send("❌ Sorry, generation failed.", ephemeral=True)
            return

        fp = io.BytesIO(img_bytes)
        fp.seek(0)
        file = discord.File(fp, filename="venice.png")
        await interaction.followup.send(
            content=f"Here’s your image, {interaction.user.mention}! Prompt: `{self.prompt.value}`",
            file=file
        )

# ===== Cog =====
class VeniceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    @app_commands.command(name="pic", description="Generate an image with Venice AI")
    async def pic(self, interaction: discord.Interaction):
        await interaction.response.send_modal(VeniceModal(self.session))

# ===== Setup function =====
async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
