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

TOPUSER_CHOICES = [
    app_commands.Choice(name="Top 5", value="5"),
    app_commands.Choice(name="Top 10", value="10"),
    app_commands.Choice(name="Top 20", value="20"),
]

# Jahr-Choices: dieses Jahr und letztes Jahr
current_year = datetime.utcnow().year
YEAR_CHOICES = [
    app_commands.Choice(name=str(current_year), value=str(current_year)),
    app_commands.Choice(name=str(current_year - 1), value=str(current_year - 1)),
]

# Monate als Dropdown
MONTH_CHOICES = [
    app_commands.Choice(name=calendar.month_name[i], value=str(i)) for i in range(1, 13)
]

# Map für hübsche Category-Namen
CATEGORY_NAME_MAP = {c.value: c.name for c in CATEGORY_CHOICES}

# Emoji → Caption Mapping (wird jetzt nur für Reihenfolge genutzt)
REACTION_CAPTIONS = {
    "<:01sthumb:1387086056498921614>": "Great!",
    "<:01smile_piper:1387083454575022213>": "LMFAO",
    "<:02No:1347536448831754383>": "No... just... no",
    "<:011:1346549711817146400>": "Better than 10",
    "<:011pump:1346549688836296787>": "Pump that Puppet!",
}


class HutVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="hut_vote",
        description="Shows the top posts by reactions for a category/month/year"
    )
    @app_commands.describe(
        year="Select year",
        month="Select month",
        category="Select category",
        topuser="Number of top posts to display",
        public="Whether the posts are public or ephemeral"
    )
    @app_commands.choices(
        year=YEAR_CHOICES,
        month=MONTH_CHOICES,
        category=CATEGORY_CHOICES,
        topuser=TOPUSER_CHOICES
    )
    @app_commands.checks.cooldown(1, 5)
    async def hut_vote(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        category: app_commands.Choice[str],
        topuser: app_commands.Choice[str] = None,
        public: bool = False
    ):
        top_count = int(topuser.value) if topuser else 5
        ephemeral_flag = not public

        # Permissions
        if not any(r.id == ALLOWED_ROLE for r in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return

        guild = interaction.guild
        category_obj = guild.get_channel(int(category.value))
        if not category_obj or not isinstance(category_obj, discord.CategoryChannel):
            await interaction.response.send_message("❌ Invalid category.", ephemeral=True)
            return

        pretty_category_name = CATEGORY_NAME_MAP.get(str(category_obj.id), category_obj.name)

        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        # Time range
        start_dt = datetime(int(year.value), int(month.value), 1, tzinfo=timezone.utc)
        last_day = calendar.monthrange(int(year.value), int(month.value))[1]
        end_dt = datetime(int(year.value), int(month.value), last_day, 23, 59, 59, tzinfo=timezone.utc)

        matched_msgs = []
        for channel in category_obj.channels:
            if not isinstance(channel, discord.TextChannel):
                continue
            overwrites = channel.overwrites_for(guild.default_role)
            if overwrites.view_channel is False:
                continue
            perms = channel.permissions_for(guild.me)
            if not perms.view_channel or not perms.read_message_history:
                continue
            try:
                async for msg in channel.history(after=start_dt, before=end_dt, limit=None):
                    if msg.author.id != BOT_ID:
                        continue
                    matched_msgs.append(msg)
            except Exception:
                continue

        if not matched_msgs:
            await interaction.followup.send(
                f"No posts found in {calendar.month_name[int(month.value)]} {year.value}.",
                ephemeral=ephemeral_flag
            )
            return

        # Sortierfunktion
        def sort_key(msg: discord.Message):
            sorted_reacts = sorted(msg.reactions, key=lambda r: r.count, reverse=True)
            top5_sum = sum(r.count for r in sorted_reacts[:5])
            extra_sum = sum(r.count for r in sorted_reacts[5:])
            return (top5_sum, extra_sum, msg.created_at)

        top_msgs = sorted(matched_msgs, key=sort_key, reverse=True)[:top_count]

        # INTRO
        intro_embed = discord.Embed(
            title=f"🏆 Top {top_count} in {pretty_category_name} ({calendar.month_name[int(month.value)]} {year.value})",
            description=(f"This is the **Top {top_count}** in **{pretty_category_name}** "
                         f"for {calendar.month_name[int(month.value)]} {year.value}."),
            color=discord.Color.gold()
        )
        intro_embed.set_footer(
            text=f"{guild.name} Rankings",
            icon_url=guild.icon.url if guild.icon else discord.Embed.Empty
        )
        intro_msg = await interaction.followup.send(embed=intro_embed, wait=True)

        # Sub-Messages als normale Nachrichten
        for idx, msg in enumerate(top_msgs, start=1):
            sorted_reacts = sorted(msg.reactions, key=lambda r: r.count, reverse=True)

            # Top-5 Emojis nebeneinander (nur >0 anzeigen)
            reaction_parts = []
            used_emojis = set()
            for emoji_key in REACTION_CAPTIONS:
                r = next(
                    (r for r in sorted_reacts if (
                        str(r.emoji) if not isinstance(r.emoji, discord.Emoji)
                        else f"<:{r.emoji.name}:{r.emoji.id}>"
                    ) == emoji_key),
                    None
                )
                if r:
                    count = r.count - 1  # Bot-Reaction abziehen
                    if count > 0:  # nur anzeigen, wenn größer als 0
                        reaction_parts.append(f"{str(r.emoji)} {count}")
                        used_emojis.add(r.emoji)

            reaction_line = " ".join(reaction_parts)


            # Additional Reactions auch nebeneinander
            extra_parts = []
            extra_reacts = [r for r in sorted_reacts if r.emoji not in used_emojis]
            for r in extra_reacts:
                if r.count > 0:
                    extra_parts.append(f"{str(r.emoji)} {r.count}")

            extra_text = " ".join(extra_parts) if extra_parts else ""

            # Beschreibung zusammensetzen
            if extra_text:
                description_text = f"{reaction_line}\n\n{extra_text}\n\n[◀️ Jump to Original to vote 📈]({msg.jump_url})"
            else:
                description_text = f"{reaction_line}\n\n[◀️ Jump to Original to vote 📈]({msg.jump_url})"

            # Creator Infos
            creator = msg.mentions[0] if msg.mentions else msg.author
            creator_name = creator.display_name
            creator_avatar = creator.display_avatar.url

            # Titel zeigt Channel-Name
            title = f"🎨 #{idx}\n👉 **Creator: ** *{creator_name}*"

            # Bildquelle
            img_url = None
            if msg.attachments:
                img_url = msg.attachments[0].url
            elif msg.embeds:
                for e in msg.embeds:
                    if e.image:
                        img_url = e.image.url
                        break
                    elif e.thumbnail:
                        img_url = e.thumbnail.url
                        break

            # Embed bauen
            embed = discord.Embed(
                title=title,
                description=description_text,
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=creator_avatar)
            embed.set_footer(
                text=f"Category: {pretty_category_name} | Channel: {msg.channel.name}",
                icon_url=guild.icon.url if guild.icon else discord.Embed.Empty
            )
            if img_url:
                embed.set_image(url=img_url)

            await intro_msg.channel.send(embed=embed)

        # Top1 Creator extra Announcement
        top1_msg = top_msgs[0]
        top1_creator_mention = top1_msg.mentions[0].mention if top1_msg.mentions else top1_msg.author.mention
        await intro_msg.channel.send(
            f"In {calendar.month_name[int(month.value)]}/{year.value}, the user {top1_creator_mention} "
            f"has created the image with most total votes in {pretty_category_name}! 🎉"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))
