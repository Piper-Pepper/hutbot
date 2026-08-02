# hut_vote_cog.py — Part 1/2
import asyncio
import logging
import calendar
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

logger = logging.getLogger(__name__)

# =====================
# CONFIG
# =====================
ALLOWED_ROLE_IDS = {1346414581643219029, 1346428405368750122}
VOTER_EXCLUDED_ROLE_ID = 1346414581643219029   # Reactions from these members do NOT count

BOT_ID = 1379906834588106883

SCAN_CHANNEL_IDS = [
    1415769909874524262,
    1415769966573260970,
    1416267309399670917,
    1416267383160442901,
    1416468498305126522,
]

CUSTOM_5_EMOJI_ID = 1346549711817146400
STARBOARD_IGNORE_ID = 1346549688836296787

EMOJI_POINTS = {
    "1️⃣": 1,
    "2️⃣": 2,
    "3️⃣": 3,
    CUSTOM_5_EMOJI_ID: 5,
}
IGNORE_IDS = {1292194320786522223}

# XP rewards — only ranks 1-3 earn XP; ranks 4-5 are honorable mention
WINNER_XP = {0: 5000, 1: 3000, 2: 1000}
VOTER_XP  = {1: 2000, 2: 1000, 3: 500}

# Icons — 5 entries so /top5 has medals for 4th & 5th place
WINNER_MEDAL   = ["🥇", "🥈", "🥉", "🏅", "🎖️"]
WINNER_SPARKLE = ["✨", "⭐", "💫", "🌠", "🔆"]
VOTER_MEDAL    = {1: "🥇", 2: "🥈", 3: "🥉", 4: "🏅", 5: "🎖️"}
VOTER_SPARKLE  = {1: "💎", 2: "🔥", 3: "🌟", 4: "🎯", 5: "⚡"}

# Section titles
WINNERS_TITLE_IMAGE = "🏆 TOP Goon Hut AI Artists 🏆"
WINNERS_TITLE_VIDEO = "🎬 TOP Goon Hut AI Video Makers 🎬"
VOTERS_TITLE_TOP3   = "🗳️ Top 3 Voters 🗳️"
VOTERS_TITLE_TOP5   = "🗳️ Top 5 Voters 🗳️"

# Performance — extra gentle against Discord's rate limiter
VOTER_FETCH_CONCURRENCY   = 3
VOTER_INTRA_MSG_DELAY_SEC = 0.25
DETAIL_EMBEDS_PER_MSG     = 5
FOLLOWUP_DELAY_SEC        = 0.3

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
TOP5_KIND_CHOICES = [
    app_commands.Choice(name="🎨 Artists only", value="artists"),
    app_commands.Choice(name="🗳️ Voters only", value="voters"),
    app_commands.Choice(name="🎨🗳️ Both", value="both"),
]

current_year = datetime.now(timezone.utc).year
YEAR_CHOICES = [
    app_commands.Choice(name=str(current_year), value=str(current_year)),
    app_commands.Choice(name=str(current_year - 1), value=str(current_year - 1)),
]
MONTH_CHOICES = [
    app_commands.Choice(name=calendar.month_name[i], value=str(i))
    for i in range(1, 13)
]


# =========================================================
# POST DETECTOR — only edit if video post format changes
# =========================================================
class VideoPostDetector:
    VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv", ".m4v")
    CONTENT_MARKERS  = ("🎬",)
    CONTENT_HINTS    = ("click to play", "video")

    @classmethod
    def is_video_post(cls, msg: discord.Message) -> bool:
        for att in msg.attachments:
            if att.content_type and att.content_type.startswith("video/"):
                return True
            if (att.filename or "").lower().endswith(cls.VIDEO_EXTENSIONS):
                return True
        if not msg.attachments:
            return False
        content = msg.content or ""
        cl = content.lower()
        return (
            any(m in content for m in cls.CONTENT_MARKERS)
            and any(h in cl for h in cls.CONTENT_HINTS)
        )


