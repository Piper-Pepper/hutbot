# venice_control.py
import asyncio
import uuid
import aiohttp

import discord
from discord.ext import commands

# We will access VeniceGenerationCog at runtime via bot.get_cog("VeniceGenerationCog")
# so we avoid circular imports.

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

VIP_ROLE_ID = 1377051179615522926
NSFW_CATEGORY_ID = 1415769711052062820
SFW_CATEGORY_ID = 1416461717038170294

class VeniceView(discord.ui.View):
    def __init__(self, session: aiohttp.ClientSession, channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.session = session
        self.category_id = channel.category.id if channel.category else None

        # get variant map from generation cog instance dynamically at runtime
        # (we don't import it here to avoid circular import)
        try:
            gen_cog = channel.guild and channel.guild._state.loop and None  # placeholder to silence linters
        except Exception:
            pass

        # We'll populate buttons lazily in on_load below
        # but for immediate compatibility we'll try to get the variant map from bot if possible
        self._populated = False

    async def on_load(self):
        # This method populates buttons when we have access to a bot/guild context if needed.
        if self._populated:
            return
        # Nothing to do here by default; the control cog's ensure_button_message will build a fresh view anyway.
        self._populated = True

    def make_callback(self, variant):
        async def callback(interaction: discord.Interaction):
            member = interaction.user
            is_vip = any(r.id == VIP_ROLE_ID for r in getattr(member, "roles", []))
            category_id = interaction.channel.category.id if interaction.channel and interaction.channel.category else None
            hidden_suffix = "(NSFW, show explicit details)" if category_id == NSFW_CATEGORY_ID else "(SFW, no explicit details)"

            # restrict certain models to VIP unless they are in the minimal allowed set
            if not is_vip and variant["model"] not in ["lustify-sdxl", "stable-diffusion-3.5"]:
                await interaction.response.send_message(
                    f"❌ You need <@&{VIP_ROLE_ID}> to use this model! (Basic models are for all)",
                    ephemeral=True
                )
                return

            # Get generation cog and its VeniceModal class (runtime)
            gen_cog = interaction.client.get_cog("VeniceGenerationCog")
            if not gen_cog:
                await interaction.response.send_message("❌ Generation cog not loaded.", ephemeral=True)
                return

            VeniceModal = getattr(gen_cog, "VeniceModal", None)
            if not VeniceModal:
                await interaction.response.send_message("❌ Modal class missing in generation cog.", ephemeral=True)
                return

            # open modal from generation cog, passing the session from control cog or gen cog
            session_to_use = self.session or getattr(gen_cog, "session", None)
            await interaction.response.send_modal(VeniceModal(session_to_use, variant, hidden_suffix, is_vip))
        return callback

    @classmethod
    def build_for_channel(cls, session: aiohttp.ClientSession, channel: discord.TextChannel, bot: commands.Bot):
        """
        Build a VeniceView populated with buttons appropriate for the channel's category.
        We pull VARIANT_MAP from VeniceGenerationCog at runtime to ensure consistent mapping.
        """
        view = cls(session, channel)
        gen_cog = bot.get_cog("VeniceGenerationCog")
        variant_map = getattr(gen_cog, "VARIANT_MAP", None) if gen_cog else None
        category_id = channel.category.id if channel.category else None
        variants = variant_map.get(category_id, []) if variant_map else []

        for variant in variants:
            btn = discord.ui.Button(label=variant["label"], style=discord.ButtonStyle.blurple,
                                    custom_id=f"model_{variant['model']}_{uuid.uuid4().hex}")
            btn.callback = view.make_callback(variant)
            view.add_item(btn)

        view._populated = True
        return view


class VeniceControlCog(commands.Cog):
    """Buttons and channel control (ensure button messages, on_ready, etc.)"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    async def ensure_button_message(self, channel: discord.TextChannel):
        """
        Delete old control messages (with components) that don't have embeds/attachments,
        then send a fresh button message built from VeniceView.
        """
        # Delete existing control messages (components-only) to avoid duplicates
        try:
            async for msg in channel.history(limit=10):
                if msg.components and not msg.embeds and not msg.attachments:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
        except Exception:
            pass

        # Build a view populated with buttons for this channel's category by referencing generation cog
        try:
            view = VeniceView.build_for_channel(self.session, channel, self.bot)
            await channel.send("💡 Click a button to start generating a 🖼️**NEW** image!", view=view)
        except Exception as e:
            print(f"Failed to ensure button message in {channel}: {e}")

    @staticmethod
    async def ensure_button_message_static(channel: discord.TextChannel, session: aiohttp.ClientSession):
        """
        Static helper available to other cogs: deletes old control messages and posts a fresh one.
        This mirrors original behavior but is a separate static method requiring session passed in.
        """
        try:
            async for msg in channel.history(limit=10):
                if msg.components and not msg.embeds and not msg.attachments:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
        except Exception:
            pass

        # Build view using runtime variant map via the bot that owns the channel
        try:
            bot = channel._state._get_client()  # internal access but generally available
            view = VeniceView.build_for_channel(session, channel, bot)
            await channel.send("💡 Click a button to start generating a 🖼️**NEW** image!", view=view)
        except Exception as e:
            print(f"ensure_button_message_static failed for {channel}: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """
        On ready, find all text channels that belong to categories present in VARIANT_MAP
        (VARIANT_MAP is sourced from VeniceGenerationCog at runtime).
        """
        gen_cog = self.bot.get_cog("VeniceGenerationCog")
        variant_map = getattr(gen_cog, "VARIANT_MAP", {}) if gen_cog else {}
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                try:
                    if channel.category and channel.category.id in variant_map:
                        await self.ensure_button_message(channel)
                except Exception:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceControlCog(bot))
