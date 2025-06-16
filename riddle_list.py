import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import json
import os

class WinnerModal(discord.ui.Modal, title="Rätsel schließen - Gewinner angeben"):
    winner_id = discord.ui.TextInput(label="Gewinner User-ID oder @mention", required=True)

    def __init__(self, cog, rid):
        super().__init__()
        self.cog = cog
        self.rid = rid

    async def on_submit(self, interaction: discord.Interaction):
        try:
            winner_raw = self.winner_id.value.strip("<@!>")
            winner = interaction.guild.get_member(int(winner_raw))
            if not winner:
                raise ValueError
        except Exception:
            await interaction.response.send_message("❌ Ungültiges Mitglied.", ephemeral=True)
            return

        await self.cog.close_riddle(self.rid, winner=winner)
        await interaction.response.send_message(f"✅ Rätsel {self.rid} mit Gewinner {winner.mention} geschlossen.", ephemeral=True)

class RiddleManageView(discord.ui.View):
    def __init__(self, cog, selected_rid):
        super().__init__(timeout=120)
        self.cog = cog
        self.selected_rid = selected_rid

    @discord.ui.button(label="✅ Rätsel schließen (mit Gewinner)", style=discord.ButtonStyle.green)
    async def close_with_winner(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = WinnerModal(self.cog, self.selected_rid)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🔒 Rätsel schließen (ohne Gewinner)", style=discord.ButtonStyle.blurple)
    async def close_without_winner(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.cog.close_riddle(self.selected_rid)
        await interaction.response.send_message(f"Rätsel {self.selected_rid} ohne Gewinner geschlossen.", ephemeral=True)

    @discord.ui.button(label="❌ Rätsel löschen", style=discord.ButtonStyle.danger)
    async def delete_riddle(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.cog.delete_riddle(self.selected_rid)
        await interaction.response.send_message(f"Rätsel {self.selected_rid} gelöscht.", ephemeral=True)

class RiddleSelect(discord.ui.Select):
    def __init__(self, cog, options):
        super().__init__(placeholder="Wähle ein Rätsel aus", options=options, max_values=1)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        selected_rid = self.values[0]
        riddle_data = self.cog.riddle_cache.get(selected_rid)
        if not riddle_data:
            await interaction.response.send_message("❌ Dieses Rätsel existiert nicht mehr.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🧩 Goon Hut Rätsel (ID: {selected_rid})",
            description=riddle_data["text"].replace("\\n", "\n"),
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=riddle_data["creator_avatar"])
        embed.add_field(name="🎯 Lösung", value=riddle_data["solution"], inline=False)
        embed.add_field(name="📅 Erstellt von", value=riddle_data['creator_name'], inline=True)
        embed.set_footer(text=f"Erstellt am {riddle_data['created_at'][:10]}")

        view = RiddleManageView(self.cog, selected_rid)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class RiddleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Hier solltest du deinen Cache laden oder initialisieren
        self.riddle_cache = {}  # Beispiel: dict mit allen Rätseln

        # Optional: Lade vorhandene Rätsel aus JSON-Dateien beim Start
        self.load_riddles()

    def load_riddles(self):
        for filename in os.listdir():
            if filename.endswith(".json"):
                with open(filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    rid = filename[:-5]  # Entferne .json
                    self.riddle_cache[rid] = data

    @app_commands.command(name="list", description="List all open riddles.")
    async def list_riddles(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        open_riddles = {rid: data for rid, data in self.riddle_cache.items() if not data.get("closed", False)}

        if not open_riddles:
            await interaction.followup.send("🎉 Es sind aktuell keine offenen Rätsel vorhanden.", ephemeral=True)
            return

        options = []
        for rid, data in open_riddles.items():
            label = f"ID: {rid} | von {data['creator_name']} am {data['created_at'][:10]}"
            options.append(discord.SelectOption(label=label[:100], value=rid))

        view = discord.ui.View(timeout=120)
        view.add_item(RiddleSelect(self, options))
        await interaction.followup.send("Wähle ein Rätsel aus:", view=view, ephemeral=True)

    async def close_riddle(self, rid, winner=None):
        riddle = self.riddle_cache.get(rid)
        if not riddle:
            return
        riddle["closed"] = True
        if winner:
            riddle["winner"] = f"{winner.id} ({winner.display_name})"
        # Speichere aktualisiertes Rätsel in JSON
        filename = f"{rid}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(riddle, f, ensure_ascii=False, indent=4)

    async def delete_riddle(self, rid):
        filename = f"{rid}.json"
        if os.path.exists(filename):
            os.remove(filename)
        self.riddle_cache.pop(rid, None)

async def setup(bot):
    await bot.add_cog(RiddleCog(bot))
