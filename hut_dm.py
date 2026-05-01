import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
from typing import Optional
import random
import logging

logger = logging.getLogger("hut_dm")

DM_OPEN_ROLE_ID = 1387850018471284760          # Rolle "DM open"
HUT_DM_ACCESS_ROLE_ID = 1377051179615522926    # Lvl4 / Zugriff
ACCESS_LEVEL_LABEL = "Lvl4"

PAGE_SIZE = 10

# Footer
FOOTER_ICON_URL = "https://cdn-icons-png.flaticon.com/512/25/25231.png"
FOOTER_TEXT = "Hut DM List"

# Bildpool
DEFAULT_IMAGE_POOL = [
    "https://cdn.discordapp.com/attachments/1383652563408392232/1396193102280265778/dm_open2.jpg",
    "https://cdn.discordapp.com/attachments/1383652563408392232/1396193294429716480/dm_open3.jpg",
    "https://cdn.discordapp.com/attachments/1383652563408392232/1396193609858023444/alcohol.jpg",
    "https://cdn.discordapp.com/attachments/1383652563408392232/1396193784747786363/lick_it2.jpg",
    "https://cdn.discordapp.com/attachments/1383652563408392232/1396193419277369424/dm_open4.jpg"
]


def get_random_image() -> str:
    return random.choice(DEFAULT_IMAGE_POOL)


# =========================
# ACCESS CHECK
# =========================
class MissingHutDMAccess(app_commands.CheckFailure):
    pass


def has_hut_dm_access(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False

    # Admin immer erlaubt
    if interaction.user.guild_permissions.administrator:
        return True

    return any(r.id == HUT_DM_ACCESS_ROLE_ID for r in interaction.user.roles)


def hut_dm_access_required():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not has_hut_dm_access(interaction):
            raise MissingHutDMAccess()
        return True
    return app_commands.check(predicate)


async def send_hut_dm_access_denied(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔒 Zugriff gesperrt",
        description=(
            f"Dieser Befehl ist nur für **{ACCESS_LEVEL_LABEL}** "
            f"mit Rolle <@&{HUT_DM_ACCESS_ROLE_ID}>."
        ),
        color=discord.Color.orange()
    )
    embed.set_footer(text="Zugriff nur mit passender Rolle")

    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# MODAL
# =========================
class DMModal(Modal, title="Send a DM"):
    def __init__(self, target_user: discord.abc.User):
        super().__init__()
        self.target_user = target_user
        self.message = TextInput(
            label="Your message",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1800
        )
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_hut_dm_access(interaction):
            await send_hut_dm_access_denied(interaction)
            return

        content = (self.message.value or "").strip()
        if not content:
            await interaction.response.send_message("❌ Nachricht ist leer.", ephemeral=True)
            return

        try:
            await self.target_user.send(
                f"📩 You received a message from {interaction.user.mention}:\n\n{content}"
            )
            await interaction.response.send_message("✅ DM sent successfully!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Cannot send DM — user might have DMs disabled.", ephemeral=True
            )
        except discord.HTTPException as e:
            logger.warning("DM send failed: %s", e)
            await interaction.response.send_message(
                "❌ DM konnte nicht gesendet werden.", ephemeral=True
            )


# =========================
# BUTTONS / VIEW
# =========================
class MemberButton(Button):
    def __init__(self, user: discord.Member):
        label = f"📩 {user.display_name}"
        if len(label) > 80:
            label = label[:77] + "..."
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"hut_dm_user_{user.id}"
        )
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DMModal(self.user))


class NavButton(Button):
    def __init__(self, label: str, target_page: int, row: Optional[int] = None):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=row
        )
        self.target_page = target_page

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, PaginationView):
            return

        view.set_page(self.target_page)
        await interaction.response.edit_message(embed=view.create_embed(), view=view)


class PaginationView(View):
    def __init__(self, members: list[discord.Member], page: int = 0, owner_id: Optional[int] = None):
        super().__init__(timeout=300)
        self.members = members
        self.page = page
        self.total_pages = max((len(members) - 1) // PAGE_SIZE + 1, 1)
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None
        self.image_url = get_random_image()
        self.update_buttons()

    def set_page(self, page: int):
        self.page = max(0, min(page, self.total_pages - 1))
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()

        start = self.page * PAGE_SIZE
        end = start + PAGE_SIZE
        for member in self.members[start:end]:
            self.add_item(MemberButton(member))

        nav_row = 4
        if self.total_pages > 1:
            if self.page > 0:
                self.add_item(NavButton("⬅️ Previous", self.page - 1, row=nav_row))
            if self.page < self.total_pages - 1:
                self.add_item(NavButton("Next ➡️", self.page + 1, row=nav_row))

    def create_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="📫 DM Open Members",
            color=discord.Color.green()
        )
        embed.set_footer(
            text=f"{FOOTER_TEXT} — Page {self.page + 1}/{self.total_pages}",
            icon_url=FOOTER_ICON_URL
        )
        embed.set_image(url=self.image_url)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not has_hut_dm_access(interaction):
            await send_hut_dm_access_denied(interaction)
            return False

        # Bei ephemeren Menüs nur Owner erlauben
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message("🚫 Dieses Menü gehört nicht dir.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


# =========================
# COG
# =========================
class HutDM(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.hut_dm_group = app_commands.Group(
            name="hut_dm",
            description="🛖📬 Hut DM commands"
        )
        self.hut_dm_group.command(
            name="list",
            description="Show members with open DMs"
        )(self.hut_dm_list)

    async def cog_load(self):
        try:
            self.bot.tree.add_command(self.hut_dm_group)
        except app_commands.CommandAlreadyRegistered:
            logger.info("hut_dm group already registered, skipping.")

    def cog_unload(self):
        try:
            self.bot.tree.remove_command(
                self.hut_dm_group.name,
                type=discord.AppCommandType.chat_input
            )
        except Exception:
            pass

    @app_commands.describe(
        visible="Public im Channel oder nur für dich (default: False)",
        mention="Optionale Rolle zum Pingen (nur sichtbar bei visible=True)"
    )
    @hut_dm_access_required()
    async def hut_dm_list(
        self,
        interaction: discord.Interaction,
        visible: Optional[bool] = False,
        mention: Optional[discord.Role] = None
    ):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ Dieser Befehl funktioniert nur in einem Server.",
                ephemeral=True
            )
            return

        role = guild.get_role(DM_OPEN_ROLE_ID)
        if not role:
            await interaction.response.send_message(
                "❌ DM-open Rolle wurde auf diesem Server nicht gefunden.",
                ephemeral=True
            )
            return

        members = [m for m in role.members if not m.bot]
        if not members:
            await interaction.response.send_message(
                "Keine DM-open Mitglieder gefunden.",
                ephemeral=True
            )
            return

        members.sort(key=lambda m: m.display_name.casefold())

        view = PaginationView(
            members=members,
            page=0,
            owner_id=(interaction.user.id if not visible else None)
        )
        embed = view.create_embed()

        content = mention.mention if (visible and mention) else None

        await interaction.response.send_message(
            content=content,
            embed=embed,
            view=view,
            ephemeral=not visible,
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
        )
        view.message = await interaction.original_response()

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingHutDMAccess):
            await send_hut_dm_access_denied(interaction)
            return

        logger.exception("hut_dm command error: %s", error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Command error.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Command error.", ephemeral=True)
        except Exception:
            pass


# =========================
# SETUP
# =========================
async def setup(bot: commands.Bot):
    await bot.add_cog(HutDM(bot))