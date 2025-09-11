import discord
from discord.ext import commands
import aiohttp
import io
import os
from dotenv import load_dotenv

load_dotenv()
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env!")

VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"

TEST_CHANNEL_ID = 1346843244067160074  # Replace with your test channel

# Test models: Lustify fixed, 3 others we want to probe
TEST_MODELS = {
    "Lustify": {"model": "lustify-sdxl", "steps": 30},
    "ModelA": {"model": "pony-realism", "steps": 1},  # Start with 1, increase until error
    "ModelB": {"model": "flux-dev-uncensored", "steps": 1},
    "ModelC": {"model": "qwen-image", "steps": 1},
}

NEGATIVE_PROMPT = "blurry, bad anatomy, missing fingers, extra limbs, text, watermark"

async def venice_generate(session, prompt, model_name, steps):
    variant = TEST_MODELS[model_name]
    payload = {
        "model": variant["model"],
        "prompt": prompt,
        "width": 512,
        "height": 512,
        "steps": steps,
        "cfg_scale": 4.0,
        "negative_prompt": NEGATIVE_PROMPT,
        "safe_mode": False,
        "hide_watermark": True,
        "return_binary": True
    }
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
    async with session.post(VENICE_IMAGE_URL, headers=headers, json=payload) as resp:
        text = await resp.text()
        if resp.status != 200:
            return None, text
        return await resp.read(), None

class TestCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        import asyncio
        asyncio.create_task(self.session.close())

    @commands.command()
    async def test_models(self, ctx: commands.Context):
        """Test 4 models to check max allowed steps"""
        for model_name in TEST_MODELS:
            steps = TEST_MODELS[model_name]["steps"]
            img_bytes, error = await venice_generate(self.session, "Test prompt", model_name, steps)
            if error:
                await ctx.send(f"❌ Model `{model_name}` with {steps} steps failed:\n{error}")
            else:
                await ctx.send(f"✅ Model `{model_name}` with {steps} steps succeeded.")
                if img_bytes:
                    await ctx.send(file=discord.File(io.BytesIO(img_bytes), filename=f"{model_name}.png"))

async def setup(bot):
    await bot.add_cog(TestCog(bot))
