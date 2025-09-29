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
PIPER_SUFFIX = "20 years old girl. Pale white skin with freckles. She has red bangs and green eyes. She wears green headphones with black auricles. Her mouth has a slight overbite. Shwe is completely nude. Her skin is oily and wet. We see her whole nude and skinny body. She has perky little tits with small errects niplles. She has a freshly shaved anf puffy and wet vagina. She is naked except her black Doc Martens Boots. ,Areola wrinkles,pointy nipples,small nipples,pink nipples"
POPPY_SUFFIX = "This is Poppy dummy suffix"

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
    def __init__(self, session, variant, is_vip, previous_inputs=None):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
        self.is_vip = is_vip
        previous_inputs = previous_inputs if previous_inputs is not None else {}

        # Prompt
        self.prompt = discord.ui.TextInput(
            label="Main Prompt",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            default=previous_inputs.get("prompt", "")
        )

        # Piper
        self.piper = discord.ui.TextInput(
            label="Piper (at least one of Piper/Poppy required)",
            style=discord.TextStyle.short,
            required=False,
            max_length=300,
            default=previous_inputs.get("piper", "")
        )

        # Poppy
        self.poppy = discord.ui.TextInput(
            label="Poppy",
            style=discord.TextStyle.short,
            required=False,
            max_length=300,
            default=previous_inputs.get("poppy", "")
        )

        # Negative
        neg_value = previous_inputs.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        self.negative_prompt = discord.ui.TextInput(
            label="Negative Prompt (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300,
            default=neg_value
        )

        # CFG
        cfg_default = str(CFG_REFERENCE[variant["model"]]["cfg_scale"])
        self.cfg_value = discord.ui.TextInput(
            label="CFG",
            style=discord.TextStyle.short,
            required=False,
            placeholder=cfg_default,
            default=previous_inputs.get("cfg_value", "")
        )

        # Steps
        max_steps = CFG_REFERENCE[variant["model"]]["max_steps"]
        default_steps = CFG_REFERENCE[variant["model"]]["default_steps"]
        prev_steps = previous_inputs.get("steps")
        self.steps_value = discord.ui.TextInput(
            label=f"Steps (1-{max_steps})",
            style=discord.TextStyle.short,
            required=False,
            placeholder=str(default_steps),
            default=str(prev_steps) if prev_steps else ""
        )

        # add items
        self.add_item(self.prompt)
        self.add_item(self.piper)
        self.add_item(self.poppy)
        self.add_item(self.negative_prompt)
        self.add_item(self.cfg_value)
        self.add_item(self.steps_value)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.piper.value.strip() and not self.poppy.value.strip():
            await interaction.response.send_message("❌ You must fill at least Piper or Poppy.", ephemeral=True)
            return

        try:
            cfg_val = float(self.cfg_value.value)
        except:
            cfg_val = CFG_REFERENCE[self.variant["model"]]["cfg_scale"]

        try:
            steps_val = int(self.steps_value.value)
            steps_val = max(1, min(steps_val, CFG_REFERENCE[self.variant["model"]]["max_steps"]))
        except:
            steps_val = CFG_REFERENCE[self.variant['model']]['default_steps']

        negative_prompt = self.negative_prompt.value or DEFAULT_NEGATIVE_PROMPT

        # build suffixes
        suffix_parts = []
        if self.piper.value.strip():
            suffix_parts.append(PIPER_SUFFIX)
        if self.poppy.value.strip():
            suffix_parts.append(POPPY_SUFFIX)
        full_prompt = self.prompt.value + " " + " ".join(suffix_parts)

        variant = {
            **self.variant,
            "cfg_scale": cfg_val,
            "negative_prompt": negative_prompt,
            "steps": steps_val
        }

        self.previous_inputs = {
            "prompt": self.prompt.value,
            "piper": self.piper.value,
            "poppy": self.poppy.value,
            "negative_prompt": negative_prompt,
            "cfg_value": self.cfg_value.value,
            "steps": steps_val,
        }

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(
                self.session,
                variant,
                full_prompt,
                interaction.user,
                self.is_vip,
                previous_inputs=self.previous_inputs
            ),
            ephemeral=True
        )

