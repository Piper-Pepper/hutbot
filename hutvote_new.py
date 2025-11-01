#hutvote_new.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import calendar

ALLOWED_ROLE = 1346428405368750122
BOT_ID = 1379906834588106883
CATEGORY_ID = 1415769711052062820  # Feste Kategorie-ID

TOPUSER_CHOICES = [
    app_commands.Choice(name="Top 5", value="5"),
    app_commands.Choice(name="Top 10", value="10"),
    app_commands.Choice(name="Top 20", value="20"),
]

current_year = datetime.utcnow().year
YEAR_CHOICES = [
    app_commands.Choice(name=str(current_year), value=str(current_year)),
    app_commands.Choice(name=str(current_year - 1), value=str(current_year - 1)),
]

MONTH_CHOICES = [
    app_commands.Choice(name=calendar.month_name[i], value=str(i)) for i in range(1, 13)
]


class HutVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="ai_vote",
        description="Shows AI image ranking by custom reaction scoring system"
    )
    @app_commands.describe(
        year="Select year",
        month="Select month",
        topuser="Number of top posts to display",
        public="Whether the posts are public or ephemeral"
    )
    @app_commands.choices(
        year=YEAR_CHOICES,
        month=MONTH_CHOICES,
        topuser=TOPUSER_CHOICES
    )
    @app_commands.checks.cooldown(1, 5)
    async def ai_vote(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        topuser: app_commands.Choice[str] = None,
        public: bool = False
    ):
        top_count = int(topuser.value) if topuser else 5
        ephemeral_flag = not public

        if not any(r.id == ALLOWED_ROLE for r in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return

        guild = interaction.guild
        category_obj = guild.get_channel(CATEGORY_ID)
        if not category_obj or not isinstance(category_obj, discord.CategoryChannel):
            await interaction.response.send_message("❌ Category not found.", ephemeral=True)
            return

        category_icon = "🤖"
        category_name = category_obj.name

        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

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
                f"No AI posts found in {calendar.month_name[int(month.value)]} {year.value}.",
                ephemeral=ephemeral_flag
            )
            return

        # Punktebewertungssystem
        EMOJI_POINTS = {
            "1️⃣": 1,
            "2️⃣": 2,
            "3️⃣": 3,
            "<:011:1346549711817146400>": 5
        }

        def calc_ai_points(msg: discord.Message):
            react_map = {}
            for r in msg.reactions:
                key = str(r.emoji) if not isinstance(r.emoji, discord.Emoji) else f"<:{r.emoji.name}:{r.emoji.id}>"
                if key in EMOJI_POINTS:
                    react_map[key] = r.count

            # Wenn alle vier Emojis vorkommen → 0 Punkte
            if all(key in react_map and react_map[key] > 0 for key in EMOJI_POINTS):
                return 0

            total_reacts = sum(react_map.values())
            if total_reacts <= 1:
                return 0

            score = 0
            for emoji_key, count in react_map.items():
                if count > 1:
                    score += (count - 1) * EMOJI_POINTS[emoji_key]
            return score

        # Sortierung nach Punktzahl
        top_msgs = sorted(
            matched_msgs,
            key=lambda m: (calc_ai_points(m), m.created_at),
            reverse=True
        )[:top_count]

        intro_embed = discord.Embed(
            title=f"🤖 AI Top {top_count} — {calendar.month_name[int(month.value)]} {year.value}",
            description=("Scoring:\n"
                         "1️⃣ = 1 point\n"
                         "2️⃣ = 2 points\n"
                         "3️⃣ = 3 points\n"
                         "<:011:1346549711817146400> = 5 points\n\n"
                         "If all four are present → 0 points."),
            color=discord.Color.blurple()
        )
        intro_embed.set_footer(
            text=f"{guild.name} AI Rankings | {category_name}",
            icon_url=guild.icon.url if guild.icon else discord.Embed.Empty
        )

        intro_msg = await interaction.followup.send(embed=intro_embed, wait=True)

        # Ergebnisanzeigen
        for idx, msg in enumerate(top_msgs, start=1):
            score = calc_ai_points(msg)

            creator = msg.mentions[0] if msg.mentions else msg.author
            creator_name = creator.display_name
            creator_avatar = creator.display_avatar.url

            title = f"{category_icon} #{idx} by {creator_name} — **{score} pts**"
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

            embed = discord.Embed(
                title=title,
                description=f"[Jump to Post]({msg.jump_url})",
                color=discord.Color.teal()
            )
            embed.set_thumbnail(url=creator_avatar)
            if img_url:
                embed.set_image(url=img_url)

            await intro_msg.channel.send(embed=embed)

        top1_msg = top_msgs[0]
        top1_creator = top1_msg.mentions[0] if top1_msg.mentions else top1_msg.author
        await intro_msg.channel.send(
            f"🏅 **{top1_creator.mention}** achieved the highest AI score in "
            f"{calendar.month_name[int(month.value)]} {year.value}! 🎉"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))
