import discord
from discord.ext import commands

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
    @commands.has_permissions(administrator=True)  # Only admins can run
    async def reset_reactions(self, ctx: commands.Context):
        """
        Scans the last 300 messages:
        Resets reactions only if **any of the 4 custom reactions are missing**.
        Number of occurrences does not matter.
        """
        await ctx.defer(ephemeral=True)

        changed = 0
        async for msg in ctx.channel.history(limit=300):
            if not msg.embeds:
                continue
            embed = msg.embeds[0]
            if not embed.image or not embed.image.url:
                continue

            # Gather current reactions as a set (only emojis)
            current = {str(r.emoji) for r in msg.reactions}

            # If all 4 custom reactions are present → skip
            if all(emoji in current for emoji in CUSTOM_REACTIONS):
                continue

            # Otherwise: clear and re-add reactions
            try:
                await msg.clear_reactions()
            except discord.Forbidden:
                await ctx.send("❌ Missing permissions to clear reactions.", ephemeral=True)
                return
            except discord.HTTPException:
                pass  # Already empty or other minor error

            for emoji in CUSTOM_REACTIONS:
                try:
                    await msg.add_reaction(emoji)
                except discord.HTTPException:
                    pass

            changed += 1

        await ctx.send(f"✅ {changed} messages have been reset with reactions.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
