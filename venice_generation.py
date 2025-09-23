# venice_generation.py
import os
import re
import io
import uuid
import time
import asyncio
import aiohttp
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load environment
load_dotenv()
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env!")

VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"

# ---------------- Constants ----------------
NSFW_CATEGORY_ID = 1415769711052062820
SFW_CATEGORY_ID = 1416461717038170294
VIP_ROLE_ID = 1377051179615522926

DEFAULT_NEGATIVE_PROMPT = "lores, bad anatomy, missing fingers, extra limbs, watermark"
NSFW_PROMPT_SUFFIX = " (NSFW, show explicit details)"
SFW_PROMPT_SUFFIX = " (SFW, no explicit details)"

CFG_REFERENCE = {
    "lustify-sdxl": {"cfg_scale": 6.0, "steps": 30},
    "pony-realism": {"cfg_scale": 8.5, "steps": 30},
    "flux-dev-uncensored": {"cfg_scale": 6.0, "steps": 30},
    "stable-diffusion-3.5": {"cfg_scale": 6.0, "steps": 30},
    "flux-dev": {"cfg_scale": 6.5, "steps": 30},
    "hidream": {"cfg_scale": 6.5, "steps": 30},
    "wai-Illustrious": {"cfg_scale": 8.0, "steps": 30},
}

VARIANT_MAP = {
    NSFW_CATEGORY_ID: [
        {"label": "Lustify", "model": "lustify-sdxl"},
        {"label": "Pony", "model": "pony-realism"},
        {"label": "FluxUnc", "model": "flux-dev-uncensored"},
        {"label": "Anime", "model": "wai-Illustrious"},
    ],
    SFW_CATEGORY_ID: [
        {"label": "SD3.5", "model": "stable-diffusion-3.5"},
        {"label": "Flux", "model": "flux-dev"},
        {"label": "HiDream", "model": "hidream"},
    ]
}

CUSTOM_REACTIONS = [
    "<:01sthumb:1387086056498921614>",
    "<:01smile_piper:1387083454575022213>",
    "<:02No:1347536448831754383>",
    "<:011:1346549711817146400>",
    "<:011pump:1346549688836296787>",
]

CHANNEL_REACTIONS = {
    1418956422086922320: ["1️⃣", "2️⃣", "3️⃣"],
    1418956422086922321: ["1️⃣", "2️⃣", "3️⃣"]
}

# ---------------- Helpers ----------------
def make_safe_filename(prompt: str) -> str:
    base = "_".join((prompt or "image").split()[:5])
    base = re.sub(r"[^a-zA-Z0-9_]", "_", base) or "image"
    if not base[0].isalnum():
        base = "img_" + base
    return f"{base}_{int(time.time_ns())}_{uuid.uuid4().hex[:8]}.png"


async def venice_generate(
    session: aiohttp.ClientSession,
    prompt: str,
    variant: dict,
    width: int,
    height: int,
    steps=None,
    cfg_scale=None,
    negative_prompt=None,
) -> bytes | None:
    """
    Calls Venice API and returns binary image bytes, or None on error.
    """
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": variant["model"],
        "prompt": prompt,
        "width": width,
        "height": height,
        "steps": steps or CFG_REFERENCE.get(variant["model"], {}).get("steps", 30),
        "cfg_scale": cfg_scale or CFG_REFERENCE.get(variant["model"], {}).get("cfg_scale", 6.0),
        "negative_prompt": negative_prompt or DEFAULT_NEGATIVE_PROMPT,
        "safe_mode": False,
        "hide_watermark": True,
        "return_binary": True,
    }
    try:
        async with session.post(VENICE_IMAGE_URL, headers=headers, json=payload) as resp:
            if resp.status != 200:
                # log & return None
                print(f"Venice API Error {resp.status}: {await resp.text()}")
                return None
            return await resp.read()
    except Exception as e:
        print(f"Exception calling Venice API: {e}")
        return None


