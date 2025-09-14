import discord
from discord.ext import commands

# Custom Emoji IDs
CUSTOM_EMOJI_IDS = [
    1387086056498921614,  # <:01sthumb:1387086056498921614>
    1387083454575022213,  # <:01smile_piper:1387083454575022213>
    1347536448831754383,  # <:02No:1347536448831754383>
    1346549711817146400   # <:011:1346549711817146400>
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
        """
        await ctx.defer(ephemeral=True)

        changed = 0
        async for msg in ctx.channel.history(limit=200):
            # Nur Nachrichten mit Embed
            if not msg.embeds:
                continue
            embed = msg.embeds[0]
            # Nur Embed mit Bild
            if not embed.image or not embed.image.url:
                continue

            # Aktuelle Reactions als Set von IDs sammeln
            current_ids = set()
            for r in msg.reactions:
                if isinstance(r.emoji, discord.Emoji):
                    current_ids.add(r.emoji.id)

            # Prüfen: alle Custom-Emojis vorhanden?
            if all(eid in current_ids for eid in CUSTOM_EMOJI_IDS):
                continue  # alles da, nix löschen

            # Sonst: löschen + neu setzen
            try:
                await msg.clear_reactions()
            except discord.Forbidden:
                await ctx.send("❌ Keine Berechtigung, Reaktionen zu löschen.", ephemeral=True)
                return
            except discord.HTTPException:
                pass  # falls schon leer oder Fehler

            # Reactions hinzufügen
            for eid in CUSTOM_EMOJI_IDS:
                try:
                    emoji = discord.utils.get(ctx.guild.emojis, id=eid)
                    if emoji:
                        await msg.add_reaction(emoji)
                except discord.HTTPException:
                    pass

            changed += 1

        await ctx.send(f"✅ {changed} Nachrichten wurden neu mit Reaktionen versehen.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
