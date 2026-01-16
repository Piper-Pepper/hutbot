import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import calendar
import traceback

ALLOWED_ROLE = 1346428405368750122
BOT_ID = 1379906834588106883

SCAN_CHANNEL_IDS = [
    1415769909874524262,
    1415769966573260970,
    1416267309399670917,
    1416267383160442901,
    1416468498305126522,
]

CUSTOM_5_EMOJI_ID = 1346549711817146400  # 5-Punkte Emoji
STARBOARD_IGNORE_ID = 1346549688836296787  # wird nicht mitgezählt

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
    app_commands.Choice(name=calendar.month_name[i], value=str(i))
    for i in range(1, 13)
]

EMOJI_POINTS = {
    "1️⃣": 1,
    "2️⃣": 2,
    "3️⃣": 3,
    CUSTOM_5_EMOJI_ID: 5,
}

def normalize_emoji(r):
    if isinstance(r.emoji, (discord.PartialEmoji, discord.Emoji)):
        return r.emoji.id
    return str(r.emoji)

class HutVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ai_vote", description="Shows AI image ranking by reactions")
    @app_commands.describe(
        year="Select year",
        month="Select month",
        topuser="Number of top posts to display",
        public="Whether the result is public or ephemeral"
    )
    @app_commands.choices(
        year=YEAR_CHOICES,
        month=MONTH_CHOICES,
        topuser=TOPUSER_CHOICES
    )
    async def ai_vote(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        topuser: app_commands.Choice[str] = None,
        public: bool = False
    ):

        if not any(r.id == ALLOWED_ROLE for r in getattr(interaction.user, "roles", [])):
            return await interaction.response.send_message(
                "❌ You don't have permission.",
                ephemeral=True
            )

        top_count = int(topuser.value) if topuser else 5
        ephemeral_flag = not public
        guild = interaction.guild

        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        # DATE RANGE
        year_v = int(year.value)
        month_v = int(month.value)
        start_dt = datetime(year_v, month_v, 1, tzinfo=timezone.utc)
        last_day = calendar.monthrange(year_v, month_v)[1]
        end_dt = datetime(year_v, month_v, last_day, 23, 59, 59, tzinfo=timezone.utc)

        # SCAN CHANNELS
        matched_msgs = []
        for channel_id in SCAN_CHANNEL_IDS:
            channel = guild.get_channel(channel_id)
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
                traceback.print_exc()

        if not matched_msgs:
            return await interaction.followup.send(
                f"No AI posts found in {calendar.month_name[month_v]} {year_v}.",
                ephemeral=ephemeral_flag
            )

        # SCORING + BREAKDOWN
        def calc_ai_points(msg: discord.Message):
            breakdown = {}
            score = 0
            various_score = 0
            various_count = 0

            for r in msg.reactions:
                key = normalize_emoji(r)
                if key == STARBOARD_IGNORE_ID:
                    continue  # Ignoriert dieses Emoji

                extra_votes = max(r.count - 1, 0)
                if extra_votes == 0:
                    continue

                if key in EMOJI_POINTS:
                    points = extra_votes * EMOJI_POINTS[key]
                    breakdown[key] = {"votes": extra_votes, "points": points}
                    score += points
                else:
                    various_score += extra_votes
                    various_count += extra_votes

            zeroed = False
            if len(breakdown) == 4:
                score = 0
                zeroed = True

            if various_count > 0:
                breakdown["Various"] = {"votes": various_count, "points": various_score}
                score += various_score

            return score, breakdown, zeroed

        # SORT
        top_msgs = sorted(
            matched_msgs,
            key=lambda m: (calc_ai_points(m)[0], m.created_at),
            reverse=True
        )[:top_count]

        # TOP 3 NAMES für Intro
        top_names = []
        for m in top_msgs[:3]:
            creator = m.mentions[0] if m.mentions else m.author
            if creator.display_name not in top_names:
                top_names.append(creator.display_name)
        top_names_text = ", ".join(top_names)

        # INTRO EMBED
        intro_embed = discord.Embed(
            title=f"🤖 AI Top {top_count} — {calendar.month_name[month_v]} {year_v}",
            description=(
                f"Top 3 Users: {top_names_text}\n\n"
                f"⚠️ Note: The <:{STARBOARD_IGNORE_ID}> emoji is NOT counted here (used for normal starboard).\n\n"
                "Scoring system:\n"
                "1️⃣, 2️⃣, 3️⃣, Custom 5️⃣ = points\n"
                "Various = 📝\n"
                "Bot reaction ignored\n"
                "All four present → 0 points"
            ),
            color=discord.Color.blurple()
        )
        intro_embed.set_footer(
            text=f"{guild.name} AI Rankings",
            icon_url=guild.icon.url if guild.icon else None
        )
        intro_msg = await interaction.followup.send(embed=intro_embed)

        # OUTPUT
        for idx, msg in enumerate(top_msgs, start=1):
            score, breakdown, zeroed = calc_ai_points(msg)
            creator = msg.mentions[0] if msg.mentions else msg.author

            lines = []
            for key, data in breakdown.items():
                if key == "Various":
                    emoji_disp = "📝"
                elif isinstance(key, str):
                    emoji_disp = key
                else:
                    emoji_obj = guild.get_emoji(key)
                    emoji_disp = str(emoji_obj) if emoji_obj else "<?>"

                lines.append(f"{emoji_disp} × {data['votes']} → {data['points']} pts")

            if zeroed:
                lines.append("⚠️ All four emojis present → score reset to 0")
            if not lines:
                lines.append("No extra reactions")

            embed = discord.Embed(
                title=f"#{idx} — {creator.display_name} — {score} pts",
                description=f"[Jump to Post]({msg.jump_url})\n\n**Breakdown:**\n" + "\n".join(lines),
                color=discord.Color.teal()
            )
            embed.set_thumbnail(url=creator.display_avatar.url)

            img_url = None
            if msg.attachments:
                img_url = msg.attachments[0].url
            else:
                for e in msg.embeds:
                    if e.image:
                        img_url = e.image.url
                        break
                    if e.thumbnail:
                        img_url = e.thumbnail.url
                        break
            if img_url:
                embed.set_image(url=img_url)

            await intro_msg.channel.send(embed=embed)

        # FINAL TOP 3 POST MIT MENTIONS
        final_lines = []
        for idx, msg in enumerate(top_msgs[:3], start=1):
            creator = msg.mentions[0] if msg.mentions else msg.author
            score, _, _ = calc_ai_points(msg)
            final_lines.append(f"#{idx} — {creator.mention} — {score} pts")

        await intro_msg.channel.send(
            "🏁 **Top 3 AI Posts:**\n" + "\n".join(final_lines)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))
