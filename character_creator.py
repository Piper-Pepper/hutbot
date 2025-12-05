import discord
from discord.ext import commands
from discord import app_commands


# -----------------------------------------------------
# Helper prompt builder (positive + negative)
# -----------------------------------------------------

DEFAULT_NEGATIVE = "low quality, blurry, bad anatomy, extra limbs, distorted face"

def build_positive_prompt(data: dict) -> str:
    parts = []

    parts.append(f"{data.get('gender', '')}")
    parts.append(f"{data.get('age', '')} years old")
    parts.append(f"body: {data.get('body', '')}")
    parts.append(f"appearance: {data.get('appearance', '')}")
    parts.append(f"style: {data.get('style', '')}")

    # Remove empty tokens + join
    return ", ".join(p for p in parts if p.strip())


def build_negative_prompt(user_neg: str = "") -> str:
    parts = [DEFAULT_NEGATIVE]

    if user_neg:
        parts.append(user_neg)

    return ", ".join(parts)


# -----------------------------------------------------
# Character Creator Cog
# -----------------------------------------------------

class CharacterCreator(commands.Cog):
    """5-Step Character Wizard for AI Image Bots"""

    def __init__(self, bot):
        self.bot = bot

        # user_id -> dict with data
        self.active_sessions = {}
        # user_id -> current step index
        self.user_step = {}

        # Steps definition
        self.steps = [
            ("gender", "What's the **gender** of your character? (male / female / other)"),
            ("age", "How **old** is the character?"),
            ("body", "Describe the **body type** (athletic, slim, curvy, bulky...)"),
            ("appearance", "Describe **hair, skin, face, eyes** etc."),
            ("style", "What is the **clothing / vibe / style**?")
        ]

    # -----------------------------------------------------
    # Slash command: /createcharacter
    # -----------------------------------------------------

    @app_commands.command(name="createcharacter", description="Start the 5-step character creation wizard")
    async def createcharacter(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        self.active_sessions[user_id] = {}
        self.user_step[user_id] = 0

        first_step = self.steps[0][1]

        await interaction.response.send_message(
            f"Alright, let's create a character! 🧪\n\n{first_step}",
            ephemeral=True
        )

    # -----------------------------------------------------
    # Message listener for the steps
    # -----------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bot messages & DMs
        if message.author.bot or not isinstance(message.channel, discord.abc.GuildChannel):
            return

        user_id = message.author.id

        # Not in a session → ignore
        if user_id not in self.active_sessions:
            return

        step_index = self.user_step.get(user_id, 0)

        # Safety check
        if step_index >= len(self.steps):
            return

        key, question = self.steps[step_index]

        # Save the user's answer
        self.active_sessions[user_id][key] = message.content.strip()

        # Move to next step
        step_index += 1
        self.user_step[user_id] = step_index

        # If wizard is finished
        if step_index >= len(self.steps):
            data = self.active_sessions[user_id]

            # Build prompts
            positive = build_positive_prompt(data)
            negative = build_negative_prompt()

            # Cleanup session
            del self.active_sessions[user_id]
            del self.user_step[user_id]

            # Here you insert your real API call
            # ---------------------------------------------------
            # image_bytes = await venice_generate( ... )
            # ---------------------------------------------------

            await message.reply(
                f"✨ **Character complete!**\n\n"
                f"**Positive Prompt:**\n```{positive}```\n"
                f"**Negative Prompt:**\n```{negative}```\n"
                f"(Insert API call here.)"
            )
            return

        # Otherwise ask the next question
        next_question = self.steps[step_index][1]
        await message.reply(next_question)


# -----------------------------------------------------
# Setup
# -----------------------------------------------------

async def setup(bot):
    await bot.add_cog(CharacterCreator(bot))
