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
        Scans the last 300 messages in the channel.
        Adds missing reactions only (Embeds with images & attachments).
        Provides detailed feedback per message.
        """
        # Slash vs Text Command
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
            feedback_method = ctx.interaction.followup
        else:
            await ctx.defer()
            feedback_method = ctx

        changed = 0
        skipped = 0

        async for msg in ctx.channel.history(limit=300, oldest_first=True):
            has_image = (msg.embeds and msg.embeds[0].image and msg.embeds[0].image.url) or msg.attachments
            if not has_image:
                continue  # Keine Bilder/Embeds, skip

            # Aktuelle Reactions als Set
            current_reactions = {str(r.emoji) for r in msg.reactions}
            missing = [emoji for emoji in CUSTOM_REACTIONS if emoji not in current_reactions]

            if not missing:
                skipped += 1
                print(f"⏭ Skipped message {msg.id} (all reactions present)")
                continue

            # Fehlende Reactions hinzufügen
            success = True
            for emoji in missing:
                try:
                    if emoji.startswith("<:") and ":" in emoji:
                        name_id = emoji[2:-1]
                        name, id_ = name_id.split(":")
                        emoji_obj = discord.PartialEmoji(name=name, id=int(id_))
                        await msg.add_reaction(emoji_obj)
                    else:
                        await msg.add_reaction(emoji)
                    await asyncio.sleep(0.3)  # kurze Pause pro Reaction
                except discord.HTTPException:
                    success = False
                    print(f"⚠️ Failed to add {emoji} to message {msg.id}")

            if success:
                changed += 1
                print(f"✅ Updated message {msg.id} with missing reactions: {missing}")

            await asyncio.sleep(0.5)  # Pause pro Message, nur bei Änderungen

        await feedback_method.send(
            f"✅ {changed} messages updated with missing reactions.\n⏭ {skipped} messages skipped (all reactions present)."
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
