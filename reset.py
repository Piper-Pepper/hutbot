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
        Scans the last 300 messages.
        Adds missing reactions only.
        Works for embeds with images AND messages with attachments.
        Provides feedback for each message.
        """
        is_slash = ctx.interaction is not None
        # Erste Antwort vorbereiten
        if is_slash:
            await ctx.interaction.response.defer(ephemeral=True)

        updated_count = 0
        skipped_count = 0

        for msg in await ctx.channel.history(limit=300).flatten():
            has_image = (msg.embeds and msg.embeds[0].image and msg.embeds[0].image.url) or msg.attachments
            if not has_image:
                continue

            current_reactions = {str(r.emoji) for r in msg.reactions}
            missing = [emoji for emoji in CUSTOM_REACTIONS if emoji not in current_reactions]

            if not missing:
                skipped_count += 1
                feedback = f"✅ Skipped message {msg.id} (all reactions present)"
                if is_slash:
                    await ctx.interaction.followup.send(feedback, ephemeral=True)
                else:
                    await ctx.send(feedback)
                continue

            # Fehlende Reactions hinzufügen
            for emoji in missing:
                try:
                    if emoji.startswith("<:") and ":" in emoji:
                        name_id = emoji[2:-1]
                        name, id_ = name_id.split(":")
                        await msg.add_reaction(discord.PartialEmoji(name=name, id=int(id_)))
                    else:
                        await msg.add_reaction(emoji)
                    await asyncio.sleep(0.25)  # Pause zwischen Reactions
                except discord.HTTPException:
                    pass

            updated_count += 1
            feedback = f"✅ Updated message {msg.id} with missing reactions: {missing}"
            if is_slash:
                await ctx.interaction.followup.send(feedback, ephemeral=True)
            else:
                await ctx.send(feedback)

            await asyncio.sleep(0.5)  # Pause zwischen Nachrichten

        final_summary = f"✅ Done! {updated_count} messages updated, {skipped_count} messages already complete."
        if is_slash:
            await ctx.interaction.followup.send(final_summary, ephemeral=True)
        else:
            await ctx.send(final_summary)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