# =====================
# HELPERS
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
        if key in EMOJI_POINTS:
            extra = max(votes - 1, 0)
            if extra <= 0:
                continue
            points = extra * EMOJI_POINTS[key]
            breakdown[key] = {"votes": extra, "points": points}
            score += points
            emoji_total += extra
        else:
            breakdown.setdefault("Various", {"votes": 0, "points": 0})
            breakdown["Various"]["votes"] += votes
            breakdown["Various"]["points"] += votes
            score += votes
            emoji_total += votes
    return score, breakdown, emoji_total


def get_month_utc_range(year: int, month: int):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


def get_target_user(msg: discord.Message):
    return msg.mentions[0] if msg.mentions else msg.author


def get_image_url_from_post(msg: discord.Message) -> Optional[str]:
    if msg.attachments:
        att = msg.attachments[0]
        is_video = (
            (att.content_type and att.content_type.startswith("video/"))
            or (att.filename or "").lower().endswith(VideoPostDetector.VIDEO_EXTENSIONS)
        )
        if not is_video:
            return att.url
    for e in msg.embeds:
        if e.image and e.image.url:
            return e.image.url
    return None


def build_position_maps(
    per_channel: dict[int, list[discord.Message]],
) -> tuple[dict[int, list[discord.Message]], dict[int, discord.Message]]:
    """All videos between two image posts belong to the preceding image
    (same channel, chronological)."""
    img_to_videos: dict[int, list[discord.Message]] = {}
    video_to_img: dict[int, discord.Message] = {}
    for _cid, msgs in per_channel.items():
        msgs_sorted = sorted(msgs, key=lambda m: m.created_at)
        current_img: Optional[discord.Message] = None
        for m in msgs_sorted:
            if VideoPostDetector.is_video_post(m):
                if current_img is not None:
                    img_to_videos.setdefault(current_img.id, []).append(m)
                    video_to_img[m.id] = current_img
            else:
                current_img = m
    return img_to_videos, video_to_img


def top_unique_users(
    msgs: list[discord.Message],
    stats: dict[int, tuple[int, dict, int]],
    max_count: int,
) -> list[discord.Message]:
    """Return top-N messages by score, one per unique user."""
    ranked = sorted(
        msgs,
        key=lambda m: (stats[m.id][0], stats[m.id][2], m.created_at),
        reverse=True,
    )
    result: list[discord.Message] = []
    seen: set[int] = set()
    for m in ranked:
        u = get_target_user(m)
        if u.id in IGNORE_IDS or u.name == "Deleted User":
            continue
        if u.id in seen:
            continue
        result.append(m)
        seen.add(u.id)
        if len(result) == max_count:
            break
    return result


async def collect_voter_counts(
    msgs: list[discord.Message],
    guild: discord.Guild,
) -> dict[int, int]:
    """Count EVERY reaction click as 1 point (per-click). Throttled."""
    counts: dict[int, int] = {}
    excluded_cache: dict[int, bool] = {}

    def is_excluded(uid: int) -> bool:
        if uid in excluded_cache:
            return excluded_cache[uid]
        if uid in IGNORE_IDS:
            excluded_cache[uid] = True
            return True
        member = guild.get_member(uid) if guild else None
        if member and any(r.id == VOTER_EXCLUDED_ROLE_ID for r in member.roles):
            excluded_cache[uid] = True
            return True
        excluded_cache[uid] = False
        return False

    sem = asyncio.Semaphore(VOTER_FETCH_CONCURRENCY)

    async def clicks_of_msg(msg: discord.Message) -> dict[int, int]:
        local: dict[int, int] = {}
        async with sem:
            first = True
            for reaction in msg.reactions:
                key = normalize_emoji(reaction)
                if str(key) == str(STARBOARD_IGNORE_ID):
                    continue
                if key in EMOJI_POINTS and reaction.count <= 1:
                    continue
                if not first:
                    await asyncio.sleep(VOTER_INTRA_MSG_DELAY_SEC)
                first = False
                try:
                    async for user in reaction.users():
                        if user.bot:
                            continue
                        if is_excluded(user.id):
                            continue
                        local[user.id] = local.get(user.id, 0) + 1
                except Exception:
                    logger.exception("Error in reaction.users()")
        return local

    results = await asyncio.gather(
        *(clicks_of_msg(m) for m in msgs), return_exceptions=True
    )
    for r in results:
        if isinstance(r, dict):
            for uid, c in r.items():
                counts[uid] = counts.get(uid, 0) + c
    return counts


