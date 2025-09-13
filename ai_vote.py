import asyncio
import discord
from discord.ext import commands

# ✅ Channels, in denen Bilder automatisch reagiert werden
ALLOWED_CHANNELS = [
    1415769909874524262,
    1415769966573260970,
    1416267309399670917,
    1416267352378572820,
    1416267383160442901,
    1416276593709420544
]

# ✅ Emojis – die ersten drei sind "entscheidend"
REACTIONS = [
    "<:01hotlips:1347157151616995378>",     # Ziel 1
    "<:01smile_piper:1387083454575022213>", # Ziel 2
    "<:01scream:1377706250690625576>",      # Ziel 3
    "<:011:1346549711817146400>",           # Bonus-Reaction
]

# ✅ Zielkanäle für die ersten drei Reaktionen
TARGET_CHANNELS = {
    0: 1416267309399670917,  # reaction index 0
    1: 1416267352378572820,
    2: 1416267383160442901,
}

# ✅ Channel für die vierte Reaction
BONUS_CHANNEL = 1416276593709420544


class AutoReactCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {original_msg_id: {target_channel_id: mirrored_msg_id}}
        self.mirrored_messages: dict[int, dict[int, int]] = {}
        self.bot.loop.create_task(self.background_scan())

    async def _get_channel(self, channel_id: int) -> discord.TextChannel | None:
        channel = self.bot.get_channel(channel_id)
        if channel:
            return channel
        try:
            channel = await self.bot.fetch_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel
        except discord.NotFound:
            pass
        except discord.Forbidden:
            print(f"⚠️ Keine Berechtigung für Channel {channel_id}")
        except Exception as e:
            print(f"⚠️ Fehler beim Laden von Channel {channel_id}: {e}")
        return None

    async def background_scan(self):
        await self.bot.wait_until_ready()
        print("🔍 Starte Hintergrund-Scan der ALLOWED_CHANNELS...")

        async def scan_channel(channel_id: int):
            channel = await self._get_channel(channel_id)
            if not channel:
                return
            try:
                async for msg in channel.history(limit=20):
                    if msg.attachments:
                        await self.ensure_all_reactions(msg)
                        await self.handle_reactions(msg)
                    await asyncio.sleep(0.5)
            except discord.HTTPException as e:
                print(f"⚠️ Fehler beim Scannen von Channel {channel_id}: {e}")

        await asyncio.gather(*(scan_channel(cid) for cid in ALLOWED_CHANNELS))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id not in ALLOWED_CHANNELS or not message.attachments:
            return
        await self.ensure_all_reactions(message)
        await self.handle_reactions(message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.channel_id not in ALLOWED_CHANNELS:
            return
        channel = await self._get_channel(payload.channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        if msg.attachments:
            await self.handle_reactions(msg)

    async def ensure_all_reactions(self, msg: discord.Message):
        existing = {str(r.emoji) for r in msg.reactions}
        for r in REACTIONS:
            if r not in existing:
                try:
                    await msg.add_reaction(discord.PartialEmoji.from_str(r))
                    await asyncio.sleep(0.2)
                except discord.HTTPException:
                    print(f"⚠️ Konnte Reaction {r} nicht zu {msg.id} hinzufügen")

    async def handle_reactions(self, msg: discord.Message):
        counts = []
        for r in REACTIONS[:3]:
            emoji_obj = discord.PartialEmoji.from_str(r)
            reaction = discord.utils.get(msg.reactions, emoji=emoji_obj)
            counts.append(reaction.count if reaction else 0)

        max_count = max(counts)
        if max_count <= 1:
            await self.remove_from_all_targets(msg.id)
            return

        # Channels für gleichstarke Reaktionen auswählen
        selected_indices = [i for i, c in enumerate(counts) if c == max_count]
        keep_channels = {TARGET_CHANNELS[i] for i in selected_indices}
        await self.remove_from_all_targets(msg.id, keep_channels=keep_channels)

        # Nachricht in alle Zielkanäle verschieben
        for idx in selected_indices:
            target_channel_id = TARGET_CHANNELS[idx]
            if msg.id in self.mirrored_messages and target_channel_id in self.mirrored_messages[msg.id]:
                continue
            target_channel = await self._get_channel(target_channel_id)
            if not target_channel:
                continue
            content = msg.content or ""
            files = [await attachment.to_file() for attachment in msg.attachments]
            mirrored_msg = await target_channel.send(content=content, files=files)
            self.mirrored_messages.setdefault(msg.id, {})[target_channel_id] = mirrored_msg.id

        # Bonus-Reaction immer hinzufügen
        bonus_channel = await self._get_channel(BONUS_CHANNEL)
        if bonus_channel:
            if msg.id not in self.mirrored_messages or BONUS_CHANNEL not in self.mirrored_messages[msg.id]:
                content = msg.content or ""
                files = [await attachment.to_file() for attachment in msg.attachments]
                mirrored_msg = await bonus_channel.send(content=content, files=files)
                self.mirrored_messages.setdefault(msg.id, {})[BONUS_CHANNEL] = mirrored_msg.id

    async def remove_from_all_targets(self, original_msg_id: int, keep_channels: set[int] = None):
        if original_msg_id not in self.mirrored_messages:
            return
        for channel_id, mirrored_id in list(self.mirrored_messages[original_msg_id].items()):
            if keep_channels and channel_id in keep_channels:
                continue
            channel = await self._get_channel(channel_id)
            if channel:
                try:
                    m = await channel.fetch_message(mirrored_id)
                    await m.delete()
                    await asyncio.sleep(0.3)
                except discord.NotFound:
                    pass
            del self.mirrored_messages[original_msg_id][channel_id]
        if not self.mirrored_messages[original_msg_id]:
            del self.mirrored_messages[original_msg_id]


async def setup(bot):
    await bot.add_cog(AutoReactCog(bot))
