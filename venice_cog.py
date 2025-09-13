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
NSFW_PROMPT_SUFFIX = " NSFW, show explicit details"
SFW_PROMPT_SUFFIX = " SFW, no explicit content"

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

VARIANT_MAP = {
    ">": {"label": "Lustify", "model": "lustify-sdxl", "cfg_scale": 4.5, "steps": 30, "channel": NSFW_CHANNEL_ID},
    "!!": {"label": "Pony", "model": "pony-realism", "cfg_scale": 5.0, "steps": 20, "channel": NSFW_CHANNEL_ID},
    "##": {"label": "FluxUnc", "model": "flux-dev-uncensored", "cfg_scale": 4.5, "steps": 30, "channel": NSFW_CHANNEL_ID},
    "**": {"label": "FluxDev", "model": "flux-dev", "cfg_scale": 5.0, "steps": 30, "channel": NSFW_CHANNEL_ID},

    "?": {"label": "SD3.5", "model": "stable-diffusion-3.5", "cfg_scale": 4.0, "steps": 8, "channel": SFW_CHANNEL_ID},
    "&": {"label": "Flux", "model": "flux-dev", "cfg_scale": 5.0, "steps": 30, "channel": SFW_CHANNEL_ID},
    "~": {"label": "Qwen", "model": "qwen-image", "cfg_scale": 3.5, "steps": 8, "channel": SFW_CHANNEL_ID},
    "$$": {"label": "HiDream", "model": "hidream", "cfg_scale": 4.0, "steps": 20, "channel": SFW_CHANNEL_ID},
}

# ---------------- Venice API Call ----------------
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
                print(f"Venice API Error {resp.status}: {await resp.text()}")
                return None
            return await resp.read()
    except Exception as e:
        print(f"Exception calling Venice API: {e}")
        return None

# ---------------- Aspect Ratio View ----------------
class AspectRatioView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, hidden_suffix, author):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author

    async def generate_image(self, interaction: discord.Interaction, width: int, height: int):
        await interaction.response.defer(ephemeral=True)

        # Fortschritt
        steps = self.variant["steps"]
        cfg = self.variant["cfg_scale"]
        progress_msg = await interaction.followup.send(f"⏳ Generating image... 0%", ephemeral=True)
        for i in range(1, 11):
            await asyncio.sleep(0.2 + steps*0.01 + cfg*0.02)
            try:
                await progress_msg.edit(content=f"⏳ Generating image... {i*10}%")
            except:
                pass

        img_bytes = await venice_generate(self.session, self.prompt_text + self.hidden_suffix, self.variant, width, height)
        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            return

        fp = io.BytesIO(img_bytes)
        file = discord.File(fp, filename="image.png")

        # Embed zusammenbauen
        embed = discord.Embed(title="Generated Image", color=discord.Color.blurple())
        embed.add_field(name="Prompt", value=self.prompt_text, inline=False)

        neg_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if neg_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.add_field(name="Negative Prompt", value=neg_prompt, inline=False)

        embed.set_image(url="attachment://image.png")

        # Autor & Avatar
        if hasattr(self.author, "avatar") and self.author.avatar:
            embed.set_author(name=str(self.author), icon_url=self.author.avatar.url)

        # Footer mit Servername + Icon + Modellinfo
        guild = interaction.guild
        footer_text = f"{self.variant['model']} | CFG: {self.variant['cfg_scale']} | Steps: {self.variant['steps']}"
        if guild:
            embed.set_footer(text=footer_text, icon_url=guild.icon.url if guild.icon else None)
        else:
            embed.set_footer(text=footer_text)

        # Bild posten
        await interaction.followup.send(content=self.author.mention, embed=embed, file=file)

        # Danach Venice-Buttons posten
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel):
            await VeniceCog.ensure_button_message_static(channel, self.session)

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

# ---------------- Modal ----------------
class VeniceModal(discord.ui.Modal):
    def __init__(self, session: aiohttp.ClientSession, variant: dict, channel_id: int):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
        self.channel_id = channel_id

        self.prompt = discord.ui.TextInput(label="Describe your image", style=discord.TextStyle.paragraph, required=True, max_length=500)
        self.negative_prompt = discord.ui.TextInput(label="Negative Prompt (optional)", style=discord.TextStyle.paragraph, required=False, max_length=300)
        normal_cfg = CFG_REFERENCE[variant['model']]
        self.cfg_value = discord.ui.TextInput(label="CFG Value (optional)", style=discord.TextStyle.short, placeholder=f"{variant['cfg_scale']} (Normal: {normal_cfg})", required=False, max_length=5)

        self.add_item(self.prompt)
        self.add_item(self.negative_prompt)
        self.add_item(self.cfg_value)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cfg_value = float(self.cfg_value.value)
        except:
            cfg_value = self.variant['cfg_scale']

        hidden_suffix = NSFW_PROMPT_SUFFIX if self.channel_id == NSFW_CHANNEL_ID else SFW_PROMPT_SUFFIX
        variant = {**self.variant, "cfg_scale": cfg_value, "negative_prompt": self.negative_prompt.value or DEFAULT_NEGATIVE_PROMPT}

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(self.session, variant, self.prompt.value, hidden_suffix, interaction.user),
            ephemeral=True
        )

# ---------------- Buttons View ----------------
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

# ---------------- Cog ----------------
class VeniceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    async def ensure_button_message(self, channel: discord.TextChannel):
        async for msg in channel.history(limit=10):
            if msg.components:
                try:
                    await msg.delete()
                except:
                    pass
        view = VeniceView(self.session, channel.id)
        await channel.send("💡 Click a button to start generating images!", view=view)

    @staticmethod
    async def ensure_button_message_static(channel: discord.TextChannel, session: aiohttp.ClientSession):
        async for msg in channel.history(limit=10):
            if msg.components:
                try:
                    await msg.delete()
                except:
                    pass
        view = VeniceView(session, channel.id)
        await channel.send("💡 Click a button to start generating images!", view=view)

    @commands.Cog.listener()
    async def on_ready(self):
        for channel_id in [NSFW_CHANNEL_ID, SFW_CHANNEL_ID]:
            channel = self.bot.get_channel(channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                await self.ensure_button_message(channel)

async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
