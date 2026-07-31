# pepper.py
from __future__ import annotations

import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


# =============================================================================
# CONFIG
# =============================================================================
DEFAULT_IMAGE_URL = (
    "https://cdn.discordapp.com/attachments/"
    "1383652563408392232/1414114417800515607/idcard_small.png"
)
NOT_MEMBER_IMAGE_URL = (
    "https://cdn.discordapp.com/attachments/"
    "1383652563408392232/1415301679242280980/Sad_piper.gif"
)

# Roles that get a special badge next to their mention
SPECIAL_ROLES_TO_HIGHLIGHT: dict[int, str] = {
    1346428405368750122: "*(Mod👮‍♂️)*",
    1346414581643219029: "",
    1375143857024401478: "*(XP🏆)*",
    1346439507171475457: "",
    1378442177763479654: "*(3.🎤)*",
    1375481531426144266: "*(1.🎤)*",
    1378130233693306950: "*(2.🎤)*",
    1381454281500262520: "*(1.✍️)*",
    1381454805205258250: "*(2.✍️)*",
    1381455215215247481: "*(3.✍️)*",
    1379909107926171668: "",
    1346479048175652924: "",
    1361993080013717678: "",
    1379175952147546215: "",
    1346549280617271326: "",
    1380610400416043089: "",
}

# XP / level roles: (prefix, suffix) wrapped around the mention
LEVEL_ROLES: dict[int, tuple[str, str]] = {
    1377051179615522926: ("0️⃣3️⃣", "ₜᵢₑᵣ ₁"),
    1375147276413964408: ("1️⃣1️⃣", "ₜᵢₑᵣ ₂"),
    1376592697606930593: ("2️⃣1️⃣", "ₜᵢₑᵣ ₃"),
    1381791848875430069: ("3️⃣3️⃣", "ₜᵢₑᵣ ₄"),
    1375666588404940830: ("4️⃣2️⃣", "ₜᵢₑᵣ ₅"),
    1375584380914896978: ("6️⃣9️⃣", "ₜᵢₑᵣ ₆"),
}

LOCATION_ROLE_NAMES: set[str] = {
    "Europe", "North America", "Asia", "Oceania",
    "Africa", "South America", "Outer Gσσɳʋҽɾʂҽ",
}
GENDER_ROLE_NAMES: set[str] = {"Male", "Female", "Non-Binary"}

STONER_ROLE_ID = 1346461573392105474
DM_OPEN_ROLE_ID = 1387850018471284760

FOOTER_TEXT = "👅...and don't forget to lick the butt... of your favourite Goonette-Slut!"


# =============================================================================
# HELPERS
# =============================================================================
def _fmt_date_with_days(date: Optional[datetime.datetime]) -> str:
    if not date:
        return "Unknown"
    now = discord.utils.utcnow()
    days_ago = (now - date).days
    return f"\n{date.strftime('%Y-%m-%d %H:%M UTC')}\n(**{days_ago} days ago**)"


async def _resolve_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.HTTPException):
        return None


async def _get_riddle_stats(bot: commands.Bot, guild_id: int, user_id: int) -> Optional[dict]:
    """Fetch live riddle stats from the RiddleCog repo. Returns None on any failure."""
    cog = bot.get_cog("RiddleCog")
    if cog is None or not hasattr(cog, "repo"):
        return None
    try:
        rows = await cog.repo.stats_entries(guild_id)  # [(uid, solved, xp), ...]
    except Exception as e:
        print(f"[pepper] riddle data lookup failed: {e}")
        return None
    for uid, solved, xp in rows:
        if uid == user_id:
            return {"solved": int(solved), "xp": int(xp)}
    return None


class _RoleBuckets:
    """Sorts a member's roles into the display buckets pepper cares about."""

    def __init__(self):
        self.highlighted: list[str] = []
        self.level: list[str] = []
        self.normal: list[str] = []
        self.location: Optional[str] = None
        self.gender: Optional[str] = None
        self.stoner: Optional[str] = None
        self.dm_open: Optional[str] = None

    @classmethod
    def from_member(cls, member: discord.Member) -> "_RoleBuckets":
        b = cls()
        # highest role first
        for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
            if role.is_default():
                continue

            # 1) level roles
            if role.id in LEVEL_ROLES:
                prefix, suffix = LEVEL_ROLES[role.id]
                b.level.append(f"{prefix}\u200b{role.mention}\u200b{suffix}")
                continue

            # 2) highlighted / special roles
            badge = SPECIAL_ROLES_TO_HIGHLIGHT.get(role.id)
            if badge is not None:
                b.highlighted.append(f"▶ {role.mention} ⏭ {badge}" if badge else f"▶ {role.mention}")
                continue

            # 3) meta roles
            if role.id == STONER_ROLE_ID:
                b.stoner = " ₛₜₒₙₑᵣ Bᵤddy💨"
                continue
            if role.id == DM_OPEN_ROLE_ID:
                b.dm_open = "✅💌"
                continue

            # 4) location / gender (first match wins)
            if b.location is None and role.name in LOCATION_ROLE_NAMES:
                b.location = role.mention
                continue
            if b.gender is None and role.name in GENDER_ROLE_NAMES:
                b.gender = role.mention
                continue

            # 5) everything else
            b.normal.append(role.mention)
        return b


