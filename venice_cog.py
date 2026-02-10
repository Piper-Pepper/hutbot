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

# ---------------- Channels & Roles ----------------
NSFW_CHANNELS = [
    1415769909874524262,
    1415769966573260970,
    1416267309399670917,
    1416267383160442901,
    1416468498305126522,
    1346843244067160074
]
SFW_CHANNEL = 1461752750550552741

VIP_ROLE_ID = 1377051179615522926
SPECIAL_ROLE_ID = 1375147276413964408  # für High-Res Button

DEFAULT_NEGATIVE_PROMPT = "disfigured, missing fingers, extra limbs, watermark, underage"
NSFW_PROMPT_SUFFIX = " "
SFW_PROMPT_SUFFIX = " "

pepper = "<a:01pepper_icon:1377636862847619213>"

# ---------------- Model Config ----------------
CFG_REFERENCE = {
    "lustify-sdxl": {"cfg_scale": 6.0, "default_steps": 25, "max_steps": 50},
    "venice-sd35": {"cfg_scale": 6.0, "default_steps": 22, "max_steps": 30},
    "hidream": {"cfg_scale": 6.5, "default_steps": 25, "max_steps": 50},
    "wai-Illustrious": {"cfg_scale": 8.0, "default_steps": 22, "max_steps": 30},
    "lustify-v7": {"cfg_scale": 6.0, "default_steps": 30, "max_steps": 50},
    "grok-imagine": {"cfg_scale": 6.0, "default_steps": 25, "max_steps": 50},
    "nano-banana-pro": {"cfg_scale": 6.0, "default_steps": 25, "max_steps": 50},
}

# ---------------- Aspect Ratio Mapping ----------------
MODEL_ASPECTS = {
    "grok-imagine": ["1:1"],  # nur normale 1:1
    "lustify-sdxl": ["1:1","16:9","9:16","1:1 Hi-Res"],
    "venice-sd35": ["1:1","16:9","9:16","1:1 Hi-Res"],
    "hidream": ["1:1","16:9","9:16","1:1 Hi-Res"],
    "wai-Illustrious": ["1:1","16:9","9:16","1:1 Hi-Res"],
    "lustify-v7": ["1:1","16:9","9:16","1:1 Hi-Res"],
    "nano-banana-pro": ["1:1","16:9","9:16","1:1 Hi-Res"],
}

