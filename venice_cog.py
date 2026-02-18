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
SPECIAL_ROLE_ID = 1375147276413964408

DEFAULT_NEGATIVE_PROMPT = "disfigured, missing fingers, extra limbs, watermark, underage"
NSFW_PROMPT_SUFFIX = " "
SFW_PROMPT_SUFFIX = " "

pepper = "<a:01pepper_icon:1377636862847619213>"

# ---------------- Model Labels ----------------
MODEL_LABELS = {
    "lustify-sdxl":    {"full_label": "🔥 Lustify", "button_icon": "🔥LF"},
    "venice-sd35":     {"full_label": "🚀 SD35", "button_icon": "🚀S3"},
    "wai-Illustrious": {"full_label": "🎨 Wai", "button_icon": "🎨WI"},
    "z-image-turbo":   {"full_label": "🌀 Z-Image", "button_icon": "🌀ZI"},
    "nano-banana-pro": {"full_label": "🍌 Nano Banana", "button_icon": "🍌NB"},
    "lustify-v7":      {"full_label": "⚡ Lustify V7", "button_icon": "⚡V7"},
    "hidream":         {"full_label": "🌙 HiDream", "button_icon": "🌙HD"},    
}

# ---------------- Model Config ----------------
CFG_REFERENCE = {
    "lustify-sdxl": {"cfg_scale": 5.0, "default_steps": 25, "max_steps": 50},
    "venice-sd35": {"cfg_scale": 6.0, "default_steps": 20, "max_steps": 30},
    "hidream": {"cfg_scale": 6.5, "default_steps": 20, "max_steps": 50},
    "wai-Illustrious": {"cfg_scale": 7.0, "default_steps": 20, "max_steps": 30},
    "lustify-v7": {"cfg_scale": 5.0, "default_steps": 20, "max_steps": 50},
    "z-image-turbo": {"cfg_scale": 6.0, "default_steps": 8, "max_steps": 8},
    "nano-banana-pro": {"cfg_scale": 5.0, "default_steps": 20, "max_steps": 50},
}

ROLE_LEVEL_LABELS = {
    VIP_ROLE_ID: "⭐*(Lvl 4)*",
    SPECIAL_ROLE_ID: "💎*(Lvl 11)*"
}

MODEL_ASPECTS = {
    "lustify-sdxl":    {"ratios": ["🟦1:1", "📺16:9", "📱9:16", "🖼️1:1 (Hi)"], "role_id": None},
    "venice-sd35":     {"ratios": ["🟦1:1", "📺16:9", "📱9:16", "🖼️1:1 (Hi)"], "role_id": None},
    "hidream":         {"ratios": ["🟦1:1", "📺16:9", "📱9:16", "🖼️1:1 (Hi)"], "role_id": SPECIAL_ROLE_ID},
    "wai-Illustrious": {"ratios": ["🟦1:1", "📺16:9", "📱9:16", "🖼️1:1 (Hi)"], "role_id": VIP_ROLE_ID},
    "lustify-v7":      {"ratios": ["🟦1:1", "📺16:9", "📱9:16", "🖼️1:1 (Hi)"], "role_id": SPECIAL_ROLE_ID},
    "z-image-turbo":   {"ratios": ["🟦1:1", "📺16:9", "📱9:16", "🖼️1:1 (Hi)"], "role_id": VIP_ROLE_ID},
    "nano-banana-pro": {"ratios": ["🟦1:1"], "role_id": VIP_ROLE_ID},
}

VARIANT_MAP = {
    **{ch: [{"model": m} for m in MODEL_LABELS] for ch in NSFW_CHANNELS},
    SFW_CHANNEL: [{"model": m} for m in MODEL_LABELS],
}

# ---------------- Helper ----------------
def make_safe_filename(prompt: str) -> str:
    base = "_".join(prompt.split()[:5]) or "image"
    base = re.sub(r"[^a-zA-Z0-9_]", "_", base)
    return f"{base}_{int(time.time_ns())}_{uuid.uuid4().hex[:8]}.png"

