import discord
from discord import app_commands
from discord.ext import commands
import os
import glob

class ResetConfirmView(discord.ui.View):
    def __init__(self, author, timeout=60):
        super().__init__(timeout=timeout)
        self.author = author
        self.confirmed = None
        self.interaction = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("⛔ Nur der Befehl-Ersteller kann bestätigen.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Ja, löschen", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.interaction = interaction
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="❌ Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        await interaction.response.send_message("🚫 Reset abgebrochen.", ephemeral=True)
        self.stop()

class RiddleReset(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="riddle_reset", description="⚠️ Löscht alle Rätsel-Daten (JSON-Dateien, nur Admins)")
    @app_commands.checks.has_permissions(administrator=True)
    async def riddle_reset(self, interaction: discord.Interaction):
        view = ResetConfirmView(author=interaction.user)
        await interaction.response.send_message(
            "⚠️ **Willst du wirklich ALLE `.json`-Dateien löschen?**\nDiese Aktion ist **nicht umkehrbar**!",
            view=view,
            ephemeral=True
        )

        await view.wait()

        if view.confirmed:
            deleted_files = []
            for file in glob.glob("*.json"):
                try:
                    os.remove(file)
                    deleted_files.append(file)
                except Exception as e:
                    await view.interaction.followup.send(f"❌ Fehler beim Löschen von `{file}`: {e}", ephemeral=True)

            if deleted_files:
                await view.interaction.followup.send(
                    f"✅ **{len(deleted_files)} Dateien gelöscht:**\n" +
                    "\n".join(f"- `{f}`" for f in deleted_files),
                    ephemeral=True
                )
            else:
                await view.interaction.followup.send("⚠️ Keine `.json`-Dateien gefunden.", ephemeral=True)

    @riddle_reset.error
    async def riddle_reset_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message("❌ Du hast keine Berechtigung für diesen Befehl.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Fehler: {error}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(RiddleReset(bot))
