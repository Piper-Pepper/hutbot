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
VIP_ROLE_ID = 1377051179615522926
SPECIAL_ROLE_ID = 1375147276413964408  # für High-Res Button

DEFAULT_NEGATIVE_PROMPT = "disfigured, missing fingers, extra limbs, watermark, underage"
NSFW_PROMPT_SUFFIX = " "
SFW_PROMPT_SUFFIX = " "

pepper = "<a:01pepper_icon:1377636862847619213>"

# ---------------- Model Config ----------------
CFG_REFERENCE = {
    "lustify-sdxl": {"cfg_scale": 6.0, "default_steps": 25, "max_steps": 50},
    "flux-dev-unc": {"cfg_scale": 6.0, "default_steps": 20, "max_steps": 30},
    "venice-sd35": {"cfg_scale": 6.0, "default_steps": 20, "max_steps": 30},
    "flux-dev": {"cfg_scale": 6.5, "default_steps": 20, "max_steps": 30},
    "hidream": {"cfg_scale": 6.5, "default_steps": 25, "max_steps": 50},
    "wai-Illustrious": {"cfg_scale": 8.0, "default_steps": 20, "max_steps": 30},
}

VARIANT_MAP = {
    NSFW_CATEGORY_ID: [
        {"label": "Lustify🔞", "model": "lustify-sdxl"},
        {"label": "SD35", "model": "venice-sd35"},
        {"label": "Flux", "model": "flux-dev-unc"},
        {"label": "Wai🔞", "model": "wai-Illustrious"},
        {"label": "HiDream", "model": "hidream"},
    ],
    SFW_CATEGORY_ID: [
        {"label": "SD35", "model": "venice-sd35"},
        {"label": "Flux", "model": "flux-dev"},
        {"label": "HiDream", "model": "hidream"},
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

        # Hidden suffix – garantiert, dass nie ein leerer Wert an die API geht
        prev_hidden = previous_inputs.get("hidden_suffix", None)

        if prev_hidden and prev_hidden.strip():
            # User hat wirklich etwas eingegeben
            default_value = prev_hidden
            placeholder_value = ""
        else:
            # Feld leer oder nur whitespace → Standard
            default_value = ""
            placeholder_value = hidden_suffix_default[:100] if hidden_suffix_default else ""

        self.hidden_suffix = discord.ui.TextInput(
            label="Hidden Suffix",
            style=discord.TextStyle.paragraph,  # vorher: short
            required=False,
            placeholder=placeholder_value,
            default=default_value,
            max_length=800  # vorher: 500
        )

        # Add items
        self.add_item(self.prompt)
        self.add_item(self.negative_prompt)
        self.add_item(self.cfg_value)
        self.add_item(self.steps_value)
        self.add_item(self.hidden_suffix)


    async def on_submit(self, interaction: discord.Interaction):
        try:
            cfg_val = float(self.cfg_value.value)
        except:
            cfg_val = CFG_REFERENCE[self.variant["model"]]["cfg_scale"]

        try:
            steps_val = int(self.steps_value.value)
            steps_val = max(1, min(steps_val, CFG_REFERENCE[self.variant["model"]]["max_steps"]))
        except:
            steps_val = CFG_REFERENCE[self.variant['model']]['default_steps']

        negative_prompt = (self.negative_prompt.value or "").strip()
        if not negative_prompt:
            negative_prompt = DEFAULT_NEGATIVE_PROMPT

        # -------- Hidden Suffix Logic --------
        user_hidden = (self.hidden_suffix.value or "").strip()
        if user_hidden:
            hidden_to_use = user_hidden
            stored_hidden_for_reuse = user_hidden
        else:
            hidden_to_use = self.hidden_suffix_value  # NSFW/SFW Standard
            stored_hidden_for_reuse = None

        # -------- Prepare variant dict --------
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

        category_id = interaction.channel.category.id if interaction.channel and interaction.channel.category else None

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(
                self.session,
                variant,
                self.prompt.value,
                hidden_to_use,
                interaction.user,
                self.is_vip,
                category_id=category_id,
                previous_inputs=self.previous_inputs
            ),
            ephemeral=True
        )

# ---------------- AspectRatioView ----------------
class AspectRatioView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, hidden_suffix, author, is_vip, category_id=None, previous_inputs=None):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.is_vip = is_vip
        self.category_id = category_id
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
        for i in range(1, 11):
            await asyncio.sleep(0.9 + steps * 0.08 + cfg * 0.38 + prompt_factor * 0.9)
            try:
                progress_text = (
                    f"{pepper} Generating image for **{self.author.display_name}** "
                    f"with **{self.variant['label']}** "
                    f" ... {i*10}%"
                )
                await progress_msg.edit(content=progress_text)
            except:
                pass

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
        if len(truncated_prompt) > 600:
            truncated_prompt = truncated_prompt[:600] + " [...]"
        embed.description = f"🔮 Prompt:\n{truncated_prompt}"

        default_hidden_suffix = NSFW_PROMPT_SUFFIX if self.category_id == NSFW_CATEGORY_ID else SFW_PROMPT_SUFFIX
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

        # --- Hier neue Reaktionen ---
        msg = await interaction.channel.send(content=f"{self.author.mention}", embed=embed, file=discord_file)
        reactions = [
            "1️⃣",
            "2️⃣",
            "3️⃣",
            "<:011:1346549711817146400>",
            "<:011pump:1346549688836296787>"
        ]
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

