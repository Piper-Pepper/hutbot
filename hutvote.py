import logging
import calendar
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands
from discord import app_commands

# =====================
# LOGGING
# =====================
logger = logging.getLogger(__name__)

# =====================
# KONFIG
# =====================
ALLOWED_ROLE_IDS = {1346414581643219029, 1346428405368750122}
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
def normalize_emoji(reaction: discord.Reaction):
    if isinstance(reaction.emoji, (discord.PartialEmoji, discord.Emoji)):
        return reaction.emoji.id
    return str(reaction.emoji)


def calc_ai_points(msg: discord.Message):
    breakdown = {}
    score = 0
    emoji_total = 0

    for reaction in msg.reactions:
        key = normalize_emoji(reaction)

        if str(key) == str(STARBOARD_IGNORE_ID):
            continue

        votes = reaction.count

        # bekannte Voting-Emojis: 1 Bot-React wird abgezogen
        if key in EMOJI_POINTS:
            extra_votes = max(votes - 1, 0)
            if extra_votes <= 0:
                continue

            points = extra_votes * EMOJI_POINTS[key]
            breakdown[key] = {"votes": extra_votes, "points": points}
            score += points
            emoji_total += extra_votes
        else:
            # alle anderen Emojis zählen 1:1
            points = votes
            breakdown.setdefault("Various", {"votes": 0, "points": 0})
            breakdown["Various"]["votes"] += votes
            breakdown["Various"]["points"] += points
            score += points
            emoji_total += votes

    return score, breakdown, emoji_total


def get_month_utc_range(year: int, month: int):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end_exclusive = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_exclusive = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end_exclusive


def get_target_user(msg: discord.Message):
    return msg.mentions[0] if msg.mentions else msg.author


