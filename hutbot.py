import discord
from discord.ext import commands

ALLOWED_CHANNELS = [
    1378018756843933767,
    1375457632394936340,
    1375457683531890688,
    1377502522788417558,
    1378456514955710646
]

# Deine Emojis (die letzten drei sind "entscheidend")
REACTIONS = [
    "<:01hotlips:1347157151616995378>",  # Ziel 1
    "<:01smile_piper:1387083454575022213>",  # Ziel 2
    "<:01scream:1377706250690625576>",  # Ziel 3
    "<:011:1346549711817146400>"  # entscheidet nichts
]

# Zielkanäle für Reaction 1-3
TARGET_CHANNELS = {
    0: 1416267309399670917,  # reaction index 0
    1: 1416267352378572820,
    2: 1416267383160442901
}

class AutoReactCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Merkt sich, wo Nachrichten gespiegelt wurden, um doppelte Posts zu verhindern
        self.mirrored_messages = {}  # {original_msg_id: {target_channel_id: mirrored_msg_id}}

    async def cog_load(self):
        # Beim Start die letzten 20 Nachrichten der relevanten Channels scannen
        for channel_id in ALLOWED_CHANNELS:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            async for msg in channel.history(limit=20):
                if msg.attachments:
                    await self.handle_reactions(msg)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id not in ALLOWED_CHANNELS:
            return
        if not message.attachments:
            return  # nur Bilder
        # direkt alle Reaktionen hinzufügen
        await self.ensure_all_reactions(message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Reaktionsänderungen überwachen
        channel = self.bot.get_channel(payload.channel_id)
        if not channel or channel.id not in ALLOWED_CHANNELS:
            return
        try:
            msg = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        if msg.attachments:
            await self.handle_reactions(msg)

    async def ensure_all_reactions(self, msg: discord.Message):
        """Stellt sicher, dass eine Nachricht alle vier Reaktionen hat."""
        existing_emojis = {str(r.emoji) for r in msg.reactions}
        for r in REACTIONS:
            if r not in existing_emojis:
                try:
                    emoji_obj = discord.PartialEmoji.from_str(r)
                    await msg.add_reaction(emoji_obj)
                except discord.HTTPException:
                    pass

    async def handle_reactions(self, msg: discord.Message):
        """Analysiert Reactions, kopiert oder entfernt Nachrichten in Zielkanälen."""
        # Erst sicherstellen, dass alle Reaktionen vorhanden sind
        await self.ensure_all_reactions(msg)

        counts = [0, 0, 0]  # nur für die ersten 3 Reactions relevant
        for i, r in enumerate(REACTIONS[:3]):
            emoji_obj = discord.PartialEmoji.from_str(r)
            reaction = discord.utils.get(msg.reactions, emoji=emoji_obj)
            if reaction:
                counts[i] = reaction.count

        max_count = max(counts)
        if max_count <= 1:
            # alles <= 1 → Nachricht bleibt nur im Ursprungs-Channel
            await self.remove_from_all_targets(msg.id)
            return

        # Alle Reactions, die gleichauf sind, bestimmen die Zielkanäle
        selected_indices = [i for i, c in enumerate(counts) if c == max_count]

        # Zuerst alte Spiegelungen löschen, die nicht mehr gebraucht werden
        current_targets = set(TARGET_CHANNELS[i] for i in selected_indices)
        await self.remove_from_all_targets(msg.id, keep_channels=current_targets)

        for idx in selected_indices:
            target_channel_id = TARGET_CHANNELS[idx]
            target_channel = self.bot.get_channel(target_channel_id)
            if not target_channel:
                continue

            # Prüfen, ob bereits gespiegelt
            if msg.id in self.mirrored_messages and target_channel_id in self.mirrored_messages[msg.id]:
                continue  # existiert schon

            embed = discord.Embed(description=f"[Jump to original message]({msg.jump_url})", color=discord.Color.purple())
            embed.set_author(name=msg.author.display_name, icon_url=msg.author.display_avatar.url)
            embed.set_image(url=msg.attachments[0].url)

            mirrored_msg = await target_channel.send(embed=embed)

            # speichern
            self.mirrored_messages.setdefault(msg.id, {})[target_channel_id] = mirrored_msg.id

    async def remove_from_all_targets(self, original_msg_id: int, keep_channels: set[int] = None):
        """Löscht alle Spiegelungen außer denen, die in keep_channels bleiben sollen."""
        if original_msg_id not in self.mirrored_messages:
            return
        for channel_id, mirrored_id in list(self.mirrored_messages[original_msg_id].items()):
            if keep_channels and channel_id in keep_channels:
                continue
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    m = await channel.fetch_message(mirrored_id)
                    await m.delete()
                except discord.NotFound:
                    pass
            del self.mirrored_messages[original_msg_id][channel_id]
        if not self.mirrored_messages[original_msg_id]:
            del self.mirrored_messages[original_msg_id]

async def setup(bot):
    await bot.add_cog(AutoReactCog(bot))
