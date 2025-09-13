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

# CFG map (nur normal und high)
CFG_MAP = {
    "lustify-sdxl": {"normal": 4.5, "high": 5.5},
    "flux-dev-uncensored": {"normal": 4.5, "high": 5.5},
    "pony-realism": {"normal": 5.0, "high": 6.0},
    "qwen-image": {"normal": 3.5, "high": 4.5},
    "flux-dev": {"normal": 5.0, "high": 6.0},
    "stable-diffusion-3.5": {"normal": 4.0, "high": 5.0},
    "hidream": {"normal": 4.0, "high": 5.0},
    "venice-sd35": {"normal": 4.0, "high": 5.0},
}

VARIANT_MAP = {
    # NSFW
    ">":  {"label": "Lustify", "model": "lustify-sdxl", "cfg_scale": CFG_MAP['lustify-sdxl']['normal'], "steps": 30, "channel": NSFW_CHANNEL_ID},
    "!!": {"label": "Pony", "model": "pony-realism", "cfg_scale": CFG_MAP['pony-realism']['normal'], "steps": 20, "channel": NSFW_CHANNEL_ID},
    "##": {"label": "FluxUnc", "model": "flux-dev-uncensored", "cfg_scale": CFG_MAP['flux-dev-uncensored']['normal'], "steps": 30, "channel": NSFW_CHANNEL_ID},
    "**": {"label": "FluxDev", "model": "flux-dev", "cfg_scale": CFG_MAP['flux-dev']['normal'], "steps": 30, "channel": NSFW_CHANNEL_ID},
    "++": {"label": "Qwen", "model": "qwen-image", "cfg_scale": CFG_MAP['qwen-image']['normal'], "steps": 12, "channel": NSFW_CHANNEL_ID},

    # SFW
    "?":  {"label": "SD3.5", "model": "stable-diffusion-3.5", "cfg_scale": CFG_MAP['stable-diffusion-3.5']['normal'], "steps": 8, "channel": SFW_CHANNEL_ID},
    "&":  {"label": "Flux", "model": "flux-dev", "cfg_scale": CFG_MAP['flux-dev']['normal'], "steps": 30, "channel": SFW_CHANNEL_ID},
    "~":  {"label": "Qwen", "model": "qwen-image", "cfg_scale": CFG_MAP['qwen-image']['normal'], "steps": 8, "channel": SFW_CHANNEL_ID},
    "$$": {"label": "HiDream", "model": "hidream", "cfg_scale": CFG_MAP['hidream']['normal'], "steps": 20, "channel": SFW_CHANNEL_ID},
    "%%": {"label": "Venice", "model": "venice-sd35", "cfg_scale": CFG_MAP['venice-sd35']['normal'], "steps": 20, "channel": SFW_CHANNEL_ID},
}

# Venice API call
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

# Modal
class VeniceModal(discord.ui.Modal):
    def __init__(self, session: aiohttp.ClientSession, variant: dict, channel_id: int, cfg_value: float, cfg_label: str):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
        self.channel_id = channel_id
        self.cfg_value = cfg_value
        self.cfg_label = cfg_label

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

        self.add_item(self.prompt)
        self.add_item(self.negative_prompt)

    async def on_submit(self, interaction: discord.Interaction):
        variant = {**self.variant,
                   "cfg_scale": self.cfg_value,
                   "negative_prompt": self.negative_prompt.value or DEFAULT_NEGATIVE_PROMPT}
        prompt_text = self.prompt.value
        if self.channel_id == NSFW_CHANNEL_ID:
            prompt_text += " NSFW, explicit content"
        else:
            prompt_text += " SFW, no explicit content"

        await interaction.response.send_message(
            f"🎨 Generating with {variant['label']} | CFG: {self.cfg_label} ({self.cfg_value}) | Steps: {variant['steps']}...",
            ephemeral=True
        )

        img_bytes = await venice_generate(self.session, prompt_text, variant)
        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            return

        channel = interaction.client.get_channel(self.channel_id)
        if channel:
            fp = io.BytesIO(img_bytes)
            file = discord.File(fp, filename="image.png")
            await channel.send(
                content=(f"{interaction.user.mention} generated an image with {variant['label']}\n"
                         f"CFG: {self.cfg_label} ({self.cfg_value}) | Model: {variant['model']} | Steps: {variant['steps']}"),
                file=file
            )
            await channel.send("💡 Choose the next generation:", view=VeniceView(self.session, self.channel_id))

# Button View
class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel_id: int):
        super().__init__(timeout=None)
        self.session = session
        self.channel_id = channel_id
        for prefix, variant in VARIANT_MAP.items():
            if variant['channel'] == channel_id:
                style = discord.ButtonStyle.red if channel_id == NSFW_CHANNEL_ID else discord.ButtonStyle.blurple
                for cfg_label, cfg_value in {"Normal": CFG_MAP[variant['model']]['normal'], "High": CFG_MAP[variant['model']]['high']}.items():
                    btn = discord.ui.Button(label=f"{variant['label']} | {cfg_label}", style=style)
                    async def _cb(interaction: discord.Interaction, pref=prefix, cfg_value=cfg_value, cfg_label=cfg_label):
                        await interaction.response.send_modal(VeniceModal(self.session, VARIANT_MAP[pref], self.channel_id, cfg_value, cfg_label))
                    btn.callback = _cb
                    self.add_item(btn)

# Cog
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
                        except Exception:
                            pass
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
        await message.reply(content=f"Generated with {variant['label']} | CFG: {variant['cfg_scale']} | Steps: {variant['steps']} | Model: {variant['model']}", file=file)
        async for msg in message.channel.history(limit=10):
            if msg.components:
                try:
                    await msg.delete()
                except Exception:
                    pass
        await message.channel.send("💡 Choose the next generation:", view=VeniceView(self.session, message.channel.id))

async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
