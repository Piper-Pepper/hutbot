# hut_vote_cog.py — Teil 1/2
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
# KONFIG
# =====================
ALLOWED_ROLE_IDS = {1346414581643219029, 1346428405368750122}
VOTER_EXCLUDED_ROLE_ID = 1346414581643219029   # Deren Reactions zählen NICHT

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

EMOJI_POINTS = {
    "1️⃣": 1,
    "2️⃣": 2,
    "3️⃣": 3,
    CUSTOM_5_EMOJI_ID: 5,
}
IGNORE_IDS = {1292194320786522223}

# XP-Belohnungen
WINNER_XP = {0: 5000, 1: 3000, 2: 1000}         # nach Position im Top-3 (0-indexed)
VOTER_XP  = {1: 3000, 2: 2000, 3: 1000}         # nach Dense-Rank

# Fancy Icons pro Rang
WINNER_MEDAL   = ["🥇", "🥈", "🥉"]
WINNER_SPARKLE = ["✨", "⭐", "💫"]
VOTER_MEDAL    = {1: "🥇", 2: "🥈", 3: "🥉"}
VOTER_SPARKLE  = {1: "💎", 2: "🔥", 3: "🌟"}

# Section-Titles
WINNERS_TITLE_IMAGE = "🏆 TOP Goon Hut AI Artists 🏆"
WINNERS_TITLE_VIDEO = "🎬 TOP Goon Hut AI Video Makers 🎬"
VOTERS_TITLE        = "🗳️ Top 3 Voters 🗳️"

# Performance — Discord rate-limited pro Endpoint. 5 ist der Sweet-Spot.
VOTER_FETCH_CONCURRENCY = 5
DETAIL_EMBEDS_PER_MSG   = 5
FOLLOWUP_DELAY_SEC      = 0.3

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
# POST DETECTOR — NUR HIER anpassen bei Format-Änderung
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
    """Alle Videos zwischen zwei Bildern gehören zum vorherigen Bild
    (im selben Channel, chronologisch aufsteigend)."""
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


async def collect_voter_counts(
    msgs: list[discord.Message],
    guild: discord.Guild,
) -> dict[int, int]:
    """Zählt pro User, auf wie vielen Posts er/sie reagiert hat.
    Mehrere Emojis auf demselben Post = trotzdem nur 1 Punkt.

    Filter:
      - Bots raus
      - IGNORE_IDS raus
      - Mitglieder mit VOTER_EXCLUDED_ROLE_ID raus
      - STARBOARD-Emoji ignoriert
      - Bekannte Voting-Emojis mit count<=1 = nur Bot-Vorreaktion → skip API-Call
    """
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

    async def voters_of_msg(msg: discord.Message) -> set[int]:
        voters: set[int] = set()
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
                        if is_excluded(user.id):
                            continue
                        voters.add(user.id)
                except Exception:
                    logger.exception("Fehler bei reaction.users()")
        return voters

    results = await asyncio.gather(
        *(voters_of_msg(m) for m in msgs), return_exceptions=True
    )
    for r in results:
        if isinstance(r, set):
            for uid in r:
                counts[uid] = counts.get(uid, 0) + 1
    return counts


def compute_voter_ranks(counts: dict[int, int]) -> list[tuple[int, int, int]]:
    """Dense-Ranking mit Cap auf 3 Ränge (mehrere User pro Rang möglich).
    Rückgabe: Liste (rank, user_id, count)."""
    sorted_voters = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    out: list[tuple[int, int, int]] = []
    current_rank = 0
    last_count = None
    for uid, cnt in sorted_voters:
        if cnt != last_count:
            current_rank += 1
            if current_rank > 3:
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

