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

# =====================
# HELPER
# =====================
def normalize_emoji(r):
    if isinstance(r.emoji, (discord.PartialEmoji, discord.Emoji)):
        return r.emoji.id
    return str(r.emoji)

def calc_ai_points(msg: discord.Message):
    breakdown = {}
    score = 0

    for r in msg.reactions:
        key = normalize_emoji(r)
        if key == STARBOARD_IGNORE_ID:
            continue

        if key in EMOJI_POINTS:
            extra_votes = max(r.count - 1, 0)
            if extra_votes <= 0:
                continue
            points = extra_votes * EMOJI_POINTS[key]
            breakdown[key] = {"votes": extra_votes, "points": points}
            score += points
        else:
            if r.count <= 0:
                continue
            breakdown.setdefault("Various", {"votes": 0, "points": 0})
            breakdown["Various"]["votes"] += r.count
            breakdown["Various"]["points"] += r.count
            score += r.count

    return score, breakdown, False

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
    @app_commands.describe(year="Select year", month="Select month", topuser="Top entries", public="Public output")
    @app_commands.choices(year=YEAR_CHOICES, month=MONTH_CHOICES, topuser=TOPUSER_CHOICES)
    async def ai_vote(self, interaction, year, month, topuser=None, public=False):
        if not any(r.id == ALLOWED_ROLE for r in getattr(interaction.user, "roles", [])):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)

        ephemeral = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        year_v = int(year.value)
        month_v = int(month.value)

        start_dt = datetime(year_v, month_v, 1, tzinfo=timezone.utc)
        end_dt = datetime(
            year_v, month_v,
            calendar.monthrange(year_v, month_v)[1],
            23, 59, 59,
            tzinfo=timezone.utc
        )

        matched = []
        for cid in SCAN_CHANNEL_IDS:
            channel = interaction.guild.get_channel(cid)
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                async for msg in channel.history(after=start_dt, before=end_dt, limit=None):
                    if msg.author.id == BOT_ID:
                        matched.append(msg)
            except Exception:
                traceback.print_exc()

        if not matched:
            return await interaction.followup.send("No AI posts found.", ephemeral=ephemeral)

        await self._render_ranking(
            interaction,
            matched,
            f"🤖 AI Top — {calendar.month_name[month_v]} {year_v}",
            ephemeral,
            int(topuser.value) if topuser else 5
        )

    # =====================
    # /ai_contest
    # =====================
    @app_commands.command(name="ai_contest", description="Shows AI contest ranking for a channel")
    @app_commands.choices(topuser=TOPUSER_CHOICES)
    async def ai_contest(self, interaction, channel=None, topuser=None, public=False):
        if not any(r.id == ALLOWED_ROLE for r in getattr(interaction.user, "roles", [])):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)

        ephemeral = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        target = channel or interaction.guild.get_channel(DEFAULT_CONTEST_CHANNEL_ID)
        if not isinstance(target, discord.TextChannel):
            return await interaction.followup.send("Invalid channel.", ephemeral=ephemeral)

        matched = []
        async for msg in target.history(limit=None):
            if msg.author.id == BOT_ID:
                matched.append(msg)

        await self._render_ranking(
            interaction,
            matched,
            f"🏁 AI Contest Ranking — {target.name}",
            ephemeral,
            int(topuser.value) if topuser else 5
        )

    # =====================
    # SHARED OUTPUT
    # =====================
    async def _render_ranking(self, interaction, msgs, title, ephemeral, limit):
        guild = interaction.guild
        medals = ["🥇", "🥈", "🥉"]

        msgs_sorted = sorted(
            msgs,
            key=lambda m: (calc_ai_points(m)[0], m.created_at),
            reverse=True
        )

        # ---------- INTRO ----------
        top_unique = []
        seen = set()
        for m in msgs_sorted:
            u = m.mentions[0] if m.mentions else m.author
            if u.id not in seen:
                top_unique.append(m)
                seen.add(u.id)
            if len(top_unique) == 3:
                break

        intro_txt = ""
        for i, m in enumerate(top_unique):
            u = m.mentions[0] if m.mentions else m.author
            intro_txt += f"{medals[i]} {u.display_name}\n"

        now = datetime.utcnow().strftime("%Y/%m/%d %H:%M")
        intro_embed = discord.Embed(
            title=title,
            description=f"**Top 3 Hut Dwellers:**\n{intro_txt}",
            color=discord.Color.blurple()
        )
        intro_embed.set_footer(text=f"Updated: {now} UTC")
        await interaction.followup.send(embed=intro_embed, ephemeral=ephemeral)

        # ---------- DETAIL EMBEDS ----------
        for idx, m in enumerate(msgs_sorted[:limit], start=1):
            score, breakdown, _ = calc_ai_points(m)
            u = m.mentions[0] if m.mentions else m.author

            channel_url = f"https://discord.com/channels/{guild.id}/{m.channel.id}"

            lines = []
            for k, d in breakdown.items():
                emoji = "📝" if k == "Various" else str(guild.get_emoji(k) or k)
                lines.append(f"{emoji} × {d['votes']} → {d['points']} pts")

            embed = discord.Embed(
                title=f"#{idx} — {u.display_name} — {score} pts",
                description=(
                    f"**Channel:** [{m.channel.name}]({channel_url})\n"
                    f"[Jump to Post]({m.jump_url})\n\n"
                    + "\n".join(lines)
                ),
                color=discord.Color.teal()
            )

            embed.set_thumbnail(url=u.display_avatar.url)

            if m.attachments:
                embed.set_image(url=m.attachments[0].url)
            else:
                for e in m.embeds:
                    if e.image and e.image.url:
                        embed.set_image(url=e.image.url)
                        break
                    if e.thumbnail and e.thumbnail.url:
                        embed.set_image(url=e.thumbnail.url)
                        break

            embed.set_footer(text=f"Posted: {m.created_at.strftime('%Y/%m/%d %H:%M')} UTC")
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)

        # ---------- FINAL ----------
        mentions = []
        final_lines = []
        for i, m in enumerate(top_unique):
            u = m.mentions[0] if m.mentions else m.author
            s, _, _ = calc_ai_points(m)
            mentions.append(u.mention)
            final_lines.append(f"{medals[i]} {u.display_name} — {s} pts")

        final_time = datetime.utcnow().strftime("%Y/%m/%d %H:%M")
        await interaction.followup.send(
            content=" ".join(mentions),
            embed=discord.Embed(
                title=f"🏆 Final Top 3 (as of {final_time} UTC)",
                description="\n".join(final_lines),
                color=discord.Color.gold()
            ).set_footer(text=f"Timestamp: {final_time} UTC"),
            ephemeral=ephemeral
        )

# =====================
# SETUP
# =====================
async def setup(bot):
    await bot.add_cog(HutVote(bot))
