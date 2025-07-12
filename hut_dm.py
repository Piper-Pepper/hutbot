import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
from typing import Optional

ROLE_ID = 1387850018471284760  # Rolle "DM open"

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
        # Include discriminator to help distinguish similar names
        label = f"{user.display_name} ({user.discriminator})"
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DMModal(self.user))

class DMListView(View):
    def __init__(self, members: list[discord.Member]):
        super().__init__(timeout=120)  # 2 Minuten Timeout

        # Trim to Discord's 25-component limit
        members = members[:25]
        for member in members:
            self.add_item(MemberButton(member))

class HutDM(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Command group registration
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

        if open:
            members = [m for m in role.members if not m.bot]
        else:
            members = [m for m in guild.members if not m.bot]

        if not members:
            await interaction.response.send_message(
                "No members found matching the criteria.",
                ephemeral=True
            )
            return

        members = sorted(members, key=lambda m: m.display_name.lower())

        embed = discord.Embed(
            title="DM Open Members" if open else "All Members (excluding bots)",
            color=discord.Color.green()
        )
        if image_url:
            embed.set_image(url=image_url)

        await interaction.response.send_message(
            embed=embed,
            view=DMListView(members),
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(HutDM(bot))