def compute_voter_ranks(
    counts: dict[int, int], max_ranks: int = 3
) -> list[tuple[int, int, int]]:
    """Dense ranking capped at `max_ranks` (multiple users per rank possible).
    Returns list of (rank, user_id, count)."""
    sorted_voters = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    out: list[tuple[int, int, int]] = []
    current_rank = 0
    last_count = None
    for uid, cnt in sorted_voters:
        if cnt != last_count:
            current_rank += 1
            if current_rank > max_ranks:
                break
            last_count = cnt
        out.append((current_rank, uid, cnt))
    return out


async def resolve_display_name(
    uid: int, guild: discord.Guild, bot: commands.Bot
) -> str:
    if guild:
        m = guild.get_member(uid)
        if m:
            return m.display_name
    try:
        u = await bot.fetch_user(uid)
        return u.name
    except Exception:
        return f"User#{uid}"


async def resolve_user_object(
    uid: int, guild: discord.Guild, bot: commands.Bot
) -> Optional[discord.abc.User]:
    if guild:
        m = guild.get_member(uid)
        if m:
            return m
    try:
        return await bot.fetch_user(uid)
    except Exception:
        return None

# hut_vote_cog.py — Part 2/2 (append to Part 1)

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
                logger.exception("Could not load channel %s.", channel_id)
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _scan_bot_messages(
        self,
        channel: discord.TextChannel,
        start: Optional[datetime] = None,
        end_exclusive: Optional[datetime] = None,
    ) -> list[discord.Message]:
        matched: list[discord.Message] = []
        after_dt = (start - timedelta(seconds=1)) if start else None
        try:
            async for msg in channel.history(after=after_dt, before=end_exclusive, limit=None):
                if msg.author.id != BOT_ID:
                    continue
                if start and end_exclusive and not (start <= msg.created_at < end_exclusive):
                    continue
                matched.append(msg)
        except Exception:
            logger.exception("Error reading #%s (%s)", channel.name, channel.id)
        return matched

    async def _scan_multi(self, guild, channel_ids, start=None, end_exclusive=None):
        async def scan_one(cid):
            ch = await self._safe_text_channel(guild, cid)
            if ch is None:
                return cid, []
            msgs = await self._scan_bot_messages(ch, start, end_exclusive)
            return cid, msgs
        results = await asyncio.gather(*(scan_one(cid) for cid in channel_ids))
        return {cid: msgs for cid, msgs in results}

    @staticmethod
    def _split_by_type(msgs):
        images, videos = [], []
        for m in msgs:
            (videos if VideoPostDetector.is_video_post(m) else images).append(m)
        return images, videos

    async def _paced_send(self, interaction: discord.Interaction, **kwargs):
        try:
            await interaction.followup.send(**kwargs)
        except discord.HTTPException:
            logger.exception("Followup send failed")
        await asyncio.sleep(FOLLOWUP_DELAY_SEC)

    # ============ /ai_vote ============
    @app_commands.command(name="ai_vote", description="Shows AI image ranking by reactions")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        year="Select year", month="Select month",
        topuser="Number of top posts to display",
        sort="Sort order", public="Public or ephemeral",
    )
    @app_commands.choices(
        year=YEAR_CHOICES, month=MONTH_CHOICES,
        topuser=TOPUSER_CHOICES, sort=SORT_CHOICES,
    )
    async def ai_vote(
        self, interaction: discord.Interaction,
        year: app_commands.Choice[str], month: app_commands.Choice[str],
        topuser: app_commands.Choice[str] = None,
        sort: app_commands.Choice[str] = None,
        public: bool = False,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)
        if interaction.guild is None:
            return await interaction.followup.send("Server-only.", ephemeral=True)

        start_dt, end_ex = get_month_utc_range(int(year.value), int(month.value))
        per_ch = await self._scan_multi(interaction.guild, SCAN_CHANNEL_IDS, start_dt, end_ex)
        all_msgs = [m for msgs in per_ch.values() for m in msgs]
        image_msgs, _ = self._split_by_type(all_msgs)
        if not image_msgs:
            return await interaction.followup.send("No AI posts found.", ephemeral=ephemeral_flag)

        img_to_videos, video_to_img = build_position_maps(per_ch)
        voter_counts = await collect_voter_counts(all_msgs, interaction.guild)

        await self._render_ranking(
            interaction=interaction, msgs=image_msgs,
            title=f"🤖 AI Top — {calendar.month_name[int(month.value)]} {year.value}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc",
            kind="image",
            img_to_videos=img_to_videos, video_to_img=video_to_img,
            voter_counts=voter_counts,
        )

    # ============ /ai_video ============
    @app_commands.command(name="ai_video", description="Shows AI video ranking by reactions")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        year="Select year", month="Select month",
        topuser="Number of top posts to display",
        sort="Sort order", public="Public or ephemeral",
    )
    @app_commands.choices(
        year=YEAR_CHOICES, month=MONTH_CHOICES,
        topuser=TOPUSER_CHOICES, sort=SORT_CHOICES,
    )
    async def ai_video(
        self, interaction: discord.Interaction,
        year: app_commands.Choice[str], month: app_commands.Choice[str],
        topuser: app_commands.Choice[str] = None,
        sort: app_commands.Choice[str] = None,
        public: bool = False,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)
        if interaction.guild is None:
            return await interaction.followup.send("Server-only.", ephemeral=True)

        start_dt, end_ex = get_month_utc_range(int(year.value), int(month.value))
        per_ch = await self._scan_multi(interaction.guild, SCAN_CHANNEL_IDS, start_dt, end_ex)
        all_msgs = [m for msgs in per_ch.values() for m in msgs]
        _, video_msgs = self._split_by_type(all_msgs)
        if not video_msgs:
            return await interaction.followup.send("No AI video posts found.", ephemeral=ephemeral_flag)

        img_to_videos, video_to_img = build_position_maps(per_ch)
        voter_counts = await collect_voter_counts(all_msgs, interaction.guild)

        await self._render_ranking(
            interaction=interaction, msgs=video_msgs,
            title=f"🎬 AI Video Top — {calendar.month_name[int(month.value)]} {year.value}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc",
            kind="video",
            img_to_videos=img_to_videos, video_to_img=video_to_img,
            voter_counts=voter_counts,
        )

    # ============ /top5 ============
    @app_commands.command(
        name="top5",
        description="Show top 5 artists and/or top 5 voters with avatars",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        kind="What to show: artists, voters, or both",
        year="Select year",
        month="Select month",
        public="Post publicly (default: no)",
        ping_role="Optional role to ping (only used when public)",
    )
    @app_commands.choices(
        kind=TOP5_KIND_CHOICES,
        year=YEAR_CHOICES,
        month=MONTH_CHOICES,
    )
    async def top5(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        public: bool = False,
        ping_role: Optional[discord.Role] = None,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)
        if interaction.guild is None:
            return await interaction.followup.send("Server-only.", ephemeral=True)

        year_v = int(year.value)
        month_v = int(month.value)
        start_dt, end_ex = get_month_utc_range(year_v, month_v)

        want_artists = kind.value in ("artists", "both")
        want_voters  = kind.value in ("voters",  "both")

        per_ch = await self._scan_multi(
            interaction.guild, SCAN_CHANNEL_IDS, start_dt, end_ex
        )
        all_msgs = [m for msgs in per_ch.values() for m in msgs]
        image_msgs, _ = self._split_by_type(all_msgs)

        # ---- Top 5 Artists ----
        # entries: list of (user_obj, score, xp, idx)
        top_artists: list[tuple[discord.abc.User, int, int, int]] = []
        if want_artists:
            stats = {m.id: calc_ai_points(m) for m in image_msgs}
            top_msgs = top_unique_users(image_msgs, stats, max_count=5)
            for i, m in enumerate(top_msgs):
                u = get_target_user(m)
                top_artists.append((u, stats[m.id][0], WINNER_XP.get(i, 0), i))

        # ---- Top 5 Voters ----
        # entries: list of (user_obj, count, xp, rank)
        top_voters: list[tuple[discord.abc.User, int, int, int]] = []
        if want_voters:
            voter_counts = await collect_voter_counts(all_msgs, interaction.guild)
            for rank, uid, cnt in compute_voter_ranks(voter_counts, max_ranks=5):
                u = await resolve_user_object(uid, interaction.guild, self.bot)
                if u is None:
                    continue
                top_voters.append((u, cnt, VOTER_XP.get(rank, 0), rank))

        if not top_artists and not top_voters:
            return await interaction.followup.send(
                "No data found for that period.", ephemeral=ephemeral_flag
            )

        month_label = f"{calendar.month_name[month_v]} {year_v}"

        # ---- Intro embed: summary of all listed users ----
        intro_lines: list[str] = []
        if top_artists:
            intro_lines.append(f"**{WINNERS_TITLE_IMAGE}**")
            for u, score, xp, idx in top_artists:
                sparkle = WINNER_SPARKLE[idx]
                if xp > 0:
                    intro_lines.append(
                        f"{WINNER_MEDAL[idx]} {sparkle} **{u.display_name}** — "
                        f"{score} pts — **+{xp} XP**"
                    )
                else:
                    intro_lines.append(
                        f"{WINNER_MEDAL[idx]} {sparkle} **{u.display_name}** — "
                        f"{score} pts"
                    )
            intro_lines.append("")

        if top_voters:
            intro_lines.append(f"**{VOTERS_TITLE_TOP5}**")
            for u, cnt, xp, rank in top_voters:
                sparkle = VOTER_SPARKLE.get(rank, "")
                if xp > 0:
                    intro_lines.append(
                        f"{VOTER_MEDAL[rank]} {sparkle} **{u.display_name}** — "
                        f"{cnt} reactions — **+{xp} XP**"
                    )
                else:
                    intro_lines.append(
                        f"{VOTER_MEDAL[rank]} {sparkle} **{u.display_name}** — "
                        f"{cnt} reactions"
                    )

        intro = discord.Embed(
            title=f"🏅 Top 5 Champions — {month_label}",
            description="\n".join(intro_lines),
            color=discord.Color.gold(),
        )
        intro.set_footer(
            text=f"Requested: {datetime.now(timezone.utc).strftime('%Y/%m/%d %H:%M')} UTC"
        )

        # ---- Pings (only when public), attached to intro ----
        content: Optional[str] = None
        if public:
            mention_parts: list[str] = []
            if ping_role:
                mention_parts.append(ping_role.mention)
            pinged: set[int] = set()
            for u, *_ in top_artists:
                if u.id not in pinged:
                    pinged.add(u.id)
                    mention_parts.append(u.mention)
            for u, *_ in top_voters:
                if u.id not in pinged:
                    pinged.add(u.id)
                    mention_parts.append(u.mention)
            if mention_parts:
                content = " ".join(mention_parts)

        await self._paced_send(
            interaction,
            content=content,
            embed=intro,
            ephemeral=ephemeral_flag,
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=[ping_role] if (public and ping_role) else False,
                everyone=False,
            ),
        )

        # ---- Individual embeds with avatar as THUMBNAIL ----
        # Artists batch
        if top_artists:
            artist_embeds: list[discord.Embed] = []
            for u, score, xp, idx in top_artists:
                sparkle = WINNER_SPARKLE[idx]
                if xp > 0:
                    desc = f"🎨 {score} pts • **+{xp} XP**"
                else:
                    desc = f"🎨 {score} pts"
                e = discord.Embed(
                    title=f"{WINNER_MEDAL[idx]} {sparkle} {u.display_name}",
                    description=(
                        f"**{WINNERS_TITLE_IMAGE}**\n{desc}"
                    ),
                    color=discord.Color.gold(),
                )
                e.set_thumbnail(url=u.display_avatar.url)
                artist_embeds.append(e)
            await self._paced_send(interaction, embeds=artist_embeds, ephemeral=ephemeral_flag)

        # Voters batch
        if top_voters:
            voter_embeds: list[discord.Embed] = []
            for u, cnt, xp, rank in top_voters:
                sparkle = VOTER_SPARKLE.get(rank, "")
                if xp > 0:
                    desc = f"🗳️ {cnt} reactions • **+{xp} XP**"
                else:
                    desc = f"🗳️ {cnt} reactions"
                e = discord.Embed(
                    title=f"{VOTER_MEDAL[rank]} {sparkle} {u.display_name}",
                    description=(
                        f"**{VOTERS_TITLE_TOP5}**\n{desc}"
                    ),
                    color=discord.Color.blurple(),
                )
                e.set_thumbnail(url=u.display_avatar.url)
                voter_embeds.append(e)
            await self._paced_send(interaction, embeds=voter_embeds, ephemeral=ephemeral_flag)

    # =====================================================
    # RENDERING (for /ai_vote and /ai_video — top 3 winners & voters)
    # =====================================================
    async def _render_ranking(
        self, interaction: discord.Interaction,
        msgs: list[discord.Message], title: str,
        ephemeral: bool, limit: int, sort_order: str,
        kind: str,   # "image" | "video"
        img_to_videos: dict[int, list[discord.Message]],
        video_to_img: dict[int, discord.Message],
        voter_counts: dict[int, int],
    ):
        guild = interaction.guild
        is_public = not ephemeral

        stats = {m.id: calc_ai_points(m) for m in msgs}

        def sort_key(m):
            s, _, e = stats[m.id]
            return s, e, m.created_at

        ranked = sorted(msgs, key=sort_key, reverse=True)

        tie_counts: dict[tuple[int, int], int] = {}
        for m in ranked:
            s, _, e = stats[m.id]
            tie_counts[(s, e)] = tie_counts.get((s, e), 0) + 1
        tied_keys = {k for k, c in tie_counts.items() if c > 1}

        rank_map: dict[int, int] = {}
        last_key = None
        current_rank = 0
        for idx, m in enumerate(ranked, start=1):
            s, _, e = stats[m.id]
            key = (s, e)
            if key == last_key:
                rank_map[m.id] = current_rank
            else:
                current_rank = idx
                rank_map[m.id] = current_rank
                last_key = key

        top_msgs = ranked[:limit]
        display_msgs = top_msgs if sort_order == "asc" else list(reversed(top_msgs))

        # Top 3 unique winners
        top_unique = top_unique_users(msgs, stats, max_count=3)

        winners_title = WINNERS_TITLE_IMAGE if kind == "image" else WINNERS_TITLE_VIDEO

        winners_txt = ""
        for i, m in enumerate(top_unique):
            u = get_target_user(m)
            xp = WINNER_XP.get(i, 0)
            sparkle = WINNER_SPARKLE[i]
            winners_txt += (
                f"{WINNER_MEDAL[i]} {sparkle} **{u.display_name}** — **+{xp} XP**\n"
            )
        if not winners_txt:
            winners_txt = "—\n"

        # Top 3 voters
        voter_ranks = compute_voter_ranks(voter_counts, max_ranks=3)
        voters_txt = ""
        for rank, uid, cnt in voter_ranks:
            name = await resolve_display_name(uid, guild, self.bot)
            xp = VOTER_XP.get(rank, 0)
            sparkle = VOTER_SPARKLE.get(rank, "")
            voters_txt += (
                f"{VOTER_MEDAL[rank]} {sparkle} **{name}** — "
                f"{cnt} reactions — **+{xp} XP**\n"
            )
        if not voters_txt:
            voters_txt = "—\n"

        now_str = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
        intro = discord.Embed(
            title=title,
            description=(
                f"**{winners_title}**\n{winners_txt}\n"
                f"**{VOTERS_TITLE_TOP3}**\n{voters_txt}"
            ),
            color=discord.Color.blurple(),
        )
        intro.set_footer(text=f"Updated: {now_str} UTC")
        await self._paced_send(interaction, embed=intro, ephemeral=ephemeral)

        top_user_ids = [get_target_user(m).id for m in top_unique]

        # Detail embeds batched
        detail_batch: list[discord.Embed] = []

        async def flush_batch():
            if detail_batch:
                await self._paced_send(
                    interaction, embeds=list(detail_batch), ephemeral=ephemeral
                )
                detail_batch.clear()

        for m in display_msgs:
            u = get_target_user(m)
            score, breakdown, emoji_total = stats[m.id]
            rank_number = rank_map[m.id]

            medal = WINNER_MEDAL[top_user_ids.index(u.id)] if u.id in top_user_ids else ""
            tie_suffix = f" ({emoji_total} 📊)" if (score, emoji_total) in tied_keys else ""

            lines = []
            for k, d in breakdown.items():
                lbl = "📝" if k == "Various" else str(guild.get_emoji(k) or k)
                lines.append(f"{lbl} × {d['votes']} → {d['points']} pts")
            detail_text = "\n".join(lines) if lines else "_No reactions_"

            links = f"[Jump to Post 🎖️(**VOTE**🎖️)]({m.jump_url})"
            if kind == "image":
                vids = img_to_videos.get(m.id, [])
                if len(vids) == 1:
                    links += f" • [🎬 **VIDEO**]({vids[0].jump_url})"
                elif len(vids) > 1:
                    parts = " ".join(
                        f"[🎬 V{i+1}]({v.jump_url})" for i, v in enumerate(vids)
                    )
                    links += f" • {parts}"
            elif kind == "video":
                src = video_to_img.get(m.id)
                if src:
                    links += f" • [🖼️ Source]({src.jump_url})"

            embed = discord.Embed(
                title=f"#{rank_number} — {u.display_name} {medal} — {score} pts{tie_suffix}",
                description=f"{links}\n\n{detail_text}",
                color=discord.Color.gold() if medal else discord.Color.teal(),
            )
            embed.set_thumbnail(url=u.display_avatar.url)
            embed.set_footer(text=f"Posted: {m.created_at.strftime('%Y/%m/%d %H:%M')} UTC")

            image_url = None
            if kind == "image":
                image_url = get_image_url_from_post(m)
            else:
                src = video_to_img.get(m.id)
                if src:
                    image_url = get_image_url_from_post(src)
            if image_url:
                embed.set_image(url=image_url)

            detail_batch.append(embed)
            if len(detail_batch) >= DETAIL_EMBEDS_PER_MSG:
                await flush_batch()
        await flush_batch()

        # Final summary + pings
        if top_unique or voter_ranks:
            final_desc_lines = []

            if top_unique:
                final_desc_lines.append(f"**{winners_title}**")
                for i, m in enumerate(top_unique):
                    u = get_target_user(m)
                    score, _, emoji_total = stats[m.id]
                    tie_suffix = f" ({emoji_total} 📊)" if (score, emoji_total) in tied_keys else ""
                    xp = WINNER_XP.get(i, 0)
                    sparkle = WINNER_SPARKLE[i]
                    final_desc_lines.append(
                        f"{WINNER_MEDAL[i]} {sparkle} **{u.display_name}** — "
                        f"{score} pts{tie_suffix} — **+{xp} XP**"
                    )
                final_desc_lines.append("")

            if voter_ranks:
                final_desc_lines.append(f"**{VOTERS_TITLE_TOP3}**")
                for rank, uid, cnt in voter_ranks:
                    name = await resolve_display_name(uid, guild, self.bot)
                    xp = VOTER_XP.get(rank, 0)
                    sparkle = VOTER_SPARKLE.get(rank, "")
                    final_desc_lines.append(
                        f"{VOTER_MEDAL[rank]} {sparkle} **{name}** — "
                        f"{cnt} reactions — **+{xp} XP**"
                    )

            winner_uids: set[int] = set()
            winner_mentions: list[str] = []
            for m in top_unique:
                u = get_target_user(m)
                winner_uids.add(u.id)
                winner_mentions.append(u.mention)

            voter_mentions: list[str] = []
            if is_public:
                for _rank, uid, _cnt in voter_ranks:
                    if uid in winner_uids:
                        continue
                    voter_mentions.append(f"<@{uid}>")

            all_mentions = winner_mentions + voter_mentions
            content = " ".join(all_mentions) if (is_public and all_mentions) else None

            final_time = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
            await self._paced_send(
                interaction,
                content=content,
                embed=discord.Embed(
                    title=f"🏆 Final Results (as of {final_time} UTC)",
                    description="\n".join(final_desc_lines),
                    color=discord.Color.gold(),
                ),
                ephemeral=ephemeral,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingAnyRole):
            if interaction.response.is_done():
                await interaction.followup.send("❌ No permission.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return
        logger.exception("Unhandled app command error", exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ An unexpected error occurred.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ An unexpected error occurred.", ephemeral=True
                )
        except Exception:
            pass


# =====================
# SETUP
# =====================
async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))