# ---------------- Post Generation View ----------------
class PostGenerationView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, hidden_suffix, author, message, previous_inputs=None):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix  # value actually used for generation (may be default)
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

        if message.channel.category and message.channel.category.id == SFW_CATEGORY_ID:
            submit_btn = discord.ui.Button(
                label="Submit image to contest🏆",
                style=discord.ButtonStyle.blurple,
                row=1,
                emoji=discord.PartialEmoji(id=1346555409095331860, name="02WeeWoo")
            )
            submit_btn.callback = self.post_gallery_callback
            self.add_item(submit_btn)


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
        contest_msg = await channel.send(content=mention_text, embed=embed)

        for emoji in ["1️⃣", "2️⃣", "3️⃣"]:
            try: await contest_msg.add_reaction(emoji)
            except: pass

        for child in self.children:
            if getattr(child, 'label', '') and 'Submit' in getattr(child, 'label', ''):
                child.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except:
            try:
                await interaction.followup.send("✅ Submitted to contest.", ephemeral=True)
            except:
                pass

    async def show_reuse_models(self, interaction: discord.Interaction):
        member = interaction.user
        is_vip = any(r.id == VIP_ROLE_ID for r in member.roles)

        class ReuseModelView(discord.ui.View):
            def __init__(self, session, author, prompt_text, hidden_suffix_marker, variant):
                super().__init__(timeout=None)
                self.session = session
                self.author = author
                self.prompt_text = prompt_text
                self.hidden_suffix_marker = hidden_suffix_marker
                self.variant = variant

                category_id = interaction.channel.category.id if interaction.channel.category else None
                variants = VARIANT_MAP.get(category_id, [])

                for idx, v in enumerate(variants):
                    btn = discord.ui.Button(label=v["label"], style=discord.ButtonStyle.danger)
                    btn.callback = self.make_model_callback(v, idx)
                    self.add_item(btn)

            def make_model_callback(self, variant, idx):
                async def callback(inner_interaction: discord.Interaction):
                    member = inner_interaction.user
                    is_vip = any(r.id == VIP_ROLE_ID for r in member.roles)
                    category_id = inner_interaction.channel.category.id if inner_interaction.channel.category else None
                    variants = VARIANT_MAP.get(category_id, [])
                    if variants:
                        if variant != variants[0] and not is_vip:
                            await inner_interaction.response.send_message(
                                f"❌ You need <@&{VIP_ROLE_ID}> to use this model! (Basic model is for all)",
                                ephemeral=True
                            )
                            return

                    prev_inputs = {
                        "prompt": self.prompt_text,
                        "negative_prompt": self.variant.get("negative_prompt", ""),
                        "steps": None,
                        "hidden_suffix": self.hidden_suffix_marker
                    }

                    await inner_interaction.response.send_modal(VeniceModal(
                        self.session,
                        variant,
                        self.hidden_suffix_marker if self.hidden_suffix_marker is not None else (NSFW_PROMPT_SUFFIX if category_id == NSFW_CATEGORY_ID else SFW_PROMPT_SUFFIX),
                        is_vip=is_vip,
                        previous_inputs=prev_inputs
                    ))
                return callback

        hidden_marker = self.previous_inputs.get("hidden_suffix", None)
        await interaction.response.send_message(
            f"{interaction.user.mention}, which model for the re-used prompt?",
            view=ReuseModelView(self.session, interaction.user, self.prompt_text, hidden_marker, self.variant),
            ephemeral=True
        )

# ---------------- Buttons View ----------------
class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.session = session
        self.category_id = channel.category.id if channel.category else None
        variants = VARIANT_MAP.get(self.category_id, [])
        for variant in variants:
            btn = discord.ui.Button(label=variant["label"], style=discord.ButtonStyle.blurple,
                                   custom_id=f"model_{variant['model']}_{uuid.uuid4().hex}")
            btn.callback = self.make_callback(variant)
            self.add_item(btn)

    def make_callback(self, variant):
        async def callback(interaction: discord.Interaction):
            member = interaction.user
            is_vip = any(r.id == VIP_ROLE_ID for r in member.roles)
            category_id = interaction.channel.category.id if interaction.channel.category else None

            variants = VARIANT_MAP.get(category_id, [])
            if variants:
                if variant != variants[0] and not is_vip:
                    await interaction.response.send_message(
                        f"❌ You need <@&{VIP_ROLE_ID}> to use this model! (Basic model is for all)",
                        ephemeral=True
                    )
                    return

            hidden_suffix = NSFW_PROMPT_SUFFIX if category_id == NSFW_CATEGORY_ID else SFW_PROMPT_SUFFIX
            await interaction.response.send_modal(VeniceModal(self.session, variant, hidden_suffix, is_vip))
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
                try: await msg.delete()
                except: pass
        view = VeniceView(self.session, channel)
        await channel.send("💡 Choose Model for 🖼️**NEW** image!", view=view)

    @staticmethod
    async def ensure_button_message_static(channel: discord.TextChannel, session: aiohttp.ClientSession):
        async for msg in channel.history(limit=10):
            if msg.components and not msg.embeds and not msg.attachments:
                try: await msg.delete()
                except: pass
        view = VeniceView(session, channel)
        await channel.send("💡 Choose Model for 🖼️**NEW** image!", view=view)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.category and channel.category.id in VARIANT_MAP:
                    await self.ensure_button_message(channel)

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
