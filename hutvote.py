# cogs/hutvote.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import calendar

ALLOWED_ROLE = 1346428405368750122
BOT_ID = 1379906834588106883

CATEGORY_CHOICES = [
    app_commands.Choice(name="💯 SFW", value="1416461717038170294"),
    app_commands.Choice(name="🔞 NSFW", value="1415769711052062820"),
]

# Map ID -> Anzeigename
CATEGORY_NAME_MAP = {c.value: c.name for c in CATEGORY_CHOICES}


class HutVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="hut_vote", description="Shows the Top voted posts for a category and month.")
    @app_commands.describe(
        category="Select a category",
        month="Month (as number, e.g. 9 for September)",
        year="Year (e.g. 2025)",
        top_count="How many top posts should be displayed?",
        ephemeral="Only visible to you?"
    )
    @app_commands.choices(category=CATEGORY_CHOICES)
    async def hut_vote(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
        month: int,
        year: int,
        top_count: int = 5,
        ephemeral: bool = False
    ):
        await interaction.response.defer(ephemeral=ephemeral)

        guild = interaction.guild
        category_obj = guild.get_channel(int(category.value))

        if not category_obj:
            await interaction.followup.send("❌ Could not find the selected category.", ephemeral=True)
            return

        # Pretty Category Name
        pretty_category_name = CATEGORY_NAME_MAP.get(str(category_obj.id), category_obj.name)

        # Sammle Messages
        messages = []
        for channel in category_obj.text_channels:
            try:
                async for msg in channel.history(limit=200):
                    if msg.author.id == BOT_ID and msg.embeds:
                        embed = msg.embeds[0]
                        if embed.footer and "Votes" in embed.footer.text:
                            messages.append(msg)
            except Exception:
                continue

        if not messages:
            await interaction.followup.send("❌ No valid messages with votes found in this category.", ephemeral=True)
            return

        # Votes sortieren
        def extract_votes(message: discord.Message):
            try:
                footer = message.embeds[0].footer.text
                return int(footer.split("Votes: ")[1].split(" ")[0])
            except Exception:
                return 0

        messages.sort(key=extract_votes, reverse=True)
        top_messages = messages[:top_count]

        # Intro Embed
        intro_embed = discord.Embed(
            title=f"🏆 Top {top_count} in {pretty_category_name}",
            description=(
                f"This is the **Top {top_count}** in **{pretty_category_name}** "
                f"for {calendar.month_name[int(month)]} {year}."
            ),
            color=discord.Color.gold()
        )
        intro_embed.set_footer(text=f"Requested by {interaction.user.display_name}")

        # Startpost (öffentlich oder ephemeral)
        if ephemeral:
            intro_msg = await interaction.followup.send(embed=intro_embed, wait=True)
        else:
            intro_msg = await interaction.channel.send(embed=intro_embed)

        # Alle Top-N Ergebnisse als Antworten auf Intro
        for msg in top_messages:
            embed = msg.embeds[0]
            # Footer um Category ergänzen
            embed.set_footer(
                text=f"Category: {pretty_category_name} | Channel: {msg.channel.name} | Votes: {extract_votes(msg)}"
            )
            if ephemeral:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await intro_msg.channel.send(embed=embed, reference=intro_msg)

        # Top 1 Ansage
        top1 = top_messages[0]
        top1_creator = top1.embeds[0].author
        top1_mention = top1.embeds[0].author.name if top1_creator else "Unknown"

        announce_text = (
            f"In {calendar.month_name[int(month)]}/{year}, "
            f"the user **{top1_mention}** has created the image with the most total votes "
            f"in **{pretty_category_name}**!"
        )

        if ephemeral:
            await interaction.followup.send(announce_text, ephemeral=True)
        else:
            await intro_msg.channel.send(announce_text, reference=intro_msg)


async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))