# hut_vote_cog.py — Teil 2/2 (an Teil 1 anhängen)

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
            logger.exception("Fehler beim Lesen von #%s (%s)", channel.name, channel.id)
        return matched

    async def _scan_multi(self, guild, channel_ids, start=None, end_exclusive=None):
        """Alle Channels parallel scannen."""
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

    # ============ /ai_contest ============
    @app_commands.command(name="ai_contest", description="Shows AI contest ranking for a single channel")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        channel="Channel to scan",
        topuser="Number of top posts to display",
        sort="Sort order", public="Public or ephemeral",
    )
    @app_commands.choices(topuser=TOPUSER_CHOICES, sort=SORT_CHOICES)
    async def ai_contest(
        self, interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        topuser: app_commands.Choice[str] = None,
        sort: app_commands.Choice[str] = None,
        public: bool = False,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)
        if interaction.guild is None:
            return await interaction.followup.send("Server-only.", ephemeral=True)

        target = channel or await self._safe_text_channel(interaction.guild, DEFAULT_CONTEST_CHANNEL_ID)
        if not isinstance(target, discord.TextChannel):
            return await interaction.followup.send("Invalid channel.", ephemeral=ephemeral_flag)

        msgs = await self._scan_bot_messages(target)
        per_ch = {target.id: msgs}
        image_msgs, _ = self._split_by_type(msgs)
        if not image_msgs:
            return await interaction.followup.send("No AI posts found.", ephemeral=ephemeral_flag)

        img_to_videos, video_to_img = build_position_maps(per_ch)
        voter_counts = await collect_voter_counts(msgs, interaction.guild)

        await self._render_ranking(
            interaction=interaction, msgs=image_msgs,
            title=f"🏁 AI Contest Ranking — {target.name}",
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

    # ============ /ai_video_contest ============
    @app_commands.command(name="ai_video_contest", description="Shows AI video contest ranking for a single channel")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        channel="Channel to scan",
        topuser="Number of top posts to display",
        sort="Sort order", public="Public or ephemeral",
    )
    @app_commands.choices(topuser=TOPUSER_CHOICES, sort=SORT_CHOICES)
    async def ai_video_contest(
        self, interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        topuser: app_commands.Choice[str] = None,
        sort: app_commands.Choice[str] = None,
        public: bool = False,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)
        if interaction.guild is None:
            return await interaction.followup.send("Server-only.", ephemeral=True)

        target = channel or await self._safe_text_channel(interaction.guild, DEFAULT_CONTEST_CHANNEL_ID)
        if not isinstance(target, discord.TextChannel):
            return await interaction.followup.send("Invalid channel.", ephemeral=ephemeral_flag)

        msgs = await self._scan_bot_messages(target)
        per_ch = {target.id: msgs}
        _, video_msgs = self._split_by_type(msgs)
        if not video_msgs:
            return await interaction.followup.send("No AI video posts found.", ephemeral=ephemeral_flag)

        img_to_videos, video_to_img = build_position_maps(per_ch)
        voter_counts = await collect_voter_counts(msgs, interaction.guild)

        await self._render_ranking(
            interaction=interaction, msgs=video_msgs,
            title=f"🎬 AI Video Contest — {target.name}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc",
            kind="video",
            img_to_videos=img_to_videos, video_to_img=video_to_img,
            voter_counts=voter_counts,
        )

    # =====================================================
    # RENDERING
    # =====================================================
    async def _render_ranking(
        self, interaction: discord.Interaction,
        msgs: list[discord.Message], title: str,
        ephemeral: bool, limit: int, sort_order: str,
        kind: str,
        img_to_videos: dict[int, list[discord.Message]],
        video_to_img: dict[int, discord.Message],
        voter_counts: dict[int, int],
    ):
        guild = interaction.guild
        is_public = not ephemeral

        # --- Score-Berechnung + Sort ---
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

        # --- Top 3 unique Winners ---
        top_unique: list[discord.Message] = []
        seen_uids: set[int] = set()
        for m in ranked:
            u = get_target_user(m)
            if u.id in IGNORE_IDS or u.name == "Deleted User":
                continue
            if u.id in seen_uids:
                continue
            top_unique.append(m)
            seen_uids.add(u.id)
            if len(top_unique) == 3:
                break

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

        # --- Top 3 Voters ---
        voter_ranks = compute_voter_ranks(voter_counts)
        voters_txt = ""
        for rank, uid, cnt in voter_ranks:
            name = await resolve_display_name(uid, guild, self.bot)
            xp = VOTER_XP.get(rank, 0)
            sparkle = VOTER_SPARKLE.get(rank, "")
            voters_txt += (
                f"{VOTER_MEDAL[rank]} {sparkle} **{name}** — "
                f"voted on {cnt} posts — **+{xp} XP**\n"
            )
        if not voters_txt:
            voters_txt = "—\n"

        # --- Intro-Embed ---
        now_str = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
        intro = discord.Embed(
            title=title,
            description=(
                f"**{winners_title}**\n{winners_txt}\n"
                f"**{VOTERS_TITLE}**\n{voters_txt}"
            ),
            color=discord.Color.blurple(),
        )
        intro.set_footer(text=f"Updated: {now_str} UTC")
        await self._paced_send(interaction, embed=intro, ephemeral=ephemeral)

        top_user_ids = [get_target_user(m).id for m in top_unique]

        # --- Detail-Embeds gebündelt ---
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
            detail_text = "\n".join(lines) if lines else "_Keine Reaktionen_"

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

        # --- Final Summary + Pings ---
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
                final_desc_lines.append(f"**{VOTERS_TITLE}**")
                for rank, uid, cnt in voter_ranks:
                    name = await resolve_display_name(uid, guild, self.bot)
                    xp = VOTER_XP.get(rank, 0)
                    sparkle = VOTER_SPARKLE.get(rank, "")
                    final_desc_lines.append(
                        f"{VOTER_MEDAL[rank]} {sparkle} **{name}** — "
                        f"voted on {cnt} posts — **+{xp} XP**"
                    )

            # Winner-Mentions immer (wenn public)
            winner_uids: set[int] = set()
            winner_mentions: list[str] = []
            for m in top_unique:
                u = get_target_user(m)
                winner_uids.add(u.id)
                winner_mentions.append(u.mention)

            # Voter-Mentions nur wenn public UND nicht schon Winner
            # (excluded User sind bereits durch collect_voter_counts weggefiltert)
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
                    "❌ Ein unerwarteter Fehler ist aufgetreten.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Ein unerwarteter Fehler ist aufgetreten.", ephemeral=True
                )
        except Exception:
            pass


# =====================
# SETUP
# =====================
async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))