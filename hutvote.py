# hut_vote_cog.py
import asyncio
import calendar
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

# =====================
# CONFIG
# =====================
ALLOWED_ROLE_IDS = {1346414581643219029, 1346428405368750122}
VOTER_EXCLUDED_ROLE_ID = 1346414581643219029   # Reactions von diesen Membern zählen NICHT

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

# XP — nur Rang 1-3 bekommen XP; Rang 4-5 sind Honorable Mention
WINNER_XP = {0: 5000, 1: 3000, 2: 1000}
VOTER_XP  = {1: 2000, 2: 1000, 3: 500}

WINNER_MEDAL   = ["🥇", "🥈", "🥉", "🏅", "🎖️"]
WINNER_SPARKLE = ["✨", "⭐", "💫", "🌠", "🔆"]
VOTER_MEDAL    = {1: "🥇", 2: "🥈", 3: "🥉", 4: "🏅", 5: "🎖️"}
VOTER_SPARKLE  = {1: "💎", 2: "🔥", 3: "🌟", 4: "🎯", 5: "⚡"}

WINNERS_TITLE_IMAGE = "🏆 TOP Goon Hut AI Artists 🏆"
WINNERS_TITLE_VIDEO = "🎬 TOP Goon Hut AI Video Makers 🎬"
VOTERS_TITLE_TOP3   = "🗳️ Top 3 Voters 🗳️"
VOTERS_TITLE_TOP5   = "🗳️ Top 5 Voters 🗳️"

# Performance
VOTER_FETCH_CONCURRENCY = 8      # discord.py drosselt selbst; bei "We are being rate limited" im Log runter
DETAIL_EMBEDS_PER_MSG   = 5
FOLLOWUP_DELAY_SEC      = 0.3
CACHE_DB_PATH           = "hut_vote_cache.sqlite3"
YEAR_MIN                = 2024
YEAR_MAX                = 2100

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
MONTH_CHOICES = [
    app_commands.Choice(name=calendar.month_name[i], value=str(i))
    for i in range(1, 13)
]


def resolve_year(year: Optional[int]) -> int:
    return year if year is not None else datetime.now(timezone.utc).year


# =========================================================
# POST DETECTOR
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


def calc_ai_points(msg: discord.Message) -> tuple[int, dict, int]:
    breakdown: dict = {}
    score = 0
    emoji_total = 0
    for reaction in msg.reactions:
        key = normalize_emoji(reaction)
        if str(key) == str(STARBOARD_IGNORE_ID):
            continue
        votes = reaction.count
        if key in EMOJI_POINTS:
            extra = max(votes - 1, 0)          # Bot-Seed abziehen
            if extra <= 0:
                continue
            points = extra * EMOJI_POINTS[key]
            breakdown[key] = {"votes": extra, "points": points}
            score += points
            emoji_total += extra
        else:
            extra = votes - (1 if reaction.me else 0)   # eigene Bot-Reaction nicht zählen
            if extra <= 0:
                continue
            breakdown.setdefault("Various", {"votes": 0, "points": 0})
            breakdown["Various"]["votes"] += extra
            breakdown["Various"]["points"] += extra
            score += extra
            emoji_total += extra
    return score, breakdown, emoji_total


def get_month_utc_range(year: int, month: int):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = (
        datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    )
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
    """Alle Videos zwischen zwei Bild-Posts gehören zum vorangehenden Bild (gleicher Kanal)."""
    img_to_videos: dict[int, list[discord.Message]] = {}
    video_to_img: dict[int, discord.Message] = {}
    for msgs in per_channel.values():
        current_img: Optional[discord.Message] = None
        for m in sorted(msgs, key=lambda x: x.created_at):
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
    ranked = sorted(
        msgs,
        key=lambda m: (stats[m.id][0], stats[m.id][2], m.created_at),
        reverse=True,
    )
    result: list[discord.Message] = []
    seen: set[int] = set()
    for m in ranked:
        u = get_target_user(m)
        if u.id in IGNORE_IDS or u.name == "Deleted User" or u.id in seen:
            continue
        result.append(m)
        seen.add(u.id)
        if len(result) == max_count:
            break
    return result


