# hutkick.py

import discord
from discord.ext import commands
from discord import app_commands

SAFE_ROLE_ID = 1377051179615522926

class HutKick(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Slash Command
    @app_commands.command(name="kick_non_safe", description="Kick everyone without the safe role")
    @app_commands.checks.has_permissions(administrator=True)
    async def kick_non_safe(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)  # <-- VERY IMPORTANT for long tasks

        guild = interaction.guild
        role = guild.get_role(SAFE_ROLE_ID)

        if not role:
            await interaction.followup.send(f"Role with ID {SAFE_ROLE_ID} not found!")
            return

        kicked_count = 0
        for member in guild.members:
            if role not in member.roles and not member.bot:
                try:
                    await member.kick(reason="Does not have the required role")
                    kicked_count += 1
                except Exception as e:
                    await interaction.followup.send(f"Failed to kick {member}: {e}")

        await interaction.followup.send(
            f"Kicked {kicked_count} members who didn't have the role '{role.name}'."
        )

# Required for discord.py v2.x
async def setup(bot: commands.Bot):
    await bot.add_cog(HutKick(bot))
    # Sync slash commands globally (or guild-specific for testing)
    await bot.tree.sync()