async def venice_generate(session, prompt, variant, width, height, steps, cfg_scale, negative_prompt):
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}"}
    payload = {
        "model": variant["model"],
        "prompt": prompt,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg_scale": cfg_scale,
        "negative_prompt": negative_prompt,
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
    def __init__(self, session, variant, hidden_suffix, is_vip=False, previous_inputs=None):
        super().__init__(title=f"Generate with {MODEL_LABELS[variant['model']]['full_label']}")
        self.session = session
        self.variant = variant
        self.hidden_suffix_value = hidden_suffix
        self.is_vip = is_vip
        previous_inputs = previous_inputs or {}

        self.prompt = discord.ui.TextInput(
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1500,
            default=previous_inputs.get("prompt", "")
        )
        neg_value = previous_inputs.get("negative_prompt", "") or DEFAULT_NEGATIVE_PROMPT
        self.negative_prompt = discord.ui.TextInput(
            label="Negative Prompt (optional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=500,
            default=neg_value
        )
        cfg_default = str(CFG_REFERENCE[variant["model"]]["cfg_scale"])
        self.cfg_value = discord.ui.TextInput(
            label="CFG (> stricter AI adherence)",
            style=discord.TextStyle.short,
            required=False,
            max_length=5,
            placeholder=cfg_default,
            default=previous_inputs.get("cfg_value", "")
        )
        max_steps = CFG_REFERENCE[variant["model"]]["max_steps"]
        default_steps = CFG_REFERENCE[variant["model"]]["default_steps"]
        prev_steps = previous_inputs.get("steps")
        self.steps_value = discord.ui.TextInput(
            label=f"Steps (1-{max_steps})",
            style=discord.TextStyle.short,
            required=False,
            max_length=3,
            placeholder=str(default_steps),
            default=str(prev_steps) if prev_steps else ""
        )
        prev_hidden = previous_inputs.get("hidden_suffix", "")
        self.hidden_suffix = discord.ui.TextInput(
            label="Hidden Suffix",
            style=discord.TextStyle.paragraph,
            required=False,
            placeholder=hidden_suffix[:100] if hidden_suffix else "",
            default=prev_hidden,
            max_length=800
        )

        for item in [self.prompt, self.negative_prompt, self.cfg_value, self.steps_value, self.hidden_suffix]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try: cfg_val = float(self.cfg_value.value)
        except: cfg_val = CFG_REFERENCE[self.variant["model"]]["cfg_scale"]
        try:
            steps_val = int(self.steps_value.value)
            steps_val = max(1, min(steps_val, CFG_REFERENCE[self.variant["model"]]["max_steps"]))
        except:
            steps_val = CFG_REFERENCE[self.variant['model']]['default_steps']
        negative_prompt = self.negative_prompt.value.strip() or DEFAULT_NEGATIVE_PROMPT
        user_hidden = self.hidden_suffix.value.strip() or self.hidden_suffix_value

        variant = {**self.variant, "cfg_scale": cfg_val, "negative_prompt": negative_prompt, "steps": steps_val}
        self.previous_inputs = {
            "prompt": self.prompt.value,
            "negative_prompt": negative_prompt,
            "cfg_value": self.cfg_value.value,
            "steps": steps_val if steps_val != CFG_REFERENCE[self.variant['model']]['default_steps'] else None,
            "hidden_suffix": user_hidden
        }

        channel_id = interaction.channel.id if interaction.channel else None
        hidden_suffix_default = NSFW_PROMPT_SUFFIX if channel_id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX
        await interaction.response.send_message(
            f"🎨 {MODEL_LABELS[variant['model']]['full_label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(
                self.session, variant, self.prompt.value, user_hidden, interaction.user,
                self.is_vip, channel_id=channel_id, previous_inputs=self.previous_inputs
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

        aspect_map = {
            "🟦1:1": (1024, 1024),
            "📺16:9": (1280, 816),
            "📱9:16": (816, 1280),
            "🖼️1:1 (Hi)": (1280, 1280)
        }

        for ratio_name, (w, h) in aspect_map.items():
            if ratio_name in MODEL_ASPECTS[self.variant["model"]]["ratios"]:
                role_needed = MODEL_ASPECTS[self.variant["model"]].get("role_id")
                btn = discord.ui.Button(label=ratio_name, style=discord.ButtonStyle.success)
                btn.callback = self.make_callback(w, h, ratio_name, role_needed)
                self.add_item(btn)

    def make_callback(self, width, height, ratio_name, role_id=None):
        async def callback(interaction: discord.Interaction):
            if role_id and not any(r.id == role_id for r in interaction.user.roles):
                await interaction.response.send_message(f"❌ You need <@&{role_id}> to use this aspect ratio!", ephemeral=True)
                return
            await self.generate_image(interaction, width, height, ratio_name)
        return callback

    async def generate_image(self, interaction, width, height, ratio_name):
        await interaction.response.defer(ephemeral=True)
        cfg = self.variant["cfg_scale"]
        steps = self.variant.get("steps", CFG_REFERENCE[self.variant["model"]]["default_steps"])

        progress_msg = await interaction.followup.send(f"{pepper} Generating image...", ephemeral=True)
        prompt_factor = len(self.prompt_text) / 1000
        for i in range(1, 11):
            await asyncio.sleep(0.9 + steps * 0.08 + cfg * 0.38 + prompt_factor * 0.9)
            try:
                await progress_msg.edit(content=f"{pepper} Generating image for **{self.author.display_name}** ... {i*10}%")
            except: pass

        full_prompt = (self.prompt_text or "") + (self.hidden_suffix or "")
        if full_prompt and not full_prompt[0].isalnum(): full_prompt = " " + full_prompt

        img_bytes = await venice_generate(
            self.session, full_prompt, self.variant, width, height, steps=steps, cfg_scale=cfg,
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
        tech_info = f"{MODEL_LABELS[self.variant['model']]['full_label']} | {width}x{height} | CFG: {cfg} | Steps: {steps}"
        embed.set_footer(text=tech_info, icon_url=guild_icon)

        msg = await interaction.channel.send(content=f"{self.author.mention}", embed=embed, file=discord_file)
        reactions = ["1️⃣", "2️⃣", "3️⃣", "<:011:1346549711817146400>", "<:011pump:1346549688836296787>"]
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

# ---------------- PostGenerationView ----------------
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

    async def interaction_check(self, interaction):
        return interaction.user.id == self.author.id

    async def reuse_callback(self, interaction):
        await self.show_reuse_models(interaction)

    async def delete_callback(self, interaction):
        try: await self.message.delete()
        except: pass
        await interaction.response.send_message("✅ Post deleted", ephemeral=True)

    async def delete_reuse_callback(self, interaction):
        try: await self.message.delete()
        except: pass
        await self.show_reuse_models(interaction)

    async def show_reuse_models(self, interaction):
        class ReuseModelSelect(discord.ui.Select):
            def __init__(self, session, channel_id, author, prompt_text, hidden_suffix):
                self.session = session
                self.channel_id = channel_id
                self.author = author
                self.prompt_text = prompt_text
                self.hidden_suffix = hidden_suffix

                options = []

                for variant in VARIANT_MAP.get(channel_id, []):
                    model = variant["model"]

                    role_needed = MODEL_ASPECTS[model].get("role_id")
                    level_label = ROLE_LEVEL_LABELS.get(role_needed, "")

                    label_text = MODEL_LABELS[model]["full_label"]
                    if level_label:
                        label_text = f"{label_text} {level_label}"

                    options.append(
                        discord.SelectOption(
                            label=label_text,
                            value=model
                        )
                    )

                super().__init__(
                    placeholder="♻️ Re-use with model...",
                    min_values=1,
                    max_values=1,
                    options=options
                )

            async def callback(self, interaction: discord.Interaction):
                model = self.values[0]
                role_needed = MODEL_ASPECTS[model]["role_id"]
                if role_needed and not any(r.id == role_needed for r in interaction.user.roles):
                    await interaction.response.send_message(f"❌ You need <@&{role_needed}> to use this model!", ephemeral=True)
                    return

                await interaction.response.send_modal(
                    VeniceModal(self.session, {"model": model}, self.hidden_suffix, previous_inputs={"prompt": self.prompt_text})
                )

        view = discord.ui.View()
        view.add_item(ReuseModelSelect(self.session, interaction.channel.id, self.author, self.prompt_text, self.hidden_suffix))
        await interaction.response.send_message("♻️ Choose model to re-use:", view=view, ephemeral=True)

# ---------------- ModelSelect ----------------
class ModelSelect(discord.ui.Select):
    def __init__(self, session, channel_id):
        self.session = session
        self.channel_id = channel_id

        options = []
        for variant in VARIANT_MAP.get(channel_id, []):
            model = variant["model"]

            role_needed = MODEL_ASPECTS[model].get("role_id")
            level_label = ROLE_LEVEL_LABELS.get(role_needed, "")

            label_text = MODEL_LABELS[model]["full_label"]
            if level_label:
                label_text = f"{label_text} {level_label}"

            options.append(
                discord.SelectOption(
                    label=label_text,
                    value=model
                )
            )


        super().__init__(
            placeholder="🎨 Choose your model...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        model = self.values[0]
        role_needed = MODEL_ASPECTS[model]["role_id"]

        if role_needed and not any(r.id == role_needed for r in interaction.user.roles):
            await interaction.response.send_message(
                f"❌ You need <@&{role_needed}> to use this model!",
                ephemeral=True
            )
            return

        hidden_suffix = NSFW_PROMPT_SUFFIX if interaction.channel.id in NSFW_CHANNELS else SFW_PROMPT_SUFFIX

        await interaction.response.send_modal(
            VeniceModal(self.session, {"model": model}, hidden_suffix)
        )

class VeniceView(discord.ui.View):
    def __init__(self, session, channel):
        super().__init__(timeout=None)
        self.add_item(ModelSelect(session, channel.id))

# ---------------- Cog ----------------
class VeniceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        await self.session.close()

    async def ensure_button_message(self, channel):
        async for msg in channel.history(limit=10):
            if msg.components and not msg.embeds and not msg.attachments:
                await msg.delete()

        await channel.send(
            "💡 Choose Model for 🖼️ NEW image!",
            view=VeniceView(self.session, channel)
        )

    @staticmethod
    async def ensure_button_message_static(channel, session):
        async for msg in channel.history(limit=10):
            if msg.components and not msg.embeds and not msg.attachments:
                await msg.delete()

        await channel.send(
            "💡 Choose Model for 🖼️ NEW image!",
            view=VeniceView(session, channel)
        )

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.id in VARIANT_MAP:
                    await self.ensure_button_message(channel)

async def setup(bot):
    await bot.add_cog(VeniceCog(bot))
