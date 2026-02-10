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
SPECIAL_ROLE_ID = 1375147276413964408  # High-Res
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
        previous_inputs = previous_inputs if previous_inputs else {}

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
        neg_value = previous_inputs.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        self.negative_prompt = discord.ui.TextInput(
            label="Negative Prompt (optional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=500,
            default=neg_value
        )

        # CFG
        cfg_default = str(CFG_REFERENCE[variant["model"]]["cfg_scale"])
        self.cfg_value = discord.ui.TextInput(
            label="CFG (> stricter AI adherence)",
            style=discord.TextStyle.short,
            required=False,
            max_length=5,
            placeholder=cfg_default,
            default=previous_inputs.get("cfg_value", "")
        )

        # Steps
        max_steps = CFG_REFERENCE[variant["model"]]["max_steps"]
        default_steps = CFG_REFERENCE[variant["model"]]["default_steps"]
        previous_steps = previous_inputs.get("steps")
        self.steps_value = discord.ui.TextInput(
            label=f"Steps (1-{max_steps})",
            style=discord.TextStyle.short,
            required=False,
            max_length=3,
            placeholder=str(default_steps),
            default=str(previous_steps) if previous_steps else ""
        )

        # Hidden suffix
        prev_hidden = previous_inputs.get("hidden_suffix", "")
        self.hidden_suffix = discord.ui.TextInput(
            label="Hidden Suffix",
            style=discord.TextStyle.paragraph,
            required=False,
            placeholder=hidden_suffix_default[:100],
            default=prev_hidden,
            max_length=800
        )

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
            steps_val = CFG_REFERENCE[self.variant["model"]]["default_steps"]

        negative_prompt = (self.negative_prompt.value or "").strip()
        if not negative_prompt:
            negative_prompt = DEFAULT_NEGATIVE_PROMPT

        user_hidden = (self.hidden_suffix.value or "").strip()
        hidden_to_use = user_hidden or self.hidden_suffix_value
        stored_hidden_for_reuse = user_hidden if user_hidden else None

        variant = {
            **self.variant,
            "cfg_scale": cfg_val,
            "negative_prompt": negative_prompt,
            "steps": steps_val
        }

        previous_inputs = {
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
                channel_id=channel_id,
                previous_inputs=previous_inputs
            ),
            ephemeral=True
        )

