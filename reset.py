import discord
from discord.ext import commands

CUSTOM_REACTIONS = [
    "<:01sthumb:1387086056498921614>",
    "<:01smile_piper:1387083454575022213>",
    "<:02No:1347536448831754383>",
    "<:011:11346549711817146400>"
]

class ReactionResetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="reset_reactions")
    @commands.has_permissions(manage_messages=True)
    async def reset_reactions(self, ctx: commands.Context):
        """
        Durchsucht die letzten 50 Nachrichten:
        Wenn Embed mit Bild und die gewünschten Reactions NICHT alle da sind →
        alle Reaktionen löschen und die Custom-Reactions setzen.
        """
        changed = 0
        async for msg in ctx.channel.history(limit=50):
            if not msg.embeds:
                continue
            embed = msg.embeds[0]
            if not embed.image or not embed.image.url:
                continue

            # Aktuelle Reactions einsammeln
            current = {str(r.emoji) for r in msg.reactions}

            # Wenn alle Custom-Reactions schon da sind → skip
            if all(e in current for e in CUSTOM_REACTIONS):
                continue

            # Sonst: alles löschen + neu setzen
            try:
                await msg.clear_reactions()
            except discord.Forbidden:
                await ctx.reply("❌ Keine Berechtigung, Reaktionen zu löschen.", ephemeral=True)
                return
            except discord.HTTPException:
                pass

            for emoji in CUSTOM_REACTIONS:
                try:
                    await msg.add_reaction(emoji)
                except discord.HTTPException:
                    pass

            changed += 1

        await ctx.reply(f"✅ {changed} Nachrichten wurden neu mit Reaktionen versehen.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
