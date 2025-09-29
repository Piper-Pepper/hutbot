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

TARGET_CHANNEL_ID = 1422144220214329375
VIP_ROLE_ID = 1377051179615522926
SPECIAL_ROLE_ID = 1375147276413964408

DEFAULT_NEGATIVE_PROMPT = "lores, bad anatomy, missing fingers, extra limbs, watermark"
POPPY_SUFFIX = "Poppy:(18years old woman. Pale super-white gothic skin. Black pigtails and blazing blue eyes. She has many piercings, especially her firm C-Cup breast whit smallh nipples and areola are always pierced. Her clitoris is pierced as well. She has tattoos. She is just 4 feet tall.)"

pepper = "<a:01pepper_icon:1377636862847619213>"

# ---------------- Model Config ----------------
CFG_REFERENCE = {
    "lustify-sdxl": {"cfg_scale": 6.0, "default_steps": 25, "max_steps": 50},
    "flux-dev-uncensored": {"cfg_scale": 6.0, "default_steps": 25, "max_steps": 30},
    "venice-sd35": {"cfg_scale": 6.0, "default_steps": 25, "max_steps": 30},
    "hidream": {"cfg_scale": 6.5, "default_steps": 25, "max_steps": 50},
    "wai-Illustrious": {"cfg_scale": 8.0, "default_steps": 25, "max_steps": 30},
}

ALL_VARIANTS = [
    {"label": "Lustify", "model": "lustify-sdxl"},
    {"label": "FluxUnc", "model": "flux-dev-uncensored"},
    {"label": "Venice SD35", "model": "venice-sd35"},
    {"label": "HiDream", "model": "hidream"},
    {"label": "Wai (Anime)", "model": "wai-Illustrious"},
]

# ---------------- Helper ----------------
def make_safe_filename(prompt: str) -> str:
    base = "_".join(prompt.split()[:5]) or "image"
    base = re.sub(r"[^a-zA-Z0-9_]", "_", base)
    if not base or not base[0].isalnum():
        base = "img_" + base
    return f"{base}_{int(time.time_ns())}_{uuid.uuid4().hex[:8]}.png"

async def venice_generate(session: aiohttp.ClientSession, prompt: str, variant: dict, width: int, height: int, steps=None, cfg_scale=None, negative_prompt=None) -> bytes | None:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": variant["model"],
        "prompt": prompt,
        "width": width,
        "height": height,
        "steps": steps or CFG_REFERENCE[variant["model"]]["default_steps"],
        "cfg_scale": cfg_scale or CFG_REFERENCE[variant["model"]]["cfg_scale"],
        "negative_prompt": negative_prompt or DEFAULT_NEGATIVE_PROMPT,
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

# ---------------- Modal ----------------
class VeniceModal(discord.ui.Modal):
    def __init__(self, session, previous_inputs=None):
        super().__init__(title="Enter Prompt")
        self.session = session
        previous_inputs = previous_inputs or {}

        self.main_prompt = discord.ui.TextInput(
            label="Main Prompt (required)",
            style=discord.TextStyle.short,
            required=True,
            max_length=300,
            default=previous_inputs.get("main_prompt", ""),
            placeholder="Enter your prompt..."
        )

        self.negative_prompt = discord.ui.TextInput(
            label="Negative Prompt (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300,
            default=previous_inputs.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        )

        self.cfg_value = discord.ui.TextInput(
            label="CFG",
            style=discord.TextStyle.short,
            required=False,
            placeholder="6.0",
            default=previous_inputs.get("cfg_value", "")
        )

        self.steps_value = discord.ui.TextInput(
            label="Steps",
            style=discord.TextStyle.short,
            required=False,
            placeholder="25",
            default=str(previous_inputs.get("steps", ""))
        )

        self.add_item(self.main_prompt)
        self.add_item(self.negative_prompt)
        self.add_item(self.cfg_value)
        self.add_item(self.steps_value)

    async def on_submit(self, interaction: discord.Interaction):
        cfg_val = float(self.cfg_value.value) if self.cfg_value.value else 6.0
        steps_val = int(self.steps_value.value) if self.steps_value.value else 25

        steps_val = max(1, min(steps_val, 50))
        negative_prompt = self.negative_prompt.value or DEFAULT_NEGATIVE_PROMPT

        full_prompt = self.main_prompt.value + " " + POPPY_SUFFIX

        previous_inputs = {
            "main_prompt": self.main_prompt.value,
            "negative_prompt": negative_prompt,
            "cfg_value": self.cfg_value.value,
            "steps": steps_val,
        }

        # zeigt KI-Modell Auswahl
        await interaction.response.send_message(
            f"🎨 Choose AI Model:",
            view=VeniceView(self.session, full_prompt, interaction.user, previous_inputs, is_reuse=True),
            ephemeral=True
        )

