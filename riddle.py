import os
import aiohttp
import asyncio
from dotenv import load_dotenv
from discord.ext import commands
import discord
import uuid
from datetime import datetime

load_dotenv()

JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID")
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
JSONBIN_HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

RIDDLE_CREATOR_ROLE_ID = 1380610400416043089
LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"
DEFAULT_SOLUTION_IMAGE = "https://cdn.discordapp.com/attachments/1383652563408392232/1384295668176388229/zombie_piper.gif"

def get_timestamp():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

class RiddleManager:
    def __init__(self):
        self.cache = {}
        self.lock = asyncio.Lock()

    async def load_data(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(JSONBIN_URL, headers=JSONBIN_HEADERS) as resp:
                    if resp.status == 200:
                        json_data = await resp.json()
                        self.cache = json_data.get("record", {})
                        print(f"[RiddleManager] Loaded {len(self.cache)} riddles from jsonbin.io")
                    else:
                        print(f"[RiddleManager] Failed to load data: HTTP {resp.status}")
            except Exception as e:
                print(f"[RiddleManager] Exception while loading data: {e}")

    async def save_data(self):
        async with self.lock:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.put(JSONBIN_URL, headers=JSONBIN_HEADERS, json=self.cache) as resp:
                        if resp.status in (200, 201):
                            print(f"[RiddleManager] Saved {len(self.cache)} riddles to jsonbin.io")
                        else:
                            print(f"[RiddleManager] Failed to save data: HTTP {resp.status}")
                except Exception as e:
                    print(f"[RiddleManager] Exception while saving data: {e}")

    def add_riddle(self, riddle_id: str, data: dict):
        self.cache[riddle_id] = data

    def get_riddle(self, riddle_id: str):
        return self.cache.get(riddle_id)

    def remove_riddle(self, riddle_id: str):
        if riddle_id in self.cache:
            del self.cache[riddle_id]

riddle_manager = RiddleManager()

# --- Embeds & Views helpers ---

def create_riddle_embed(riddle_id, author: discord.User, text, created_at,
                        image_url=None, award=None, mention1=None, mention2=None,
                        solution_image=None, status="open", winner=None):
    embed = discord.Embed(
        title=f"Goon Hut Riddle (Created: {created_at})",
        description=text,
        color=discord.Color.blue()
    )
    embed.set_image(url=image_url or DEFAULT_IMAGE_URL)
    # Safe check for author avatar
    avatar_url = author.avatar.url if author and author.avatar else discord.utils.MISSING
    if avatar_url is not discord.utils.MISSING:
        embed.set_thumbnail(url=avatar_url)
    embed.set_footer(text=f"{author.name if author else 'Unknown'} | ID: {riddle_id}")

    if award:
        embed.add_field(name="Award", value=award, inline=False)

    if status == "closed":
        embed.title = f"✅ Goon Hut Riddle - Closed (Created: {created_at})"
        if winner:
            embed.add_field(name="Winner", value=winner.mention, inline=False)
        else:
            embed.add_field(name="Winner", value="No winner", inline=False)
        if solution_image:
            embed.set_image(url=solution_image)

    return embed

# --- Submit Solution ---

class SubmitSolutionView(discord.ui.View):
    def __init__(self, riddle_id, creator_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.creator_id = creator_id

    @discord.ui.button(label="Submit Solution", style=discord.ButtonStyle.primary, custom_id="submit_solution_button")
    async def submit_solution(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddle = riddle_manager.get_riddle(self.riddle_id)
        if not riddle or riddle.get("status") != "open":
            await interaction.response.send_message("The riddle is already closed.", ephemeral=True)
            return

        modal = SolutionModal(self.riddle_id, self.creator_id)
        await interaction.response.send_modal(modal)

class SolutionModal(discord.ui.Modal, title="Submit Riddle Solution"):
    def __init__(self, riddle_id, creator_id):
        super().__init__()
        self.riddle_id = riddle_id
        self.creator_id = creator_id

        self.solution = discord.ui.TextInput(label="Your solution", style=discord.TextStyle.paragraph, max_length=200)
        self.add_item(self.solution)

    async def on_submit(self, interaction: discord.Interaction):
        riddle = riddle_manager.get_riddle(self.riddle_id)
        if not riddle or riddle.get("status") != "open":
            await interaction.response.send_message("This riddle is already closed.", ephemeral=True)
            return

        creator = interaction.client.get_user(self.creator_id)
        if not creator:
            await interaction.response.send_message("Could not find riddle creator.", ephemeral=True)
            return

        embed = discord.Embed(title=f"Solution Proposal for Riddle {self.riddle_id}",
                              description=riddle["text"],
                              color=discord.Color.green())
        embed.add_field(name="Proposed Solution", value=self.solution.value, inline=False)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

        view = SolutionDecisionView(self.riddle_id, interaction.user.id, self.solution.value)

        try:
            await creator.send(embed=embed, view=view)
            log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(embed=embed)
        except Exception:
            await interaction.response.send_message("Failed to send your solution to the riddle creator.", ephemeral=True)
            return

        await interaction.response.send_message("Your solution was sent to the riddle creator for approval.", ephemeral=True)

class SolutionDecisionView(discord.ui.View):
    def __init__(self, riddle_id, submitter_id, solution_text):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.submitter_id = submitter_id
        self.solution_text = solution_text

    @discord.ui.button(emoji="👍", style=discord.ButtonStyle.success, custom_id="solution_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddle = riddle_manager.get_riddle(self.riddle_id)
        if not riddle:
            await interaction.response.send_message("Riddle not found.", ephemeral=True)
            return

        # Jeder darf editieren – Hinweis kann freiwillig sein
        await interaction.response.send_modal(EditRiddleModal(self.riddle_id))


        # Close the riddle and set winner
        riddle["status"] = "closed"
        riddle["winner_id"] = self.submitter_id
        await riddle_manager.save_data()

        await interaction.response.send_message("Solution accepted, riddle closed!", ephemeral=True)

    @discord.ui.button(emoji="👎", style=discord.ButtonStyle.danger, custom_id="solution_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddle = riddle_manager.get_riddle(self.riddle_id)
        if not riddle:
            await interaction.response.send_message("Riddle not found.", ephemeral=True)
            return

        submitter = interaction.client.get_user(self.submitter_id)
        if submitter:
            try:
                await submitter.send("Sorry, your solution was not correct!")
            except:
                pass

        await interaction.response.send_message("Solution rejected.", ephemeral=True)

# --- Edit Riddle Modal ---

class EditRiddleModal(discord.ui.Modal, title="Edit Riddle"):
    def __init__(self, riddle_id):
        super().__init__()
        self.riddle_id = riddle_id

        riddle = riddle_manager.get_riddle(riddle_id)

        self.text = discord.ui.TextInput(
            label="Riddle Text", 
            style=discord.TextStyle.paragraph, 
            max_length=1000, 
            default=riddle.get("text", "")
        )
        self.award = discord.ui.TextInput(
            label="Award (optional)", 
            required=False, 
            max_length=100, 
            default=riddle.get("award", "")
        )
        self.image_url = discord.ui.TextInput(
            label="Image URL (optional)", 
            required=False, 
            max_length=300, 
            default=riddle.get("image_url", "")
        )
        self.solution_image = discord.ui.TextInput(
            label="Solution Image URL (optional)", 
            required=False, 
            max_length=300, 
            default=riddle.get("solution_image", "")
        )
        self.solution = discord.ui.TextInput(
            label="Correct Solution", 
            required=True, 
            max_length=200, 
            default=riddle.get("solution", "")
        )

        self.add_item(self.text)
        self.add_item(self.award)
        self.add_item(self.image_url)
        self.add_item(self.solution_image)
        self.add_item(self.solution)

    async def on_submit(self, interaction: discord.Interaction):
        riddle = riddle_manager.get_riddle(self.riddle_id)
        if not riddle:
            await interaction.response.send_message("Riddle not found.", ephemeral=True)
            return


        # Update fields
        riddle["text"] = self.text.value
        riddle["award"] = self.award.value if self.award.value.strip() else None
        riddle["image_url"] = self.image_url.value or DEFAULT_IMAGE_URL
        riddle["solution_image"] = self.solution_image.value or None
        riddle["solution"] = self.solution.value.strip().lower()

        await riddle_manager.save_data()
        await interaction.response.send_message("Riddle updated successfully!", ephemeral=True)


# --- Riddle List Select & View ---

class RiddleSelect(discord.ui.Select):
    def __init__(self, riddles: dict):
        options = []
        for rid, riddle in riddles.items():
            label = rid
            desc = riddle["text"][:50] + ("..." if len(riddle["text"]) > 50 else "")
            options.append(discord.SelectOption(label=label, description=desc))
        super().__init__(placeholder="Wähle ein Rätsel aus...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        try:
            riddle_id = self.values[0]
            riddle = riddle_manager.get_riddle(riddle_id)
            if not riddle:
                await interaction.response.send_message("Rätsel nicht gefunden.", ephemeral=True)
                return

            author = interaction.client.get_user(riddle["author_id"])
            winner = interaction.client.get_user(riddle.get("winner_id")) if riddle.get("winner_id") else None
            embed = create_riddle_embed(
                riddle_id,
                author,
                riddle["text"],
                riddle["created_at"],
                image_url=riddle.get("image_url"),
                award=riddle.get("award"),
                mention1=None,
                mention2=None,
                solution_image=riddle.get("solution_image"),
                status=riddle.get("status"),
                winner=winner
            )

            class EditButtonView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=300)
                    self.riddle_id = riddle_id

                @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary)
                async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
                    riddle = riddle_manager.get_riddle(self.riddle_id)
    
                    modal = EditRiddleModal(self.riddle_id)
                    await interaction.response.send_modal(modal)

            view = EditButtonView()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)
            print(f"[ERROR][RiddleSelect.callback]: {e}")

class RiddleListView(discord.ui.View):
    def __init__(self, open_riddles: dict):
        super().__init__(timeout=60)
        self.add_item(RiddleSelect(open_riddles))

# --- Commands Cog ---

class RiddleCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="riddle_add", description="Create a new riddle")
    @discord.app_commands.describe(text="Riddle text", solution="Solution to the riddle", channel_name="Channel to post the riddle")
    async def riddle_add(self, interaction: discord.Interaction,
                         text: str,
                         solution: str,
                         channel_name: discord.TextChannel,
                         image_url: str = None,
                         mention_group1: discord.Role = None,
                         mention_group2: discord.Role = None,
                         solution_image: str = None,
                         award: str = None):
        author = interaction.user
        if RIDDLE_CREATOR_ROLE_ID not in [r.id for r in author.roles]:
            await interaction.response.send_message("You don't have permission to add riddles.", ephemeral=True)
            return

        riddle_id = str(uuid.uuid4())[:8]
        created_at = get_timestamp()

        riddle_data = {
            "author_id": author.id,
            "text": text,
            "solution": solution.lower(),
            "channel_id": channel_name.id,
            "created_at": created_at,
            "status": "open",
            "image_url": image_url or DEFAULT_IMAGE_URL,
            "award": award,
            "mention1": mention_group1.id if mention_group1 else None,
            "mention2": mention_group2.id if mention_group2 else None,
            "solution_image": solution_image
        }

        riddle_manager.add_riddle(riddle_id, riddle_data)
        await riddle_manager.save_data()

        embed = create_riddle_embed(
            riddle_id,
            author,
            text,
            created_at,
            image_url=riddle_data["image_url"],
            award=award,
            mention1=mention_group1,
            mention2=mention_group2,
            solution_image=solution_image
        )
        mentions = f"<@&{RIDDLE_CREATOR_ROLE_ID}>"
        if mention_group1:
            mentions += f" {mention_group1.mention}"
        if mention_group2:
            mentions += f" {mention_group2.mention}"

        view = SubmitSolutionView(riddle_id, author.id)

        await channel_name.send(content=mentions, embed=embed, view=view)
        await interaction.response.send_message(f"Riddle created with ID `{riddle_id}`.", ephemeral=True)

    @discord.app_commands.command(name="riddle_list", description="List all riddles")
    async def riddle_list(self, interaction: discord.Interaction):
        open_riddles = {k: v for k, v in riddle_manager.cache.items() if v["status"] == "open"}
        closed_riddles = {k: v for k, v in riddle_manager.cache.items() if v["status"] == "closed"}

        embed = discord.Embed(title="Riddle List", color=discord.Color.teal())
        embed.add_field(name=f"Open Riddles ({len(open_riddles)})",
                        value="\n".join(f"`{k}` | {v['created_at']}" for k, v in open_riddles.items()) or "None",
                        inline=False)
        embed.add_field(name=f"Closed Riddles ({len(closed_riddles)})",
                        value="\n".join(f"`{k}` | {v['created_at']}" for k, v in closed_riddles.items()) or "None",
                        inline=False)

        view = RiddleListView(open_riddles)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup_persistent_views(bot):
    for riddle_id, riddle in riddle_manager.cache.items():
        if riddle.get("status") == "open":
            bot.add_view(SubmitSolutionView(riddle_id, riddle["author_id"]))

async def setup(bot):
    await bot.add_cog(RiddleCommands(bot))
