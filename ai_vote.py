import asyncio
import discord
from discord.ext import commands

SOURCE_CHANNELS = [
    1415769909874524262,
    1415769966573260970
]

REACTIONS = [
    "<:01hotlips:1347157151616995378>",
    "<:01smile_piper:1387083454575022213>",
    "<:01scream:1377706250690625576>",
    "<:011:1346549711817146400>",
]

REACTION_CHANNELS = [
    1416267309399670917,
    1416267352378572820,
    1416267383160442901,
    1416276593709420544
]

SCAN_LIMIT = 20  # Letzte 20 Nachrichten im Auge behalten
SCAN_INTERVAL = 10  # Sekunden zwischen Scans

class AutoReactCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.mirrored_messages: dict[int, dict[int, int]] = {}
        self.bot.loop.create_task(self.initial_scan())
        self.bot.loop.create_task(self.background_monitor())

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

    # -------------------- Initial Scan beim Start --------------------
    async def initial_scan(self):
        await self.bot.wait_until_ready()
        print("🔍 Starte Initial-Scan der SOURCE_CHANNELS...")
        for channel_id in SOURCE_CHANNELS:
            channel = await self._get_channel(channel_id)
            if not channel:
                continue
            try:
                async for msg in channel.history(limit=SCAN_LIMIT):
                    if msg.attachments:
                        await self.ensure_all_reactions(msg)
                    await asyncio.sleep(0.3)
            except Exception as e:
                print(f"⚠️ Fehler beim Initial-Scan in Channel {channel_id}: {e}")

    async def ensure_all_reactions(self, msg: discord.Message):
        existing = {str(r.emoji) for r in msg.reactions}
        for r in REACTIONS:
            if r not in existing:
                try:
                    await msg.add_reaction(discord.PartialEmoji.from_str(r))
                    await asyncio.sleep(0.2)
                except discord.HTTPException:
                    print(f"⚠️ Konnte Reaction {r} nicht zu {msg.id} hinzufügen")

    # -------------------- Background Monitor --------------------
    async def background_monitor(self):
        await self.bot.wait_until_ready()
        print("⏱️ Starte kontinuierliche Überwachung der letzten 20 Posts...")
        while True:
            try:
                for channel_id in SOURCE_CHANNELS:
                    channel = await self._get_channel(channel_id)
                    if not channel:
                        continue
                    async for msg in channel.history(limit=SCAN_LIMIT):
                        if msg.attachments:
                            await self.ensure_all_reactions(msg)
                            await self.update_reaction_channels(msg)
                        await asyncio.sleep(0.2)
            except Exception as e:
                print(f"⚠️ Fehler im Background Monitor: {e}")
            await asyncio.sleep(SCAN_INTERVAL)

    async def update_reaction_channels(self, msg: discord.Message):
        # Count Reactions
        counts = []
        for r in REACTIONS:
            emoji_obj = discord.PartialEmoji.from_str(r)
            reaction = discord.utils.get(msg.reactions, emoji=emoji_obj)
            counts.append(reaction.count if reaction else 0)

        max_count = max(counts)
        if max_count <= 1:
            # Alles löschen aus REACTION_CHANNELS
            await self.remove_from_all_targets(msg.id)
            return

        # Channels mit höchster Reaktion bestimmen
        selected_indices = [i for i, c in enumerate(counts) if c == max_count]
        keep_channels = {REACTION_CHANNELS[i] for i in selected_indices}
        await self.remove_from_all_targets(msg.id, keep_channels=keep_channels)

        # Spiegeln in die ausgewählten Kanäle
        for idx in selected_indices:
            target_channel_id = REACTION_CHANNELS[idx]
            if msg.id in self.mirrored_messages and target_channel_id in self.mirrored_messages[msg.id]:
                continue
            target_channel = await self._get_channel(target_channel_id)
            if not target_channel:
                continue
            content = msg.content or ""
            files = [await attachment.to_file() for attachment in msg.attachments]
            mirrored_msg = await target_channel.send(content=content, files=files)
            self.mirrored_messages.setdefault(msg.id, {})[target_channel_id] = mirrored_msg.id

    # -------------------- Entfernen von Posts aus REACTION_CHANNELS --------------------
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
                    await asyncio.sleep(0.2)
                except discord.NotFound:
                    pass
            del self.mirrored_messages[original_msg_id][channel_id]
        if not self.mirrored_messages[original_msg_id]:
            del self.mirrored_messages[original_msg_id]


async def setup(bot):
    await bot.add_cog(AutoReactCog(bot))
