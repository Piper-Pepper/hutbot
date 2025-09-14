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
    @commands.has_permissions(manage_messages=True)
    async def reset_reactions(self, ctx: commands.Context):
        """
        Durchsucht die letzten 200 Nachrichten:
        Reset nur, wenn **eine der 4 Custom-Reactions fehlt**.
        Anzahl der Vorkommen ist egal.
        """
        await ctx.defer(ephemeral=True)

        changed = 0
        async for msg in ctx.channel.history(limit=200):
            if not msg.embeds:
                continue
            embed = msg.embeds[0]
            if not embed.image or not embed.image.url:
                continue

            # Aktuelle Reactions als Set sammeln (nur die Emojis)
            current = {str(r.emoji) for r in msg.reactions}

            # Wenn alle 4 Custom-Reactions vorhanden sind → skip
            if all(emoji in current for emoji in CUSTOM_REACTIONS):
                continue

            # Sonst: alles löschen + neu setzen
            try:
                await msg.clear_reactions()
            except discord.Forbidden:
                await ctx.send("❌ Keine Berechtigung, Reaktionen zu löschen.", ephemeral=True)
                return
            except discord.HTTPException:
                pass  # Falls schon leer oder Fehler

            for emoji in CUSTOM_REACTIONS:
                try:
                    await msg.add_reaction(emoji)
                except discord.HTTPException:
                    pass

            changed += 1

        await ctx.send(f"✅ {changed} Nachrichten wurden neu mit Reaktionen versehen.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
