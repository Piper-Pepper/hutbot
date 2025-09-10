import discord
from discord.ext import commands
from discord import app_commands
import asyncio

SAFE_ROLE_ID = 1377051179615522926
BATCH_SIZE = 5   # Anzahl Mitglieder pro Batch
DELAY = 1        # Sekunden zwischen den Batches

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

        # Kick in Batches, um Rate Limits zu vermeiden
        for i in range(0, len(members_to_kick), BATCH_SIZE):
            batch = members_to_kick[i:i+BATCH_SIZE]
            tasks = [m.kick(reason="Does not have the required role") for m in batch]

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    await interaction.followup.send(f"Failed to kick a member: {r}")
                else:
                    kicked_count += 1

            await asyncio.sleep(DELAY)  # kurze Pause zwischen Batches

        await interaction.followup.send(
            f"Kicked {kicked_count} members who didn't have the role '{role.name}'."
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(HutKick(bot))
