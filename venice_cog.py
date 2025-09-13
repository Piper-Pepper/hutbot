import discord
from discord.ext import commands
import aiohttp
import io
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env!")

VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"

# Channels
NSFW_CHANNEL_ID = 1415769909874524262
SFW_CHANNEL_ID = 1415769966573260970

DEFAULT_NEGATIVE_PROMPT = "blurry, bad anatomy, missing fingers, extra limbs, text, watermark"

# Image variants (5 per channel). Lustify (>) only in NSFW.
VARIANT_MAP = {
    # NSFW (5)
    ">":  {"label": "Lustify", "model": "lustify-sdxl",            "cfg_scale": 4.0, "steps": 30, "channel": NSFW_CHANNEL_ID},
    "!!": {"label": "Pony",    "model": "pony-realism",            "cfg_scale": 5.0, "steps": 20, "channel": NSFW_CHANNEL_ID},
    "##": {"label": "FluxUnc", "model": "flux-dev-uncensored",     "cfg_scale": 4.5, "steps": 30, "channel": NSFW_CHANNEL_ID},
    "**": {"label": "FluxDev", "model": "flux-dev",                "cfg_scale": 4.5, "steps": 30, "channel": NSFW_CHANNEL_ID},
    "++": {"label": "Qwen",    "model": "qwen-image",             "cfg_scale": 3.5, "steps": 12, "channel": NSFW_CHANNEL_ID},

    # SFW (5)
    "?":  {"label": "SD3.5",   "model": "stable-diffusion-3.5",   "cfg_scale": 4.0, "steps": 8,  "channel": SFW_CHANNEL_ID},
    "&":  {"label": "Flux",    "model": "flux-dev",               "cfg_scale": 5.0, "steps": 30, "channel": SFW_CHANNEL_ID},
    "~":  {"label": "Qwen",    "model": "qwen-image",             "cfg_scale": 3.5, "steps": 8,  "channel": SFW_CHANNEL_ID},
    "$$": {"label": "HiDream", "model": "hidream",                "cfg_scale": 4.0, "steps": 20, "channel": SFW_CHANNEL_ID},
    "%%": {"label": "Venice",  "model": "venice-sd35",           "cfg_scale": 4.0, "steps": 20, "channel": SFW_CHANNEL_ID},
}

# ----- Venice Image Generation -----
async def venice_generate(session: aiohttp.ClientSession, prompt: str, variant: dict) -> bytes | None:
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": variant["model"],
        "prompt": prompt,
        "width": 1024,
        "height": 1024,
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
                text = await resp.text()
                print(f"Venice API Error {resp.status}: {text}")
                return None
            return await resp.read()
    except Exception as e:
        print(f"Exception calling Venice API: {e}")
        return None

# ----- Modal -----
class VeniceModal(discord.ui.Modal, title="Generate Image"):
    prompt = discord.ui.TextInput(
        label="Describe your image",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your character or scene",
        required=True,
        max_length=500
    )

    negative_prompt = discord.ui.TextInput(
        label="Negative Prompt (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Optional: describe things you DON'T want in the image (leave empty to use default)",
        required=False,
        max_length=300
    )

    def __init__(self, session: aiohttp.ClientSession, variant: dict, channel_id: int):
        super().__init__()
        self.session = session
        self.variant = variant
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        # Acknowledge quickly (ephemeral)
        await interaction.response.send_message(f"🎨 Generating your image with {self.variant['label']}...", ephemeral=True)
        neg_prompt = self.negative_prompt.value.strip() or DEFAULT_NEGATIVE_PROMPT

        # Try to post a progress message in the target channel and simulate progress.
        channel = interaction.client.get_channel(self.channel_id)
        progress_msg = None
        progress_task = None

        if channel:
            try:
                progress_msg = await channel.send(f"⏳ {interaction.user.mention} started a generation with `{self.variant['label']}` — 0%")
            except Exception:
                progress_msg = None

            if progress_msg:
                async def fake_progress():
                    # Simple simulated progress based on steps (not real API progress)
                    steps = max(1, int(self.variant.get("steps", 30)))
                    # adjust sleep so long-step models don't take forever in the simulation
                    sleep_time = 0.5 if steps <= 30 else 0.6
                    try:
                        for i in range(1, steps + 1):
                            await asyncio.sleep(sleep_time)
                            perc = int(i / steps * 100)
                            # clamp to 99 until done
                            if perc >= 100:
                                perc = 99
                            try:
                                await progress_msg.edit(content=f"⏳ {interaction.user.mention} generation with `{self.variant['label']}` — {perc}%")
                            except Exception:
                                pass
                    except asyncio.CancelledError:
                        # allow clean cancellation
                        raise

                progress_task = asyncio.create_task(fake_progress())

        # Call Venice API (this is the real blocking call)
        img_bytes = await venice_generate(self.session, self.prompt.value, {**self.variant, "negative_prompt": neg_prompt})

        # Stop simulated progress
        if progress_task:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        if not img_bytes:
            if progress_msg:
                try:
                    await progress_msg.edit(content=f"❌ {interaction.user.mention} generation failed.")
                except Exception:
                    pass
            await interaction.followup.send("❌ Sorry, generation failed.", ephemeral=True)
            return

        # Send final image and buttons, delete old button posts
        fp = io.BytesIO(img_bytes)
        file = discord.File(fp, filename="image.png")
        if channel:
            try:
                # delete previous button posts in last 10 messages
                async for msg in channel.history(limit=10):
                    if msg.components:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
                await channel.send(
                    content=(f"{interaction.user.mention} generated an image:\n"
                             f"Prompt: `{self.prompt.value}`\n"
                             f"Negative Prompt: `{neg_prompt}`\n"
                             f"Model: `{self.variant['model']}` | Steps: {self.variant['steps']}"),
                    file=file
                )
                # Post the 5-button-view again
                await channel.send("💡 Choose the next generation:", view=VeniceView(self.session, self.channel_id))
                # tidy up progress message if present
                if progress_msg:
                    try:
                        await progress_msg.delete()
                    except Exception:
                        pass
            except Exception as e:
                print("Error while sending result:", e)
                await interaction.followup.send("✅ Generation finished but I couldn't post it to the target channel.", ephemeral=True)
        else:
            await interaction.followup.send("✅ Generation finished — but couldn't post to the configured channel.", ephemeral=True)

