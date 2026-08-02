# hut_vote_cog.py
import asyncio
import logging
import calendar
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

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

current_year = datetime.now(timezone.utc).year
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

# Rate-Limit-Pacing für Followups (Discord Webhook ~5/5s)
FOLLOWUP_DELAY_SEC = 0.9


# =========================================================
# POST DETECTOR
# ---------------------------------------------------------
# WICHTIG: Wenn sich das Aussehen der Video-Posts ändert
# (Content-String, Attachment-Format, Embed-Struktur),
# NUR DIESE KLASSE anpassen.
# =========================================================
class VideoPostDetector:
    """Erkennung & Metadaten-Extraktion für Video-Posts.

    Aktuelles Format (v1):
      - Content:   "<icon> 🎬 **Video** • @user • ▶ **CLICK TO PLAY**"
      - Attachment: AI_video.mp4 (content_type video/*)
      - Embed hat ein Feld "Prompt" mit dem Original-Prompt in ```code```
      - mentions[0] ist der User, für den das Video generiert wurde
    """

    VIDEO_EXTENSIONS   = (".mp4", ".webm", ".mov", ".mkv", ".m4v")
    CONTENT_MARKERS    = ("🎬",)
    CONTENT_HINTS      = ("click to play", "video")
    PROMPT_FIELD_NAMES = ("prompt",)   # case-insensitive

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
        content_lower = content.lower()
        has_marker = any(m in content for m in cls.CONTENT_MARKERS)
        has_hint   = any(h in content_lower for h in cls.CONTENT_HINTS)
        return has_marker and has_hint

    @classmethod
    def get_video_url(cls, msg: discord.Message) -> Optional[str]:
        for att in msg.attachments:
            is_video = (
                (att.content_type and att.content_type.startswith("video/"))
                or (att.filename or "").lower().endswith(cls.VIDEO_EXTENSIONS)
            )
            if is_video:
                return att.url
        return None

    @classmethod
    def extract_prompt(cls, msg: discord.Message) -> Optional[str]:
        for embed in msg.embeds:
            for field in embed.fields:
                name = (field.name or "").strip().lower()
                if name in cls.PROMPT_FIELD_NAMES:
                    text = (field.value or "").strip()
                    # ```lang\ntext\n``` -> text
                    text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
                    text = re.sub(r"\s*```$", "", text)
                    return text.strip() or None
        return None

    @classmethod
    def extract_target_user_id(cls, msg: discord.Message) -> Optional[int]:
        if msg.mentions:
            return msg.mentions[0].id
        return msg.author.id if msg.author else None


def _prompt_signatures(text: str) -> list[str]:
    """Progressiv lockere Signaturen für Bild↔Video-Matching.

    Reihenfolge: strengste zuerst. Bei mehreren Levels wird das ERSTE Match
    verwendet — je enger, desto zuverlässiger.
    """
    if not text:
        return []

    # Ebene 1: Whitespace normalisiert, lowercase, erste 200 Zeichen
    normalized = " ".join(text.split()).lower()

    # Ebene 2: nur Alphanumerisch (ignoriert Markdown-Reste, Sonderzeichen)
    alphanum = re.sub(r"[^a-z0-9\s]", "", normalized)
    alphanum = " ".join(alphanum.split())

    variants = [
        normalized[:200],
        normalized[:80],
        alphanum[:150],
        alphanum[:60],
    ]

    # Dedup mit Reihenfolgen-Erhalt, leere Strings raus
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen and len(v) >= 8:
            seen.add(v)
            out.append(v)
    return out


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
            extra_votes = max(votes - 1, 0)   # Bot-Vorreaktion abziehen
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


def get_month_utc_range(year: int, month: int):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end_exclusive = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_exclusive = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end_exclusive


def get_target_user(msg: discord.Message):
    return msg.mentions[0] if msg.mentions else msg.author


