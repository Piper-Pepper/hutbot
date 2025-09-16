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
            has_image = (msg.embeds and msg.embeds[0].image and msg.embeds[0].image.url) or msg.attachments
            if not has_image:
                continue

            current_reactions = {str(r.emoji) for r in msg.reactions}
            missing = [emoji for emoji in CUSTOM_REACTIONS if emoji not in current_reactions]

            if not missing:
                continue  # alles da, skip

            for emoji in missing:
                try:
                    if emoji.startswith("<:") and ":" in emoji:
                        name_id = emoji[2:-1]
                        name, id_ = name_id.split(":")
                        emoji_obj = discord.PartialEmoji(name=name, id=int(id_))
                        await msg.add_reaction(emoji_obj)
                    else:
                        await msg.add_reaction(emoji)
                    # Dynamische Pause zwischen Reactions
                    await asyncio.sleep(0.1 + 0.05 * len(missing))
                except discord.HTTPException:
                    pass

            changed += 1
            # Dynamische Pause zwischen Nachrichten
            await asyncio.sleep(0.2 + 0.1 * len(missing))

        await ctx.send(f"✅ {changed} messages had missing reactions added.")

async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
