import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
from typing import Optional

ROLE_ID = 1387850018471284760  # Rolle "DM open"
PAGE_SIZE = 8  # Max. Buttons pro Seite

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
    def __init__(self, user: discord.Member, row: int = None):
        super().__init__(label=user.display_name, style=discord.ButtonStyle.primary, row=row)
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DMModal(self.user))

class NavButton(Button):
    def __init__(self, label: str, target_page: int, row: int = None):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self.target_page = target_page

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, PaginationView):
            return
        new_view = PaginationView(view.members, self.target_page)
        embed = view.message.embeds[0] if view.message and view.message.embeds else None
        await interaction.response.edit_message(embed=embed, view=new_view)
        new_view.message = await interaction.original_response()

PAGE_SIZE = 4  # 4 Buttons pro Seite

class PaginationView(View):
    def __init__(self, members: list[discord.Member], page: int = 0):
        super().__init__(timeout=120)
        self.members = members
        self.page = page
        self.total_pages = (len(members) - 1) // PAGE_SIZE + 1
        self.message: Optional[discord.Message] = None
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()

        start = self.page * PAGE_SIZE
        end = start + PAGE_SIZE
        # Buttons in rows 0,1,2,3 jeweils einzeln
        for i, member in enumerate(self.members[start:end]):
            self.add_item(MemberButton(member, row=i))

        nav_row = 4  # Navigation unten in Row 4 (Discord erlaubt max. 5 Reihen: 0-4)
        if self.total_pages > 1:
            if self.page > 0:
                self.add_item(NavButton("⬅️ Previous", self.page - 1, row=nav_row))
            if self.page < self.total_pages - 1:
                self.add_item(NavButton("Next ➡️", self.page + 1, row=nav_row))

class HutDM(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.hut_dm_group = app_commands.Group(
            name="hut_dm",
            description="🛖📬Hut DM commands"
        )
        self.hut_dm_group.command(
            name="list",
            description="Show members with open DMs",
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
            await interaction.response.send_message("❌ This command can only be used inside a server.", ephemeral=True)
            return

        role = guild.get_role(ROLE_ID)
        if not role:
            await interaction.response.send_message("❌ DM open role not found on this server.", ephemeral=True)
            return

        members = [m for m in (role.members if open else guild.members) if not m.bot]

        if not members:
            await interaction.response.send_message("No members found matching the criteria.", ephemeral=True)
            return

        members = sorted(members, key=lambda m: m.display_name.lower())

        embed = discord.Embed(
            title="DM Open Members" if open else "All Members (excluding bots)",
            color=discord.Color.green()
        )
        if image_url:
            embed.set_image(url=image_url)

        view = PaginationView(members, page=0)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

async def setup(bot: commands.Bot):
    await bot.add_cog(HutDM(bot))