VARIANT_MAP = {
    **{ch: [
        {"label": "Lustify🔞", "model": "lustify-sdxl"},
        {"label": "SD35", "model": "venice-sd35"},
        {"label": "Wai🔞", "model": "wai-Illustrious"},
        {"label": "Lustify V7🔞", "model": "lustify-v7"},
        {"label": "HiDream", "model": "hidream"},
        {"label": "Grok", "model": "grok-imagine"},
        {"label": "NB Pro", "model": "nano-banana-pro"},
    ] for ch in NSFW_CHANNELS},
    SFW_CHANNEL: [
        {"label": "Lustify🔞", "model": "lustify-sdxl"},
        {"label": "SD35", "model": "venice-sd35"},
        {"label": "Wai🔞", "model": "wai-Illustrious"},
        {"label": "Lustify V7🔞", "model": "lustify-v7"},
        {"label": "HiDream", "model": "hidream"},
        {"label": "Grok", "model": "grok-imagine"},
        {"label": "NB Pro", "model": "nano-banana-pro"},
    ]
}

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
    def __init__(self, session, variant, hidden_suffix_default, is_vip, previous_inputs=None):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
        self.hidden_suffix_value = hidden_suffix_default
        self.is_vip = is_vip
        previous_inputs = previous_inputs if previous_inputs is not None else {}

        self._had_previous_hidden = "hidden_suffix" in previous_inputs

        # Prompt
        self.prompt = discord.ui.TextInput(
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1500,
            default=previous_inputs.get("prompt", "")
        )

        # Negative prompt
        neg_value = previous_inputs.get("negative_prompt", None)
        if neg_value is None or (isinstance(neg_value, str) and neg_value.strip() == ""):
            neg_value = DEFAULT_NEGATIVE_PROMPT
        self.negative_prompt = discord.ui.TextInput(
            label="Negative Prompt (optional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=500,
            default=neg_value
        )

        # CFG
        cfg_default = str(CFG_REFERENCE[variant["model"]]["cfg_scale"])
        cfg_placeholder = cfg_default[:100]
        self.cfg_value = discord.ui.TextInput(
            label="CFG (> stricter AI adherence)",
            style=discord.TextStyle.short,
            required=False,
            max_length=2,
            placeholder=cfg_placeholder,
            default=previous_inputs.get("cfg_value", "")
        )

        # Steps
        max_steps = CFG_REFERENCE[variant["model"]]["max_steps"]
        default_steps = CFG_REFERENCE[variant["model"]]["default_steps"]
        previous_steps = previous_inputs.get("steps")
        steps_placeholder = str(default_steps)[:100]
        self.steps_value = discord.ui.TextInput(
            label=f"Steps (1-{max_steps})",
            style=discord.TextStyle.short,
            required=False,
            max_length=2,
            placeholder=steps_placeholder,
            default=str(previous_steps) if previous_steps is not None and previous_steps != default_steps else ""
        )

        # Hidden suffix
        prev_hidden = previous_inputs.get("hidden_suffix", None)
        if prev_hidden and prev_hidden.strip():
            default_value = prev_hidden
            placeholder_value = ""
        else:
            default_value = ""
            placeholder_value = hidden_suffix_default[:100] if hidden_suffix_default else ""

        self.hidden_suffix = discord.ui.TextInput(
            label="Hidden Suffix",
            style=discord.TextStyle.paragraph,
            required=False,
            placeholder=placeholder_value,
            default=default_value,
            max_length=800
        )

        # Add items
        self.add_item(self.prompt)
        self.add_item(self.negative_prompt)
        self.add_item(self.cfg_value)
        self.add_item(self.steps_value)
        self.add_item(self.hidden_suffix)

    async def on_submit(self, interaction: discord.Interaction):
        try: cfg_val = float(self.cfg_value.value)
        except: cfg_val = CFG_REFERENCE[self.variant["model"]]["cfg_scale"]

        try:
            steps_val = int(self.steps_value.value)
            steps_val = max(1, min(steps_val, CFG_REFERENCE[self.variant["model"]]["max_steps"]))
        except:
            steps_val = CFG_REFERENCE[self.variant['model']]['default_steps']

        negative_prompt = (self.negative_prompt.value or "").strip()
        if not negative_prompt:
            negative_prompt = DEFAULT_NEGATIVE_PROMPT

        user_hidden = (self.hidden_suffix.value or "").strip()
        if user_hidden:
            hidden_to_use = user_hidden
            stored_hidden_for_reuse = user_hidden
        else:
            hidden_to_use = self.hidden_suffix_value
            stored_hidden_for_reuse = None

        variant = {
            **self.variant,
            "cfg_scale": cfg_val,
            "negative_prompt": negative_prompt,
            "steps": steps_val
        }

        self.previous_inputs = {
            "prompt": self.prompt.value,
            "negative_prompt": negative_prompt,
            "cfg_value": self.cfg_value.value,
            "steps": steps_val if steps_val != CFG_REFERENCE[self.variant['model']]['default_steps'] else None,
            "hidden_suffix": stored_hidden_for_reuse
        }

        channel_id = interaction.channel.id if interaction.channel else None
        hidden_suffix_default = NSFW_PROMPT_SUFFIX if channel_id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(
                self.session,
                variant,
                self.prompt.value,
                hidden_to_use,
                interaction.user,
                self.is_vip,
                channel_id=channel_id,
                previous_inputs=self.previous_inputs
            ),
            ephemeral=True
        )