def _safe_truncate_prompt(prompt: str, max_chars: int = 500) -> str:
    if not prompt:
        return "ℹ️ (No prompt provided)"
    s = prompt.replace("\n\n", "\n")
    if len(s) > max_chars:
        s = s[:max_chars] + " [...]"
    if not s.strip():
        return "ℹ️ (No prompt provided)"
    return s


def safe_embed(author: discord.abc.User, prompt_text: str, neg_prompt: str, filename: str, guild_icon: str, variant: dict, cfg: float, steps: int) -> discord.Embed:
    """
    Build an embed safely: ensure non-empty description, truncate long prompts,
    attach file via attachment://filename (so Discord renders the image in the embed).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    embed = discord.Embed(color=discord.Color.blurple())
    try:
        embed.set_author(name=f"{author.display_name} ({today})", icon_url=author.display_avatar.url)
    except Exception:
        embed.set_author(name=f"{getattr(author, 'name', 'User')} ({today})")

    truncated_prompt = _safe_truncate_prompt(prompt_text, max_chars=500)
    embed.description = f"🔮 Prompt:\n{truncated_prompt}"

    if neg_prompt and neg_prompt != DEFAULT_NEGATIVE_PROMPT:
        embed.description += f"\n\n🚫 Negative Prompt:\n{neg_prompt}"

    # set image via attachment://filename; if this fails, we fallback later when sending
    try:
        embed.set_image(url=f"attachment://{filename}")
    except Exception:
        pass

    tech_info = f"{variant['model']} | CFG: {cfg} | Steps: {steps}"
    try:
        if guild_icon:
            embed.set_footer(text=tech_info, icon_url=guild_icon)
        else:
            embed.set_footer(text=tech_info)
    except Exception:
        embed.set_footer(text=tech_info)

    return embed


# ---------------- Modal ----------------
class VeniceModal(discord.ui.Modal):
    def __init__(self, session: aiohttp.ClientSession, variant: dict, hidden_suffix: str, is_vip: bool, previous_inputs: dict | None = None):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
        self.hidden_suffix = hidden_suffix
        self.is_vip = is_vip
        previous_inputs = previous_inputs or {}

        prompt_value = previous_inputs.get("prompt", "")
        self.prompt = discord.ui.TextInput(
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            default=prompt_value,
            placeholder="Describe your image. Be creative for best results!" if not prompt_value else None,
        )

        neg_value = previous_inputs.get("negative_prompt", "")
        if neg_value:
            if not neg_value.startswith(DEFAULT_NEGATIVE_PROMPT):
                neg_value = DEFAULT_NEGATIVE_PROMPT + ", " + neg_value
        else:
            neg_value = DEFAULT_NEGATIVE_PROMPT

        self.negative_prompt = discord.ui.TextInput(
            label="Negative Prompt (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300,
            default=neg_value,
            placeholder=None,
        )

        cfg_default = str(CFG_REFERENCE.get(variant["model"], {}).get("cfg_scale", 6.0))
        self.cfg_value = discord.ui.TextInput(
            label="CFG (> stricter AI adherence)",
            style=discord.TextStyle.short,
            required=False,
            placeholder=cfg_default,
        )

        self.add_item(self.prompt)
        self.add_item(self.negative_prompt)
        self.add_item(self.cfg_value)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cfg_val = float(self.cfg_value.value) if self.cfg_value.value else CFG_REFERENCE.get(self.variant["model"], {}).get("cfg_scale", 6.0)
        except Exception:
            cfg_val = CFG_REFERENCE.get(self.variant["model"], {}).get("cfg_scale", 6.0)

        negative_prompt = self.negative_prompt.value.strip()
        if negative_prompt:
            if not negative_prompt.startswith(DEFAULT_NEGATIVE_PROMPT):
                negative_prompt = DEFAULT_NEGATIVE_PROMPT + ", " + negative_prompt
        else:
            negative_prompt = DEFAULT_NEGATIVE_PROMPT

        variant = {
            **self.variant,
            "cfg_scale": cfg_val,
            "negative_prompt": negative_prompt,
        }

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(
                self.session,
                variant,
                self.prompt.value,
                self.hidden_suffix,
                interaction.user,
                self.is_vip,
            ),
            ephemeral=True,
        )


# ---------------- Aspect Ratio View ----------------
class AspectRatioView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, variant: dict, prompt_text: str, hidden_suffix: str, author: discord.abc.User, is_vip: bool):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.is_vip = is_vip

        btn_1_1 = discord.ui.Button(label="⏹️1:1", style=discord.ButtonStyle.success)
        btn_16_9 = discord.ui.Button(label="🖥️16:9", style=discord.ButtonStyle.success)
        btn_9_16 = discord.ui.Button(label="📱9:16", style=discord.ButtonStyle.success)

        btn_1_1.callback = self.make_callback(1024, 1024, "1:1")
        btn_16_9.callback = self.make_callback(1280, 816, "16:9")
        btn_9_16.callback = self.make_callback(816, 1280, "9:16")

        self.add_item(btn_1_1)
        self.add_item(btn_16_9)
        self.add_item(btn_9_16)

    def make_callback(self, width: int, height: int, ratio_name: str):
        async def callback(interaction: discord.Interaction):
            await self.generate_image(interaction, width, height, ratio_name)
        return callback

    async def generate_image(self, interaction: discord.Interaction, width: int, height: int, ratio_name: str):
        if not self.is_vip and ratio_name in ["16:9", "9:16"]:
            await interaction.response.send_message(
                f"❌ You need <@&{VIP_ROLE_ID}> to use this aspect ratio! 1:1 works for all.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        cfg = self.variant.get("cfg_scale", CFG_REFERENCE.get(self.variant["model"], {}).get("cfg_scale", 6.0))
        steps = self.variant.get("steps", CFG_REFERENCE.get(self.variant["model"], {}).get("steps", 30))

        progress_msg = await interaction.followup.send("⏳ Generating image... 0%", ephemeral=True)

        prompt_factor = len(self.prompt_text or "") / 1000
        for i in range(1, 11):
            await asyncio.sleep(0.9 + steps * 0.04 + float(cfg) * 0.25 + prompt_factor * 0.9)
            try:
                await progress_msg.edit(content=f"⏳ Generating image... {i * 10}%")
            except Exception:
                pass

        full_prompt = (self.prompt_text or "") + (self.hidden_suffix or "")
        if full_prompt and not full_prompt[0].isalnum():
            full_prompt = " " + full_prompt

        img_bytes = await venice_generate(
            self.session,
            full_prompt,
            self.variant,
            width,
            height,
            steps=self.variant.get("steps", steps),
            cfg_scale=cfg,
            negative_prompt=self.variant.get("negative_prompt"),
        )

        if not img_bytes:
            await interaction.followup.send("❌ Generation failed!", ephemeral=True)
            # Try to refresh control cog's button message if available
            try:
                control_cog = interaction.client.get_cog("VeniceControlCog")
                if control_cog and hasattr(control_cog, "ensure_button_message_static"):
                    await control_cog.ensure_button_message_static(interaction.channel, control_cog.session)
            except Exception:
                pass
            self.stop()
            return

        filename = make_safe_filename(self.prompt_text or "image")
        fp = io.BytesIO(img_bytes)
        fp.seek(0)
        discord_file = discord.File(fp, filename=filename)

        guild_icon = None
        try:
            if interaction.guild and interaction.guild.icon:
                guild_icon = interaction.guild.icon.url
        except Exception:
            guild_icon = None

        embed = safe_embed(self.author, self.prompt_text or "", self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT), filename, guild_icon, self.variant, cfg, self.variant.get("steps", steps))

        # Send embed+file; if embed posting fails, fallback to file-only
        try:
            msg = await interaction.channel.send(content=f"{self.author.mention}", embed=embed, file=discord_file)
        except Exception:
            try:
                msg = await interaction.channel.send(content=f"{self.author.mention}", file=discord_file)
            except Exception:
                await interaction.followup.send("❌ Failed to post the generated image to the channel.", ephemeral=True)
                self.stop()
                return

        # Add reactions
        reactions = CHANNEL_REACTIONS.get(interaction.channel.id, CUSTOM_REACTIONS)
        for emoji in reactions:
            try:
                await msg.add_reaction(emoji)
            except Exception:
                pass

        # Post-generation ephemeral controls
        try:
            await interaction.followup.send(
                content=f"🚨{interaction.user.mention}, would you like to use your prompts again? You can tweak them, if you like...",
                view=PostGenerationView(self.session, self.variant, self.prompt_text, self.hidden_suffix, self.author, msg),
                ephemeral=True,
            )
        except Exception:
            pass

        # Refresh control cog's button message
        try:
            control_cog = interaction.client.get_cog("VeniceControlCog")
            if control_cog and hasattr(control_cog, "ensure_button_message_static"):
                await control_cog.ensure_button_message_static(interaction.channel, control_cog.session)
        except Exception:
            pass

        self.stop()


# ---------------- Post Generation View ----------------
class PostGenerationView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, variant: dict, prompt_text: str, hidden_suffix: str, author: discord.abc.User, message: discord.Message):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.message = message

        reuse_btn = discord.ui.Button(label="♻️ Re-use Prompt", style=discord.ButtonStyle.success)
        reuse_btn.callback = self.reuse_callback
        self.add_item(reuse_btn)

        del_btn = discord.ui.Button(label="🗑️ Delete", style=discord.ButtonStyle.red)
        del_btn.callback = self.delete_callback
        self.add_item(del_btn)

        del_reuse_btn = discord.ui.Button(label="🧹 Delete & Re-use", style=discord.ButtonStyle.red)
        del_reuse_btn.callback = self.delete_reuse_callback
        self.add_item(del_reuse_btn)

        try:
            if message.channel and message.channel.category and message.channel.category.id == SFW_CATEGORY_ID:
                submit_btn = discord.ui.Button(label="🏆🖼️ Submit for competition", style=discord.ButtonStyle.secondary, row=1)
                submit_btn.callback = self.post_gallery_callback
                self.add_item(submit_btn)
        except Exception:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author.id

    async def reuse_callback(self, interaction: discord.Interaction):
        await self.show_reuse_models(interaction)

    async def delete_callback(self, interaction: discord.Interaction):
        try:
            await self.message.delete()
        except Exception:
            pass
        await interaction.response.send_message("✅ Post deleted", ephemeral=True)

    async def delete_reuse_callback(self, interaction: discord.Interaction):
        try:
            await self.message.delete()
        except Exception:
            pass
        await self.show_reuse_models(interaction)

    async def post_gallery_callback(self, interaction: discord.Interaction):
        channel_id = 1418956422086922320  # Contest channel
        role_id = 1419024270201454684
        channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
        if not channel:
            await interaction.response.send_message("❌ Gallery channel not found!", ephemeral=True)
            return

        files = []
        for attachment in self.message.attachments:
            fp = io.BytesIO()
            await attachment.save(fp)
            fp.seek(0)
            files.append(discord.File(fp, filename=attachment.filename))

        embed = None
        if self.message.embeds:
            try:
                original_embed = self.message.embeds[0]
                embed = discord.Embed.from_dict(original_embed.to_dict())
                full_prompt = (self.prompt_text or "").replace("\n\n", "\n")
                if len(full_prompt) > 2000:
                    full_prompt = full_prompt[:2000] + " [...]"
                embed.description = f"🔮 Prompt:\n{full_prompt}"
                neg_prompt = self.variant.get("negative_prompt")
                if neg_prompt and neg_prompt != DEFAULT_NEGATIVE_PROMPT:
                    embed.description += f"\n\n🚫 Negative Prompt:\n{neg_prompt}"
            except Exception:
                embed = None

        mention_text = f"<@&{role_id}> {self.author.mention} has submitted an image to the contest!"
        try:
            contest_msg = await channel.send(content=mention_text, embed=embed, files=files)
        except Exception:
            try:
                contest_msg = await channel.send(content=mention_text, files=files)
            except Exception:
                await interaction.response.send_message("❌ Failed to submit to contest.", ephemeral=True)
                return

        for emoji in ["1️⃣", "2️⃣", "3️⃣"]:
            try:
                await contest_msg.add_reaction(emoji)
            except Exception:
                pass

        for child in self.children:
            if getattr(child, "label", "") and "Submit" in getattr(child, "label", ""):
                child.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            try:
                await interaction.followup.send("✅ Submitted to contest.", ephemeral=True)
            except Exception:
                pass

    async def show_reuse_models(self, interaction: discord.Interaction):
        member = interaction.user
        is_vip = any(r.id == VIP_ROLE_ID for r in getattr(member, "roles", []))

        class ReuseModelView(discord.ui.View):
            def __init__(self, session: aiohttp.ClientSession, author: discord.abc.User, prompt_text: str, hidden_suffix: str, variant: dict):
                super().__init__(timeout=None)
                self.session = session
                self.author = author
                self.prompt_text = prompt_text
                self.hidden_suffix = hidden_suffix
                self.variant = variant

                category_id = interaction.channel.category.id if interaction.channel and interaction.channel.category else None
                variants = VARIANT_MAP.get(category_id, [])

                for v in variants:
                    btn = discord.ui.Button(label=v["label"], style=discord.ButtonStyle.success)
                    btn.callback = self.make_model_callback(v)
                    self.add_item(btn)

            def make_model_callback(self, variant):
                async def callback(inner_interaction: discord.Interaction):
                    member = inner_interaction.user
                    is_vip = any(r.id == VIP_ROLE_ID for r in getattr(member, "roles", []))
                    if not is_vip and variant["model"] not in ["lustify-sdxl", "stable-diffusion-3.5"]:
                        await inner_interaction.response.send_message(
                            f"❌ You need <@&{VIP_ROLE_ID}> to use this model!", ephemeral=True
                        )
                        return

                    await inner_interaction.response.send_modal(
                        VeniceModal(
                            self.session,
                            variant,
                            self.hidden_suffix,
                            is_vip=is_vip,
                            previous_inputs={"prompt": self.prompt_text, "negative_prompt": self.variant.get("negative_prompt", "")},
                        )
                    )
                return callback

        try:
            await interaction.response.send_message(
                f"{interaction.user.mention}, which model do you want to use with your re-used prompt?",
                view=ReuseModelView(self.session, interaction.user, self.prompt_text, self.hidden_suffix, self.variant),
                ephemeral=True,
            )
        except Exception:
            try:
                await interaction.followup.send(
                    f"{interaction.user.mention}, which model do you want to use with your re-used prompt?",
                    ephemeral=True,
                )
            except Exception:
                pass


# ---------------- Cog ----------------
class VeniceGenerationCog(commands.Cog):
    """Cog that contains all generation logic, modals and views."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        # expose modal class to control cog via instance attribute
        self.VeniceModal = VeniceModal
        self.VARIANT_MAP = VARIANT_MAP
        self.CFG_REFERENCE = CFG_REFERENCE
        self.DEFAULT_NEGATIVE_PROMPT = DEFAULT_NEGATIVE_PROMPT

    def cog_unload(self):
        asyncio.create_task(self.session.close())


# Setup function for extension loading
async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceGenerationCog(bot))
