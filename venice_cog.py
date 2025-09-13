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

# Kategorie IDs
NSFW_CATEGORY_ID = 1415769711052062820
SFW_CATEGORY_ID = 1416461717038170294

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
}

# Varianten pro Kategorie
VARIANT_MAP = {
    NSFW_CATEGORY_ID: [
        {"label": "Lustify", "model": "lustify-sdxl", "cfg_scale": 4.5, "steps": 30},
        {"label": "Pony", "model": "pony-realism", "cfg_scale": 5.0, "steps": 25},
        {"label": "FluxUnc", "model": "flux-dev-uncensored", "cfg_scale": 4.5, "steps": 30},
        {"label": "FluxDev", "model": "flux-dev", "cfg_scale": 5.0, "steps": 28},
    ],
    SFW_CATEGORY_ID: [
        {"label": "SD3.5", "model": "stable-diffusion-3.5", "cfg_scale": 4.0, "steps": 22},
        {"label": "Flux", "model": "flux-dev", "cfg_scale": 5.0, "steps": 30},
        {"label": "Qwen", "model": "qwen-image", "cfg_scale": 3.5, "steps": 8},
        {"label": "HiDream", "model": "hidream", "cfg_scale": 4.0, "steps": 20},
    ]
}

# ---------------- Venice API ----------------
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
    def __init__(self, session, variant, prompt_text, hidden_suffix, author, category_id):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.category_id = category_id

    async def generate_image(self, interaction: discord.Interaction, width: int, height: int):
        await interaction.response.defer(ephemeral=True)

        # NSFW-Schutz: Keine NSFW-Inhalte in SFW-Kategorien
        if self.hidden_suffix == NSFW_PROMPT_SUFFIX and self.category_id != NSFW_CATEGORY_ID:
            await interaction.followup.send("❌ Cannot generate NSFW image in SFW category!", ephemeral=True)
            self.stop()
            return

        # Fortschrittsanzeige
        steps = self.variant["steps"]
        cfg = self.variant["cfg_scale"]
        progress_msg = await interaction.followup.send(f"⏳ Generating image... 0%", ephemeral=True)
        for i in range(1, 11):
            await asyncio.sleep(0.2 + steps*0.01 + cfg*0.02)
            try:
                await progress_msg.edit(content=f"⏳ Generating image... {i*10}%")
            except:
                pass

        full_prompt = self.prompt_text + self.hidden_suffix
        img_bytes = await venice_generate(self.session, full_prompt, self.variant, width, height)
        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            channel = interaction.channel
            if isinstance(channel, discord.TextChannel):
                await VeniceCog.ensure_button_message_static(channel, self.session)
            self.stop()
            return

        fp = io.BytesIO(img_bytes)
        file = discord.File(fp, filename="image.png")

        title_text = (self.prompt_text[:15].capitalize() + "...") if len(self.prompt_text) > 15 else self.prompt_text.capitalize()
        embed = discord.Embed(title=title_text, color=discord.Color.blurple())
        short_prompt = self.prompt_text[:50] + "... [more info]" if len(self.prompt_text) > 50 else self.prompt_text
        embed.add_field(name="Prompt", value=short_prompt, inline=False)

        neg_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if neg_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.add_field(name="Negative Prompt", value=neg_prompt, inline=False)

        embed.set_image(url="attachment://image.png")
        if hasattr(self.author, "avatar") and self.author.avatar:
            embed.set_author(name=str(self.author), icon_url=self.author.avatar.url)

        guild = interaction.guild
        footer_text = f"{self.variant['model']} | CFG: {self.variant['cfg_scale']} | Steps: {self.variant['steps']}"
        embed.set_footer(text=footer_text, icon_url=guild.icon.url if guild and guild.icon else None)

        await interaction.followup.send(content=self.author.mention, embed=embed, file=file)

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
    def __init__(self, session: aiohttp.ClientSession, variant: dict, category_id: int):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
        self.category_id = category_id

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

        hidden_suffix = NSFW_PROMPT_SUFFIX if self.category_id == NSFW_CATEGORY_ID else SFW_PROMPT_SUFFIX
        variant = {**self.variant, "cfg_scale": cfg_value, "negative_prompt": self.negative_prompt.value or DEFAULT_NEGATIVE_PROMPT}

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(self.session, variant, self.prompt.value, hidden_suffix, interaction.user, self.category_id),
            ephemeral=True
        )

# ---------------- Buttons View ----------------
class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.session = session
        self.category_id = channel.category.id if channel.category else None

        variants = VARIANT_MAP.get(self.category_id, [])
        style = discord.ButtonStyle.red if self.category_id == NSFW_CATEGORY_ID else discord.ButtonStyle.blurple

        for variant in variants:
            btn = discord.ui.Button(label=variant['label'], style=style)
            btn.callback = self.make_callback(variant)
            self.add_item(btn)

    def make_callback(self, variant):
        async def callback(interaction: discord.Interaction):
            await interaction.response.send_modal(VeniceModal(self.session, variant, self.category_id))
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
        view = VeniceView(self.session, channel)
        await channel.send("💡 Click a button to start generating images!", view=view)

    @staticmethod
    async def ensure_button_message_static(channel: discord.TextChannel, session: aiohttp.ClientSession):
        async for msg in channel.history(limit=10):
            if msg.components:
                try:
                    await msg.delete()
                except:
                    pass
        view = VeniceView(session, channel)
        await channel.send("💡 Click a button to start generating images!", view=view)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.category and channel.category.id in VARIANT_MAP:
                    await self.ensure_button_message(channel)

async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
