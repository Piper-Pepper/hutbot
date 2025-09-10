import discord
from discord.ext import commands
from discord import app_commands
import asyncio

SAFE_ROLE_ID = 1377051179615522926
BATCH_SIZE = 5
DELAY_BETWEEN_BATCHES = 10
DELAY_PER_KICK = 1

class HutKick(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="kick_non_safe", description="Kick everyone without the safe role")
    async def kick_non_safe(self, interaction: discord.Interaction):
        # Berechtigungscheck manuell
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need Administrator permissions!", ephemeral=True)
            return

        await interaction.response.send_message("Kick process started… this may take a while.", ephemeral=True)

        # Lade alle Mitglieder, falls nicht gecached
        guild = interaction.guild
        await guild.fetch_members().flatten()  # nur bei großen Servern nötig

        role = guild.get_role(SAFE_ROLE_ID)
        if not role:
            await interaction.followup.send(f"Role with ID {SAFE_ROLE_ID} not found!")
            return

        members_to_kick = [m for m in guild.members if role not in m.roles and not m.bot]
        kicked_count = 0

        if not members_to_kick:
            await interaction.followup.send("No members to kick.")
            return

        for i in range(0, len(members_to_kick), BATCH_SIZE):
            batch = members_to_kick[i:i+BATCH_SIZE]
            for member in batch:
                try:
                    await member.kick(reason="Does not have the required role")
                    kicked_count += 1
                    await asyncio.sleep(DELAY_PER_KICK)
                except Exception as e:
                    print(f"Failed to kick {member}: {e}")
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

        await interaction.followup.send(f"Kicked {kicked_count}/{len(members_to_kick)} members.")