# ---------------- AspectRatioView ----------------
class AspectRatioView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, hidden_suffix, author, is_vip, channel_id=None, previous_inputs=None):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.is_vip = is_vip
        self.channel_id = channel_id
        self.previous_inputs = previous_inputs or {}

        for ratio in MODEL_ASPECTS.get(self.variant['model'], []):
            if ratio == "1:1":
                btn = discord.ui.Button(label="⏹️1:1", style=discord.ButtonStyle.success)
                btn.callback = self.make_callback(1024, 1024, "1:1")
            elif ratio == "16:9":
                btn = discord.ui.Button(label="🖥️16:9", style=discord.ButtonStyle.success)
                btn.callback = self.make_callback(1280, 816, "16:9")
            elif ratio == "9:16":
                btn = discord.ui.Button(label="📱9:16", style=discord.ButtonStyle.success)
                btn.callback = self.make_callback(816, 1280, "9:16")
            elif ratio == "1:1 Hi-Res":
                btn = discord.ui.Button(label="🟥1:1⚡", style=discord.ButtonStyle.success)
                btn.callback = self.make_special_callback(1280, 1280, "1:1 Hi-Res", SPECIAL_ROLE_ID)
            self.add_item(btn)

    def make_callback(self, width, height, ratio_name):
        async def callback(interaction: discord.Interaction):
            if not self.is_vip and ratio_name in ["16:9", "9:16"]:
                await interaction.response.send_message(f"❌ You need <@&{VIP_ROLE_ID}> to use this option", ephemeral=True)
                return
            await self.generate_image(interaction, width, height, ratio_name)
        return callback

    def make_special_callback(self, width, height, ratio_name, role_id):
        async def callback(interaction: discord.Interaction):
            if not any(r.id == role_id for r in interaction.user.roles):
                await interaction.response.send_message(f"❌ You need <@&{role_id}> to use this high-res option!", ephemeral=True)
                return
            await self.generate_image(interaction, width, height, ratio_name)
        return callback

    async def generate_image(self, interaction: discord.Interaction, width: int, height: int, ratio_name: str):
        # --- (Der Rest des Codes bleibt unverändert, genauso wie in deinem Original) ---
        await interaction.response.defer(ephemeral=True)
        cfg = self.variant["cfg_scale"]
        steps = self.variant.get("steps", CFG_REFERENCE[self.variant['model']]['default_steps'])

        progress_msg = await interaction.followup.send(f"{pepper} Generating image... starting", ephemeral=True)
        prompt_factor = len(self.prompt_text) / 1000
        for i in range(1, 11):
            await asyncio.sleep(0.9 + steps * 0.08 + cfg * 0.38 + prompt_factor * 0.9)
            try:
                progress_text = f"{pepper} Generating image for **{self.author.display_name}** with **{self.variant['label']}** ... {i*10}%"
                await progress_msg.edit(content=progress_text)
            except: pass

        full_prompt = (self.prompt_text or "") + (self.hidden_suffix or "")
        if full_prompt and not full_prompt[0].isalnum():
            full_prompt = " " + full_prompt

        img_bytes = await venice_generate(
            self.session, full_prompt, self.variant, width, height,
            steps=self.variant.get("steps"), cfg_scale=cfg,
            negative_prompt=self.variant.get("negative_prompt")
        )

        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            if isinstance(interaction.channel, discord.TextChannel):
                await VeniceCog.ensure_button_message_static(interaction.channel, self.session)
            self.stop()
            return

        fp = io.BytesIO(img_bytes)
        fp.seek(0)
        discord_file = discord.File(fp, filename=make_safe_filename(self.prompt_text))

        today = datetime.now().strftime("%Y-%m-%d")
        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=f"{self.author.display_name} ({today})", icon_url=self.author.display_avatar.url)
        truncated_prompt = (self.prompt_text or "").replace("\n\n", "\n")
        if len(truncated_prompt) > 600: truncated_prompt = truncated_prompt[:600] + " [...]"
        embed.description = f"🔮 Prompt:\n{truncated_prompt}"

        default_hidden_suffix = NSFW_PROMPT_SUFFIX if self.channel_id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX
        prev_hidden_marker = self.previous_inputs.get("hidden_suffix", None)
        if isinstance(prev_hidden_marker, str) and prev_hidden_marker != "" and prev_hidden_marker != default_hidden_suffix:
            embed.description += "\n\n🔒 Hidden Prompt"

        neg_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if neg_prompt and neg_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.description += f"\n\n🚫 Negative Prompt:\n{neg_prompt}"

        embed.set_image(url=f"attachment://{discord_file.filename}")
        guild_icon = interaction.guild.icon.url if interaction.guild.icon else None
        MODEL_SHORT = {
            "lustify-sdxl": "lustify",
            "flux-dev-uncensored": "flux-unc",
            "venice-sd35": "sd35",
            "flux-dev": "flux",
            "hidream": "hidreams",
            "wai-Illustrious": "wai"
        }
        short_model_name = MODEL_SHORT.get(self.variant['model'], self.variant['model'])
        tech_info = f"{short_model_name} | {width}x{height} | CFG: {cfg} | Steps: {self.variant.get('steps', CFG_REFERENCE[self.variant['model']]['default_steps'])}"
        embed.set_footer(text=tech_info, icon_url=guild_icon)

        msg = await interaction.channel.send(content=f"{self.author.mention}", embed=embed, file=discord_file)
        reactions = ["1️⃣","2️⃣","3️⃣","<:011:1346549711817146400>","<:011pump:1346549688836296787>"]
        for emoji in reactions:
            try: await msg.add_reaction(emoji)
            except: pass

        await interaction.followup.send(
            content=f"🚨{interaction.user.mention}, re-use & edit your prompt?",
            view=PostGenerationView(self.session, self.variant, self.prompt_text, self.hidden_suffix, self.author, msg, previous_inputs=self.previous_inputs),
            ephemeral=True
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.ensure_button_message_static(interaction.channel, self.session)
        self.stop()

# ---------------- Modell-Auswahl als Drop-Down ----------------
class ModelSelect(discord.ui.Select):
    def __init__(self, session, channel_id, author):
        options = []
        variants = VARIANT_MAP.get(channel_id, [])
        for v in variants:
            options.append(discord.SelectOption(label=v["label"], value=v["model"]))
        super().__init__(placeholder="Choose a model...", min_values=1, max_values=1, options=options)
        self.session = session
        self.channel_id = channel_id
        self.author = author

    async def callback(self, interaction: discord.Interaction):
        model_name = self.values[0]
        variants = VARIANT_MAP.get(self.channel_id, [])
        variant = next((v for v in variants if v["model"] == model_name), None)
        if not variant:
            await interaction.response.send_message("❌ Model not found!", ephemeral=True)
            return
        is_vip = any(r.id == VIP_ROLE_ID for r in interaction.user.roles)
        hidden_suffix = NSFW_PROMPT_SUFFIX if self.channel_id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX
        await interaction.response.send_modal(VeniceModal(self.session, variant, hidden_suffix, is_vip))

class ModelSelectView(discord.ui.View):
    def __init__(self, session, channel_id, author):
        super().__init__(timeout=None)
        self.add_item(ModelSelect(session, channel_id, author))

# ---------------- Cog ----------------
class VeniceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sessions = {}

    @staticmethod
    async def ensure_button_message_static(channel: discord.TextChannel, session: aiohttp.ClientSession):
        # send model dropdown only once
        async for m in channel.history(limit=20):
            if m.author == channel.guild.me and m.embeds:
                return m
        view = ModelSelectView(session, channel.id, None)
        return await channel.send("💡 Choose Model for 🖼️**NEW** image!", view=view)

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.bot.user} VeniceCog loaded")

