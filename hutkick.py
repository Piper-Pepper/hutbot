import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio

SAFE_ROLE_ID = 1377051179615522926
BATCH_SIZE = 5          # Mitglieder pro Batch
DELAY_BETWEEN_BATCHES = 10  # Sekunden Pause nach jedem Batch
DELAY_PER_KICK = 1      # Sekunden zwischen einzelnen Kicks

LOG_CHANNEL_ID = None  # Optional: Channel für Fortschritt, z.B. 123456789012345678

class HutKick(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="kick_non_safe", description="Kick everyone without the safe role")
    @app_commands.checks.has_permissions(administrator=True)
    async def kick_non_safe(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Kick process started… this may take a while.", ephemeral=True
        )
        # Starte Hintergrundtask
        self.bot.loop.create_task(self._kick_task(interaction.guild))

    async def _kick_task(self, guild: discord.Guild):
        role = guild.get_role(SAFE_ROLE_ID)
        if not role:
            return

        members_to_kick = [m for m in guild.members if role not in m.roles and not m.bot]
        kicked_count = 0

        log_channel = guild.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None

        for i in range(0, len(members_to_kick), BATCH_SIZE):
            batch = members_to_kick[i:i+BATCH_SIZE]
            for member in batch:
                try:
                    await member.kick(reason="Does not have the required role")
                    kicked_count += 1
                    if log_channel:
                        await log_channel.send(f"Kicked {member} ({kicked_count}/{len(members_to_kick)})")
                    await asyncio.sleep(DELAY_PER_KICK)
                except Exception as e:
                    print(f"Failed to kick {member}: {e}")
                    if log_channel:
                        await log_channel.send(f"Failed to kick {member}: {e}")

            # Pause zwischen Batches, um Rate Limits zu schonen
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

        # Fertigmeldung
        if log_channel:
            await log_channel.send(f"KICK PROCESS COMPLETE: {kicked_count}/{len(members_to_kick)} members kicked.")
        print(f"KICK PROCESS COMPLETE: {kicked_count}/{len(members_to_kick)} members kicked.")

async def setup(bot: commands.Bot):
    await bot.add_cog(HutKick(bot))
