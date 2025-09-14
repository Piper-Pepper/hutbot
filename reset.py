import discord
from discord.ext import commands

# Custom Emoji IDs
CUSTOM_EMOJI_IDS = [
    1387086056498921614,  # <:01sthumb:1387086056498921614>
    1387083454575022213,  # <:01smile_piper:1387083454575022213>
    1347536448831754383,  # <:02No:1347536448831754383>
    1346549711817146300   # <:011:1346549711817146300>
]

class ReactionResetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="reset_reactions")
    @commands.has_permissions(manage_messages=True)
    async def reset_reactions(self, ctx: commands.Context):
        """
        Durchsucht die letzten 300 Nachrichten:
        Reset nur, wenn eine der 4 Custom-Reactions fehlt.
        Gilt für Nachrichten mit:
          - Embed mit Bild
          - oder Attachment, das ein Bild ist
        """
        await ctx.defer(ephemeral=True)

        changed = 0
        async for msg in ctx.channel.history(limit=300):
            has_image = False

            # Prüfen: Embed mit Bild
            if msg.embeds:
                for embed in msg.embeds:
                    if embed.image and embed.image.url:
                        has_image = True
                        break

            # Prüfen: Attachment ist ein Bild
            if not has_image and msg.attachments:
                for att in msg.attachments:
                    if att.content_type and att.content_type.startswith("image"):
                        has_image = True
                        break

            if not has_image:
                continue  # keine Bildnachricht

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