# =============================================================================
# EMBED BUILDER
# =============================================================================
def _build_pepper_embed(
    guild: discord.Guild,
    user: discord.abc.User,
    member: discord.Member,
    buckets: _RoleBuckets,
    riddles: Optional[dict],
    image_url: Optional[str],
) -> discord.Embed:
    embed_color = (
        member.top_role.color
        if member.top_role.color.value
        else discord.Color.dark_gold()
    )
    display_name = member.global_name or member.name

    e = discord.Embed(
        title=f"ᕼᑌT ᗰEᗰᗷEᖇ:\n{display_name} *({user.name})*",
        color=embed_color,
    )
    e.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)

    e.add_field(name="ᴀᴄᴄᴏᴜɴᴛ", value=_fmt_date_with_days(user.created_at), inline=True)
    e.add_field(name="ᴊᴏɪɴᴇᴅ", value=_fmt_date_with_days(member.joined_at), inline=True)
    e.add_field(
        name="ᴛᴏᴘ ʀᴏʟᴇ",
        value=member.top_role.mention if not member.top_role.is_default() else "No top role",
        inline=True,
    )
    e.add_field(name="🌍ʟᴏᴄᴀᴛɪᴏɴ", value=buckets.location or "No location role", inline=True)
    e.add_field(name="🚻ɢᴇɴᴅᴇʀ", value=buckets.gender or "No gender role", inline=True)

    if buckets.stoner:
        e.add_field(name="✅ɢᴀɴᴊᴀ", value=buckets.stoner, inline=True)
    if buckets.dm_open:
        e.add_field(name="📬 Open for DM", value=buckets.dm_open, inline=False)

    if buckets.level:
        e.add_field(name="🏆 𝙇𝙀𝙑𝙀𝙇𝙎", value="\n".join(buckets.level), inline=False)
    if buckets.highlighted:
        e.add_field(name="⭐ Special Roles", value="\n".join(buckets.highlighted), inline=False)

    if riddles:
        e.add_field(
            name="🧩ℜ𝔦𝔡𝔡𝔩𝔢 𝔇𝔞𝔱𝔞",
            value=f"🔓 {riddles['solved']} / 🧠 {riddles['xp']} XP",
            inline=True,
        )

    e.add_field(
        name="🎭 𝙍𝙊𝙇𝙀𝙎",
        value=", ".join(buckets.normal) if buckets.normal else "No roles",
        inline=False,
    )

    avatar_url = user.avatar.url if user.avatar else user.default_avatar.url
    e.set_thumbnail(url=avatar_url)
    e.set_image(url=image_url or DEFAULT_IMAGE_URL)
    e.set_footer(text=FOOTER_TEXT)
    return e


def _build_not_member_embed() -> discord.Embed:
    e = discord.Embed(
        title="❌ Member not found",
        description="This member is currently not a member of the **Goon Hut.**",
        color=discord.Color.red(),
    )
    e.set_image(url=NOT_MEMBER_IMAGE_URL)
    return e


# =============================================================================
# MAIN HANDLER
# =============================================================================
async def send_pepper_embed(
    interaction: discord.Interaction,
    user: discord.User,
    *,
    open: bool = False,
    mention_group: Optional[discord.Role] = None,
    text: Optional[str] = None,
    image_url: Optional[str] = None,
) -> None:
    await interaction.response.defer(ephemeral=not open)

    guild = interaction.guild
    if guild is None:
        return

    member = await _resolve_member(guild, user.id)
    if member is None:
        await interaction.followup.send(embed=_build_not_member_embed(), ephemeral=not open)
        return

    buckets = _RoleBuckets.from_member(member)
    riddles = await _get_riddle_stats(interaction.client, guild.id, user.id)  # type: ignore[arg-type]
    embed = _build_pepper_embed(guild, user, member, buckets, riddles, image_url)

    content_parts: list[str] = []
    if open and mention_group is not None:
        content_parts.append(mention_group.mention)
    if text:
        content_parts.append(text)
    content = "\n".join(content_parts) if content_parts else None

    await interaction.followup.send(
        content=content,
        embed=embed,
        ephemeral=not open,
        allowed_mentions=discord.AllowedMentions(
            roles=bool(open and mention_group is not None),
            users=False,
            everyone=False,
        ),
    )


# =============================================================================
# EXTENSION SETUP
# =============================================================================
async def setup(bot: commands.Bot):

    @bot.tree.context_menu(name="🛖 Goon Hut Info")
    async def pepper_context(interaction: discord.Interaction, user: discord.User):
        await send_pepper_embed(interaction, user)

    @bot.tree.command(
        name="pepper",
        description="Do it Pepper-Style 🫦 ... and show your ID Card..",
    )
    @app_commands.describe(
        user="Whose ID card to show",
        open="Post publicly instead of ephemeral",
        mention_group="Role to ping (only usable when open=True)",
        text="Optional text to include with the post",
        image_url="Optional custom footer image URL",
    )
    async def pepper_slash(
        interaction: discord.Interaction,
        user: discord.User,
        open: bool = False,
        mention_group: Optional[discord.Role] = None,
        text: Optional[str] = None,
        image_url: Optional[str] = None,
    ):
        if not open and mention_group is not None:
            await interaction.response.send_message(
                "⚠️ You can only use **mention-group** if **open** is set to True.",
                ephemeral=True,
            )
            return
        await send_pepper_embed(
            interaction, user,
            open=open, mention_group=mention_group,
            text=text, image_url=image_url,
        )