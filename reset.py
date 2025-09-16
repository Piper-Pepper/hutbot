import discord
from discord.ext import commands
import asyncio

CUSTOM_REACTIONS = [
    "<:01sthumb:1387086056498921614>",
    "<:01smile_piper:1387083454575022213>",
    "<:02No:1347536448831754383>",
    "<:011:1346549711817146400>"
]

class ReactionResetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="reset_reactions")
    @commands.has_permissions(administrator=True)
    async def reset_reactions(self, ctx: commands.Context):
        """
        Scans the last 300 messages:
        Adds missing reactions only. Works for both embeds with images AND messages with attachments.
        """
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        else:
            await ctx.defer()

        changed = 0
        async for msg in ctx.channel.history(limit=300):
            # nur Nachrichten mit Embed-Bild oder Anhängen
            has_image = (msg.embeds and msg.embeds[0].image and msg.embeds[0].image.url) or msg.attachments
            if not has_image:
                continue

            # Alle Custom-Emojis aktuell als Set (IDs)
            current_ids = {r.emoji.id for r in msg.reactions if isinstance(r.emoji, discord.PartialEmoji)}
            # Normale Emojis als String
            current_str = {str(r.emoji) for r in msg.reactions if not isinstance(r.emoji, discord.PartialEmoji)}

            # fehlende Emojis erkennen
            missing = []
            for emoji in CUSTOM_REACTIONS:
                if emoji.startswith("<:") and ":" in emoji:
                    name, id_ = emoji[2:-1].split(":")
                    if int(id_) not in current_ids:
                        missing.append(emoji)
                else:
                    if emoji not in current_str:
                        missing.append(emoji)

            if not missing:
                continue  # alles da, skip

            # fehlende Reactions hinzufügen
            for emoji in missing:
                try:
                    if emoji.startswith("<:") and ":" in emoji:
                        name, id_ = emoji[2:-1].split(":")
                        emoji_obj = discord.PartialEmoji(name=name, id=int(id_))
                        await msg.add_reaction(emoji_obj)
                    else:
                        await msg.add_reaction(emoji)
                    await asyncio.sleep(0.3)  # kurze Pause zwischen Reactions
                except discord.HTTPException:
                    pass

            changed += 1
            await asyncio.sleep(0.2)  # kleine Pause zwischen Nachrichten, nur bei Änderungen

        await ctx.send(f"✅ {changed} messages had missing reactions added.")

async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