# ---------------- AspectRatioView ----------------
class AspectRatioView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, hidden_suffix, author, channel_id=None, previous_inputs=None):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.channel_id = channel_id
        self.previous_inputs = previous_inputs or {}

        buttons = []

        # nur Grok/Nano-Banana = 1 Button 1:1
        if variant["model"] in ["grok-imagine", "nano-banana-pro"]:
            btn_1_1 = discord.ui.Button(label="⏹️1:1", style=discord.ButtonStyle.success)
            btn_1_1.callback = self.make_callback(1280, 1280, "1:1")
            buttons.append(btn_1_1)
        else:
            btn_1_1 = discord.ui.Button(label="⏹️1:1", style=discord.ButtonStyle.success)
            btn_16_9 = discord.ui.Button(label="🖥️16:9", style=discord.ButtonStyle.success)
            btn_9_16 = discord.ui.Button(label="📱9:16", style=discord.ButtonStyle.success)
            btn_hi = discord.ui.Button(label="🟥1:1⚡", style=discord.ButtonStyle.success)

            btn_1_1.callback = self.make_callback(1024, 1024, "1:1")
            btn_16_9.callback = self.make_callback(1280, 816, "16:9")
            btn_9_16.callback = self.make_callback(816, 1280, "9:16")
            btn_hi.callback = self.make_special_callback(1280, 1280, "1:1 Hi-Res", SPECIAL_ROLE_ID)
            buttons.extend([btn_1_1, btn_16_9, btn_9_16, btn_hi])

        for b in buttons:
            self.add_item(b)

    def make_callback(self, width, height, ratio_name):
        async def callback(interaction: discord.Interaction):
            await self.generate_image(interaction, width, height, ratio_name)
        return callback

    def make_special_callback(self, width, height, ratio_name, role_id):
        async def callback(interaction: discord.Interaction):
            if not any(r.id == role_id for r in interaction.user.roles):
                await interaction.response.send_message(f"❌ You need <@&{role_id}> for this high-res option!", ephemeral=True)
                return
            await self.generate_image(interaction, width, height, ratio_name)
        return callback

    async def generate_image(self, interaction: discord.Interaction, width, height, ratio_name):
        await interaction.response.defer(ephemeral=True)
        cfg = self.variant["cfg_scale"]
        steps = self.variant.get("steps", CFG_REFERENCE[self.variant["model"]]["default_steps"])

        progress_msg = await interaction.followup.send(f"{pepper} Generating image...", ephemeral=True)
        prompt_factor = len(self.prompt_text) / 1000
        for i in range(1, 11):
            await asyncio.sleep(0.9 + steps*0.08 + cfg*0.38 + prompt_factor*0.9)
            try:
                progress_text = f"{pepper} Generating image for **{self.author.display_name}** with **{self.variant['label']}** ... {i*10}%"
                await progress_msg.edit(content=progress_text)
            except: pass

        full_prompt = (self.prompt_text or "") + (self.hidden_suffix or "")
        if full_prompt and not full_prompt[0].isalnum(): full_prompt = " " + full_prompt

        img_bytes = await venice_generate(
            self.session, full_prompt, self.variant, width, height,
            steps=steps, cfg_scale=cfg, negative_prompt=self.variant.get("negative_prompt")
        )

        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            if isinstance(interaction.channel, discord.TextChannel):
                await VeniceCog.ensure_dropdown_message_static(interaction.channel, self.session)
            self.stop()
            return

        fp = io.BytesIO(img_bytes)
        fp.seek(0)
        discord_file = discord.File(fp, filename=make_safe_filename(self.prompt_text))

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=f"{self.author.display_name}", icon_url=self.author.display_avatar.url)
        truncated_prompt = (self.prompt_text or "").replace("\n\n", "\n")
        if len(truncated_prompt) > 600: truncated_prompt = truncated_prompt[:600] + " [...]"
        embed.description = f"🔮 Prompt:\n{truncated_prompt}"

        # Hidden prompt marker
        prev_hidden_marker = self.previous_inputs.get("hidden_suffix", None)
        default_hidden_suffix = NSFW_PROMPT_SUFFIX if self.channel_id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX
        if isinstance(prev_hidden_marker, str) and prev_hidden_marker != "" and prev_hidden_marker != default_hidden_suffix:
            embed.description += "\n\n🔒 Hidden Prompt"

        neg_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if neg_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.description += f"\n\n🚫 Negative Prompt:\n{neg_prompt}"

        embed.set_image(url=f"attachment://{discord_file.filename}")

        msg = await interaction.channel.send(content=f"{self.author.mention}", embed=embed, file=discord_file)
        reactions = ["1️⃣","2️⃣","3️⃣","<:011:1346549711817146400>","<:011pump:1346549688836296787>"]
        for emoji in reactions:
            try: await msg.add_reaction(emoji)
            except: pass

        # Post Generation View mit Dropdown
        await interaction.followup.send(
            content=f"🚨{interaction.user.mention}, re-use & edit your prompt?",
            view=PostGenerationView(self.session, self.variant, self.prompt_text, self.hidden_suffix, self.author, msg, previous_inputs=self.previous_inputs),
            ephemeral=True
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.ensure_dropdown_message_static(interaction.channel, self.session)
        self.stop()

# ---------------- Post Generation View ----------------
class PostGenerationView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, hidden_suffix, author, message, previous_inputs=None):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.message = message
        self.previous_inputs = previous_inputs or {}

        reuse_btn = discord.ui.Button(label="♻️ Re-use Prompt", style=discord.ButtonStyle.success)
        reuse_btn.callback = self.reuse_callback
        self.add_item(reuse_btn)

        del_btn = discord.ui.Button(label="🗑️ Delete", style=discord.ButtonStyle.red)
        del_btn.callback = self.delete_callback
        self.add_item(del_btn)

        del_reuse_btn = discord.ui.Button(label="🧹 Delete & Re-use", style=discord.ButtonStyle.red)
        del_reuse_btn.callback = self.delete_reuse_callback
        self.add_item(del_reuse_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author.id

    async def reuse_callback(self, interaction: discord.Interaction):
        await self.show_reuse_models(interaction)

    async def delete_callback(self, interaction: discord.Interaction):
        try: await self.message.delete()
        except: pass
        await interaction.response.send_message("✅ Post deleted", ephemeral=True)

    async def delete_reuse_callback(self, interaction: discord.Interaction):
        try: await self.message.delete()
        except: pass
        await self.show_reuse_models(interaction)

    async def show_reuse_models(self, interaction: discord.Interaction):
        member = interaction.user
        is_vip = any(r.id == VIP_ROLE_ID for r in member.roles)
        hidden_marker = self.previous_inputs.get("hidden_suffix", None)
        view = ReuseModelDropdownView(self.session, interaction.user, self.prompt_text, hidden_marker, interaction.channel)
        await interaction.response.send_message(f"{interaction.user.mention}, choose model for reuse:", view=view, ephemeral=True)

# ---------------- Dropdown View ----------------
class ReuseModelDropdownView(discord.ui.View):
    def __init__(self, session, user, prompt_text, hidden_suffix, channel):
        super().__init__(timeout=None)
        self.session = session
        self.user = user
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.channel = channel

        variants = VARIANT_MAP.get(channel.id, [])

        class DynamicDropdown(discord.ui.Select):
            def __init__(self_inner):
                options = []
                for v in variants:
                    # Rollenprüfung
                    if v["model"] == "lustify-sdxl":
                        allowed = True
                    elif v["model"] in ["flux-dev-uncensored","flux-dev","venice-sd35","hidream","grok-imagine","nano-banana-pro","wai-Illustrious","lustify-v7"]:
                        allowed = True
                    else:
                        allowed = False
                    if allowed:
                        options.append(discord.SelectOption(label=v["label"], value=v["model"]))
                super().__init__(placeholder="Select Model", options=options, min_values=1, max_values=1)

            async def callback(self_inner, interaction: discord.Interaction):
                model_name = self_inner.values[0]
                variant = next((v for vlist in VARIANT_MAP.values() for v in vlist if v["model"] == model_name), None)
                if not variant:
                    await interaction.response.send_message("❌ Model not found", ephemeral=True)
                    return
                hidden_suffix_to_use = self.hidden_suffix or (NSFW_PROMPT_SUFFIX if channel.id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX)
                await interaction.response.send_modal(VeniceModal(session, variant, hidden_suffix_to_use, False))

        self.add_item(DynamicDropdown())

# ---------------- Cog ----------------
class VeniceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    @staticmethod
    async def ensure_dropdown_message_static(channel, session):
        # Alte Dropdowns löschen
        async for msg in channel.history(limit=None):  # Alle Nachrichten durchsuchen
            if msg.components:
                for row in msg.components:
                    # row.children enthält die Buttons/Selects
                    for comp in getattr(row, "children", []):
                        if getattr(comp, "type", None) == 3:  # Select Menu
                            try:
                                await msg.delete()
                            except:
                                pass

        # Neue Dropdown Nachricht posten
        variants = VARIANT_MAP.get(channel.id, [])
        if not variants:
            return

        view = ReuseModelDropdownView(session, None, "", None, channel)
        await channel.send("💡 Choose Model for 🖼️ **NEW** image!", view=view)




    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.id in NSFW_CHANNELS + [SFW_CHANNEL]:
                    await self.ensure_dropdown_message_static(channel, self.session)

# ---------------- Bot ----------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    cog = VeniceCog(bot)
    bot.add_cog(cog)

bot.run(os.getenv("DISCORD_TOKEN"))
