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
    ],
    SFW_CATEGORY_ID: [
        {"label": "SD3.5", "model": "stable-diffusion-3.5", "cfg_scale": 4.0, "steps": 20},
        {"label": "Flux", "model": "flux-dev", "cfg_scale": 5.0, "steps": 30},
        {"label": "HiDream", "model": "hidream", "cfg_scale": 4.0, "steps": 20},
    ]
}

# Custom Reactions
CUSTOM_REACTIONS = [
    "<:01thumb02:1346577526478344272>",
    "<:01thumb01:1378013768495140884>",
    "<:011:1346549711817146400>"
]

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

        # Fortschrittsanzeige simulieren
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
            if isinstance(interaction.channel, discord.TextChannel):
                await VeniceCog.ensure_button_message_static(interaction.channel, self.session)
            self.stop()
            return

        fp = io.BytesIO(img_bytes)
        file = discord.File(fp, filename="image.png")

        # Titel erst nach 25 Zeichen kürzen
        title_text = (self.prompt_text[:25].capitalize() + "...") if len(self.prompt_text) > 25 else self.prompt_text.capitalize()
        embed = discord.Embed(title=title_text, color=discord.Color.blurple())

        # Prompt erst nach 120 Zeichen kürzen
        display_text = self.prompt_text[:120] + "..." if len(self.prompt_text) > 120 else self.prompt_text
        embed.add_field(name="Prompt", value=display_text, inline=False)

        neg_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if neg_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.add_field(name="Negative Prompt", value=neg_prompt, inline=False)
        embed.set_image(url="attachment://image.png")

        if hasattr(self.author, "avatar") and self.author.avatar:
            embed.set_author(name=str(self.author), icon_url=self.author.avatar.url)
        guild = interaction.guild
        footer_text = f"{self.variant['model']} | CFG: {self.variant['cfg_scale']} | Steps: {self.variant['steps']}"
        embed.set_footer(text=footer_text, icon_url=guild.icon.url if guild and guild.icon else None)

        # View for [more info] Button mit Emoji
        view = discord.ui.View()
        if len(self.prompt_text) > 120:
            button = discord.ui.Button(label="📜 More Info", style=discord.ButtonStyle.secondary)

            async def moreinfo_callback(inter: discord.Interaction):
                if inter.user.id == self.author.id:
                    embed = discord.Embed(
                        title="📜 Full Image Info",
                        description="Here are all the juicy details about your image generation:",
                        color=discord.Color.gold()
                    )
                    embed.add_field(name="🖊️ Full Prompt", value=f"```{self.prompt_text + self.hidden_suffix}```", inline=False)
                    embed.add_field(name="🚫 Negative Prompt", value=f"```{self.variant.get('negative_prompt', DEFAULT_NEGATIVE_PROMPT)}```", inline=False)
                    embed.add_field(name="🤫 Hidden Suffix", value=f"```{self.hidden_suffix.strip()}```", inline=False)
                    embed.add_field(name="🎨 Model", value=self.variant["model"], inline=True)
                    embed.add_field(name="🎚️ CFG", value=str(self.variant["cfg_scale"]), inline=True)
                    embed.add_field(name="🧮 Steps", value=str(self.variant["steps"]), inline=True)
                    embed.add_field(name="📅 Created", value=discord.utils.format_dt(inter.message.created_at, "F"), inline=False)

                    creator = inter.guild.get_member(self.author.id)
                    if creator:
                        embed.set_author(name=f"Created by {creator.display_name}", icon_url=creator.avatar.url if creator.avatar else None)

                    await inter.response.send_message(embed=embed, ephemeral=True)
                else:
                    await inter.response.send_message("❌ Only the original author can view the full prompt.", ephemeral=True)

            button.callback = moreinfo_callback
            view.add_item(button)

        # Bild posten
        msg = await interaction.followup.send(content=self.author.mention, embed=embed, file=file, view=view)

        # Custom Emojis automatisch hinzufügen
        for emoji in CUSTOM_REACTIONS:
            try:
                await msg.add_reaction(emoji)
            except Exception as e:
                print(f"Fehler beim Hinzufügen der Reaktion {emoji}: {e}")

        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.ensure_button_message_static(interaction.channel, self.session)

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
    def __init__(self, session: aiohttp.ClientSession, variant: dict):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
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

        category_id = interaction.channel.category.id if interaction.channel.category else None
        hidden_suffix = NSFW_PROMPT_SUFFIX if category_id == NSFW_CATEGORY_ID else SFW_PROMPT_SUFFIX
        variant = {**self.variant, "cfg_scale": cfg_value, "negative_prompt": self.negative_prompt.value or DEFAULT_NEGATIVE_PROMPT}

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(self.session, variant, self.prompt.value, hidden_suffix, interaction.user),
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
            await interaction.response.send_modal(VeniceModal(self.session, variant))
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
            # Nur Button-Nachrichten löschen, die KEIN Embed haben
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

async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
