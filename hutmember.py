import datetime as dt
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# =========================
# Config
# =========================
DEFAULT_HUTMEMBER_IMAGE_URL = "https://example.com/default_hutmember_image.jpg"
LEVEL4_GATE_IMAGE_URL = "https://example.com/level4-gate-placeholder.jpg"  # <- Placeholder
LEVEL4_REQUIRED_ROLE_ID = 1377051179615522926
PER_PAGE = 15

MAX_AWARE_DT = dt.datetime.max.replace(tzinfo=dt.timezone.utc)


# =========================
# Access Control (reusable)
# =========================
class MissingLevel4Role(app_commands.CheckFailure):
    pass


def level4_required():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise MissingLevel4Role()

        has_role = any(r.id == LEVEL4_REQUIRED_ROLE_ID for r in interaction.user.roles)
        if not has_role:
            raise MissingLevel4Role()
        return True

    return app_commands.check(predicate)


async def send_level4_locked_message(interaction: discord.Interaction):
    role_mention = f"<@&{LEVEL4_REQUIRED_ROLE_ID}>"

    embed = discord.Embed(
        title="🔒 Command Locked",
        description=(
            f"This command unlocks at **Level 4**.\n"
            f"You need the role {role_mention} to use it.\n\n"
            f"Keep being active in chat and earn **XP** to reach Level 4 faster."
        ),
        color=discord.Color.orange()
    )
    embed.set_image(url=LEVEL4_GATE_IMAGE_URL)
    embed.set_footer(text="Stay active • Earn XP • Unlock more commands")

    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# Utility
# =========================
def sort_members(members: list[discord.Member], sort_mode: str) -> list[discord.Member]:
    if sort_mode == "alpha":
        return sorted(members, key=lambda m: m.display_name.casefold())
    return sorted(members, key=lambda m: m.joined_at or MAX_AWARE_DT)


# =========================
# Pagination View
# =========================
class HutMemberPaginationView(discord.ui.View):
    def __init__(
        self,
        *,
        owner_id: int,
        guild: discord.Guild,
        role: discord.Role,
        members: list[discord.Member],
        public_view: bool
    ):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.guild = guild
        self.role = role
        self.members = members
        self.public_view = public_view

        self.page = 0
        self.total_pages = max((len(self.members) - 1) // PER_PAGE + 1, 1)
        self.now = dt.datetime.now(dt.timezone.utc)
        self.message: Optional[discord.Message] = None

        self._sync_buttons()

    def _sync_buttons(self):
        self.back_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.total_pages - 1

    def _format_member_line(self, m: discord.Member) -> str:
        days = (self.now - m.joined_at).days if m.joined_at else "?"
        top_role = m.top_role
        top_role_display = f"**{top_role.mention}**" if top_role != self.guild.default_role else "**No Role**"
        safe_name = discord.utils.escape_markdown(m.display_name)
        name_link = f"[**{safe_name}**]({m.display_avatar.url})"
        return f"{name_link} / {top_role_display} / *({days}d)*"

    def build_embed(self) -> discord.Embed:
        start = self.page * PER_PAGE
        end = start + PER_PAGE
        chunk = self.members[start:end]

        lines = [self._format_member_line(m) for m in chunk]
        if not lines:
            lines = ["*No members on this page.*"]

        embed = discord.Embed(
            title=f"🛖 Members of: {self.role.name}\n*(name/top role/membership days)*",
            description="\n".join(lines),
            color=self.role.color if self.role.color != discord.Color.default() else discord.Color.dark_gold()
        )

        if chunk:
            embed.set_thumbnail(url=chunk[0].display_avatar.url)

        if getattr(self.role, "icon", None):
            embed.set_author(name=self.role.name, icon_url=self.role.icon.url)

        embed.set_image(url=DEFAULT_HUTMEMBER_IMAGE_URL)
        embed.set_footer(text=f"Page {self.page + 1} / {self.total_pages} • Total: {len(self.members)} member(s)")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Bei öffentlicher Ausgabe darf nur der Command-Ausführer blättern
        if self.public_view and interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Only the user who ran this command can use these buttons.",
                ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="⏪ Back", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ⏩", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


# =========================
# Core function
# =========================
async def send_paginated_hutmember(
    interaction: discord.Interaction,
    role: discord.Role,
    sort: str = "joined",
    open_mode: bool = False
):
    await interaction.response.defer(ephemeral=not open_mode)

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    members = list(role.members)  # effizienter als guild.members filtern
    if not members:
        await interaction.followup.send("❌ No members found with this role.", ephemeral=True)
        return

    members = sort_members(members, sort)

    view = HutMemberPaginationView(
        owner_id=interaction.user.id,
        guild=guild,
        role=role,
        members=members,
        public_view=open_mode
    )

    msg = await interaction.followup.send(
        embed=view.build_embed(),
        view=view,
        ephemeral=not open_mode,
        wait=True,
        allowed_mentions=discord.AllowedMentions.none()
    )
    view.message = msg


# =========================
# Cog
# =========================
class HutMemberCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="hutmember", description="Show all members with a given role")
    @level4_required()
    @app_commands.describe(
        role="The role whose members should be displayed",
        sort="Sorting order of the members",
        open="Visibility: public or only visible to you"
    )
    @app_commands.choices(
        sort=[
            app_commands.Choice(name="Joined the Hut", value="joined"),
            app_commands.Choice(name="Alphabetical", value="alpha"),
        ]
    )
    async def hutmember(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        sort: Optional[app_commands.Choice[str]] = None,
        open: bool = False
    ):
        await send_paginated_hutmember(
            interaction=interaction,
            role=role,
            sort=sort.value if sort else "joined",
            open_mode=open
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingLevel4Role):
            await send_level4_locked_message(interaction)
            return
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(HutMemberCog(bot))