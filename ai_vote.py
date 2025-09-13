import asyncio
import discord
from discord.ext import commands

# Ursprungskanäle (dort stehen die ersten Posts mit allen 4 Reaktionen)
SOURCE_CHANNELS = [
    1415769909874524262,
    1415769966573260970
]

# Reaktions-Emojis
REACTIONS = [
    "<:01hotlips:1347157151616995378>",     # Ziel 1
    "<:01smile_piper:1387083454575022213>", # Ziel 2
    "<:01scream:1377706250690625576>",      # Ziel 3
    "<:011:1346549711817146400>",           # Ziel 4
]

# Channels, die jeweils die Reaktion repräsentieren
REACTION_CHANNELS = [
    1416267309399670917, # <:01hotlips>
    1416267352378572820, # <:01smile_piper>
    1416267383160442901, # <:01scream>
    1416276593709420544  # <:011>
]

class AutoReactCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {original_msg_id: {target_channel_id: mirrored_msg_id}}
        self.mirrored_messages: dict[int, dict[int, int]] = {}
        # Startet nur den Initial-Scan der SOURCE_CHANNELS beim Bot-Start
        self.bot.loop.create_task(self.initial_scan())

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

    async def initial_scan(self):
        """Beim Start: prüft die letzten 20 Nachrichten der SOURCE_CHANNELS und fügt fehlende Reaktionen hinzu"""
        await self.bot.wait_until_ready()
        print("🔍 Starte Initial-Scan der SOURCE_CHANNELS (letzte 20 Nachrichten)...")

        async def scan_channel(channel_id: int):
            channel = await self._get_channel(channel_id)
            if not channel:
                return
            try:
                async for msg in channel.history(limit=20):
                    if msg.attachments:
                        await self.ensure_all_reactions(msg)
                    await asyncio.sleep(0.5)
            except discord.HTTPException as e:
                print(f"⚠️ Fehler beim Scannen von Channel {channel_id}: {e}")

        await asyncio.gather(*(scan_channel(cid) for cid in SOURCE_CHANNELS))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id not in SOURCE_CHANNELS or not message.attachments:
            return
        await self.ensure_all_reactions(message)
        await self.handle_reactions(message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.channel_id not in SOURCE_CHANNELS:
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
        """Sorgt dafür, dass alle definierten Reaktionen vorhanden sind"""
        existing = {str(r.emoji) for r in msg.reactions}
        for r in REACTIONS:
            if r not in existing:
                try:
                    await msg.add_reaction(discord.PartialEmoji.from_str(r))
                    await asyncio.sleep(0.2)
                except discord.HTTPException:
                    print(f"⚠️ Konnte Reaction {r} nicht zu {msg.id} hinzufügen")

    async def handle_reactions(self, msg: discord.Message):
        """Analysiert Reaktionen und verschiebt Nachricht in die entsprechenden REACTION_CHANNELS"""
        counts = []
        for r in REACTIONS:
            emoji_obj = discord.PartialEmoji.from_str(r)
            reaction = discord.utils.get(msg.reactions, emoji=emoji_obj)
            counts.append(reaction.count if reaction else 0)

        max_count = max(counts)
        if max_count <= 1:
            await self.remove_from_all_targets(msg.id)
            return

        # Indexe mit maximaler Reaktion auswählen
        selected_indices = [i for i, c in enumerate(counts) if c == max_count]

        # Zielchannels entsprechend der höchsten Reaktion
        keep_channels = {REACTION_CHANNELS[i] for i in selected_indices}
        await self.remove_from_all_targets(msg.id, keep_channels=keep_channels)

        for idx in selected_indices:
            target_channel_id = REACTION_CHANNELS[idx]
            if msg.id in self.mirrored_messages and target_channel_id in self.mirrored_messages[msg.id]:
                continue
            target_channel = await self._get_channel(target_channel_id)
            if not target_channel:
                continue
            # Nachricht verschieben (Content + Dateien)
            content = msg.content or ""
            files = [await attachment.to_file() for attachment in msg.attachments]
            mirrored_msg = await target_channel.send(content=content, files=files)
            self.mirrored_messages.setdefault(msg.id, {})[target_channel_id] = mirrored_msg.id

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
