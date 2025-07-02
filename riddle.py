import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
import uuid
import os
import requests
from datetime import datetime
from dotenv import load_dotenv
from riddle_embeds import build_riddle_embed, build_solution_submission_embed, build_wrong_solution_embed, build_win_embed

load_dotenv()

# JSONBin Config
RIDDLE_BIN_ID = os.getenv("RIDDLE_BIN_ID")
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")

HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

RIDDLE_CHANNEL_ID = 1346843244067160074
LOG_CHANNEL_ID = 1381754826710585527
MOD_ROLE_ID = 1380610400416043089

riddle_cache = {}

# Load riddles from JSONBin
def load_riddles():
    url = f"https://api.jsonbin.io/v3/b/{RIDDLE_BIN_ID}/latest"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        global riddle_cache
        riddle_cache = resp.json().get("record", {})
        print(f"✅ Riddles loaded. Count: {len(riddle_cache)}")
    else:
        print(f"❌ Failed to load riddles: {resp.status_code} {resp.text}")

# Save riddles to JSONBin
def save_riddles():
    url = f"https://api.jsonbin.io/v3/b/{RIDDLE_BIN_ID}"
    response = requests.put(url, json=riddle_cache, headers=HEADERS)
    if response.status_code == 200:
        print("✅ Riddles saved to JSONBin.")
    else:
        print(f"❌ Error saving riddles: {response.status_code} {response.text}")

# Close riddle with winner and update message
async def close_riddle_with_winner(bot, riddle_id, winner_id=None, solution_text=""):
    riddle = riddle_cache.get(riddle_id)
    if not riddle:
        return

    riddle["winner"] = winner_id if winner_id else "none"
    save_riddles()

    # Remove submit button from posted message
    if riddle.get("button_id") and riddle.get("channel_id"):
        try:
            channel = bot.get_channel(int(riddle["channel_id"]))
            msg = await channel.fetch_message(int(riddle["button_id"]))
            embed = msg.embeds[0]
            embed.description += "\n\n**This Riddle is closed.**"
            await msg.edit(embed=embed, view=None)
        except Exception as e:
            print(f"[WARN] Failed to update message button: {e}")

    # Delete solution suggestion messages from log channel
    for suggestion in riddle.get("suggestions", []):
        try:
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            msg = await log_channel.fetch_message(int(suggestion["message_id"]))
            await msg.delete()
        except:
            pass

    # Post Win embed in RIDDLE_CHANNEL
    channel = bot.get_channel(RIDDLE_CHANNEL_ID)
    mention_txt = f"<@&{MOD_ROLE_ID}>"
    for m in riddle.get("mentions", []):
        mention_txt += f" <@&{m}>"

    winner = bot.get_user(int(winner_id)) if winner_id and winner_id != "none" else None
    guild = channel.guild
    embed = build_win_embed(riddle, guild, winner, solution_text if winner else "")
    await channel.send(content=mention_txt, embed=embed)

# Modal for submitting a solution
class SubmitSolutionModal(Modal, title="Submit Your Solution"):
    def __init__(self, bot, riddle_id):
        super().__init__()
        self.bot = bot
        self.riddle_id = riddle_id
        self.solution = TextInput(label="Your Solution", required=True, style=discord.TextStyle.paragraph)
        self.add_item(self.solution)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        riddle = riddle_cache.get(self.riddle_id)
        if not riddle:
            return await interaction.followup.send("This riddle no longer exists.", ephemeral=True)

        embed = build_solution_submission_embed(riddle, interaction.user, self.solution.value)
        view = VoteView(self.bot, self.riddle_id, interaction.user.id, self.solution.value)

        msg = await self.bot.get_channel(LOG_CHANNEL_ID).send(embed=embed, view=view)
        riddle.setdefault("suggestions", []).append({
            "user_id": str(interaction.user.id),
            "solution": self.solution.value,
            "message_id": str(msg.id)
        })
        save_riddles()
        await interaction.followup.send("✅ Your solution has been submitted for review.", ephemeral=True)

# Submit Button and View
class SubmitButton(Button):
    def __init__(self, bot, riddle_id):
        super().__init__(label="Submit Solution", style=discord.ButtonStyle.blurple, custom_id=f"submit_{riddle_id}")
        self.bot = bot
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SubmitSolutionModal(self.bot, self.riddle_id))

class SubmitView(View):
    def __init__(self, bot, riddle_id):
        super().__init__(timeout=None)
        self.add_item(SubmitButton(bot, riddle_id))

# Vote Buttons (Thumbs Up/Down)
class VoteView(View):
    def __init__(self, bot, riddle_id, user_id, solution_text):
        super().__init__(timeout=None)
        self.bot = bot
        self.riddle_id = riddle_id
        self.user_id = user_id
        self.solution = solution_text
        self.add_item(VoteButton(bot, riddle_id, user_id, solution_text, True))
        self.add_item(VoteButton(bot, riddle_id, user_id, solution_text, False))

class VoteButton(Button):
    def __init__(self, bot, riddle_id, user_id, solution_text, upvote):
        label = "👍" if upvote else "👎"
        custom_id = f"{'up' if upvote else 'down'}vote_{riddle_id}_{user_id}"
        super().__init__(style=discord.ButtonStyle.green if upvote else discord.ButtonStyle.red,
                         label=label, custom_id=custom_id)
        self.bot = bot
        self.riddle_id = riddle_id
        self.user_id = user_id
        self.solution_text = solution_text
        self.upvote = upvote

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        riddle = riddle_cache.get(self.riddle_id)
        if not riddle:
            return

        if self.upvote:
            await close_riddle_with_winner(self.bot, self.riddle_id, winner_id=self.user_id, solution_text=self.solution_text)
        else:
            embed = build_wrong_solution_embed(riddle, interaction.user, self.solution_text, interaction.guild)
            await self.bot.get_channel(RIDDLE_CHANNEL_ID).send(content=f"<@{self.user_id}>", embed=embed)

        # Delete vote message to keep channel clean
        try:
            await interaction.message.delete()
        except:
            pass

# Setup persistent views on bot startup
async def setup_persistent_views(bot: commands.Bot):
    load_riddles()
    for riddle_id, data in riddle_cache.items():
        if data.get("winner") == "none" or data.get("winner") is None:
            if "button_id" in data:
                bot.add_view(SubmitView(bot, riddle_id))
            if "suggestions" in data:
                for s in data["suggestions"]:
                    bot.add_view(VoteView(bot, riddle_id, int(s["user_id"]), s["solution"]))

# Setup function for bot extension loading
async def setup(bot: commands.Bot):
    await setup_persistent_views(bot)
