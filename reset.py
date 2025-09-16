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
        Resets reactions if any of the 4 custom reactions are missing.
        Works for both embeds with images AND messages with attachments.
        """
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        else:
            await ctx.defer()

        changed = 0
        async for msg in ctx.channel.history(limit=300):
            # Check if message has either an embed with an image OR attachments
            has_image = (msg.embeds and msg.embeds[0].image and msg.embeds[0].image.url) or msg.attachments
            if not has_image:
                continue

            current_reactions = {str(r.emoji) for r in msg.reactions}

            # All reactions present? Skip
            if all(emoji in current_reactions for emoji in CUSTOM_REACTIONS):
                continue

            # Reset reactions
            try:
                await msg.clear_reactions()
            except discord.Forbidden:
                await ctx.send("❌ Missing permissions to clear reactions.")
                return
            except discord.HTTPException:
                pass

            for emoji in CUSTOM_REACTIONS:
                try:
                    if emoji.startswith("<:") and ":" in emoji:
                        name_id = emoji[2:-1]
                        name, id_ = name_id.split(":")
                        emoji_obj = discord.PartialEmoji(name=name, id=int(id_))
                        await msg.add_reaction(emoji_obj)
                    else:
                        await msg.add_reaction(emoji)
                except discord.HTTPException:
                    pass

            changed += 1
            await asyncio.sleep(0.25)  # short wait to avoid rate limits

        await ctx.send(f"✅ {changed} messages have been reset with reactions.")

async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
