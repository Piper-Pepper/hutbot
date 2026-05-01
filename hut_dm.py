import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Optional, Callable, Awaitable

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput

logger = logging.getLogger("hut_dm")

DM_OPEN_ROLE_ID = 1387850018471284760          # Role "DM open"
HUT_DM_ACCESS_ROLE_ID = 1377051179615522926    # Lvl4 / access
ACCESS_LEVEL_LABEL = "Lvl4"

PAGE_SIZE = 10
EPHEMERAL_TIMEOUT_SECONDS = 900  # 15 minutes
PERSISTENT_STATE_FILE = Path("hut_dm_public_views.json")

# Footer
FOOTER_ICON_URL = "https://cdn-icons-png.flaticon.com/512/25/25231.png"
FOOTER_TEXT = "Hut DM List"

# Image pool
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

    # Admin is always allowed
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
        title="🔒 Access denied",
        description=(
            f"This command is only for **{ACCESS_LEVEL_LABEL}** "
            f"with role <@&{HUT_DM_ACCESS_ROLE_ID}>."
        ),
        color=discord.Color.orange()
    )
    embed.set_footer(text="Access requires the correct role")

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
            await interaction.response.send_message("❌ Message is empty.", ephemeral=True)
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
            await interaction.response.send_message("❌ DM could not be sent.", ephemeral=True)


# =========================
# BUTTONS / VIEW
# =========================
class MemberButton(Button):
    def __init__(self, user: discord.Member, guild_id: int):
        label = f"📩 {user.display_name}"
        if len(label) > 80:
            label = label[:77] + "..."

        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"hutdm:{guild_id}:user:{user.id}"
        )
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DMModal(self.user))


class NavButton(Button):
    def __init__(self, label: str, target_page: int, custom_id: str, row: Optional[int] = None):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=custom_id,
            row=row
        )
        self.target_page = target_page

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, PaginationView):
            return

        # Random image on every page switch
        view.set_page(self.target_page, randomize_image=True)
        await interaction.response.edit_message(embed=view.create_embed(), view=view)
        await view.persist_state()


