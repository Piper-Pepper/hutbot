import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
from typing import Optional
from datetime import datetime, timezone

ROLE_ID = 1387850018471284760  # Rolle "DM open"
PAGE_SIZE = 20  # 20 Buttons pro Seite

# Footer
FOOTER_ICON_URL = "https://cdn-icons-png.flaticon.com/512/25/25231.png"
FOOTER_TEXT = "Hut DM List"

# Fallback-Bild, wenn kein Bild angegeben wird
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1393738129734897725/dm_open2.jpg"

class DMModal(Modal, title="Send a DM"):
    def __init__(self, target_user: discord.User):
        super().__init__()
        self.target_user = target_user
        self.message = TextInput(
            label="Your message",
            style=discord.TextStyle.paragraph,
            required=True
        )
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.target_user.send(
                f"📩 You received a message from {interaction.user.mention}:\n\n{self.message.value}"
            )
            await interaction.response.send_message("✅ DM sent successfully!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Cannot send DM — user might have DMs disabled.", ephemeral=True
            )

class MemberButton(Button):
    def __init__(self, user: discord.Member):
        days_on_server = (datetime.now(timezone.utc) - user.joined_at).days if user.joined_at else 0
        label = f"📩 {user.display_name} *{days_on_server}d*"
        if len(label) > 80:
            label = label[:77] + "..."
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DMModal(self.user))

class NavButton(Button):
    def __init__(self, label: str, target_page: int, row: Optional[int] = None):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self.target_page = target_page

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, PaginationView):
            return
        new_view = PaginationView(view.members, self.target_page, view.image_url)
        embed = new_view.create_embed()
        await interaction.response.edit_message(embed=embed, view=new_view)
        new_view.message = await interaction.original_response()

class PaginationView(View):
    def __init__(self, members: list[discord.Member], page: int = 0, image_url: Optional[str] = None):
        super().__init__(timeout=None)
        self.members = members
        self.page = page
        self.total_pages = (len(members) - 1) // PAGE_SIZE + 1
        self.message: Optional[discord.Message] = None
        self.image_url = image_url
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
        embed.set_image(url=self.image_url or DEFAULT_IMAGE_URL)
        return embed

class HutDM(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.hut_dm_group = app_commands.Group(
            name="hut_dm",
            description="🛖📬Hut DM commands"
        )
        self.hut_dm_group.command(
            name="list",
            description="Show members with open DMs"
        )(self.hut_dm_list)

        bot.tree.add_command(self.hut_dm_group)

    @app_commands.describe(
        open="Only show members with the DM role (default: True)",
        image_url="Optional image to decorate the embed"
    )
    async def hut_dm_list(
        self,
        interaction: discord.Interaction,
        open: Optional[bool] = True,
        image_url: Optional[str] = None
    ):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This command can only be used inside a server.",
                ephemeral=True
            )
            return

        role = guild.get_role(ROLE_ID)
        if not role:
            await interaction.response.send_message(
                "❌ DM open role not found on this server.",
                ephemeral=True
            )
            return

        members = [m for m in (role.members if open else guild.members) if not m.bot]

        if not members:
            await interaction.response.send_message(
                "No members found matching the criteria.",
                ephemeral=True
            )
            return

        members = sorted(members, key=lambda m: m.display_name.lower())

        view = PaginationView(members, page=0, image_url=image_url)
        embed = view.create_embed()

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

async def setup(bot: commands.Bot):
    await bot.add_cog(HutDM(bot))
