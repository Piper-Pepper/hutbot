import discord
from discord.ext import commands
from discord import app_commands
import asyncio

SAFE_ROLE_ID = 1377051179615522926
BATCH_SIZE = 5
DELAY_PER_KICK = 1           # Sekunden zwischen einzelnen Kicks
DELAY_BETWEEN_BATCHES = 5    # Sekunden Pause zwischen Batches

class HutKick(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="kick_non_safe", description="Kick everyone without the safe role")
    async def kick_non_safe(self, interaction: discord.Interaction):
        # Berechtigungscheck
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need Administrator permissions!", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Kick process started… this may take a while.", ephemeral=True
        )

        guild = interaction.guild
        role = guild.get_role(SAFE_ROLE_ID)
        if not role:
            await interaction.followup.send(f"Role with ID {SAFE_ROLE_ID} not found!")
            return

        members_to_kick = [m for m in guild.members if role not in m.roles and not m.bot]
        if not members_to_kick:
            await interaction.followup.send("No members to kick.")
            return

        # Starte Hintergrundtask
        self.bot.loop.create_task(self._kick_members(interaction, members_to_kick))

    async def _kick_members(self, interaction, members_to_kick):
        kicked_count = 0
        total = len(members_to_kick)

        for i in range(0, total, BATCH_SIZE):
            batch = members_to_kick[i:i+BATCH_SIZE]

            for member in batch:
                try:
                    await member.kick(reason="Does not have the required role")
                    kicked_count += 1
                    await asyncio.sleep(DELAY_PER_KICK)
                except Exception as e:
                    await interaction.followup.send(f"Failed to kick {member}: {e}")

            # Feedback nach jedem Batch
            await interaction.followup.send(
                f"Kicked {kicked_count}/{total} members so far..."
            )

            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

        # Fertigmeldung
        await interaction.followup.send(f"✅ Kick process complete! {kicked_count}/{total} members kicked.")

# setup-Funktion für discord.py 2.x
async def setup(bot: commands.Bot):
    await bot.add_cog(HutKick(bot))