class PaginationView(View):
    def __init__(
        self,
        *,
        bot: commands.Bot,
        guild_id: int,
        role_id: int,
        page: int = 0,
        owner_id: Optional[int] = None,
        image_url: Optional[str] = None,
        persistent: bool = False,
        state_callback: Optional[Callable[["PaginationView"], Awaitable[None]]] = None
    ):
        super().__init__(timeout=None if persistent else EPHEMERAL_TIMEOUT_SECONDS)
        self.bot = bot
        self.guild_id = guild_id
        self.role_id = role_id
        self.page = page
        self.owner_id = owner_id
        self.persistent = persistent
        self.state_callback = state_callback

        self.total_pages = 1
        self.image_url = image_url or get_random_image()

        self.message: Optional[discord.Message] = None
        self.message_id: Optional[int] = None
        self.channel_id: Optional[int] = None

        self.update_buttons()

    def _get_sorted_members(self) -> list[discord.Member]:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            return []

        role = guild.get_role(self.role_id)
        if role is None:
            return []

        members = [m for m in role.members if not m.bot]
        members.sort(key=lambda m: m.display_name.casefold())
        return members

    def set_page(self, page: int, *, randomize_image: bool = False):
        members = self._get_sorted_members()
        self.total_pages = max((len(members) - 1) // PAGE_SIZE + 1, 1)
        self.page = max(0, min(page, self.total_pages - 1))

        if randomize_image:
            self.image_url = get_random_image()

        self.update_buttons()

    def update_buttons(self):
        self.clear_items()

        members = self._get_sorted_members()
        self.total_pages = max((len(members) - 1) // PAGE_SIZE + 1, 1)
        self.page = max(0, min(self.page, self.total_pages - 1))

        start = self.page * PAGE_SIZE
        end = start + PAGE_SIZE
        page_members = members[start:end]

        if not page_members:
            self.add_item(
                Button(
                    label="No DM-open members",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                    custom_id=f"hutdm:{self.guild_id}:empty"
                )
            )
        else:
            for member in page_members:
                self.add_item(MemberButton(member, self.guild_id))

        nav_row = 4
        if self.total_pages > 1:
            if self.page > 0:
                self.add_item(
                    NavButton(
                        "⬅️ Previous",
                        self.page - 1,
                        custom_id=f"hutdm:{self.guild_id}:nav:prev",
                        row=nav_row
                    )
                )
            if self.page < self.total_pages - 1:
                self.add_item(
                    NavButton(
                        "Next ➡️",
                        self.page + 1,
                        custom_id=f"hutdm:{self.guild_id}:nav:next",
                        row=nav_row
                    )
                )

    def create_embed(self) -> discord.Embed:
        members = self._get_sorted_members()
        total = len(members)

        embed = discord.Embed(
            title="📫 DM Open Members",
            color=discord.Color.green()
        )

        if total == 0:
            embed.description = f"No users currently have <@&{self.role_id}>."
        else:
            start = self.page * PAGE_SIZE + 1
            end = min((self.page + 1) * PAGE_SIZE, total)
            embed.description = f"Showing **{start}-{end}** of **{total}** users with <@&{self.role_id}>."

        embed.set_footer(
            text=f"{FOOTER_TEXT} — Page {self.page + 1}/{self.total_pages}",
            icon_url=FOOTER_ICON_URL
        )
        embed.set_image(url=self.image_url)
        return embed

    async def persist_state(self):
        if self.persistent and self.state_callback is not None:
            await self.state_callback(self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not has_hut_dm_access(interaction):
            await send_hut_dm_access_denied(interaction)
            return False

        # For ephemeral menus, only allow the owner to interact
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message("🚫 This menu is not yours.", ephemeral=True)
            return False

        return True

    async def on_timeout(self):
        # Persistent views have no timeout
        if self.persistent:
            return

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
        self.state_file = PERSISTENT_STATE_FILE
        self._state_lock = asyncio.Lock()
        self._restore_task: Optional[asyncio.Task] = None

        # message_id(str) -> record
        self.persistent_records: dict[str, dict] = {}

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

        self._restore_task = asyncio.create_task(self._restore_persistent_views())

    def cog_unload(self):
        try:
            self.bot.tree.remove_command(
                self.hut_dm_group.name,
                type=discord.AppCommandType.chat_input
            )
        except Exception:
            pass

        if self._restore_task and not self._restore_task.done():
            self._restore_task.cancel()

    # ---------- Persistence helpers ----------
    def _load_state_file_sync(self) -> dict[str, dict]:
        if not self.state_file.exists():
            return {}

        try:
            raw = self.state_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning("Failed to load state file: %s", e)
        return {}

    async def _save_state_file(self):
        async with self._state_lock:
            try:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                self.state_file.write_text(
                    json.dumps(self.persistent_records, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
            except Exception as e:
                logger.warning("Failed to save state file: %s", e)

    async def _message_still_exists(self, channel_id: int, message_id: int) -> bool:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.NotFound:
                return False
            except discord.Forbidden:
                # Cannot verify; keep record
                return True
            except discord.HTTPException:
                # Temporary API issue; keep record
                return True

        if not hasattr(channel, "fetch_message"):
            return False

        try:
            await channel.fetch_message(message_id)
            return True
        except discord.NotFound:
            return False
        except discord.Forbidden:
            return True
        except discord.HTTPException:
            return True

    async def _on_view_state_change(self, view: PaginationView):
        if not view.persistent or view.message_id is None or view.channel_id is None:
            return

        self.persistent_records[str(view.message_id)] = {
            "guild_id": view.guild_id,
            "channel_id": view.channel_id,
            "role_id": view.role_id,
            "page": view.page,
            "image_url": view.image_url
        }
        await self._save_state_file()

    async def _restore_persistent_views(self):
        await self.bot.wait_until_ready()

        loaded = self._load_state_file_sync()
        cleaned: dict[str, dict] = {}

        for message_id_str, record in loaded.items():
            try:
                message_id = int(message_id_str)
                guild_id = int(record["guild_id"])
                channel_id = int(record["channel_id"])
                role_id = int(record.get("role_id", DM_OPEN_ROLE_ID))
                page = int(record.get("page", 0))
                image_url = str(record.get("image_url") or get_random_image())
            except Exception:
                continue

            exists = await self._message_still_exists(channel_id, message_id)
            if not exists:
                continue

            view = PaginationView(
                bot=self.bot,
                guild_id=guild_id,
                role_id=role_id,
                page=page,
                owner_id=None,          # public menus only
                image_url=image_url,
                persistent=True,
                state_callback=self._on_view_state_change
            )
            view.message_id = message_id
            view.channel_id = channel_id

            self.bot.add_view(view, message_id=message_id)
            cleaned[message_id_str] = {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "role_id": role_id,
                "page": view.page,
                "image_url": view.image_url
            }

        self.persistent_records = cleaned
        await self._save_state_file()

    # ---------- Cleanup deleted messages ----------
    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        key = str(payload.message_id)
        if key in self.persistent_records:
            self.persistent_records.pop(key, None)
            await self._save_state_file()

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        changed = False
        for message_id in payload.message_ids:
            key = str(message_id)
            if key in self.persistent_records:
                self.persistent_records.pop(key, None)
                changed = True
        if changed:
            await self._save_state_file()

    # ---------- Command ----------
    @app_commands.describe(
        visible="Public in channel or only for you (default: False)",
        mention="Optional role to ping (only used when visible=True)"
    )
    @hut_dm_access_required()
    async def hut_dm_list(
        self,
        interaction: discord.Interaction,
        visible: Optional[bool] = False,
        mention: Optional[discord.Role] = None
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ This command only works in a server.",
                ephemeral=True
            )
            return

        role = guild.get_role(DM_OPEN_ROLE_ID)
        if role is None:
            await interaction.response.send_message(
                "❌ DM-open role was not found on this server.",
                ephemeral=True
            )
            return

        members = [m for m in role.members if not m.bot]
        if not members:
            await interaction.response.send_message(
                "No DM-open members found.",
                ephemeral=True
            )
            return

        # Public menus: persistent (survive restarts)
        # Ephemeral menus: owner-locked with timeout
        is_public = bool(visible)

        view = PaginationView(
            bot=self.bot,
            guild_id=guild.id,
            role_id=DM_OPEN_ROLE_ID,
            page=0,
            owner_id=(None if is_public else interaction.user.id),
            image_url=get_random_image(),
            persistent=is_public,
            state_callback=self._on_view_state_change if is_public else None
        )
        embed = view.create_embed()

        content = mention.mention if (is_public and mention) else None

        await interaction.response.send_message(
            content=content,
            embed=embed,
            view=view,
            ephemeral=not is_public,
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
        )

        msg = await interaction.original_response()
        view.message = msg
        view.message_id = msg.id
        view.channel_id = msg.channel.id

        if is_public:
            self.persistent_records[str(msg.id)] = {
                "guild_id": guild.id,
                "channel_id": msg.channel.id,
                "role_id": DM_OPEN_ROLE_ID,
                "page": view.page,
                "image_url": view.image_url
            }
            await self._save_state_file()

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