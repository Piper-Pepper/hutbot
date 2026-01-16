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
STARBOARD_IGNORE_ID = 1346549688836296787  # wird komplett ignoriert

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

# =====================
# SCORING-FUNKTION
# =====================
def calc_ai_points(msg: discord.Message):
    breakdown = {}
    score = 0

    for r in msg.reactions:
        key = normalize_emoji(r)

        # Starboard-Emoji ignorieren
        if key == STARBOARD_IGNORE_ID:
            continue

        # Pflicht-Emojis → zählen ab #2
        if key in EMOJI_POINTS:
            extra_votes = max(r.count - 1, 0)  # Bot-Set wird abgezogen
            if extra_votes == 0:
                continue
            points = extra_votes * EMOJI_POINTS[key]
            breakdown[key] = {"votes": extra_votes, "points": points}
            score += points
        # Zusätzliche Emojis → zählen ab #1
        else:
            if r.count <= 0:
                continue
            breakdown.setdefault("Various", {"votes": 0, "points": 0})
            breakdown["Various"]["votes"] += r.count
            breakdown["Various"]["points"] += r.count
            score += r.count

    return score, breakdown, False  # kein Reset mehr

# =====================
# HUTC-VOTE COG
# =====================
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
        # Berechtigung prüfen
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

        # SORT TOP-MESSAGES
        top_msgs_all = sorted(
            matched_msgs,
            key=lambda m: (calc_ai_points(m)[0], m.created_at),
            reverse=True
        )

        # TOP 3 Eindeutige User auswählen
        top_msgs_unique = []
        seen_users = set()
        for msg in top_msgs_all:
            creator = msg.mentions[0] if msg.mentions else msg.author
            if creator.id not in seen_users:
                top_msgs_unique.append(msg)
                seen_users.add(creator.id)
            if len(top_msgs_unique) >= 3:
                break

        # TOP 3 NAMES für Intro mit Medaillen
        medals = ["🥇", "🥈", "🥉"]
        top_names_text = ""
        for idx, m in enumerate(top_msgs_unique):
            creator = m.mentions[0] if m.mentions else m.author
            top_names_text += f"{medals[idx]} {creator.display_name}\n"

        # Starboard Emoji anzeigen
        emoji_obj = guild.get_emoji(STARBOARD_IGNORE_ID)
        starboard_emoji = str(emoji_obj) if emoji_obj else f"<:{STARBOARD_IGNORE_ID}>"

        # INTRO EMBED
        intro_embed = discord.Embed(
            title=f"🤖 AI Top {top_count} — {calendar.month_name[month_v]} {year_v}",
            description=(
                f"Top 3 Users:\n{top_names_text}\n"
                f"⚠️ Note: {starboard_emoji} emoji is NOT counted here (used for normal starboard).\n"
                "Additional reactions each give 1 point."
            ),
            color=discord.Color.blurple()
        )
        intro_embed.set_footer(
            text=f"{guild.name} AI Rankings",
            icon_url=guild.icon.url if guild.icon else None
        )
        intro_msg = await interaction.followup.send(embed=intro_embed)

        # OUTPUT POST-EMBEDS
        for idx, msg in enumerate(top_msgs_all[:top_count], start=1):
            score, breakdown, _ = calc_ai_points(msg)
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

            if not lines:
                lines.append("No extra reactions")

            embed = discord.Embed(
                title=f"#{idx} — {creator.display_name} — {score} pts",
                description=f"[Jump to Post]({msg.jump_url})\n\n**Breakdown:**\n" + "\n".join(lines),
                color=discord.Color.teal()
            )
            embed.set_thumbnail(url=creator.display_avatar.url)
            embed.set_footer(text=f"Posted on {msg.created_at.strftime('%d.%m.%Y %H:%M UTC')}")

            # Attachment / Embed Image
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

        # FINAL TOP 3 POST MIT MENTIONS UND MEDAILLEN
        final_lines = []
        for idx, m in enumerate(top_msgs_unique):
            creator = m.mentions[0] if m.mentions else m.author
            score, _, _ = calc_ai_points(m)
            final_lines.append(f"{medals[idx]} {creator.mention} — {score} pts")

        await intro_msg.channel.send(
            "🏁 **Top 3 AI Posts:**\n" + "\n".join(final_lines)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))