# ----- Button View (dynamically builds exactly 5 buttons per channel) -----
class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel_id: int):
        super().__init__(timeout=None)
        self.session = session
        self.channel_id = channel_id

        # Add buttons for all variants that belong to this channel (preserves dict order)
        for prefix, variant in VARIANT_MAP.items():
            if variant["channel"] == channel_id:
                btn_style = discord.ButtonStyle.red if channel_id == NSFW_CHANNEL_ID else discord.ButtonStyle.blurple
                btn = discord.ui.Button(label=variant.get("label", variant["model"]), style=btn_style, custom_id=prefix)
                # attach a callback with prefix bound into closure
                async def _cb(interaction: discord.Interaction, pref=prefix):
                    await self._send_modal(interaction, pref)
                btn.callback = _cb
                self.add_item(btn)

    async def _send_modal(self, interaction: discord.Interaction, prefix: str):
        variant = VARIANT_MAP[prefix]
        await interaction.response.send_modal(VeniceModal(self.session, variant, self.channel_id))

# ----- Cog -----
class VeniceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.session.bot = bot

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    @commands.Cog.listener()
    async def on_ready(self):
        for channel_id in [NSFW_CHANNEL_ID, SFW_CHANNEL_ID]:
            channel = self.bot.get_channel(channel_id)
            if channel:
                # Delete old button posts in last 10 messages
                async for msg in channel.history(limit=10):
                    if msg.components:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
                await channel.send(
                    "💡 Use a prefix or click a button to generate an image!\nYou can also specify a negative prompt (optional).",
                    view=VeniceView(self.session, channel_id)
                )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        allowed_channels = {v["channel"] for v in VARIANT_MAP.values()}
        if message.channel.id not in allowed_channels:
            return

        content = message.content.strip()
        # Important: match longest prefixes first to avoid conflicts (e.g. "!" vs "!!")
        sorted_prefixes = sorted(VARIANT_MAP.keys(), key=len, reverse=True)
        prefix = next((p for p in sorted_prefixes if content.startswith(p)), None)
        if not prefix:
            return

        variant = VARIANT_MAP[prefix]
        if message.channel.id != variant["channel"]:
            return

        # Split prompt and optional negative prompt if user writes "prompt || negative"
        parts = content[len(prefix):].split("||", 1)
        prompt_text = parts[0].strip()
        neg_text = parts[1].strip() if len(parts) > 1 else DEFAULT_NEGATIVE_PROMPT

        if not prompt_text:
            return

        # Create a progress message and simulate progress while the real API call completes
        progress_msg = None
        progress_task = None
        try:
            progress_msg = await message.channel.send(f"⏳ {message.author.mention} started a generation with `{variant['model']}` — 0%")
        except Exception:
            progress_msg = None

        if progress_msg:
            async def fake_progress():
                steps = max(1, int(variant.get("steps", 30)))
                sleep_time = 0.5 if steps <= 30 else 0.6
                try:
                    for i in range(1, steps + 1):
                        await asyncio.sleep(sleep_time)
                        perc = int(i / steps * 100)
                        if perc >= 100:
                            perc = 99
                        try:
                            await progress_msg.edit(content=f"⏳ {message.author.mention} generation with `{variant['model']}` — {perc}%")
                        except Exception:
                            pass
                except asyncio.CancelledError:
                    raise
            progress_task = asyncio.create_task(fake_progress())

        img_bytes = await venice_generate(self.session, prompt_text, {**variant, "negative_prompt": neg_text})

        if progress_task:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        if not img_bytes:
            if progress_msg:
                try:
                    await progress_msg.edit(content=f"❌ {message.author.mention} generation failed.")
                except Exception:
                    pass
            await message.reply("❌ Generation failed!")
            return

        fp = io.BytesIO(img_bytes)
        file = discord.File(fp, filename="image.png")
        await message.reply(
            content=(f"Generated (`{prefix}` variant) using model `{variant['model']}` | Steps: {variant['steps']}\n"
                     f"Prompt: `{prompt_text}`\nNegative Prompt: `{neg_text}`"),
            file=file
        )

        # Delete old button posts in last 10 messages
        async for msg in message.channel.history(limit=10):
            if msg.components:
                try:
                    await msg.delete()
                except Exception:
                    pass
        # Post buttons (new)
        await message.channel.send(
            "💡 Choose the next generation:",
            view=VeniceView(self.session, message.channel.id)
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceCog(bot))
