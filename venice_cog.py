import discord
from discord.ext import commands
import aiohttp
import io
import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime

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

        title_text = (self.prompt_text[:15].capitalize() + "...") if len(self.prompt_text) > 15 else self.prompt_text.capitalize()
        embed = discord.Embed(title=title_text, color=discord.Color.blurple())

        if len(self.prompt_text) > 50:
            short_prompt = f"{self.prompt_text[:50]}... *(click `[more info]` below)*"
        else:
            short_prompt = self.prompt_text

        embed.add_field(name="Prompt", value=short_prompt, inline=False)

        neg_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if neg_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.add_field(name="Negative Prompt", value="*(custom negative prompt applied)*", inline=False)

        embed.set_image(url="attachment://image.png")

        if hasattr(self.author, "avatar") and self.author.avatar:
            embed.set_author(name=str(self.author), icon_url=self.author.avatar.url)

        guild = interaction.guild
        footer_text = f"{self.variant['model']} | CFG: {self.variant['cfg_scale']} | Steps: {self.variant['steps']}"
        embed.set_footer(text=footer_text, icon_url=guild.icon.url if guild and guild.icon else None)

        msg = await interaction.followup.send(content=self.author.mention, embed=embed, file=file)

        # Metadaten in Message speichern (für /showfullprompt)
        msg.bot_data = {
            "full_prompt": full_prompt,
            "negative_prompt": neg_prompt,
            "model": self.variant['model'],
            "cfg": self.variant['cfg_scale'],
            "steps": self.variant['steps'],
            "author": self.author.id
        }

        # Custom Reactions automatisch hinzufügen
        for emoji in CUSTOM_REACTIONS:
            try:
                await msg.add_reaction(emoji)
            except Exception as e:
                print(f"Fehler beim Hinzufügen der Reaktion {emoji}: {e}")

        if len(self.prompt_text) > 50:
            await msg.reply(f"🔎 Type `/showfullprompt {msg.id}` to reveal full prompt info")

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


# ---------------- Slash Command für Full Info ----------------
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

    @commands.slash_command(name="showfullprompt", description="Shows the full prompt and generation details for an image message.")
    async def show_full_prompt(self, ctx: discord.ApplicationContext, message_id: str):
        try:
            message = await ctx.channel.fetch_message(int(message_id))
        except:
            await ctx.respond("❌ Could not fetch message.", ephemeral=True)
            return

        if not hasattr(message, "bot_data"):
            await ctx.respond("❌ No generation data found for this message.", ephemeral=True)
            return

        data = message.bot_data
        embed = discord.Embed(title="📜 Full Prompt Info", color=discord.Color.gold())
        embed.add_field(name="Prompt", value=data["full_prompt"], inline=False)
        embed.add_field(name="Negative Prompt", value=data["negative_prompt"], inline=False)
        embed.add_field(name="Model", value=data["model"], inline=True)
        embed.add_field(name="CFG", value=str(data["cfg"]), inline=True)
        embed.add_field(name="Steps", value=str(data["steps"]), inline=True)
        embed.add_field(name="Created At", value=discord.utils.format_dt(message.created_at, "F"), inline=False)
        user = ctx.guild.get_member(data["author"])
        if user:
            embed.set_author(name=f"Created by {user.display_name}", icon_url=user.avatar.url if user.avatar else None)

        await ctx.respond(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
