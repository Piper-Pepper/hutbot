import discord
from discord.ext import commands
import aiohttp
import io
import asyncio
import os
import re
import time
import uuid
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env!")

VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"

NSFW_CATEGORY_ID = 1415769711052062820
SFW_CATEGORY_ID = 1416461717038170294
ROLE_REQUIRED_ID = 1377051179615522926

DEFAULT_NEGATIVE_PROMPT = "blurry, bad anatomy, missing fingers, extra limbs, watermark"
NSFW_PROMPT_SUFFIX = " (NSFW, show explicit details)"
SFW_PROMPT_SUFFIX = " (SFW, no explicit details)"

CFG_REFERENCE = {
    "lustify-sdxl": {"cfg_scale": 5.5, "steps": 30},
    "pony-realism": {"cfg_scale": 6.0, "steps": 30},
    "flux-dev-uncensored": {"cfg_scale": 5.5, "steps": 30},
    "stable-diffusion-3.5": {"cfg_scale": 5.0, "steps": 30},
    "flux-dev": {"cfg_scale": 6.0, "steps": 30},
    "hidream": {"cfg_scale": 5.0, "steps": 30},
    "wai-Illustrious": {"cfg_scale": 6.0, "steps": 30},
}

VARIANT_MAP = {
    NSFW_CATEGORY_ID: [
        {"label": "Lustify", "model": "lustify-sdxl", "cfg_scale": 5.5, "steps": 30},
        {"label": "Pony", "model": "pony-realism", "cfg_scale": 6.0, "steps": 30},
        {"label": "FluxUnc", "model": "flux-dev-uncensored", "cfg_scale": 5.5, "steps": 30},
        {"label": "Anime", "model": "wai-Illustrious", "cfg_scale": 6.0, "steps": 30},
    ],
    SFW_CATEGORY_ID: [
        {"label": "SD3.5", "model": "stable-diffusion-3.5", "cfg_scale": 5.0, "steps": 30},
        {"label": "Flux", "model": "flux-dev", "cfg_scale": 6.0, "steps": 30},
        {"label": "HiDream", "model": "hidream", "cfg_scale": 5.0, "steps": 30},
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
    def __init__(self, session, variant, prompt_text, hidden_suffix, author, user_has_role: bool):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.user_has_role = user_has_role

    async def generate_image(self, interaction: discord.Interaction, width: int, height: int):
        await interaction.response.defer(ephemeral=True)

        steps = self.variant["steps"]
        cfg = self.variant["cfg_scale"]
        progress_msg = await interaction.followup.send(f"⏳ Generating image... 0%", ephemeral=True)
        for i in range(1, 11):
            await asyncio.sleep(0.5 + steps * 0.02 + cfg * 0.04)
            try:
                await progress_msg.edit(content=f"⏳ Generating image... {i*10}%")
            except:
                pass

        full_prompt = self.prompt_text + self.hidden_suffix
        if full_prompt and not full_prompt[0].isalnum():
            full_prompt = " " + full_prompt

        img_bytes = await venice_generate(self.session, full_prompt, self.variant, width, height)
        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            self.stop()
            return

        filename = make_safe_filename(self.prompt_text)
        fp = io.BytesIO(img_bytes)
        fp.seek(0)
        discord_file = discord.File(fp, filename=filename)

        truncated_prompt = self.prompt_text.replace("\n\n", "\n")
        if len(truncated_prompt) > 500:
            truncated_prompt = truncated_prompt[:500] + "..."

        embed = discord.Embed(color=discord.Color.blurple())
        embed.add_field(name="🔮 Prompt:", value=truncated_prompt, inline=False)

        neg_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if neg_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.add_field(name="🚫 Negative Prompt:", value=neg_prompt, inline=False)

        technical_info = (
            f"{self.variant['model']} | "
            f"CFG: {self.variant['cfg_scale']} | "
            f"Steps: {self.variant['steps']}"
        )
        embed.add_field(name="📊 Technical Info:", value=technical_info, inline=False)

        embed.set_author(name=str(self.author), icon_url=self.author.display_avatar.url)
        today = datetime.now().strftime("%Y-%m-%d")
        guild = interaction.guild
        embed.set_footer(
            text=f"© {today} by {self.author}",
            icon_url=guild.icon.url if guild and guild.icon else None
        )

        msg = await interaction.channel.send(
            content=f"{self.author.mention}\n",
            embed=embed,
            files=[discord_file]
        )

        for emoji in CUSTOM_REACTIONS:
            try:
                await msg.add_reaction(emoji)
            except:
                pass

        # Post-generation buttons nur für Autor
        await msg.edit(view=PostGenerationView(self.session, self.variant, self.prompt_text,
                                               neg_prompt, self.hidden_suffix, self.author, self.user_has_role, msg))
        self.stop()

    # Aspect Ratio Buttons mit Rollencheck
    @discord.ui.button(label="⏹️1:1", style=discord.ButtonStyle.blurple)
    async def ratio_1_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.variant['aspect_ratio'] = "1:1"
        await self.generate_image(interaction, 1024, 1024)

    @discord.ui.button(label="🖥️16:9", style=discord.ButtonStyle.blurple)
    async def ratio_16_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.user_has_role:
            await interaction.response.send_message(
                f"You have to be at least Level 4 and inhabit the role <@&{ROLE_REQUIRED_ID}> to do this.",
                ephemeral=True
            )
            return
        self.variant['aspect_ratio'] = "16:9"
        await self.generate_image(interaction, 1024, 576)

    @discord.ui.button(label="📱9:16", style=discord.ButtonStyle.blurple)
    async def ratio_9_16(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.user_has_role:
            await interaction.response.send_message(
                f"You have to be at least Level 4 and inhabit the role <@&{ROLE_REQUIRED_ID}> to do this.",
                ephemeral=True
            )
            return
        self.variant['aspect_ratio'] = "9:16"
        await self.generate_image(interaction, 576, 1024)

# ---------------- Post-Generation Buttons ----------------
class PostGenerationView(discord.ui.View):
    def __init__(self, session, variant, prompt, neg_prompt, hidden_suffix, author, user_has_role, message: discord.Message):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt = prompt
        self.neg_prompt = neg_prompt
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.user_has_role = user_has_role
        self.message = message

        # ♻️ Re-use Prompt (grau)
        self.add_item(discord.ui.Button(
            label="Re-use Prompt",
            style=discord.ButtonStyle.gray,
            emoji="♻️",
            custom_id="reuse"
        ))

        # 🗑️ Delete (rot)
        self.add_item(discord.ui.Button(
            label="Delete",
            style=discord.ButtonStyle.red,
            emoji="🗑️",
            custom_id="delete"
        ))

        # 🧹 Delete & Re-use (rot)
        self.add_item(discord.ui.Button(
            label="Delete & Re-use",
            style=discord.ButtonStyle.red,
            emoji="🧹",
            custom_id="delete_reuse"
        ))


    @discord.ui.button(label="dummy", style=discord.ButtonStyle.gray, disabled=True)
    async def dummy(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # placeholder, wird nicht verwendet

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author.id

    async def on_timeout(self):
        self.clear_items()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        print(f"Error in PostGenerationView: {error}")

    async def on_button_click(self, interaction: discord.Interaction):
        pass  # placeholder

    async def button_callback(self, interaction: discord.Interaction):
        cid = interaction.data["custom_id"]
        if cid == "delete":
            await self.message.delete()
            await interaction.response.send_message("Deleted.", ephemeral=True)
        elif cid == "reuse":
            # Modal wieder öffnen mit ausgefüllten Werten
            modal = VeniceModal(self.session, self.variant, self.hidden_suffix, self.prompt, self.neg_prompt, self.user_has_role)
            await interaction.response.send_modal(modal)
        elif cid == "delete_reuse":
            await self.message.delete()
            modal = VeniceModal(self.session, self.variant, self.hidden_suffix, self.prompt, self.neg_prompt, self.user_has_role)
            await interaction.response.send_modal(modal)

# ---------------- Modal ----------------
class VeniceModal(discord.ui.Modal):
    def __init__(self, session: aiohttp.ClientSession, variant: dict, hidden_suffix: str, prompt_prefill="", neg_prefill="", user_has_role=True):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
        self.hidden_suffix = hidden_suffix
        self.user_has_role = user_has_role

        normal_cfg = CFG_REFERENCE[variant['model']]['cfg_scale']

        self.prompt = discord.ui.TextInput(
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            default=prompt_prefill
        )

        self.negative_prompt = discord.ui.TextInput(
            label="Negative Prompt (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300,
            default=neg_prefill
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

        variant = {**self.variant, "cfg_scale": cfg_value, "negative_prompt": self.negative_prompt.value or DEFAULT_NEGATIVE_PROMPT}
        user_has_role = any(r.id == ROLE_REQUIRED_ID for r in interaction.user.roles)

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(self.session, {**variant, "aspect_ratio":"N/A"}, self.prompt.value, self.hidden_suffix, interaction.user, user_has_role),
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
            user_has_role = any(r.id == ROLE_REQUIRED_ID for r in interaction.user.roles)

            await interaction.response.send_modal(VeniceModal(self.session, variant, hidden_suffix, user_has_role=user_has_role))
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

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.category and channel.category.id in VARIANT_MAP:
                    await self.ensure_button_message(channel)

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