# ---------------- AspectRatioView ----------------
class AspectRatioView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, author, is_vip, previous_inputs=None):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.author = author
        self.is_vip = is_vip
        self.previous_inputs = previous_inputs or {}

        btn_1_1 = discord.ui.Button(label="⏹️1:1", style=discord.ButtonStyle.success)
        btn_16_9 = discord.ui.Button(label="🖥️16:9", style=discord.ButtonStyle.success)
        btn_9_16 = discord.ui.Button(label="📱9:16", style=discord.ButtonStyle.success)
        btn_hi = discord.ui.Button(label="🟥1:1⚡", style=discord.ButtonStyle.success)

        btn_1_1.callback = self.make_callback(1024, 1024, "1:1")
        btn_16_9.callback = self.make_callback(1280, 816, "16:9")
        btn_9_16.callback = self.make_callback(816, 1280, "9:16")
        btn_hi.callback = self.make_special_callback(1280, 1280, "1:1 Hi-Res", SPECIAL_ROLE_ID)

        for b in [btn_1_1, btn_16_9, btn_9_16, btn_hi]:
            self.add_item(b)

    def make_callback(self, width, height, ratio_name):
        async def callback(interaction: discord.Interaction):
            if not self.is_vip and ratio_name in ["16:9", "9:16"]:
                await interaction.response.send_message(
                    f"❌ You need <@&{VIP_ROLE_ID}> to use this option",
                    ephemeral=True
                )
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
        await interaction.response.defer(ephemeral=True)
        cfg = self.variant["cfg_scale"]
        steps = self.variant.get("steps", CFG_REFERENCE[self.variant["model"]]["default_steps"])

        progress_msg = await interaction.followup.send(f"{pepper} Generating image... starting", ephemeral=True)
        prompt_factor = len(self.prompt_text) / 1000
        for i in range(1, 6):
            await asyncio.sleep(0.7 + steps * 0.02 + cfg * 0.2 + prompt_factor * 0.5)
            try:
                await progress_msg.edit(content=f"{pepper} Generating image... {i*20}%")
            except:
                pass

        img_bytes = await venice_generate(
            self.session, self.prompt_text, self.variant, width, height,
            steps=self.variant.get("steps"), cfg_scale=cfg,
            negative_prompt=self.variant.get("negative_prompt")
        )

        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            self.stop()
            return

        fp = io.BytesIO(img_bytes)
        fp.seek(0)
        discord_file = discord.File(fp, filename=make_safe_filename(self.prompt_text))

        today = datetime.now().strftime("%Y-%m-%d")
        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=f"{self.author.display_name} ({today})", icon_url=self.author.display_avatar.url)
        truncated_prompt = (self.prompt_text or "").replace("\n\n", "\n")
        if len(truncated_prompt) > 500:
            truncated_prompt = truncated_prompt[:500] + " [...]"
        embed.description = f"🔮 Prompt:\n{truncated_prompt}"

        neg_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if neg_prompt and neg_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.description += f"\n\n🚫 Negative Prompt:\n{neg_prompt}"

        embed.set_image(url=f"attachment://{discord_file.filename}")
        guild_icon = interaction.guild.icon.url if interaction.guild.icon else None

        MODEL_SHORT = {
            "lustify-sdxl": "lustify",
            "flux-dev-uncensored": "flux-unc",
            "venice-sd35": "sd35",
            "hidream": "hidreams",
            "wai-Illustrious": "wai"
        }
        short_model_name = MODEL_SHORT.get(self.variant['model'], self.variant['model'])
        tech_info = f"{short_model_name} | {width}x{height} | CFG: {cfg} | Steps: {self.variant.get('steps', CFG_REFERENCE[self.variant['model']]['default_steps'])}"
        embed.set_footer(text=tech_info, icon_url=guild_icon)

        msg = await interaction.channel.send(content=f"{self.author.mention}", embed=embed, file=discord_file)

        await interaction.followup.send(
            content=f"🚨{interaction.user.mention}, re-use & edit your prompt?",
            view=PostGenerationView(self.session, self.variant, self.prompt_text, self.author, msg, previous_inputs=self.previous_inputs),
            ephemeral=True
        )
        self.stop()

# ---------------- Post Generation View ----------------
class PostGenerationView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, author, message, previous_inputs=None):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.author = author
        self.message = message
        self.previous_inputs = previous_inputs or {}

        reuse_btn = discord.ui.Button(label="♻️ Re-use Prompt", style=discord.ButtonStyle.success)
        reuse_btn.callback = self.reuse_callback
        self.add_item(reuse_btn)

        del_btn = discord.ui.Button(label="🗑️ Delete", style=discord.ButtonStyle.red)
        del_btn.callback = self.delete_callback
        self.add_item(del_btn)

        submit_btn = discord.ui.Button(
            label="Submit image to contest🏆",
            style=discord.ButtonStyle.blurple,
            row=1
        )
        submit_btn.callback = self.post_gallery_callback
        self.add_item(submit_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author.id

    async def reuse_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(VeniceModal(
            self.session,
            self.variant,
            is_vip=any(r.id == VIP_ROLE_ID for r in interaction.user.roles),
            previous_inputs=self.previous_inputs
        ))

    async def delete_callback(self, interaction: discord.Interaction):
        try: await self.message.delete()
        except: pass
        await interaction.response.send_message("✅ Post deleted", ephemeral=True)

    async def post_gallery_callback(self, interaction: discord.Interaction):
        channel_id = 1418956422086922320
        role_id = 1419024270201454684
        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message("❌ Gallery channel not found!", ephemeral=True)
            return

        embed = None
        if self.message.embeds:
            original_embed = self.message.embeds[0]
            embed = discord.Embed.from_dict(original_embed.to_dict())
            embed.description = f"[View original post]({self.message.jump_url})"
            if original_embed.footer:
                embed.set_footer(text=original_embed.footer.text, icon_url=original_embed.footer.icon_url)

        mention_text = f"🎖️<@&{role_id}> {self.author.mention} has submitted an image to the contest!"
        await channel.send(content=mention_text, embed=embed)

        await interaction.response.send_message("✅ Submitted to contest.", ephemeral=True)

# ---------------- Buttons View ----------------
class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession):
        super().__init__(timeout=None)
        for variant in ALL_VARIANTS:
            btn = discord.ui.Button(label=variant["label"], style=discord.ButtonStyle.blurple,
                                   custom_id=f"model_{variant['model']}_{uuid.uuid4().hex}")
            btn.callback = self.make_callback(variant)
            self.add_item(btn)
        self.session = session

    def make_callback(self, variant):
        async def callback(interaction: discord.Interaction):
            member = interaction.user
            is_vip = any(r.id == VIP_ROLE_ID for r in member.roles)
            await interaction.response.send_modal(VeniceModal(self.session, variant, is_vip))
        return callback

# ---------------- Cog ----------------
class VenicePiperPoppyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    async def ensure_button_message(self, channel: discord.TextChannel):
        async for msg in channel.history(limit=10):
            if msg.components and not msg.embeds and not msg.attachments:
                try: await msg.delete()
                except: pass
        view = VeniceView(self.session)
        await channel.send("💡 Choose Model for 🖼️**NEW** image!", view=view)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            channel = guild.get_channel(TARGET_CHANNEL_ID)
            if channel:
                await self.ensure_button_message(channel)

## ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