# =====================
# COG
# =====================
class HutVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _safe_text_channel(self, guild: discord.Guild, channel_id: int):
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                logger.exception("Konnte Channel %s nicht laden.", channel_id)
                return None

        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def _scan_messages_in_channel(
        self,
        channel: discord.TextChannel,
        start: datetime | None = None,
        end_exclusive: datetime | None = None
    ):
        matched = []

        # Discord history(after=...) ist exklusiv -> 1s zurück für sicheren Startpunkt
        after_dt = (start - timedelta(seconds=1)) if start else None

        try:
            async for msg in channel.history(
                after=after_dt,
                before=end_exclusive,
                limit=None
            ):
                if msg.author.id != BOT_ID:
                    continue

                if start and end_exclusive:
                    if not (start <= msg.created_at < end_exclusive):
                        continue

                matched.append(msg)

        except Exception:
            logger.exception("Fehler beim Lesen von #%s (%s)", channel.name, channel.id)

        return matched

    @app_commands.command(name="ai_vote", description="Shows AI image ranking by reactions")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
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
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        if interaction.guild is None:
            return await interaction.followup.send("Dieser Befehl geht nur im Server.", ephemeral=True)

        year_v = int(year.value)
        month_v = int(month.value)
        start_dt, end_exclusive = get_month_utc_range(year_v, month_v)

        matched_msgs = []
        for cid in SCAN_CHANNEL_IDS:
            channel = await self._safe_text_channel(interaction.guild, cid)
            if channel is None:
                continue
            matched_msgs.extend(
                await self._scan_messages_in_channel(
                    channel=channel,
                    start=start_dt,
                    end_exclusive=end_exclusive
                )
            )

        if not matched_msgs:
            return await interaction.followup.send("No AI posts found.", ephemeral=ephemeral_flag)

        await self._render_ranking(
            interaction=interaction,
            msgs=matched_msgs,
            title=f"🤖 AI Top — {calendar.month_name[month_v]} {year_v}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc"
        )

    @app_commands.command(name="ai_contest", description="Shows AI contest ranking for a single channel")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
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
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        if interaction.guild is None:
            return await interaction.followup.send("Dieser Befehl geht nur im Server.", ephemeral=True)

        target_channel = channel
        if target_channel is None:
            target_channel = await self._safe_text_channel(interaction.guild, DEFAULT_CONTEST_CHANNEL_ID)

        if not isinstance(target_channel, discord.TextChannel):
            return await interaction.followup.send("Invalid channel.", ephemeral=ephemeral_flag)

        matched_msgs = await self._scan_messages_in_channel(target_channel)

        if not matched_msgs:
            return await interaction.followup.send("No AI posts found.", ephemeral=ephemeral_flag)

        await self._render_ranking(
            interaction=interaction,
            msgs=matched_msgs,
            title=f"🏁 AI Contest Ranking — {target_channel.name}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc"
        )

    async def _render_ranking(self, interaction, msgs, title, ephemeral, limit, sort_order):
        guild = interaction.guild
        medals = ["🥇", "🥈", "🥉"]

        # Punkte einmal berechnen (Cache)
        stats = {m.id: calc_ai_points(m) for m in msgs}

        def sort_key(m: discord.Message):
            score, _, emoji_total = stats[m.id]
            return score, emoji_total, m.created_at

        ranked_msgs = sorted(msgs, key=sort_key, reverse=True)

        # Ties nach Score
        score_counts = {}
        for m in ranked_msgs:
            s, _, _ = stats[m.id]
            score_counts[s] = score_counts.get(s, 0) + 1
        tied_scores = {s for s, c in score_counts.items() if c > 1}

        top_msgs = ranked_msgs[:limit]

        # KORREKT:
        # asc = 1 -> X
        # desc = X -> 1
        display_msgs = top_msgs if sort_order == "asc" else list(reversed(top_msgs))

        # Ranking-Map (echtes Ranking mit Tie auf Score + EmojiTotal)
        rank_map = {}
        last_score = None
        last_emoji = None
        current_rank = 0

        for idx, m in enumerate(ranked_msgs, start=1):
            score, _, emoji_total = stats[m.id]
            if score == last_score and emoji_total == last_emoji:
                rank_map[m.id] = current_rank
            else:
                current_rank = idx
                rank_map[m.id] = current_rank
                last_score = score
                last_emoji = emoji_total

        # Top 3 Unique User
        top_unique = []
        seen = set()
        for m in ranked_msgs:
            u = get_target_user(m)
            if u.id in IGNORE_IDS or u.name == "Deleted User":
                continue
            if u.id not in seen:
                top_unique.append(m)
                seen.add(u.id)
            if len(top_unique) == 3:
                break

        intro = ""
        for i, m in enumerate(top_unique):
            u = get_target_user(m)
            intro += f"{medals[i]} {u.display_name}\n"

        now_str = datetime.utcnow().strftime("%Y/%m/%d %H:%M")
        await interaction.followup.send(
            embed=discord.Embed(
                title=title,
                description=f"**Top 3 Hut Dwellers:**\n{intro or '—'}",
                color=discord.Color.blurple()
            ).set_footer(text=f"Updated: {now_str} UTC"),
            ephemeral=ephemeral
        )

        top_user_ids = [get_target_user(m).id for m in top_unique]

        # Detail-Embeds
        for m in display_msgs:
            u = get_target_user(m)
            score, breakdown, emoji_total = stats[m.id]
            rank_number = rank_map[m.id]

            medal = medals[top_user_ids.index(u.id)] if u.id in top_user_ids else ""
            tie_suffix = f" ({emoji_total} 📊)" if score in tied_scores else ""

            lines = []
            for k, d in breakdown.items():
                emoji_label = "📝" if k == "Various" else str(guild.get_emoji(k) or k)
                lines.append(f"{emoji_label} × {d['votes']} → {d['points']} pts")

            detail_text = "\n".join(lines) if lines else "_Keine Reaktionen_"

            embed = discord.Embed(
                title=f"#{rank_number} — {u.display_name} {medal} — {score} pts{tie_suffix}",
                description=f"[Jump to Post 🎖️(**VOTE**🎖️)]({m.jump_url})\n\n{detail_text}",
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

        # Final Top 3
        if top_unique:
            final_mentions = []
            final_lines = []

            for i, m in enumerate(top_unique):
                u = get_target_user(m)
                score, _, emoji_total = stats[m.id]
                tie_suffix = f" ({emoji_total} 📊)" if score in tied_scores else ""
                final_mentions.append(u.mention)
                final_lines.append(f"{medals[i]} {u.display_name} — {score} pts{tie_suffix}")

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

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingAnyRole):
            if interaction.response.is_done():
                await interaction.followup.send("❌ No permission.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return

        logger.exception("Unhandled app command error", exc_info=error)

        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Ein unerwarteter Fehler ist aufgetreten.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Ein unerwarteter Fehler ist aufgetreten.", ephemeral=True)
        except Exception:
            pass


# =====================
# SETUP
# =====================
async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))