# ---------------- Buttons View ----------------
class VeniceView(discord.ui.View):
    def __init__(self, session, prompt_text, author, previous_inputs=None, is_reuse=False):
        super().__init__(timeout=None)
        self.session = session
        self.prompt_text = prompt_text
        self.author = author
        self.previous_inputs = previous_inputs or {}
        self.is_reuse = is_reuse

        for variant in ALL_VARIANTS:
            btn = discord.ui.Button(label=variant["label"], style=discord.ButtonStyle.blurple,
                                   custom_id=f"model_{variant['model']}_{uuid.uuid4().hex}")
            btn.callback = self.make_callback(variant)
            self.add_item(btn)

    def make_callback(self, variant):
        async def callback(interaction: discord.Interaction):
            # geht weiter zu Aspect Ratio
            cfg_val = float(self.previous_inputs.get("cfg_value") or CFG_REFERENCE[variant["model"]]["cfg_scale"])
            steps_val = int(self.previous_inputs.get("steps") or CFG_REFERENCE[variant["model"]]["default_steps"])
            negative_prompt = self.previous_inputs.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT

            variant_data = {**variant, "cfg_scale": cfg_val, "steps": steps_val, "negative_prompt": negative_prompt}

            await interaction.response.send_message(
                f"🎨 {variant['label']} ready! Choose an aspect ratio:",
                view=AspectRatioView(self.session, variant_data, self.prompt_text, interaction.user),
                ephemeral=True
            )
        return callback

# ---------------- AspectRatioView ----------------
class AspectRatioView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, author):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.author = author

        btn_1_1 = discord.ui.Button(label="⏹️1:1", style=discord.ButtonStyle.success)
        btn_16_9 = discord.ui.Button(label="🖥️16:9", style=discord.ButtonStyle.success)
        btn_9_16 = discord.ui.Button(label="📱9:16", style=discord.ButtonStyle.success)
        btn_hi = discord.ui.Button(label="🟥1:1⚡", style=discord.ButtonStyle.success)

        btn_1_1.callback = self.make_callback(1024, 1024)
        btn_16_9.callback = self.make_callback(1280, 816, requires_vip=True)
        btn_9_16.callback = self.make_callback(816, 1280, requires_vip=True)
        btn_hi.callback = self.make_callback(1280, 1280, requires_special=True)

        for b in [btn_1_1, btn_16_9, btn_9_16, btn_hi]:
            self.add_item(b)

        # immer Submit to contest
        submit_btn = discord.ui.Button(label="Submit image to contest🏆", style=discord.ButtonStyle.blurple)
        submit_btn.callback = self.submit_contest
        self.add_item(submit_btn)

    def make_callback(self, width, height, requires_vip=False, requires_special=False):
        async def callback(interaction: discord.Interaction):
            if requires_vip and not any(r.id == VIP_ROLE_ID for r in interaction.user.roles):
                await interaction.response.send_message(f"❌ You need VIP to use this option", ephemeral=True)
                return
            if requires_special and not any(r.id == SPECIAL_ROLE_ID for r in interaction.user.roles):
                await interaction.response.send_message(f"❌ You need Special role to use this option", ephemeral=True)
                return
            await self.generate_image(interaction, width, height)
        return callback

    async def generate_image(self, interaction: discord.Interaction, width: int, height: int):
        await interaction.response.defer(ephemeral=True)
        cfg = self.variant["cfg_scale"]
        steps = self.variant.get("steps", 25)

        progress_msg = await interaction.followup.send(f"{pepper} Generating image... starting", ephemeral=True)
        for i in range(1, 6):
            await asyncio.sleep(0.7 + steps * 0.02 + cfg * 0.2)
            try:
                await progress_msg.edit(content=f"{pepper} Generating image... {i*20}%")
            except: pass

        img_bytes = await venice_generate(self.session, self.prompt_text, self.variant, width, height,
                                         steps=self.variant.get("steps"), cfg_scale=cfg,
                                         negative_prompt=self.variant.get("negative_prompt"))
        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            self.stop()
            return

        fp = io.BytesIO(img_bytes)
        fp.seek(0)
        discord_file = discord.File(fp, filename=make_safe_filename(self.prompt_text))

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=f"{self.author.display_name} ({datetime.now().strftime('%Y-%m-%d')})",
                         icon_url=self.author.display_avatar.url)
        embed.description = f"🔮 Prompt:\n{self.prompt_text}"

        embed.set_image(url=f"attachment://{discord_file.filename}")

        msg = await interaction.channel.send(content=f"{self.author.mention}", embed=embed, file=discord_file)
        await interaction.followup.send("✅ Image generated!", ephemeral=True)

    async def submit_contest(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Submitted to contest (placeholder)", ephemeral=True)

# ---------------- Cog ----------------
class VeniceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    @commands.Cog.listener()
    async def on_ready(self):
        channel = self.bot.get_channel(TARGET_CHANNEL_ID)
        if channel:
            await channel.send("💡 Click below to generate a new image!", view=VeniceModalView(self.session))

# ---------------- Initial Modal View ----------------
class VeniceModalView(discord.ui.View):
    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session
        btn = discord.ui.Button(label="Generate Image", style=discord.ButtonStyle.blurple)
        btn.callback = self.open_modal
        self.add_item(btn)

    async def open_modal(self, interaction: discord.Interaction):
        await interaction.response.send_modal(VeniceModal(self.session))

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
