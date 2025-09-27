# cogs/hutvote.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta

# --- Konfiguration ---
# Custom emoji IDs -> Label (wird als Feld-Name im Embed verwendet)
REACTIONS = {
    1387086056498921614: "<:01sthumb:1387086056498921614>",
    1387083454575022213: "<:01smile_piper:1387083454575022213>",
    1347536448831754383: "<:02No:1347536448831754383>",
    1346549711817146400: "<:011:1346549711817146400>",
    1346549688836296787: "<:011pump:1346549688836296787>",
}

# Rolle, die den Command nutzen darf (als int)
ALLOWED_ROLE = 1346428405368750122

# Category choices (nur diese beiden auswählbar)
CATEGORY_CHOICES = [
    app_commands.Choice(name="📂 Kategorie 1", value="1416461717038170294"),
    app_commands.Choice(name="📂 Kategorie 2", value="1415769711052062820"),
]


class HutVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="hut_vote",
        description="Zeigt die Top 3 Posts je Reaction (seit Datum) in einer Kategorie"
    )
    @app_commands.describe(
        start_date="Startdatum im Format YYYY-MM-DD (inklusive)",
        category="Kategorie auswählen"
    )
    @app_commands.choices(category=CATEGORY_CHOICES)
    async def hut_vote(self, interaction: discord.Interaction, start_date: str, category: app_commands.Choice[str]):
        # --- Berechtigungscheck ---
        member_roles = getattr(interaction.user, "roles", [])
        if not any(r.id == ALLOWED_ROLE for r in member_roles):
            await interaction.response.send_message("❌ Keine Berechtigung für diesen Befehl.", ephemeral=True)
            return

        # --- Datum parsen (inklusive ganzer Tag) ---
        try:
            start_day = datetime.strptime(start_date, "%Y-%m-%d")
            # setze auf UTC midnight und mache tz-aware
            start_dt = start_day.replace(tzinfo=timezone.utc)
            # wir setzen 'after' auf eine Sekunde *vor* dem Startdatum,
            # damit Messages mit genau start_dt nicht verloren gehen
            after_dt = start_dt - timedelta(seconds=1)
        except ValueError:
            await interaction.response.send_message("❌ Ungültiges Datum. Bitte im Format YYYY-MM-DD angeben.", ephemeral=True)
            return

        # Kategorie holen
        guild = interaction.guild
        try:
            category_obj = guild.get_channel(int(category.value))
        except Exception:
            category_obj = None

        if not category_obj or not isinstance(category_obj, discord.CategoryChannel):
            await interaction.response.send_message("❌ Ungültige Kategorie.", ephemeral=True)
            return

        # defer (da das Durchsuchen länger dauern kann)
        await interaction.response.defer(thinking=True)

        # Ergebnisse struktur: emoji_id -> list of tuples (count, created_at, channel_name, jump_url, snippet)
        results = {eid: [] for eid in REACTIONS.keys()}

        # durch alle Textchannels in der Kategorie iterieren
        for channel in category_obj.channels:
            if not isinstance(channel, discord.TextChannel):
                continue

            # nur Channels, die @everyone sehen darf
            overwrites = channel.overwrites_for(guild.default_role)
            if overwrites.view_channel is False:
                continue

            # Bot-Permissions im Channel prüfen
            bot_member = guild.me or guild.get_member(self.bot.user.id)
            if bot_member is None:
                # fallback: versuche trotzdem; wenn Perms fehlen, skippen wir später
                perms = channel.permissions_for(guild.get_member(self.bot.user.id)) if guild.get_member(self.bot.user.id) else None
            else:
                perms = channel.permissions_for(bot_member)

            if not perms or not (perms.view_channel and perms.read_message_history):
                # Bot kann keinen Verlauf lesen -> skip
                continue

            # Messages durchgehen (nach after_dt)
            try:
                async for msg in channel.history(after=after_dt, limit=None):
                    for reaction in msg.reactions:
                        # bei Custom Emojis existiert .emoji.id (int); bei Unicode ist es ein str
                        emoji_obj = reaction.emoji
                        eid = getattr(emoji_obj, "id", None)
                        if eid is None:
                            # kein custom emoji -> ignorieren
                            continue
                        if eid in results:
                            snippet = (msg.content or "").replace("\n", " ")[:100]
                            results[eid].append((reaction.count, msg.created_at, channel.name, msg.jump_url, snippet))
            except discord.Forbidden:
                # Kanalverlauf nicht lesbar (trotz voriger Prüfung) -> skip
                continue
            except Exception:
                # sonstige Fehler in einem Channel sollen den ganzen Befehl nicht killen
                continue

        # Embed bauen mit Top-3 je Emoji
        embed = discord.Embed(
            title=f"Top 3 Posts pro Reaction seit {start_date} in {category_obj.name}",
            color=discord.Color.green()
        )

        for eid, entries in results.items():
            label = REACTIONS.get(eid, str(eid))
            if not entries:
                embed.add_field(name=label, value="Keine Daten", inline=False)
                continue

            # sortiere nach count desc, dann created_at desc (aktuellere Posts bevorzugen bei Gleichstand)
            top3 = sorted(entries, key=lambda x: (x[0], x[1]), reverse=True)[:3]
            lines = []
            for i, (count, created_at, ch_name, jump_url, snippet) in enumerate(top3, start=1):
                date_str = created_at.strftime("%Y-%m-%d")
                snippet_part = f" — {snippet}" if snippet else ""
                lines.append(f"{i}. **{count}x** in #{ch_name} ({date_str}) — [Link]({jump_url}){snippet_part}")
            embed.add_field(name=label, value="\n".join(lines), inline=False)

        # Ergebnis senden
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))