def compute_voter_ranks(
    counts: dict[int, int], max_ranks: int = 3
) -> list[tuple[int, int, int]]:
    """Dense Ranking, auf max_ranks begrenzt. Liefert (rank, user_id, count)."""
    out: list[tuple[int, int, int]] = []
    current_rank = 0
    last_count = None
    for uid, cnt in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        if cnt != last_count:
            current_rank += 1
            if current_rank > max_ranks:
                break
            last_count = cnt
        out.append((current_rank, uid, cnt))
    return out


async def resolve_display_name(uid: int, guild: discord.Guild, bot: commands.Bot) -> str:
    m = guild.get_member(uid) if guild else None
    if m:
        return m.display_name
    try:
        return (await bot.fetch_user(uid)).name
    except Exception:
        return f"User#{uid}"


async def resolve_user_object(
    uid: int, guild: discord.Guild, bot: commands.Bot
) -> Optional[discord.abc.User]:
    m = guild.get_member(uid) if guild else None
    if m:
        return m
    try:
        return await bot.fetch_user(uid)
    except Exception:
        return None


# =====================
# VOTER CACHE
# =====================
class VoterCache:
    """Speichert pro Nachricht die Roh-Klicks (user_id -> Anzahl) + Fingerprint der Reactions.
    Stimmt der Fingerprint, muss reaction.users() nicht erneut abgefragt werden."""

    def __init__(self, path: str = CACHE_DB_PATH):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS voter_cache (
                   message_id  INTEGER PRIMARY KEY,
                   fingerprint TEXT NOT NULL,
                   counts      TEXT NOT NULL,
                   updated_at  INTEGER NOT NULL
               )"""
        )
        self.conn.commit()

    def get(self, message_id: int, fingerprint: str) -> Optional[dict[int, int]]:
        row = self.conn.execute(
            "SELECT fingerprint, counts FROM voter_cache WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row and row[0] == fingerprint:
            return {int(k): v for k, v in json.loads(row[1]).items()}
        return None

    def put_many(self, rows: list[tuple[int, str, dict[int, int]]]):
        if not rows:
            return
        now = int(time.time())
        self.conn.executemany(
            "INSERT OR REPLACE INTO voter_cache VALUES (?, ?, ?, ?)",
            [(mid, fp, json.dumps(c), now) for mid, fp, c in rows],
        )
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def reaction_fingerprint(msg: discord.Message) -> str:
    parts = []
    for r in msg.reactions:
        key = normalize_emoji(r)
        if str(key) == str(STARBOARD_IGNORE_ID):
            continue
        parts.append(f"{key}:{r.count}")
    return "|".join(sorted(parts))


def has_countable_reactions(msg: discord.Message) -> bool:
    for r in msg.reactions:
        key = normalize_emoji(r)
        if str(key) == str(STARBOARD_IGNORE_ID):
            continue
        if key in EMOJI_POINTS and r.count <= 1:
            continue
        return True
    return False


async def collect_voter_counts(
    msgs: list[discord.Message],
    guild: discord.Guild,
    cache: VoterCache,
) -> dict[int, int]:
    """Jeder Reaction-Klick = 1 Punkt. Nutzt den Cache, lädt nur geänderte Posts nach."""
    counts: dict[int, int] = {}
    excluded_cache: dict[int, bool] = {}

    def is_excluded(uid: int) -> bool:
        if uid in excluded_cache:
            return excluded_cache[uid]
        if uid in IGNORE_IDS:
            excluded_cache[uid] = True
            return True
        member = guild.get_member(uid) if guild else None
        excluded_cache[uid] = bool(
            member and any(r.id == VOTER_EXCLUDED_ROLE_ID for r in member.roles)
        )
        return excluded_cache[uid]

    def merge(raw: dict[int, int]):
        for uid, c in raw.items():
            if not is_excluded(uid):
                counts[uid] = counts.get(uid, 0) + c

    sem = asyncio.Semaphore(VOTER_FETCH_CONCURRENCY)

    async def raw_clicks(msg: discord.Message) -> dict[int, int]:
        # Roh-Klicks OHNE Ausschlussregeln -> Cache bleibt gültig, auch wenn Rollen/IGNORE_IDS sich ändern
        local: dict[int, int] = {}
        async with sem:
            for reaction in msg.reactions:
                key = normalize_emoji(reaction)
                if str(key) == str(STARBOARD_IGNORE_ID):
                    continue
                if key in EMOJI_POINTS and reaction.count <= 1:
                    continue
                try:
                    async for user in reaction.users():
                        if user.bot:
                            continue
                        local[user.id] = local.get(user.id, 0) + 1
                except Exception:
                    logger.exception("reaction.users() failed for message %s", msg.id)
        return local

    to_fetch: list[tuple[discord.Message, str]] = []
    cached = 0
    for m in msgs:
        if not has_countable_reactions(m):
            continue
        fp = reaction_fingerprint(m)
        hit = cache.get(m.id, fp)
        if hit is not None:
            cached += 1
            merge(hit)
        else:
            to_fetch.append((m, fp))

    logger.info("Voter scan: %d from cache, %d to fetch", cached, len(to_fetch))

    results = await asyncio.gather(
        *(raw_clicks(m) for m, _ in to_fetch), return_exceptions=True
    )
    new_rows: list[tuple[int, str, dict[int, int]]] = []
    for (m, fp), r in zip(to_fetch, results):
        if isinstance(r, dict):
            new_rows.append((m.id, fp, r))
            merge(r)
        else:
            logger.error("Voter fetch failed for %s: %r", m.id, r)
    cache.put_many(new_rows)
    return counts


# =====================
# COG
# =====================
class HutVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voter_cache = VoterCache()

    async def cog_unload(self):
        self.voter_cache.close()

    # ---------- Scanning ----------
    async def _safe_text_channel(self, guild: discord.Guild, channel_id: int):
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                logger.exception("Could not load channel %s", channel_id)
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _scan_bot_messages(
        self,
        channel: discord.TextChannel,
        start: datetime,
        end_exclusive: datetime,
    ) -> list[discord.Message]:
        matched: list[discord.Message] = []
        try:
            async for msg in channel.history(
                after=start - timedelta(seconds=1), before=end_exclusive, limit=None
            ):
                if msg.author.id != BOT_ID:
                    continue
                if not (start <= msg.created_at < end_exclusive):
                    continue
                matched.append(msg)
        except Exception:
            logger.exception("Error reading #%s (%s)", channel.name, channel.id)
        return matched

    async def _scan_multi(self, guild, channel_ids, start, end_exclusive):
        async def scan_one(cid):
            ch = await self._safe_text_channel(guild, cid)
            if ch is None:
                return cid, []
            return cid, await self._scan_bot_messages(ch, start, end_exclusive)

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

    # ---------- Gemeinsame Logik für /ai_vote und /ai_video ----------
    async def _run_ranking(
        self,
        interaction: discord.Interaction,
        kind: str,                       # "image" | "video"
        month: app_commands.Choice[str],
        year: Optional[int],
        topuser: Optional[app_commands.Choice[str]],
        sort: Optional[app_commands.Choice[str]],
        public: bool,
        voters: bool,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)
        t0 = time.monotonic()

        year_v = resolve_year(year)
        month_v = int(month.value)
        start_dt, end_ex = get_month_utc_range(year_v, month_v)

        per_ch = await self._scan_multi(interaction.guild, SCAN_CHANNEL_IDS, start_dt, end_ex)
        all_msgs = [m for msgs in per_ch.values() for m in msgs]
        image_msgs, video_msgs = self._split_by_type(all_msgs)
        target_msgs = image_msgs if kind == "image" else video_msgs

        if not target_msgs:
            label = "AI posts" if kind == "image" else "AI video posts"
            return await interaction.followup.send(f"No {label} found.", ephemeral=ephemeral_flag)

        img_to_videos, video_to_img = build_position_maps(per_ch)
        voter_counts = (
            await collect_voter_counts(all_msgs, interaction.guild, self.voter_cache)
            if voters else {}
        )

        logger.info("Ranking data ready in %.1fs (%d posts)", time.monotonic() - t0, len(all_msgs))

        prefix = "🤖 AI Top" if kind == "image" else "🎬 AI Video Top"
        await self._render_ranking(
            interaction=interaction,
            msgs=target_msgs,
            title=f"{prefix} — {calendar.month_name[month_v]} {year_v}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc",
            kind=kind,
            img_to_videos=img_to_videos,
            video_to_img=video_to_img,
            voter_counts=voter_counts,
        )

    # ============ /ai_vote ============
    @app_commands.command(name="ai_vote", description="Shows AI image ranking by reactions")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        month="Select month",
        year="Year (default: current year)",
        topuser="Number of top posts to display",
        sort="Sort order",
        public="Public or ephemeral",
        voters="Also compute Top Voters (slower)",
    )
    @app_commands.choices(month=MONTH_CHOICES, topuser=TOPUSER_CHOICES, sort=SORT_CHOICES)
    async def ai_vote(
        self,
        interaction: discord.Interaction,
        month: app_commands.Choice[str],
        year: Optional[app_commands.Range[int, YEAR_MIN, YEAR_MAX]] = None,
        topuser: Optional[app_commands.Choice[str]] = None,
        sort: Optional[app_commands.Choice[str]] = None,
        public: bool = False,
        voters: bool = True,
    ):
        await self._run_ranking(interaction, "image", month, year, topuser, sort, public, voters)

    # ============ /ai_video ============
    @app_commands.command(name="ai_video", description="Shows AI video ranking by reactions")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        month="Select month",
        year="Year (default: current year)",
        topuser="Number of top posts to display",
        sort="Sort order",
        public="Public or ephemeral",
        voters="Also compute Top Voters (slower)",
    )
    @app_commands.choices(month=MONTH_CHOICES, topuser=TOPUSER_CHOICES, sort=SORT_CHOICES)
    async def ai_video(
        self,
        interaction: discord.Interaction,
        month: app_commands.Choice[str],
        year: Optional[app_commands.Range[int, YEAR_MIN, YEAR_MAX]] = None,
        topuser: Optional[app_commands.Choice[str]] = None,
        sort: Optional[app_commands.Choice[str]] = None,
        public: bool = False,
        voters: bool = True,
    ):
        await self._run_ranking(interaction, "video", month, year, topuser, sort, public, voters)

    # ============ /top5 ============
    @app_commands.command(name="top5", description="Show top 5 artists and/or top 5 voters with avatars")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        kind="What to show: artists, voters, or both",
        month="Select month",
        year="Year (default: current year)",
        public="Post publicly (default: no)",
        ping_role="Optional role to ping (only used when public)",
    )
    @app_commands.choices(kind=TOP5_KIND_CHOICES, month=MONTH_CHOICES)
    async def top5(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        month: app_commands.Choice[str],
        year: Optional[app_commands.Range[int, YEAR_MIN, YEAR_MAX]] = None,
        public: bool = False,
        ping_role: Optional[discord.Role] = None,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        year_v = resolve_year(year)
        month_v = int(month.value)
        start_dt, end_ex = get_month_utc_range(year_v, month_v)

        want_artists = kind.value in ("artists", "both")
        want_voters  = kind.value in ("voters", "both")

        per_ch = await self._scan_multi(interaction.guild, SCAN_CHANNEL_IDS, start_dt, end_ex)
        all_msgs = [m for msgs in per_ch.values() for m in msgs]
        image_msgs, _ = self._split_by_type(all_msgs)

        # ---- Top 5 Artists: (user, score, xp, idx) ----
        top_artists: list[tuple[discord.abc.User, int, int, int]] = []
        if want_artists and image_msgs:
            stats = {m.id: calc_ai_points(m) for m in image_msgs}
            for i, m in enumerate(top_unique_users(image_msgs, stats, max_count=5)):
                top_artists.append((get_target_user(m), stats[m.id][0], WINNER_XP.get(i, 0), i))

        # ---- Top 5 Voters: (user, count, xp, rank) ----
        top_voters: list[tuple[discord.abc.User, int, int, int]] = []
        if want_voters and all_msgs:
            voter_counts = await collect_voter_counts(all_msgs, interaction.guild, self.voter_cache)
            for rank, uid, cnt in compute_voter_ranks(voter_counts, max_ranks=5):
                u = await resolve_user_object(uid, interaction.guild, self.bot)
                if u is not None:
                    top_voters.append((u, cnt, VOTER_XP.get(rank, 0), rank))

        if not top_artists and not top_voters:
            return await interaction.followup.send("No data found for that period.", ephemeral=ephemeral_flag)

        month_label = f"{calendar.month_name[month_v]} {year_v}"

        # ---- Intro ----
        intro_lines: list[str] = []
        if top_artists:
            intro_lines.append(f"**{WINNERS_TITLE_IMAGE}**")
            for u, score, xp, idx in top_artists:
                xp_txt = f" — **+{xp} XP**" if xp > 0 else ""
                intro_lines.append(
                    f"{WINNER_MEDAL[idx]} {WINNER_SPARKLE[idx]} **{u.display_name}** — {score} pts{xp_txt}"
                )
            intro_lines.append("")
        if top_voters:
            intro_lines.append(f"**{VOTERS_TITLE_TOP5}**")
            for u, cnt, xp, rank in top_voters:
                xp_txt = f" — **+{xp} XP**" if xp > 0 else ""
                intro_lines.append(
                    f"{VOTER_MEDAL[rank]} {VOTER_SPARKLE.get(rank, '')} **{u.display_name}** — "
                    f"{cnt} reactions{xp_txt}"
                )

        intro = discord.Embed(
            title=f"🏅 Top 5 Champions — {month_label}",
            description="\n".join(intro_lines),
            color=discord.Color.gold(),
        )
        intro.set_footer(text=f"Requested: {datetime.now(timezone.utc).strftime('%Y/%m/%d %H:%M')} UTC")

        # ---- Pings (nur public) ----
        content: Optional[str] = None
        if public:
            mention_parts: list[str] = []
            if ping_role:
                mention_parts.append(ping_role.mention)
            pinged: set[int] = set()
            for u, *_ in top_artists + top_voters:
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

        # ---- Einzel-Embeds mit Avatar ----
        if top_artists:
            artist_embeds: list[discord.Embed] = []
            for u, score, xp, idx in top_artists:
                desc = f"🎨 {score} pts" + (f" • **+{xp} XP**" if xp > 0 else "")
                e = discord.Embed(
                    title=f"{WINNER_MEDAL[idx]} {WINNER_SPARKLE[idx]} {u.display_name}",
                    description=f"**{WINNERS_TITLE_IMAGE}**\n{desc}",
                    color=discord.Color.gold(),
                )
                e.set_thumbnail(url=u.display_avatar.url)
                artist_embeds.append(e)
            await self._paced_send(interaction, embeds=artist_embeds, ephemeral=ephemeral_flag)

        if top_voters:
            voter_embeds: list[discord.Embed] = []
            for u, cnt, xp, rank in top_voters:
                desc = f"🗳️ {cnt} reactions" + (f" • **+{xp} XP**" if xp > 0 else "")
                e = discord.Embed(
                    title=f"{VOTER_MEDAL[rank]} {VOTER_SPARKLE.get(rank, '')} {u.display_name}",
                    description=f"**{VOTERS_TITLE_TOP5}**\n{desc}",
                    color=discord.Color.blurple(),
                )
                e.set_thumbnail(url=u.display_avatar.url)
                voter_embeds.append(e)
            await self._paced_send(interaction, embeds=voter_embeds, ephemeral=ephemeral_flag)

    # =====================================================
    # RENDERING (/ai_vote und /ai_video)
    # =====================================================
    async def _render_ranking(
        self,
        interaction: discord.Interaction,
        msgs: list[discord.Message],
        title: str,
        ephemeral: bool,
        limit: int,
        sort_order: str,
        kind: str,
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

        # Ties + Competition-Ranking (1,1,3)
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
            if key != last_key:
                current_rank = idx
                last_key = key
            rank_map[m.id] = current_rank

        top_msgs = ranked[:limit]
        display_msgs = top_msgs if sort_order == "asc" else list(reversed(top_msgs))

        top_unique = top_unique_users(msgs, stats, max_count=3)
        top_user_ids = [get_target_user(m).id for m in top_unique]
        winners_title = WINNERS_TITLE_IMAGE if kind == "image" else WINNERS_TITLE_VIDEO

        # Voter-Ränge + Namen einmalig auflösen
        voter_ranks = compute_voter_ranks(voter_counts, max_ranks=3)
        voter_names: dict[int, str] = {}
        for _r, uid, _c in voter_ranks:
            voter_names[uid] = await resolve_display_name(uid, guild, self.bot)

        def winners_block(with_score: bool) -> str:
            lines = []
            for i, m in enumerate(top_unique):
                u = get_target_user(m)
                score, _, emoji_total = stats[m.id]
                xp = WINNER_XP.get(i, 0)
                score_txt = ""
                if with_score:
                    tie = f" ({emoji_total} 📊)" if (score, emoji_total) in tied_keys else ""
                    score_txt = f"{score} pts{tie} — "
                lines.append(
                    f"{WINNER_MEDAL[i]} {WINNER_SPARKLE[i]} **{u.display_name}** — {score_txt}**+{xp} XP**"
                )
            return "\n".join(lines) if lines else "—"

        def voters_block() -> str:
            if not voter_counts:
                return "_(voter scan skipped)_"
            lines = [
                f"{VOTER_MEDAL[rank]} {VOTER_SPARKLE.get(rank, '')} **{voter_names[uid]}** — "
                f"{cnt} reactions — **+{VOTER_XP.get(rank, 0)} XP**"
                for rank, uid, cnt in voter_ranks
            ]
            return "\n".join(lines) if lines else "—"

        now_str = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
        intro = discord.Embed(
            title=title,
            description=(
                f"**{winners_title}**\n{winners_block(False)}\n\n"
                f"**{VOTERS_TITLE_TOP3}**\n{voters_block()}"
            ),
            color=discord.Color.blurple(),
        )
        intro.set_footer(text=f"Updated: {now_str} UTC")
        await self._paced_send(interaction, embed=intro, ephemeral=ephemeral)

        # ---- Detail-Embeds ----
        detail_batch: list[discord.Embed] = []

        async def flush_batch():
            if detail_batch:
                await self._paced_send(interaction, embeds=list(detail_batch), ephemeral=ephemeral)
                detail_batch.clear()

        for m in display_msgs:
            u = get_target_user(m)
            score, breakdown, emoji_total = stats[m.id]
            rank_number = rank_map[m.id]

            medal = WINNER_MEDAL[top_user_ids.index(u.id)] if u.id in top_user_ids else ""
            tie_suffix = f" ({emoji_total} 📊)" if (score, emoji_total) in tied_keys else ""

            lines = []
            for k, d in breakdown.items():
                if k == "Various":
                    lbl = "📝"
                else:
                    lbl = str(guild.get_emoji(k) or k) if isinstance(k, int) else k
                lines.append(f"{lbl} × {d['votes']} → {d['points']} pts")
            detail_text = "\n".join(lines) if lines else "_No reactions_"

            links = f"[Jump to Post 🎖️(**VOTE**🎖️)]({m.jump_url})"
            if kind == "image":
                vids = img_to_videos.get(m.id, [])
                if len(vids) == 1:
                    links += f" • [🎬 **VIDEO**]({vids[0].jump_url})"
                elif len(vids) > 1:
                    links += " • " + " ".join(f"[🎬 V{i+1}]({v.jump_url})" for i, v in enumerate(vids))
            else:
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

            if kind == "image":
                image_url = get_image_url_from_post(m)
            else:
                src = video_to_img.get(m.id)
                image_url = get_image_url_from_post(src) if src else None
            if image_url:
                embed.set_image(url=image_url)

            detail_batch.append(embed)
            if len(detail_batch) >= DETAIL_EMBEDS_PER_MSG:
                await flush_batch()
        await flush_batch()

        # ---- Final Summary + Pings ----
        if top_unique or voter_ranks:
            final_desc = f"**{winners_title}**\n{winners_block(True)}"
            if voter_ranks:
                final_desc += f"\n\n**{VOTERS_TITLE_TOP3}**\n{voters_block()}"

            content: Optional[str] = None
            if is_public:
                winner_uids = set(top_user_ids)
                mentions = [get_target_user(m).mention for m in top_unique]
                mentions += [f"<@{uid}>" for _r, uid, _c in voter_ranks if uid not in winner_uids]
                content = " ".join(mentions) if mentions else None

            final_time = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
            await self._paced_send(
                interaction,
                content=content,
                embed=discord.Embed(
                    title=f"🏆 Final Results (as of {final_time} UTC)",
                    description=final_desc,
                    color=discord.Color.gold(),
                ),
                ephemeral=ephemeral,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

    # ---------- Error Handling ----------
    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingAnyRole):
            msg = "❌ No permission."
        else:
            logger.exception("Unhandled app command error", exc_info=error)
            msg = "❌ An unexpected error occurred."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


# =====================
# SETUP
# =====================
async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))