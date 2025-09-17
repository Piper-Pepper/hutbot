import discord
from discord.ext import commands
import aiohttp
import io
import asyncio
import os
import re
import time
import uuid
from dotenv import load_dotenv

load_dotenv()
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env!")

VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"

NSFW_CATEGORY_ID = 1415769711052062820
SFW_CATEGORY_ID = 1416461717038170294

DEFAULT_NEGATIVE_PROMPT = "blurry, bad anatomy, missing fingers, extra limbs, watermark"
NSFW_PROMPT_SUFFIX = " (NSFW, show explicit details)"
SFW_PROMPT_SUFFIX = " (SFW, no explicit details)"

CFG_REFERENCE = {
    "lustify-sdxl": 4.5,
    "flux-dev-uncensored": 4.5,
    "pony-realism": 5.0,
    "qwen-image": 3.5,
    "flux-dev": 5.0,
    "stable-diffusion-3.5": 4.0,
    "hidream": 4.0,
}

VARIANT_MAP = {
    NSFW_CATEGORY_ID: [
        {"label": "Lustify", "model": "lustify-sdxl", "cfg_scale": 4.5, "steps": 30},
        {"label": "Pony", "model": "pony-realism", "cfg_scale": 5.0, "steps": 30},
        {"label": "FluxUnc", "model": "flux-dev-uncensored", "cfg_scale": 4.5, "steps": 30},
    ],
    SFW_CATEGORY_ID: [
        {"label": "SD3.5", "model": "stable-diffusion-3.5", "cfg_scale": 4.0, "steps": 30},
        {"label": "Flux", "model": "flux-dev", "cfg_scale": 5.0, "steps": 30},
        {"label": "HiDream", "model": "hidream", "cfg_scale": 4.0, "steps": 30},
    ]
}

CUSTOM_REACTIONS = [
    "<:01sthumb:1387086056498921614>",
    "<:01smile_piper:1387083454575022213>",
    "<:02No:1347536448831754383>",
    "<:011:1346549711817146400>"
]

# ---------------- Helper: Safe Filename ----------------
def make_safe_filename(prompt: str) -> str:
    base = "_".join(prompt.split()[:5]) or "image"
    base = re.sub(r"[^a-zA-Z0-9_]", "_", base)
    if not base[0].isalnum():
        base = "img_" + base
    return f"{base}_{int(time.time_ns())}_{uuid.uuid4().hex[:8]}.png"

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

        steps = self.variant["steps"]
        cfg = self.variant["cfg_scale"]
        progress_msg = await interaction.followup.send(f"⏳ Generating image... 0%", ephemeral=True)
        for i in range(1, 11):
            await asyncio.sleep(0.4 + steps * 0.02 + cfg * 0.03)
            try:
                await progress_msg.edit(content=f"⏳ Generating image... {i*10}%")
            except:
                pass

        full_prompt = self.prompt_text + self.hidden_suffix
        # Sonderzeichen am Anfang behandeln
        if full_prompt and not full_prompt[0].isalnum():
            full_prompt = " " + full_prompt

        img_bytes = await venice_generate(self.session, full_prompt, self.variant, width, height)
        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            if isinstance(interaction.channel, discord.TextChannel):
                await VeniceCog.ensure_button_message_static(interaction.channel, self.session)
            self.stop()
            return

        filename = make_safe_filename(self.prompt_text)
        fp = io.BytesIO(img_bytes)
        fp.seek(0)
        discord_file = discord.File(fp, filename=filename)

        truncated_prompt = self.prompt_text.replace("\n\n", "\n")
        if len(truncated_prompt) > 300:
            truncated_prompt = truncated_prompt[:300] + "..."

        embed = discord.Embed(color=discord.Color.blurple())
        embed.add_field(name="🔮Prompt:", value=truncated_prompt, inline=False)

        neg_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if neg_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.add_field(name="🚫Negative Prompt:", value=neg_prompt, inline=False)

        # Bild NICHT im Embed, erscheint als Attachment
        if hasattr(self.author, "avatar") and self.author.avatar:
            embed.set_author(name=str(self.author), icon_url=self.author.avatar.url)

        guild = interaction.guild
        embed.set_footer(
            text=f"{self.variant['model']} | CFG: {self.variant['cfg_scale']} | Steps: {self.variant['steps']}",
            icon_url=guild.icon.url if guild and guild.icon else None
        )

        # ✅ Alles in einem Post: Mention oben, Bild als Attachment, Embed darunter
        msg = await interaction.channel.send(
            content=self.author.mention,
            embed=embed,
            files=[discord_file]
        )

        for emoji in CUSTOM_REACTIONS:
            try:
                await msg.add_reaction(emoji)
            except:
                pass

        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.ensure_button_message_static(interaction.channel, self.session)

        self.stop()

    # Aspect Ratio Buttons
    @discord.ui.button(label="⏹️1:1", style=discord.ButtonStyle.blurple)
    async def ratio_1_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, 1024, 1024)

    @discord.ui.button(label="🖥️16:9", style=discord.ButtonStyle.blurple)
    async def ratio_16_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, 1024, 576)

    @discord.ui.button(label="📱9:16", style=discord.ButtonStyle.blurple)
    async def ratio_9_16(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, 576, 1024)

# ---------------- Modal ----------------
class VeniceModal(discord.ui.Modal):
    def __init__(self, session: aiohttp.ClientSession, variant: dict, hidden_suffix: str):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
        self.hidden_suffix = hidden_suffix

        normal_cfg = CFG_REFERENCE[variant['model']]

        self.prompt = discord.ui.TextInput(
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
            placeholder=f"Additional hidden prompt added: {hidden_suffix}"
        )

        self.negative_prompt = discord.ui.TextInput(
            label="Negative Prompt (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300,
            placeholder=f"Default: {DEFAULT_NEGATIVE_PROMPT}"
        )

        self.cfg_value = discord.ui.TextInput(
            label="CFG (Higher=stricter AI adherence)",
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
        except:
            cfg_value = self.variant['cfg_scale']

        variant = {
            **self.variant,
            "cfg_scale": cfg_value,
            "negative_prompt": self.negative_prompt.value or DEFAULT_NEGATIVE_PROMPT
        }

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(self.session, variant, self.prompt.value, self.hidden_suffix, interaction.user),
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
            category_id = interaction.channel.category.id if interaction.channel.category else None
            hidden_suffix = NSFW_PROMPT_SUFFIX if category_id == NSFW_CATEGORY_ID else SFW_PROMPT_SUFFIX
            await interaction.response.send_modal(VeniceModal(self.session, variant, hidden_suffix))
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
            if msg.components and not msg.embeds and not msg.attachments:
                try:
                    await msg.delete()
                except:
                    pass
        view = VeniceView(self.session, channel)
        await channel.send("💡 Click a button to start generating images!", view=view)

    @staticmethod
    async def ensure_button_message_static(channel: discord.TextChannel, session: aiohttp.ClientSession):
        async for msg in channel.history(limit=10):
            if msg.components and not msg.embeds and not msg.attachments:
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

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
