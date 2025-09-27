import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime

# Diese Reactions werden gezählt
REACTIONS = {
    "<:01sthumb:1387086056498921614>": 1387086056498921614,
    "<:01smile_piper:1387083454575022213>": 1387083454575022213,
    "<:02No:1347536448831754383>": 1347536448831754383,
    "<:011:1346549711817146400>": 1346549711817146400,
    "<:011pump:1346549688836296787>": 1346549688836296787,
}

# Nur diese Rolle darf den Command ausführen
ALLOWED_ROLE = 1346428405368750122


class HutVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="hut_vote",
        description="Zeigt die Top 3 Reactions in einer Kategorie seit einem Datum"
    )
    @app_commands.describe(
        datum="Startdatum im Format YYYY-MM-DD",
        category="Kategorie auswählen"
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="📂 Kategorie 1", value="1416461717038170294"),
        app_commands.Choice(name="📂 Kategorie 2", value="1415769711052062820"),
    ])
    async def hut_vote(
        self,
        interaction: discord.Interaction,
        datum: str,
        category: app_commands.Choice[str]
    ):
        # check role
        if not any(r.id == ALLOWED_ROLE for r in interaction.user.roles):
            await interaction.response.send_message(
                "❌ Keine Berechtigung für diesen Befehl.",
                ephemeral=True
            )
            return

        # Datum validieren
        try:
            start_date = datetime.strptime(datum, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "❌ Ungültiges Datum! Bitte YYYY-MM-DD nutzen.",
                ephemeral=True
            )
            return

        category_channel = interaction.guild.get_channel(int(category.value))
        if not category_channel or not isinstance(category_channel, discord.CategoryChannel):
            await interaction.response.send_message(
                "❌ Ungültige Kategorie.",
                ephemeral=True
            )
            return

        results = {emoji: [] for emoji in REACTIONS.keys()}

        # Channels durchgehen
        for channel in category_channel.channels:
            if isinstance(channel, discord.TextChannel):
                overwrites = channel.overwrites_for(interaction.guild.default_role)
                if overwrites.view_channel is False:  # @everyone darf nicht gucken
                    continue

                async for msg in channel.history(after=start_date, limit=None):
                    for reaction in msg.reactions:
                        if str(reaction.emoji) in REACTIONS:
                            results[str(reaction.emoji)].append(
                                (reaction.count, msg.jump_url)
                            )

        # Embed bauen
        embed = discord.Embed(
            title=f"Top 3 Reactions seit {datum} in {category_channel.name}",
            color=discord.Color.green()
        )

        for emoji, entries in results.items():
            if not entries:
                embed.add_field(name=emoji, value="Keine Daten", inline=False)
                continue
            top3 = sorted(entries, key=lambda x: x[0], reverse=True)[:3]
            lines = [f"{count}x [Post]({url})" for count, url in top3]
            embed.add_field(name=emoji, value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(HutVote(bot))
