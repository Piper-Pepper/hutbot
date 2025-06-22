import asyncio
from dotenv import load_dotenv
from discord.ext import commands
import discord
from riddle_core import riddle_manager, Core  # Core.FIXED_CHANNEL_ID wird verwendet!
from solution_manager import solution_manager  # Lösungsspeicherung mit button_message_id

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

# ---------------------- Views / Buttons / Modals ----------------------

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

        # Lösung speichern im solution_manager (inkl. button_message_id später)
        solution_manager.add_solution(self.riddle_id, interaction.user.id, self.solution.value)
        await solution_manager.save_data()

        embed = discord.Embed(
            title=f"Solution Proposal for Riddle {self.riddle_id}",
            description=riddle["text"],
            color=discord.Color.green()
        )
        embed.add_field(name="Proposed Solution", value=self.solution.value, inline=False)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

        view = SolutionDecisionView(self.riddle_id, interaction.user.id, self.solution.value)

        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            msg = await log_channel.send(embed=embed, view=view)
            # Speichere die message.id der Daumen-Buttons für Persistenz
            solution_manager.set_solution_button_message_id(self.riddle_id, interaction.user.id, msg.id)
            await solution_manager.save_data()

            await interaction.response.send_message("Your solution was sent to the riddle creators for approval.", ephemeral=True)
        else:
            await interaction.response.send_message("Log channel not found. Solution not sent.", ephemeral=True)

# ------------------------------------------

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

        try:
            await interaction.message.delete()
        except Exception as e:
            print(f"Failed to delete message: {e}")

        channel = interaction.client.get_channel(Core.FIXED_CHANNEL_ID)
        if channel:
            winner = interaction.client.get_user(self.submitter_id)
            embed = create_riddle_embed(
                self.riddle_id,
                interaction.client.get_user(riddle["author_id"]),
                riddle["text"],
                riddle["created_at"],
                image_url=riddle.get("image_url"),
                award=riddle.get("award"),
                solution_image=riddle.get("solution_image"),
                status=riddle.get("status"),
                winner=winner
            )
            await channel.send(embed=embed)

        await interaction.response.send_message("Riddle closed and solution posted!", ephemeral=True)

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

class ActionButtonsView(discord.ui.View):
    def __init__(self, riddle_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, custom_id="riddle_edit_button")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = EditRiddleModal(self.riddle_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Post", style=discord.ButtonStyle.success, custom_id="riddle_post_button")
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddle = riddle_manager.get_riddle(self.riddle_id)
        if not riddle:
            await interaction.response.send_message("Rätsel nicht gefunden.", ephemeral=True)
            return

        channel = interaction.client.get_channel(Core.FIXED_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("Fixed channel not found.", ephemeral=True)
            return

        mentions = [f"<@&1380610400416043089>"]
        if riddle.get("mention1_id"):
            mentions.append(f"<@&{riddle['mention1_id']}>")
        if riddle.get("mention2_id"):
            mentions.append(f"<@&{riddle['mention2_id']}>")
        mention_text = " ".join(mentions)

        embed = create_riddle_embed(
            self.riddle_id,
            interaction.client.get_user(riddle["author_id"]),
            riddle["text"],
            riddle["created_at"],
            image_url=riddle.get("image_url"),
            award=riddle.get("award"),
            solution_image=riddle.get("solution_image"),
            status=riddle.get("status"),
            winner=None
        )

        view = SubmitSolutionView(self.riddle_id, riddle["author_id"])
        await channel.send(content=mention_text, embed=embed, view=view)

        interaction.client.add_view(view)

        await interaction.response.send_message("Rätsel gepostet mit Submit-Button!", ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="riddle_delete_button")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddle = riddle_manager.get_riddle(self.riddle_id)
        if not riddle:
            await interaction.response.send_message("Rätsel nicht gefunden.", ephemeral=True)
            return

        riddle_manager.remove_riddle(self.riddle_id)
        await riddle_manager.save_data()
        await interaction.response.send_message("Riddle deleted!", ephemeral=True)

# ---------- Select View ----------

class RiddleSelect(discord.ui.Select):
    def __init__(self, riddles: dict):
        options = [
            discord.SelectOption(label=rid, description=(r["text"][:50] + ("..." if len(r["text"]) > 50 else "")))
            for rid, r in riddles.items()
        ]
        super().__init__(placeholder="Wähle ein Rätsel aus...", min_values=1, max_values=1, options=options, custom_id="riddle_select_menu")

    async def callback(self, interaction: discord.Interaction):
        riddle_id = self.values[0]
        riddle = riddle_manager.get_riddle(riddle_id)
        if not riddle:
            await interaction.response.send_message("Rätsel nicht gefunden.", ephemeral=True)
            return

        author = interaction.client.get_user(riddle["author_id"])
        winner = interaction.client.get_user(riddle.get("winner_id")) if riddle.get("winner_id") else None
        embed = create_riddle_embed(riddle_id, author, riddle["text"], riddle["created_at"],
                                    image_url=riddle.get("image_url"),
                                    award=riddle.get("award"),
                                    solution_image=riddle.get("solution_image"),
                                    status=riddle.get("status"),
                                    winner=winner)

        view = ActionButtonsView(riddle_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

class RiddleListView(discord.ui.View):
    def __init__(self, open_riddles: dict):
        super().__init__(timeout=None)
        self.add_item(RiddleSelect(open_riddles))

# ------------- PERSISTENT VIEWS SETUP -------------

async def setup_persistent_views(bot):
    # SubmitSolutionView für offene Rätsel
    for riddle_id, riddle in riddle_manager.cache.items():
        if riddle.get("status") == "open":
            bot.add_view(SubmitSolutionView(riddle_id, riddle["author_id"]))

    # SolutionDecisionView für Lösungen mit gespeicherter Button-Message
    for riddle_id, user_solutions in solution_manager.cache.items():
        for user_id_str, sol in user_solutions.items():
            message_id = sol.get("button_message_id")
            if message_id is not None:
                bot.add_view(
                    SolutionDecisionView(riddle_id, int(user_id_str), sol["solution_text"]),
                    message_id=message_id
                )

    open_riddles = {k: v for k, v in riddle_manager.cache.items() if v.get("status") == "open"}
    bot.add_view(RiddleListView(open_riddles))

async def setup(bot):
    await solution_manager.load_data()
    await setup_persistent_views(bot)
    from riddle_commands import setup as setup_riddle_commands
    await setup_riddle_commands(bot)
