import asyncio
from dotenv import load_dotenv
from discord.ext import commands
import discord
from riddle_core import riddle_manager, Core  # Core für Konstanten und get_timestamp()

load_dotenv()

LOG_CHANNEL_ID = 1381754826710585527

def create_riddle_embed(riddle_id, author: discord.User, text, created_at,
                        image_url=None, award=None, mention1=None, mention2=None,
                        solution_image=None, status="open", winner=None):
    embed = discord.Embed(
        title=f"Goon Hut Riddle (Created: {created_at})",
        description=text,
        color=discord.Color.blue()
    )
    embed.set_image(url=image_url or Core.DEFAULT_IMAGE_URL)
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

# --- SubmitSolutionView und weitere Views/Modals ---

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
        # Wichtig: keine weitere Antwort nach send_modal!

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

        riddle["status"] = "closed"
        riddle["winner_id"] = self.submitter_id
        await riddle_manager.save_data()

        await interaction.response.send_modal(EditRiddleModal(self.riddle_id))

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
            default=riddle.get("award", "") or ""
        )
        self.image_url = discord.ui.TextInput(
            label="Image URL (optional)", 
            required=False, 
            max_length=300, 
            default=riddle.get("image_url", "") or ""
        )
        self.solution_image = discord.ui.TextInput(
            label="Solution Image URL (optional)", 
            required=False, 
            max_length=300, 
            default=riddle.get("solution_image", "") or ""
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

        riddle["text"] = self.text.value
        riddle["award"] = self.award.value if self.award.value.strip() else None
        riddle["image_url"] = self.image_url.value or Core.DEFAULT_IMAGE_URL
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

async def setup_persistent_views(bot):
    for riddle_id, riddle in riddle_manager.cache.items():
        if riddle.get("status") == "open":
            bot.add_view(SubmitSolutionView(riddle_id, riddle["author_id"]))

async def setup(bot):
    from riddle_commands import setup as setup_riddle_commands
    await setup_riddle_commands(bot)
