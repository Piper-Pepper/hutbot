# Cleaned and optimized HutVote cog
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import calendar

ALLOWED_ROLE = 1346428405368750122
BOT_ID = 1379906834588106883
CATEGORY_ID = 1415769711052062820

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

# ------------------------------------------------------------------
# SAFER EMOJI HANDLING
# ------------------------------------------------------------------
EMOJI_POINTS = {
    "1️⃣": 1,
    "2️⃣": 2,
    "3️⃣": 3,
    1346549711817146400: 5,  # CUSTOM EMOJI ID ONLY
}


def normalize_emoji(r):
    """Convert reaction emoji into a comparable key.
    Unicode → the emoji itself
    Custom → return ID
    """
    if isinstance(r.emoji, discord.PartialEmoji) or isinstance(r.emoji, discord.Emoji):
        return r.emoji.id
    return str(r.emoji)


class HutVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ai_vote", description="Shows AI image ranking by reactions")
    @app_commands.describe(
        year="Select year",
        month="Select month",
        topuser="Number of top posts to display",
        public="Whether the result is public or ephemeral"
    )
    @app_commands.choices(year=YEAR_CHOICES, month=MONTH_CHOICES, topuser=TOPUSER_CHOICES)
    @app_commands.checks.cooldown(1, 5)
    async def ai_vote(self, interaction: discord.Interaction,
                      year: app_commands.Choice[str],
                      month: app_commands.Choice[str],
                      topuser: app_commands.Choice[str] = None,
                      public: bool = False):

        # -------------------------------------------------------------
        # BASIC PERMISSION
        # -------------------------------------------------------------
        if not any(r.id == ALLOWED_ROLE for r in getattr(interaction.user, "roles", [])):
            return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

        top_count = int(topuser.value) if topuser else 5
        ephemeral_flag = not public

        guild = interaction.guild
        category_obj = guild.get_channel(CATEGORY_ID)
        if not isinstance(category_obj, discord.CategoryChannel):
            return await interaction.response.send_message("❌ Category not found.", ephemeral=True)

        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        # -------------------------------------------------------------
        # DATE RANGE
        # -------------------------------------------------------------
        start_dt = datetime(int(year.value), int(month.value), 1, tzinfo=timezone.utc)
        last_day = calendar.monthrange(int(year.value), int(month.value))[1]
        end_dt = datetime(int(year.value), int(month.value), last_day, 23, 59, 59, tzinfo=timezone.utc)

        # -------------------------------------------------------------
        # SCAN MESSAGES
        # -------------------------------------------------------------
        matched_msgs = []
        for channel in category_obj.channels:
            if not isinstance(channel, discord.TextChannel):
                continue

            perms = channel.permissions_for(guild.me)
            if not perms.view_channel or not perms.read_message_history:
                continue

            try:
                async for msg in channel.history(after=start_dt, before=end_dt, limit=None):
                    if msg.author.id == BOT_ID:
                        matched_msgs.append(msg)
            except Exception:
                pass

        if not matched_msgs:
            return await interaction.followup.send(
                f"No AI posts found in {calendar.month_name[int(month.value)]} {year.value}.",
                ephemeral=ephemeral_flag
            )

        # -------------------------------------------------------------
        # SCORING
        # -------------------------------------------------------------
        def calc_ai_points(msg: discord.Message):
            react_map = {}
            for r in msg.reactions:
                key = normalize_emoji(r)
                if key in EMOJI_POINTS:
                    react_map[key] = r.count

            # All 4 present → 0
            if len(react_map) == 4 and all(react_map[k] > 0 for k in react_map):
                return 0

            total = sum(react_map.values())
            if total <= 1:
                return 0

            score = 0
            for key, count in react_map.items():
                if count > 1:
                    score += (count - 1) * EMOJI_POINTS[key]
            return score

        top_msgs = sorted(
            matched_msgs,
            key=lambda m: (calc_ai_points(m), m.created_at),
            reverse=True
        )[:top_count]

        # -------------------------------------------------------------
        # INTRO EMBED
        # -------------------------------------------------------------
        intro_embed = discord.Embed(
            title=f"🤖 AI Top {top_count} — {calendar.month_name[int(month.value)]} {year.value}",
            description=(
                "Scoring system:\n"
                "1️⃣ = 1 point\n"
                "2️⃣ = 2 points\n"
                "3️⃣ = 3 points\n"
                "Custom Emoji = 5 points\n\n"
                "All four present → 0 points"
            ),
            color=discord.Color.blurple()
        )
        intro_embed.set_footer(
            text=f"{guild.name} AI Rankings | {category_obj.name}",
            icon_url=guild.icon.url if guild.icon else None
        )

        intro_msg = await interaction.followup.send(embed=intro_embed)

        # -------------------------------------------------------------
        # RESULTS
        # -------------------------------------------------------------
        for idx, msg in enumerate(top_msgs, start=1):
            score = calc_ai_points(msg)
            creator = msg.mentions[0] if msg.mentions else msg.author

            embed = discord.Embed(
                title=f"#{idx} — {creator.display_name} — {score} pts",
                description=f"[Jump to Post]({msg.jump_url})",
                color=discord.Color.teal()
            )
            embed.set_thumbnail(url=creator.display_avatar.url)

            img_url = None
            if msg.attachments:
                img_url = msg.attachments[0].url
            else:
                for e in msg.embeds:
                    if e.image:
                        img_url = e.image.url; break
                    if e.thumbnail:
                        img_url = e.thumbnail.url; break

            if img_url:
                embed.set_image(url=img_url)

            await intro_msg.channel.send(embed=embed)

        top_creator = top_msgs[0].mentions[0] if top_msgs[0].mentions else top_msgs[0].author
        await intro_msg.channel.send(f"🏅 **{top_creator.mention}** achieved the highest AI score!")


async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))