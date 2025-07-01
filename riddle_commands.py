import discord
from discord import app_commands
from discord.ext import commands
import uuid
from datetime import datetime

from riddle_embeds import build_riddle_embed, build_solution_submission_embed, build_wrong_solution_embed, build_win_embed
from riddle import riddle_cache, save_riddles, SubmitView, close_riddle_with_winner

MOD_ROLE_ID = 1380610400416043089
RIDDLE_CHANNEL_ID = 1346843244067160074

# -------- Modal for Editing Riddle --------
class RiddleEditModal(discord.ui.Modal, title="Edit Riddle"):
    def __init__(self, bot, riddle_id):
        super().__init__()
        self.bot = bot
        self.riddle_id = riddle_id
        r = riddle_cache[riddle_id]

        self.text = discord.ui.TextInput(label="Riddle Text", style=discord.TextStyle.paragraph, default=r["text"])
        self.solution = discord.ui.TextInput(label="Solution", default=r["solution"])
        self.image_url = discord.ui.TextInput(label="Image URL (optional)", default=r.get("image_url", ""), required=False)
        self.solution_url = discord.ui.TextInput(label="Solution Image URL (optional)", default=r.get("solution_url", ""), required=False)
        self.mentions = discord.ui.TextInput(label="Mention Role IDs (max 2, comma separated)", default=",".join(r.get("mentions", [])), required=False)
        self.award = discord.ui.TextInput(label="Award Text or Emoji (optional)", default=r.get("award", ""), required=False)

        for field in [self.text, self.solution, self.image_url, self.solution_url, self.mentions, self.award]:
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        r = riddle_cache[self.riddle_id]
        r["text"] = self.text.value
        r["solution"] = self.solution.value
        r["image_url"] = self.image_url.value or ""
        r["solution_url"] = self.solution_url.value or ""
        r["mentions"] = [x.strip() for x in self.mentions.value.split(",") if x.strip()]
        r["award"] = self.award.value
        save_riddles()
        await interaction.followup.send("✅ Riddle updated.", ephemeral=True)
        await interaction.user.send(embed=build_riddle_embed(r, interaction.guild, interaction.user), view=RiddleEditView(self.bot, self.riddle_id))

# -------- View: Edit, Post, Close, Delete Buttons --------
class RiddleEditView(discord.ui.View):
    def __init__(self, bot, riddle_id):
        super().__init__(timeout=None)
        self.add_item(EditRiddleButton(bot, riddle_id))
        self.add_item(PostRiddleButton(bot, riddle_id))
        self.add_item(CloseRiddleButton(bot, riddle_id))
        self.add_item(DeleteRiddleButton(bot, riddle_id))

class EditRiddleButton(discord.ui.Button):
    def __init__(self, bot, riddle_id):
        super().__init__(label="Edit", style=discord.ButtonStyle.primary)
        self.bot = bot
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RiddleEditModal(self.bot, self.riddle_id))

class PostRiddleButton(discord.ui.Button):
    def __init__(self, bot, riddle_id):
        super().__init__(label="Post", style=discord.ButtonStyle.success)
        self.bot = bot
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        r = riddle_cache[self.riddle_id]
        channel = self.bot.get_channel(RIDDLE_CHANNEL_ID)
        mentions = f"<@&{MOD_ROLE_ID}>" + "".join(f" <@&{x}>" for x in r.get("mentions", [])[:2])
        embed = build_riddle_embed(r, interaction.guild, interaction.user)
        view = SubmitView(self.bot, self.riddle_id)
        msg = await channel.send(content=mentions, embed=embed, view=view)
        r["channel_id"] = str(RIDDLE_CHANNEL_ID)
        r["button_id"] = str(msg.id)
        save_riddles()
        await interaction.followup.send("✅ Riddle posted.", ephemeral=True)

class CloseRiddleButton(discord.ui.Button):
    def __init__(self, bot, riddle_id):
        super().__init__(label="Close", style=discord.ButtonStyle.secondary)
        self.bot = bot
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await close_riddle_with_winner(self.bot, self.riddle_id, winner_id=None)
        await interaction.followup.send("✅ Riddle closed.", ephemeral=True)

class DeleteRiddleButton(discord.ui.Button):
    def __init__(self, bot, riddle_id):
        super().__init__(label="Delete", style=discord.ButtonStyle.danger)
        self.bot = bot
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if self.riddle_id in riddle_cache:
            del riddle_cache[self.riddle_id]
            save_riddles()
        await interaction.followup.send("🗑️ Riddle deleted.", ephemeral=True)

# -------- Main Cog --------
class RiddleCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="riddle_add", description="Create a new riddle (Mods only)")
    async def riddle_add(self, interaction: discord.Interaction,
                         text: str,
                         solution: str,
                         image_url: str = "",
                         mentions: str = "",
                         solution_image: str = "",
                         award: str = ""):
        if MOD_ROLE_ID not in [role.id for role in interaction.user.roles]:
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        riddle_id = str(uuid.uuid4())[:8]
        riddle_data = {
            "text": text,
            "solution": solution,
            "image_url": image_url,
            "solution_url": solution_image,
            "mentions": [x.strip() for x in mentions.split(",") if x.strip()][:2],
            "award": award,
            "riddle_id": riddle_id,
            "ersteller": str(interaction.user.id),
            "winner": None,
            "created_at": datetime.utcnow().isoformat()
        }

        # Debugging-Ausgabe
        print(f"Adding riddle: {riddle_data}")

        riddle_cache[riddle_id] = riddle_data
        save_riddles()

        embed = build_riddle_embed(riddle_data, interaction.guild, interaction.user)
        await interaction.followup.send("🧩 Riddle created. Here’s your preview:", embed=embed, view=RiddleEditView(self.bot, riddle_id), ephemeral=True)

    @app_commands.command(name="riddle_list", description="List all riddles")
    async def riddle_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        lines = []
        for rid, r in list(riddle_cache.items())[:20]:
            creator = f"<@{r['ersteller']}>"
            preview = r['text'][:30].replace("\n", " ") + "..."
            line = f"• [`{rid}`] by {creator} — {preview}"
            lines.append(line)

        desc = "\n".join(lines) if lines else "No riddles available."
        embed = discord.Embed(title="🧩 Active Riddles", description=desc, color=discord.Color.blurple())
        await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleCommands(bot))