def build_image_to_video_map(
    image_msgs: list[discord.Message],
    video_msgs: list[discord.Message],
) -> dict[int, discord.Message]:
    """Verknüpft Bild-Posts mit ihrem Video-Post via (user_id, prompt_signature).

    Multi-Level: Level 0 = striktes 200-Zeichen-Match, Level N = alphanumerisch
    gekürzt. Erstes Level gewinnt. Videos müssen NACH dem Bild kommen.
    """
    MAX_LEVELS = 4
    indexes: list[dict[tuple[int, str], discord.Message]] = [
        {} for _ in range(MAX_LEVELS)
    ]

    for v in video_msgs:
        uid = VideoPostDetector.extract_target_user_id(v)
        prm = VideoPostDetector.extract_prompt(v)
        if uid is None or not prm:
            continue
        for level, sig in enumerate(_prompt_signatures(prm)):
            if level >= MAX_LEVELS:
                break
            key = (uid, sig)
            prev = indexes[level].get(key)
            if prev is None or v.created_at < prev.created_at:
                indexes[level][key] = v

    mapping: dict[int, discord.Message] = {}
    unmatched = 0
    for img in image_msgs:
        uid = VideoPostDetector.extract_target_user_id(img)
        prm = VideoPostDetector.extract_prompt(img)
        if uid is None or not prm:
            continue
        found: Optional[discord.Message] = None
        for level, sig in enumerate(_prompt_signatures(prm)):
            if level >= MAX_LEVELS:
                break
            v = indexes[level].get((uid, sig))
            if v and v.created_at >= img.created_at:
                found = v
                break
        if found:
            mapping[img.id] = found
        else:
            unmatched += 1

    if unmatched:
        logger.info(
            "Image↔Video-Match: %d/%d Bilder ohne passendes Video (das ist normal, "
            "wenn zu diesen Bildern nie animiert wurde).",
            unmatched, len(image_msgs),
        )
    return mapping


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

    async def _scan_bot_messages(
        self,
        channel: discord.TextChannel,
        start: Optional[datetime] = None,
        end_exclusive: Optional[datetime] = None,
    ) -> list[discord.Message]:
        """Alle Bot-Nachrichten im Zeitraum. Kein Typ-Filter."""
        matched: list[discord.Message] = []
        after_dt = (start - timedelta(seconds=1)) if start else None

        try:
            async for msg in channel.history(
                after=after_dt, before=end_exclusive, limit=None
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

    @staticmethod
    def _split_by_type(
        msgs: list[discord.Message],
    ) -> tuple[list[discord.Message], list[discord.Message]]:
        images, videos = [], []
        for m in msgs:
            (videos if VideoPostDetector.is_video_post(m) else images).append(m)
        return images, videos

    async def _paced_send(self, interaction: discord.Interaction, **kwargs):
        """Followup mit kleiner Pause, um Discord Rate-Limit zu entlasten."""
        try:
            await interaction.followup.send(**kwargs)
        except discord.HTTPException:
            logger.exception("Followup send failed")
        await asyncio.sleep(FOLLOWUP_DELAY_SEC)

    # =====================================================
    # /ai_vote — Monatliches Bild-Ranking über alle Channels
    # =====================================================
    @app_commands.command(name="ai_vote", description="Shows AI image ranking by reactions")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        year="Select year",
        month="Select month",
        topuser="Number of top posts to display",
        sort="Sort order",
        public="Whether the result is public or ephemeral",
    )
    @app_commands.choices(
        year=YEAR_CHOICES, month=MONTH_CHOICES,
        topuser=TOPUSER_CHOICES, sort=SORT_CHOICES,
    )
    async def ai_vote(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        topuser: app_commands.Choice[str] = None,
        sort: app_commands.Choice[str] = None,
        public: bool = False,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        if interaction.guild is None:
            return await interaction.followup.send(
                "Dieser Befehl geht nur im Server.", ephemeral=True
            )

        year_v = int(year.value)
        month_v = int(month.value)
        start_dt, end_exclusive = get_month_utc_range(year_v, month_v)

        all_msgs: list[discord.Message] = []
        for cid in SCAN_CHANNEL_IDS:
            channel = await self._safe_text_channel(interaction.guild, cid)
            if channel is None:
                continue
            all_msgs.extend(
                await self._scan_bot_messages(channel, start_dt, end_exclusive)
            )

        image_msgs, video_msgs = self._split_by_type(all_msgs)
        if not image_msgs:
            return await interaction.followup.send(
                "No AI posts found.", ephemeral=ephemeral_flag
            )

        img_to_video = build_image_to_video_map(image_msgs, video_msgs)
        logger.info(
            "/ai_vote %s %s: %d Bilder, %d Videos, %d Verknüpfungen.",
            year_v, month_v, len(image_msgs), len(video_msgs), len(img_to_video),
        )

        await self._render_ranking(
            interaction=interaction,
            msgs=image_msgs,
            title=f"🤖 AI Top — {calendar.month_name[month_v]} {year_v}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc",
            kind="image",
            image_to_video_map=img_to_video,
        )

    # =====================================================
    # /ai_contest — Single-Channel Bild-Ranking
    # =====================================================
    @app_commands.command(
        name="ai_contest",
        description="Shows AI contest ranking for a single channel",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        channel="Channel to scan",
        topuser="Number of top posts to display",
        sort="Sort order",
        public="Whether the result is public or ephemeral",
    )
    @app_commands.choices(topuser=TOPUSER_CHOICES, sort=SORT_CHOICES)
    async def ai_contest(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        topuser: app_commands.Choice[str] = None,
        sort: app_commands.Choice[str] = None,
        public: bool = False,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        if interaction.guild is None:
            return await interaction.followup.send(
                "Dieser Befehl geht nur im Server.", ephemeral=True
            )

        target_channel = channel or await self._safe_text_channel(
            interaction.guild, DEFAULT_CONTEST_CHANNEL_ID
        )
        if not isinstance(target_channel, discord.TextChannel):
            return await interaction.followup.send(
                "Invalid channel.", ephemeral=ephemeral_flag
            )

        all_msgs = await self._scan_bot_messages(target_channel)
        image_msgs, video_msgs = self._split_by_type(all_msgs)

        if not image_msgs:
            return await interaction.followup.send(
                "No AI posts found.", ephemeral=ephemeral_flag
            )

        img_to_video = build_image_to_video_map(image_msgs, video_msgs)

        await self._render_ranking(
            interaction=interaction,
            msgs=image_msgs,
            title=f"🏁 AI Contest Ranking — {target_channel.name}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc",
            kind="image",
            image_to_video_map=img_to_video,
        )

    # =====================================================
    # /ai_video — Monatliches Video-Ranking
    # =====================================================
    @app_commands.command(name="ai_video", description="Shows AI video ranking by reactions")
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        year="Select year",
        month="Select month",
        topuser="Number of top posts to display",
        sort="Sort order",
        public="Whether the result is public or ephemeral",
    )
    @app_commands.choices(
        year=YEAR_CHOICES, month=MONTH_CHOICES,
        topuser=TOPUSER_CHOICES, sort=SORT_CHOICES,
    )
    async def ai_video(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        topuser: app_commands.Choice[str] = None,
        sort: app_commands.Choice[str] = None,
        public: bool = False,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        if interaction.guild is None:
            return await interaction.followup.send(
                "Dieser Befehl geht nur im Server.", ephemeral=True
            )

        year_v = int(year.value)
        month_v = int(month.value)
        start_dt, end_exclusive = get_month_utc_range(year_v, month_v)

        all_msgs: list[discord.Message] = []
        for cid in SCAN_CHANNEL_IDS:
            channel = await self._safe_text_channel(interaction.guild, cid)
            if channel is None:
                continue
            all_msgs.extend(
                await self._scan_bot_messages(channel, start_dt, end_exclusive)
            )

        _, video_msgs = self._split_by_type(all_msgs)
        if not video_msgs:
            return await interaction.followup.send(
                "No AI video posts found.", ephemeral=ephemeral_flag
            )

        await self._render_ranking(
            interaction=interaction,
            msgs=video_msgs,
            title=f"🎬 AI Video Top — {calendar.month_name[month_v]} {year_v}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc",
            kind="video",
        )

    # =====================================================
    # /ai_video_contest — Single-Channel Video-Ranking
    # =====================================================
    @app_commands.command(
        name="ai_video_contest",
        description="Shows AI video contest ranking for a single channel",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_any_role(*ALLOWED_ROLE_IDS)
    @app_commands.describe(
        channel="Channel to scan",
        topuser="Number of top posts to display",
        sort="Sort order",
        public="Whether the result is public or ephemeral",
    )
    @app_commands.choices(topuser=TOPUSER_CHOICES, sort=SORT_CHOICES)
    async def ai_video_contest(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        topuser: app_commands.Choice[str] = None,
        sort: app_commands.Choice[str] = None,
        public: bool = False,
    ):
        ephemeral_flag = not public
        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        if interaction.guild is None:
            return await interaction.followup.send(
                "Dieser Befehl geht nur im Server.", ephemeral=True
            )

        target_channel = channel or await self._safe_text_channel(
            interaction.guild, DEFAULT_CONTEST_CHANNEL_ID
        )
        if not isinstance(target_channel, discord.TextChannel):
            return await interaction.followup.send(
                "Invalid channel.", ephemeral=ephemeral_flag
            )

        all_msgs = await self._scan_bot_messages(target_channel)
        _, video_msgs = self._split_by_type(all_msgs)

        if not video_msgs:
            return await interaction.followup.send(
                "No AI video posts found.", ephemeral=ephemeral_flag
            )

        await self._render_ranking(
            interaction=interaction,
            msgs=video_msgs,
            title=f"🎬 AI Video Contest — {target_channel.name}",
            ephemeral=ephemeral_flag,
            limit=int(topuser.value) if topuser else 5,
            sort_order=sort.value if sort else "asc",
            kind="video",
        )

    # =====================================================
    # RENDERING
    # =====================================================
    async def _render_ranking(
        self,
        interaction: discord.Interaction,
        msgs: list[discord.Message],
        title: str,
        ephemeral: bool,
        limit: int,
        sort_order: str,
        kind: str = "image",                                       # "image" | "video"
        image_to_video_map: Optional[dict[int, discord.Message]] = None,
    ):
        guild = interaction.guild
        medals = ["🥇", "🥈", "🥉"]
        image_to_video_map = image_to_video_map or {}

        stats = {m.id: calc_ai_points(m) for m in msgs}

        def sort_key(m: discord.Message):
            score, _, emoji_total = stats[m.id]
            return score, emoji_total, m.created_at

        ranked_msgs = sorted(msgs, key=sort_key, reverse=True)

        # Ties auf (score, emoji_total) — konsistent mit rank_map
        tie_counts: dict[tuple[int, int], int] = {}
        for m in ranked_msgs:
            s, _, e = stats[m.id]
            tie_counts[(s, e)] = tie_counts.get((s, e), 0) + 1
        tied_keys = {k for k, c in tie_counts.items() if c > 1}

        top_msgs = ranked_msgs[:limit]
        # asc = 1 -> X, desc = X -> 1
        display_msgs = top_msgs if sort_order == "asc" else list(reversed(top_msgs))

        # Rang-Map mit Ties
        rank_map: dict[int, int] = {}
        last_key: Optional[tuple[int, int]] = None
        current_rank = 0
        for idx, m in enumerate(ranked_msgs, start=1):
            s, _, e = stats[m.id]
            key = (s, e)
            if key == last_key:
                rank_map[m.id] = current_rank
            else:
                current_rank = idx
                rank_map[m.id] = current_rank
                last_key = key

        # Top 3 unique User
        top_unique: list[discord.Message] = []
        seen: set[int] = set()
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

        now_str = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
        podium_label = "Top 3 Hut Dwellers" if kind == "image" else "Top 3 Video Makers"

        await self._paced_send(
            interaction,
            embed=discord.Embed(
                title=title,
                description=f"**{podium_label}:**\n{intro or '—'}",
                color=discord.Color.blurple(),
            ).set_footer(text=f"Updated: {now_str} UTC"),
            ephemeral=ephemeral,
        )

        top_user_ids = [get_target_user(m).id for m in top_unique]

        # Detail-Embeds
        for m in display_msgs:
            u = get_target_user(m)
            score, breakdown, emoji_total = stats[m.id]
            rank_number = rank_map[m.id]

            medal = medals[top_user_ids.index(u.id)] if u.id in top_user_ids else ""
            tie_suffix = f" ({emoji_total} 📊)" if (score, emoji_total) in tied_keys else ""

            lines = []
            for k, d in breakdown.items():
                emoji_label = "📝" if k == "Various" else str(guild.get_emoji(k) or k)
                lines.append(f"{emoji_label} × {d['votes']} → {d['points']} pts")
            detail_text = "\n".join(lines) if lines else "_Keine Reaktionen_"

            # Jump-Links: bei Bild-Ranking optional zusätzlicher VIDEO-Link
            links = f"[Jump to Post 🎖️(**VOTE**🎖️)]({m.jump_url})"
            if kind == "image" and m.id in image_to_video_map:
                video_msg = image_to_video_map[m.id]
                links += f" • [🎬 **VIDEO**]({video_msg.jump_url})"

            embed = discord.Embed(
                title=f"#{rank_number} — {u.display_name} {medal} — {score} pts{tie_suffix}",
                description=f"{links}\n\n{detail_text}",
                color=discord.Color.gold() if medal else discord.Color.teal(),
            )
            embed.set_thumbnail(url=u.display_avatar.url)
            embed.set_footer(text=f"Posted: {m.created_at.strftime('%Y/%m/%d %H:%M')} UTC")

            send_kwargs: dict = {"embed": embed, "ephemeral": ephemeral}

            if kind == "image":
                # Bild-Preview via Embed
                image_url = None
                if m.attachments:
                    att = m.attachments[0]
                    is_video_att = (
                        (att.content_type and att.content_type.startswith("video/"))
                        or (att.filename or "").lower().endswith(
                            VideoPostDetector.VIDEO_EXTENSIONS
                        )
                    )
                    if not is_video_att:
                        image_url = att.url
                if image_url is None:
                    for e in m.embeds:
                        if e.image and e.image.url:
                            image_url = e.image.url
                            break
                if image_url:
                    embed.set_image(url=image_url)
            else:
                # Video-Preview: Discord kann Videos NICHT via embed.set_image
                # anzeigen. Der einzige Weg zum inline-Player ist die
                # Attachment-URL als message content — Discord unfurled dann
                # selbst einen Video-Player unter dem Embed.
                video_url = VideoPostDetector.get_video_url(m)
                if video_url:
                    send_kwargs["content"] = video_url

            await self._paced_send(interaction, **send_kwargs)

        # Final Top 3
        if top_unique:
            final_mentions = []
            final_lines = []
            for i, m in enumerate(top_unique):
                u = get_target_user(m)
                score, _, emoji_total = stats[m.id]
                tie_suffix = f" ({emoji_total} 📊)" if (score, emoji_total) in tied_keys else ""
                final_mentions.append(u.mention)
                final_lines.append(f"{medals[i]} {u.display_name} — {score} pts{tie_suffix}")

            final_time = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
            await self._paced_send(
                interaction,
                content=" ".join(final_mentions),
                embed=discord.Embed(
                    title=f"🏆 Final Top 3 (as of {final_time} UTC)",
                    description="\n".join(final_lines),
                    color=discord.Color.gold(),
                ),
                ephemeral=ephemeral,
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