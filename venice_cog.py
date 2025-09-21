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

DEFAULT_NEGATIVE_PROMPT = "lores, bad anatomy, missing fingers, extra limbs, watermark"
NSFW_PROMPT_SUFFIX = " (NSFW, show explicit details)"
SFW_PROMPT_SUFFIX = " (SFW, no explicit details)"

CFG_REFERENCE = {
    "lustify-sdxl": {"cfg_scale": 5.5, "steps": 30},
    "pony-realism": {"cfg_scale": 7.5, "steps": 30},
    "flux-dev-uncensored": {"cfg_scale": 6.0, "steps": 30},
    "stable-diffusion-3.5": {"cfg_scale": 6.0, "steps": 30},
    "flux-dev": {"cfg_scale": 6.0, "steps": 30},
    "hidream": {"cfg_scale": 6.5, "steps": 30},
    "wai-Illustrious": {"cfg_scale": 7.0, "steps": 30},
}

VARIANT_MAP = {
    NSFW_CATEGORY_ID: [
        {"label": "Lustify", "model": "lustify-sdxl"},
        {"label": "Pony", "model": "pony-realism"},
        {"label": "FluxUnc", "model": "flux-dev-uncensored"},
        {"label": "Wai (Anime)", "model": "wai-Illustrious"},
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
        "steps": steps or CFG_REFERENCE[variant["model"]]["steps"],
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

        prompt_value = previous_inputs.get("prompt", "")
        self.prompt = discord.ui.TextInput(
            label="Describe your image",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            default=prompt_value,
            placeholder="Describe your image. Be creative for best results!" if not prompt_value else None
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
            placeholder=None
        )

        cfg_default = str(CFG_REFERENCE[variant["model"]]["cfg_scale"])
        self.cfg_value = discord.ui.TextInput(
            label="CFG (> stricter AI adherence)",
            style=discord.TextStyle.short,
            required=False,
            placeholder=cfg_default
        )

        self.add_item(self.prompt)
        self.add_item(self.negative_prompt)
        self.add_item(self.cfg_value)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cfg_val = float(self.cfg_value.value)
        except:
            cfg_val = CFG_REFERENCE[self.variant["model"]]["cfg_scale"]

        negative_prompt = self.negative_prompt.value.strip()
        if negative_prompt:
            if not negative_prompt.startswith(DEFAULT_NEGATIVE_PROMPT):
                negative_prompt = DEFAULT_NEGATIVE_PROMPT + ", " + negative_prompt
        else:
            negative_prompt = DEFAULT_NEGATIVE_PROMPT

        variant = {
            **self.variant,
            "cfg_scale": cfg_val,
            "negative_prompt": negative_prompt
        }

        await interaction.response.send_message(
            f"🎨 {variant['label']} ready! Choose an aspect ratio:",
            view=AspectRatioView(
                self.session,
                variant,
                self.prompt.value,
                self.hidden_suffix,
                interaction.user,
                self.is_vip
            ),
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

        btn_1_1 = discord.ui.Button(label="⏹️1:1", style=discord.ButtonStyle.success)
        btn_16_9 = discord.ui.Button(label="🖥️16:9", style=discord.ButtonStyle.success)
        btn_9_16 = discord.ui.Button(label="📱9:16", style=discord.ButtonStyle.success)

        btn_1_1.callback = self.make_callback(1024, 1024, "1:1")
        btn_16_9.callback = self.make_callback(1280, 816, "16:9")
        btn_9_16.callback = self.make_callback(816, 1280, "9:16")

        self.add_item(btn_1_1)
        self.add_item(btn_16_9)
        self.add_item(btn_9_16)

    def make_callback(self, width, height, ratio_name):
        async def callback(interaction: discord.Interaction):
            await self.generate_image(interaction, width, height, ratio_name)
        return callback

    async def generate_image(self, interaction: discord.Interaction, width: int, height: int, ratio_name: str):
        if not self.is_vip and ratio_name in ["16:9", "9:16"]:
            await interaction.response.send_message(
                f"❌ You need <@&{VIP_ROLE_ID}> to use this aspect ratio! 1:1 works for all.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        cfg = self.variant["cfg_scale"]
        steps = self.variant.get("steps", int(cfg))

        progress_msg = await interaction.followup.send(f"⏳ Generating image... 0%", ephemeral=True)

        prompt_factor = len(self.prompt_text) / 1000
        for i in range(1, 11):
            await asyncio.sleep(1.0 + steps * 0.04 + cfg * 0.25 + prompt_factor * 1.0)
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

        filename = make_safe_filename(self.prompt_text)
        fp = io.BytesIO(img_bytes)
        fp.seek(0)
        discord_file = discord.File(fp, filename=filename)

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

        embed.set_image(url=f"attachment://{filename}")
        guild_icon = interaction.guild.icon.url if interaction.guild.icon else None
        tech_info = f"{self.variant['model']} | CFG: {cfg} | Steps: {self.variant.get('steps', 30)}"
        embed.set_footer(text=tech_info, icon_url=guild_icon)

        msg = await interaction.channel.send(content=f"{self.author.mention}", embed=embed, file=discord_file)

        reactions = CHANNEL_REACTIONS.get(interaction.channel.id, CUSTOM_REACTIONS)
        for emoji in reactions:
            try:
                await msg.add_reaction(emoji)
            except:
                pass

        await interaction.followup.send(
            content=f"🚨{interaction.user.mention}, would you like to use your prompts again? You can tweak them, if you like...",
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

        # Delete Buttons (work reliably)
        del_btn = discord.ui.Button(label="🗑️ Delete", style=discord.ButtonStyle.red)
        del_btn.callback = self.delete_callback
        self.add_item(del_btn)

        del_reuse_btn = discord.ui.Button(label="🧹 Delete & Re-use", style=discord.ButtonStyle.red)
        del_reuse_btn.callback = self.delete_reuse_callback
        self.add_item(del_reuse_btn)

        # Submit Button only in SFW category
        if message.channel.category and message.channel.category.id == SFW_CATEGORY_ID:
            submit_btn = discord.ui.Button(label="🖼️ Submit for Contest🏆", style=discord.ButtonStyle.secondary, row=1)
            submit_btn.callback = self.post_gallery_callback
            self.add_item(submit_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author.id

    async def reuse_callback(self, interaction: discord.Interaction):
        await self.show_reuse_models(interaction)

    async def delete_callback(self, interaction: discord.Interaction):
        # Delete the original posted image message
        try:
            await self.message.delete()
        except Exception:
            pass
        await interaction.response.send_message("✅ Post deleted", ephemeral=True)

    async def delete_reuse_callback(self, interaction: discord.Interaction):
        # Delete the original posted image message, then open reuse modal flow
        try:
            await self.message.delete()
        except Exception:
            pass
        # show_reuse_models will use interaction.response.send_message, so call it and return
        await self.show_reuse_models(interaction)

    async def post_gallery_callback(self, interaction: discord.Interaction):
        channel_id = 1418956422086922320  # Contest channel
        role_id = 1419024270201454684
        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message("❌ Gallery channel not found!", ephemeral=True)
            return

        # Copy attachments (images)
        files = []
        for attachment in self.message.attachments:
            fp = io.BytesIO()
            await attachment.save(fp)
            fp.seek(0)
            files.append(discord.File(fp, filename=attachment.filename))

        # Take embed and ensure prompt is FULL
        embed = None
        if self.message.embeds:
            original_embed = self.message.embeds[0]
            embed = discord.Embed.from_dict(original_embed.to_dict())
            # overwrite description with full prompt text
            full_prompt = self.prompt_text.replace("\n\n", "\n")
            embed.description = f"🔮 Prompt:\n{full_prompt}"
            neg_prompt = self.variant.get("negative_prompt")
            if neg_prompt and neg_prompt != DEFAULT_NEGATIVE_PROMPT:
                embed.description += f"\n\n🚫 Negative Prompt:\n{neg_prompt}"

        # Single post: content + embed + files
        mention_text = f"<@&{role_id}> {self.author.mention} has submitted an image to the contest!"
        contest_msg = await channel.send(content=mention_text, embed=embed, files=files)

        # Add contest reactions (1,2,3)
        for emoji in ["1️⃣", "2️⃣", "3️⃣"]:
            try:
                await contest_msg.add_reaction(emoji)
            except Exception:
                pass

        # Disable submit button (prevent duplicate submits)
        for child in self.children:
            if getattr(child, 'label', '') and 'Submit' in getattr(child, 'label', ''):
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

                for v in variants:
                    btn = discord.ui.Button(label=v["label"], style=discord.ButtonStyle.success)
                    btn.callback = self.make_model_callback(v)
                    self.add_item(btn)

            def make_model_callback(self, variant):
                async def callback(inner_interaction: discord.Interaction):
                    member = inner_interaction.user
                    is_vip = any(r.id == VIP_ROLE_ID for r in member.roles)
                    if not is_vip and variant["model"] not in ["lustify-sdxl", "stable-diffusion-3.5"]:
                        await inner_interaction.response.send_message(
                            f"❌ You need <@&{VIP_ROLE_ID}> to use this model!", ephemeral=True
                        )
                        return

                    # open modal with previous inputs (reuse prompt)
                    await inner_interaction.response.send_modal(VeniceModal(
                        self.session,
                        variant,
                        self.hidden_suffix,
                        is_vip=is_vip,
                        previous_inputs={"prompt": self.prompt_text, "negative_prompt": self.variant.get("negative_prompt", "")}
                    ))
                return callback

        await interaction.response.send_message(
            f"{interaction.user.mention}, which model do you want to use with your re-used prompt?",
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

            if not is_vip and variant["model"] not in ["lustify-sdxl", "stable-diffusion-3.5"]:
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
                try:
                    await msg.delete()
                except Exception:
                    pass
        view = VeniceView(self.session, channel)
        await channel.send("💡 Click a button to start generating a 🖼️**NEW** image!", view=view)

    @staticmethod
    async def ensure_button_message_static(channel: discord.TextChannel, session: aiohttp.ClientSession):
        async for msg in channel.history(limit=10):
            if msg.components and not msg.embeds and not msg.attachments:
                try:
                    await msg.delete()
                except Exception:
                    pass
        view = VeniceView(session, channel)
        await channel.send("💡 Click a button to start generating a 🖼️**NEW** image!", view=view)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.category and channel.category.id in VARIANT_MAP:
                    await self.ensure_button_message(channel)

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))