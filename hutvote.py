import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import calendar
import traceback

# =====================
# KONFIG
# =====================
ALLOWED_ROLE = 1346428405368750122
BOT_ID = 1379906834588106883

SCAN_CHANNEL_IDS = [
    1415769909874524262,
    1415769966573260970,
    1416267309399670917,
    1416267383160442901,
    1416468498305126522,
]

DEFAULT_CONTEST_CHANNEL_ID = 1461752750550552741

CUSTOM_5_EMOJI_ID = 1346549711817146400
STARBOARD_IGNORE_ID = 1346549688836296787

TOPUSER_CHOICES = [
    app_commands.Choice(name="Top 5", value="5"),
    app_commands.Choice(name="Top 10", value="10"),
    app_commands.Choice(name="Top 20", value="20"),
    app_commands.Choice(name="Top 40", value="40"),
]

SORT_CHOICES = [
    app_commands.Choice(name="Ascending (1 → X)", value="asc"),
    app_commands.Choice(name="Descending (X → 1)", value="desc"),
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

IGNORE_IDS = {1292194320786522223}

# =====================
# HELPER
# =====================
def normalize_emoji(r):
    if isinstance(r.emoji, (discord.PartialEmoji, discord.Emoji)):
        return r.emoji.id
    return str(r.emoji)


def calc_ai_points(msg: discord.Message):
    """
    Punkte + Emoji-Gesamtanzahl.
    - Bot-Emojis zählen erst ab Vote #2
    - Andere Emojis zählen ab Vote #1
    """
    breakdown = {}
    score = 0
    emoji_total = 0

    for r in msg.reactions:
        key = normalize_emoji(r)

        if str(key) == str(STARBOARD_IGNORE_ID):
            continue

        votes = r.count

        if key in EMOJI_POINTS:
            extra_votes = max(votes - 1, 0)
            if extra_votes <= 0:
                continue

            points = extra_votes * EMOJI_POINTS[key]
            breakdown[key] = {"votes": extra_votes, "points": points}
            score += points
            emoji_total += extra_votes

        else:
            points = votes
            breakdown.setdefault("Various", {"votes": 0, "points": 0})
            breakdown["Various"]["votes"] += votes
            breakdown["Various"]["points"] += points
            score += points
            emoji_total += votes

    return score, breakdown, emoji_total


# =====================
# COG
# =====================
class HutVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =====================
    # /ai_vote
    # =====================
    @app_commands.command(name="ai_vote", description="Shows AI image ranking by reactions")
    @app_commands.describe(
        year="Select year",
        month="Select month",
        topuser="Number of top posts to display",
        sort="Sort order",
        public="Whether the result is public or ephemeral"
    )
    @app_commands.choices(
        year=YEAR_CHOICES,
        month=MONTH_CHOICES,
        topuser=TOPUSER_CHOICES,
        sort=SORT_CHOICES
    )
    async def ai_vote(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        topuser: app_commands.Choice[str] = None,
        sort: app_commands.Choice[str] = None,
        public: bool = False
    ):
        if not any(r.id == ALLOWED_ROLE for r in getattr(interaction.user, "roles", [])):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)

        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        year_v = int(year.value)
        month_v = int(month.value)

        start_dt = datetime(year_v, month_v, 1, tzinfo=timezone.utc)
        end_dt = datetime(
            year_v, month_v,
            calendar.monthrange(year_v, month_v)[1],
            23, 59, 59,
            tzinfo=timezone.utc
        )

        matched_msgs = []
        guild = interaction.guild

        for cid in SCAN_CHANNEL_IDS:
            channel = guild.get_channel(cid)
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                async for msg in channel.history(after=start_dt, before=end_dt, limit=None):
                    if msg.author.id == BOT_ID:
                        matched_msgs.append(msg)
            except Exception:
                traceback.print_exc()

        if not matched_msgs:
            return await interaction.followup.send("No AI posts found.", ephemeral=ephemeral_flag)

        await self._render_ranking(
            interaction,
            matched_msgs,
            title=f"🤖 AI Top — {calendar.month_name[month_v]} {year_v}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc"
        )

    # =====================
    # /ai_contest
    # =====================
    @app_commands.command(name="ai_contest", description="Shows AI contest ranking for a single channel")
    @app_commands.describe(
        channel="Channel to scan",
        topuser="Number of top posts to display",
        sort="Sort order",
        public="Whether the result is public or ephemeral"
    )
    @app_commands.choices(topuser=TOPUSER_CHOICES, sort=SORT_CHOICES)
    async def ai_contest(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        topuser: app_commands.Choice[str] = None,
        sort: app_commands.Choice[str] = None,
        public: bool = False
    ):
        if not any(r.id == ALLOWED_ROLE for r in getattr(interaction.user, "roles", [])):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)

        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        guild = interaction.guild
        target_channel = channel or guild.get_channel(DEFAULT_CONTEST_CHANNEL_ID)

        if not isinstance(target_channel, discord.TextChannel):
            return await interaction.followup.send("Invalid channel.", ephemeral=ephemeral_flag)

        matched_msgs = []
        try:
            async for msg in target_channel.history(limit=None):
                if msg.author.id == BOT_ID:
                    matched_msgs.append(msg)
        except Exception:
            traceback.print_exc()

        if not matched_msgs:
            return await interaction.followup.send("No AI posts found.", ephemeral=ephemeral_flag)

        await self._render_ranking(
            interaction,
            matched_msgs,
            title=f"🏁 AI Contest Ranking — {target_channel.name}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc"
        )

    # =====================
    # RANKING CORE
    # =====================
    async def _render_ranking(self, interaction, msgs, title, ephemeral, limit, sort_order):
        guild = interaction.guild
        medals = ["🥇", "🥈", "🥉"]

        def sort_key(m):
            score, _, emoji_total = calc_ai_points(m)
            return (score, emoji_total, m.created_at)

        ranked_msgs = sorted(msgs, key=sort_key, reverse=True)
        top_msgs = ranked_msgs[:limit]
        display_msgs = top_msgs if sort_order == "desc" else list(reversed(top_msgs))

        # -------- Top 3 Unique ----------
        top_unique = []
        seen = set()
        for m in ranked_msgs:
            u = m.mentions[0] if m.mentions else m.author
            if u.id in IGNORE_IDS or u.name == "Deleted User":
                continue
            if u.id not in seen:
                top_unique.append(m)
                seen.add(u.id)
            if len(top_unique) == 3:
                break

        intro = ""
        for i, m in enumerate(top_unique):
            u = m.mentions[0] if m.mentions else m.author
            intro += f"{medals[i]} {u.display_name}\n"

        now_str = datetime.utcnow().strftime("%Y/%m/%d %H:%M")
        await interaction.followup.send(
            embed=discord.Embed(
                title=title,
                description=f"**Top 3 Hut Dwellers:**\n{intro}",
                color=discord.Color.blurple()
            ).set_footer(text=f"Updated: {now_str} UTC"),
            ephemeral=ephemeral
        )

        # -------- Rank Numbers with Tie Handling ----------
        rank_map = {}
        last_score = None
        last_emoji = None
        current_rank = 0

        for idx, m in enumerate(ranked_msgs, start=1):
            score, _, emoji_total = calc_ai_points(m)

            if score == last_score and emoji_total == last_emoji:
                rank_map[m.id] = current_rank
            else:
                current_rank = idx
                rank_map[m.id] = current_rank
                last_score = score
                last_emoji = emoji_total

        top_user_ids = [m.mentions[0].id if m.mentions else m.author.id for m in top_unique]

        # -------- Detail Embeds ----------
        for m in display_msgs:
            u = m.mentions[0] if m.mentions else m.author
            score, breakdown, emoji_total = calc_ai_points(m)

            rank_number = rank_map[m.id]

            medal = ""
            if u.id in top_user_ids:
                medal = medals[top_user_ids.index(u.id)]

            lines = []
            for k, d in breakdown.items():
                emoji = "📝" if k == "Various" else str(guild.get_emoji(k) or k)
                lines.append(f"{emoji} × {d['votes']} → {d['points']} pts")

            embed = discord.Embed(
                title=f"#{rank_number} — {u.display_name} {medal} — {score} pts ({emoji_total} 😀)",
                description=f"[Jump to Post 🎖️(**VOTE**🎖️)]({m.jump_url})\n\n" + "\n".join(lines),
                color=discord.Color.gold() if medal else discord.Color.teal()
            )

            embed.set_thumbnail(url=u.display_avatar.url)

            if m.attachments:
                embed.set_image(url=m.attachments[0].url)
            else:
                for e in m.embeds:
                    if e.image and e.image.url:
                        embed.set_image(url=e.image.url)
                        break

            embed.set_footer(text=f"Posted: {m.created_at.strftime('%Y/%m/%d %H:%M')} UTC")
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)

        # -------- Final Top 3 ----------
        final_mentions = []
        final_lines = []
        for i, m in enumerate(top_unique):
            u = m.mentions[0] if m.mentions else m.author
            score, _, emoji_total = calc_ai_points(m)
            final_mentions.append(u.mention)
            final_lines.append(f"{medals[i]} {u.display_name} — {score} pts ({emoji_total} 😀)")

        final_time = datetime.utcnow().strftime("%Y/%m/%d %H:%M")
        await interaction.followup.send(
            content=" ".join(final_mentions),
            embed=discord.Embed(
                title=f"🏆 Final Top 3 (as of {final_time} UTC)",
                description="\n".join(final_lines),
                color=discord.Color.gold()
            ),
            ephemeral=ephemeral
        )


# =====================
# SETUP
# =====================
async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))
