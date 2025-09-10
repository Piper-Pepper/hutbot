import discord
from discord.ext import commands
from discord import app_commands
import asyncio

SAFE_ROLE_ID = 1377051179615522926
DELAY = 1  # Sekunden zwischen einzelnen Kicks, Rate-Limit safe

class HutKick(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="kick_non_safe", description="Kick everyone without the safe role")
    @app_commands.checks.has_permissions(administrator=True)
    async def kick_non_safe(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        role = guild.get_role(SAFE_ROLE_ID)
        if not role:
            await interaction.followup.send(f"Role with ID {SAFE_ROLE_ID} not found!")
            return

        members_to_kick = [m for m in guild.members if role not in m.roles and not m.bot]
        kicked_count = 0

        for member in members_to_kick:
            try:
                await member.kick(reason="Does not have the required role")
                kicked_count += 1
                await asyncio.sleep(DELAY)  # ⚠️ wichtig für Rate Limits
            except Exception as e:
                # Fehler speichern, aber nicht jedes Mal senden
                print(f"Failed to kick {member}: {e}")

        await interaction.followup.send(
            f"Kicked {kicked_count} members who didn't have the role '{role.name}'."
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(HutKick(bot))
