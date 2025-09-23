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

DEFAULT_NEGATIVE_PROMPT = "lores, bad anatomy, missing fingers, extra limbs, watermark"
NSFW_PROMPT_SUFFIX = " (NSFW, show explicit details)"
SFW_PROMPT_SUFFIX = " (SFW, no explicit details)"

# ---------------- Model Config ----------------
CFG_REFERENCE = {
    "lustify-sdxl": {"cfg_scale": 6.0, "default_steps": 25, "max_steps": 50},
    "flux-dev-uncensored": {"cfg_scale": 6.0, "default_steps": 25, "max_steps": 30},
    "venice-sd35": {"cfg_scale": 6.0, "default_steps": 25, "max_steps": 30},
    "flux-dev": {"cfg_scale": 6.5, "default_steps": 25, "max_steps": 30},
    "hidream": {"cfg_scale": 6.5, "default_steps": 25, "max_steps": 50},
    "wai-Illustrious": {"cfg_scale": 8.0, "default_steps": 25, "max_steps": 30},
}

VARIANT_MAP = {
    NSFW_CATEGORY_ID: [
        {"label": "Lustify", "model": "lustify-sdxl"},
        {"label": "FluxUnc", "model": "flux-dev-uncensored"},
        {"label": "Wai (Anime)", "model": "wai-Illustrious"},
        {"label": "HiDream", "model": "hidream"},
    ],
    SFW_CATEGORY_ID: [
        {"label": "Venice SD35", "model": "venice-sd35"},
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
    def __init__(self, session, variant, hidden_suffix, is_vip, previous_inputs=None):
        super().__init__(title=f"Generate with {variant['label']}")
        self.session = session
        self.variant = variant
        self.hidden_suffix = hidden_suffix
        self.is_vip = is_vip
        previous_inputs = previous_inputs or {}

        self.prompt = discord.ui.TextInput(
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            default=previous_inputs.get("prompt", "")
        )

        neg_value = previous_inputs.get("negative_prompt", "")
        if neg_value and not neg_value.startswith(DEFAULT_NEGATIVE_PROMPT):
            neg_value = DEFAULT_NEGATIVE_PROMPT + ", " + neg_value
        else:
            neg_value = DEFAULT_NEGATIVE_PROMPT

        self.negative_prompt = discord.ui.TextInput(
            label="Negative Prompt (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300,
            default=neg_value
        )

        cfg_default = str(CFG_REFERENCE[variant["model"]]["cfg_scale"])
        self.cfg_value = discord.ui.TextInput(
            label="CFG (> stricter AI adherence)",
            style=discord.TextStyle.short,
            required=False,
            placeholder=cfg_default
        )

        self.max_steps = CFG_REFERENCE[variant["model"]]["max_steps"]
        previous_steps = previous_inputs.get("steps")
        self.steps_value = discord.ui.TextInput(
            label=f"Steps (1-{self.max_steps})",
            style=discord.TextStyle.short,
            required=False,
            placeholder=f"{CFG_REFERENCE[variant['model']]['default_steps']} (more steps takes longer to AI render)",
            default=str(previous_steps) if previous_steps is not None else ""
        )

        self.add_item(self.prompt)
        self.add_item(self.negative_prompt)
        self.add_item(self.cfg_value)
        self.add_item(self.steps_value)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cfg_val = float(self.cfg_value.value)
        except:
            cfg_val = CFG_REFERENCE[self.variant["model"]]["cfg_scale"]

        try:
            steps_val = int(self.steps_value.value)
            steps_val = max(1, min(steps_val, self.max_steps))
        except:
            steps_val = CFG_REFERENCE[self.variant['model']]['default_steps']

        negative_prompt = self.negative_prompt.value.strip()
        if negative_prompt and not negative_prompt.startswith(DEFAULT_NEGATIVE_PROMPT):
            negative_prompt = DEFAULT_NEGATIVE_PROMPT + ", " + negative_prompt
        elif not negative_prompt:
            negative_prompt = DEFAULT_NEGATIVE_PROMPT

        variant = {
            **self.variant,
            "cfg_scale": cfg_val,
            "negative_prompt": negative_prompt,
            "steps": steps_val
        }

        self.previous_inputs = {
            "prompt": self.prompt.value,
            "negative_prompt": negative_prompt
        }

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(self.session, variant, self.prompt.value, self.hidden_suffix, interaction.user, self.is_vip),
            ephemeral=True
        )

# ---------------- Aspect Ratio View ----------------
class AspectRatioView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, hidden_suffix, author, is_vip):
        super().__init__(timeout=None)
        self.session = session
        self.variant = variant
        self.prompt_text = prompt_text
        self.hidden_suffix = hidden_suffix
        self.author = author
        self.is_vip = is_vip

        # Buttons
        btn_1_1 = discord.ui.Button(label="⏹️1:1", style=discord.ButtonStyle.success)
        btn_16_9 = discord.ui.Button(label="🖥️16:9", style=discord.ButtonStyle.success)
        btn_9_16 = discord.ui.Button(label="📱9:16", style=discord.ButtonStyle.success)
        btn_hi = discord.ui.Button(label="🟥1:1⚡", style=discord.ButtonStyle.success)

        # Callbacks
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

        progress_msg = await interaction.followup.send("⏳ Generating image... 0%", ephemeral=True)

        prompt_factor = len(self.prompt_text)/1000
        for i in range(1, 11):
            await asyncio.sleep(0.9 + steps*0.04 + cfg*0.25 + prompt_factor*0.9)
            try:
                await progress_msg.edit(content=f"⏳ Generating image... {i*10}%")
            except:
                pass

        full_prompt = self.prompt_text + self.hidden_suffix
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
        truncated_prompt = self.prompt_text.replace("\n\n", "\n")
        if len(truncated_prompt) > 500:
            truncated_prompt = truncated_prompt[:500] + " [...]"
        embed.description = f"🔮 Prompt:\n{truncated_prompt}"

        neg_prompt = self.variant.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if neg_prompt and neg_prompt != DEFAULT_NEGATIVE_PROMPT:
            embed.description += f"\n\n🚫 Negative Prompt:\n{neg_prompt}"

        embed.set_image(url=f"attachment://{discord_file.filename}")
        guild_icon = interaction.guild.icon.url if interaction.guild.icon else None

        # Modellname-Kürzel Map
        MODEL_SHORT = {
            "lustify-sdxl": "lustify",
            "flux-dev-uncensored": "flux-unc",
            "venice-sd35": "sd35",
            "flux-dev": "flux",
            "hidream": "hidreams",
            "wai-Illustrious": "wai"
        }

        # Footer
        short_model_name = MODEL_SHORT.get(self.variant['model'], self.variant['model'])
        tech_info = f"{short_model_name} | {width}x{height} | CFG: {cfg} | Steps: {self.variant.get('steps', CFG_REFERENCE[self.variant['model']]['default_steps'])}"
        embed.set_footer(text=tech_info, icon_url=guild_icon)

        msg = await interaction.channel.send(content=f"{self.author.mention}", embed=embed, file=discord_file)
        reactions = CHANNEL_REACTIONS.get(interaction.channel.id, CUSTOM_REACTIONS)
        for emoji in reactions:
            try: await msg.add_reaction(emoji)
            except: pass

        await interaction.followup.send(
            content=f"🚨{interaction.user.mention}, re-use & edit your prompt?",
            view=PostGenerationView(self.session, self.variant, self.prompt_text, self.hidden_suffix, self.author, msg),
            ephemeral=True
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await VeniceCog.ensure_button_message_static(interaction.channel, self.session)

        self.stop()

# ---------------- Post Generation View ----------------
class PostGenerationView(discord.ui.View):
    def __init__(self, session, variant, prompt_text, hidden_suffix, author, message):
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

        if message.channel.category and message.channel.category.id == SFW_CATEGORY_ID:
            submit_btn = discord.ui.Button(label="🏆🖼️ Submit for competition", style=discord.ButtonStyle.secondary, row=1)
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

        # Nur die URL des ersten Attachments verwenden
        image_url = self.message.attachments[0].url if self.message.attachments else None

        # Embed erstellen: Autor + Avatar + Datum + Bild-URL
        embed = None
        if self.message.embeds:
            original_embed = self.message.embeds[0]
            embed = discord.Embed.from_dict(original_embed.to_dict())

            # Prompt und NegPrompt entfernen
            embed.description = f"[View original post]({self.message.jump_url})"

            # Footer auf "🏅 In Contest" ändern, Icon kann bestehen bleiben
            original_footer = original_embed.footer
            footer_icon = original_footer.icon_url if original_footer else None
            embed.set_footer(text="🏅 In Contest", icon_url=footer_icon)

        mention_text = f"<@&{role_id}> {self.author.mention} has submitted an image to the contest!"
        contest_msg = await channel.send(content=mention_text, embed=embed)

        # Reactions hinzufügen
        for emoji in ["1️⃣", "2️⃣", "3️⃣"]:
            try: await contest_msg.add_reaction(emoji)
            except: pass

        # Submit-Button deaktivieren
        for child in self.children:
            if getattr(child, 'label', '') and 'Submit' in getattr(child, 'label', ''):
                child.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except:
            try: await interaction.followup.send("✅ Submitted to contest.", ephemeral=True)
            except: pass


    async def show_reuse_models(self, interaction: discord.Interaction):
        member = interaction.user
        is_vip = any(r.id == VIP_ROLE_ID for r in member.roles)

        class ReuseModelView(discord.ui.View):
            def __init__(self, session, author, prompt_text, hidden_suffix, variant):
                super().__init__(timeout=None)
                self.session = session
                self.author = author
                self.prompt_text = prompt_text
                self.hidden_suffix = hidden_suffix
                self.variant = variant

                category_id = interaction.channel.category.id if interaction.channel.category else None
                variants = VARIANT_MAP.get(category_id, [])

                for idx, v in enumerate(variants):
                    btn = discord.ui.Button(label=v["label"], style=discord.ButtonStyle.success)
                    btn.callback = self.make_model_callback(v, idx)
                    self.add_item(btn)

            def make_model_callback(self, variant, idx):
                async def callback(inner_interaction: discord.Interaction):
                    member = inner_interaction.user
                    is_vip = any(r.id == VIP_ROLE_ID for r in member.roles)

                    # SFW Modelle: nur erstes frei, rest VIP
                    category_id = inner_interaction.channel.category.id if inner_interaction.channel.category else None
                    if category_id == SFW_CATEGORY_ID and idx > 0 and not is_vip:
                        await inner_interaction.response.send_message(f"❌ You need <@&{VIP_ROLE_ID}> to use this model!", ephemeral=True)
                        return
                    previous_inputs = {
                        "prompt": self.prompt_text,
                        "negative_prompt": self.variant.get("negative_prompt", ""),
                        "steps": CFG_REFERENCE[variant["model"]]["default_steps"]  # immer Standard
                    }

                    await inner_interaction.response.send_modal(VeniceModal(
                        self.session,
                        variant,
                        self.hidden_suffix,
                        is_vip=is_vip,
                        previous_inputs=previous_inputs
                    ))
                return callback

        await interaction.response.send_message(
            f"{interaction.user.mention}, which model for the re-used prompt?",
            view=ReuseModelView(self.session, interaction.user, self.prompt_text, self.hidden_suffix, self.variant),
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
            hidden_suffix = NSFW_PROMPT_SUFFIX if category_id == NSFW_CATEGORY_ID else SFW_PROMPT_SUFFIX

            # SFW: nur erstes Modell frei
            if category_id == SFW_CATEGORY_ID:
                variants = VARIANT_MAP.get(category_id, [])
                if variant != variants[0] and not is_vip:
                    await interaction.response.send_message(
                        f"❌ You need <@&{VIP_ROLE_ID}> to use this model! (Basic models are for all)",
                        ephemeral=True
                    )
                    return

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
