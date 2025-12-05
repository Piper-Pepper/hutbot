import discord
from discord.ext import commands
from discord import app_commands

# ---------------- Helper Prompt Builder ----------------

DEFAULT_NEGATIVE = "low quality, blurry, bad anatomy, extra limbs, distorted face"

def build_positive_prompt(data: dict) -> str:
    parts = []
    parts.append(f"{data.get('gender', '')}")
    parts.append(f"{data.get('age', '')} years old")
    parts.append(f"body: {data.get('body', '')}")
    parts.append(f"appearance: {data.get('appearance', '')}")
    parts.append(f"style: {data.get('style', '')}")
    # Remove empty tokens
    return ", ".join(p for p in parts if p.strip())

def build_negative_prompt(data: dict, user_neg: str = "") -> str:
    parts = [DEFAULT_NEGATIVE]
    if user_neg:
        parts.append(user_neg)
    # Include avoid traits
    avoid = data.get("avoid", "").strip()
    if avoid:
        parts.append(f"avoid: {avoid}")
    return ", ".join(parts)

# ---------------- Character Creator Cog ----------------

class CharacterCreator(commands.Cog):
    """6-Step Character Wizard for AI Image Bots"""
    CHANNEL_ID = 1446497103391100990  # only in this channel

    def __init__(self, bot):
        self.bot = bot
        self.active_sessions = {}  # user_id -> data
        self.user_step = {}        # user_id -> step index
        self.steps = [
            ("gender", "What's the **gender** of your character? (male/female/other)"),
            ("age", "How **old** is the character?"),
            ("body", "Describe **body type** (athletic, slim, curvy...)"),
            ("appearance", "Describe **hair, skin, face, eyes** etc."),
            ("style", "Describe **clothing / vibe / style**"),
            ("avoid", "Anything the character should **not have**? (tattoos, scars...)")
        ]

    # ---------------- Slash Command ----------------
    @app_commands.command(name="createcharacter", description="Start the 6-step character creation wizard")
    async def createcharacter(self, interaction: discord.Interaction):
        if interaction.channel.id != self.CHANNEL_ID:
            await interaction.response.send_message("❌ You can only use this command in the designated channel.", ephemeral=True)
            return

        user_id = interaction.user.id
        self.active_sessions[user_id] = {}
        self.user_step[user_id] = 0

        first_question = self.steps[0][1]
        await interaction.response.send_message(
            f"🧪 Let's create your character!\n\n{first_question}",
            ephemeral=True
        )

    # ---------------- Message Listener ----------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots, DMs, wrong channel
        if message.author.bot or not isinstance(message.channel, discord.abc.GuildChannel):
            return
        if message.channel.id != self.CHANNEL_ID:
            return

        user_id = message.author.id
        if user_id not in self.active_sessions:
            return

        step_index = self.user_step.get(user_id, 0)
        if step_index >= len(self.steps):
            return

        key, question = self.steps[step_index]
        self.active_sessions[user_id][key] = message.content.strip()

        step_index += 1
        self.user_step[user_id] = step_index

        if step_index >= len(self.steps):
            data = self.active_sessions[user_id]
            positive = build_positive_prompt(data)
            negative = build_negative_prompt(data)

            # Final confirmation embed
            embed = discord.Embed(title="✅ Character Overview", color=discord.Color.green())
            embed.add_field(name="Positive Prompt", value=f"```{positive}```", inline=False)
            embed.add_field(name="Negative Prompt", value=f"```{negative}```", inline=False)
            embed.set_footer(text="Do you want to submit or restart?")

            # Buttons
            view = ConfirmationView(self, message.author, data)
            await message.reply(embed=embed, view=view)

            # cleanup active session for now
            del self.active_sessions[user_id]
            del self.user_step[user_id]
            return

        # ask next question
        next_question = self.steps[step_index][1]
        await message.reply(next_question)

# ---------------- Confirmation View ----------------

class ConfirmationView(discord.ui.View):
    def __init__(self, wizard, user, data):
        super().__init__(timeout=None)
        self.wizard = wizard
        self.user = user
        self.data = data

        submit_btn = discord.ui.Button(label="Submit ✅", style=discord.ButtonStyle.success)
        restart_btn = discord.ui.Button(label="Restart 🔄", style=discord.ButtonStyle.red)

        submit_btn.callback = self.submit_callback
        restart_btn.callback = self.restart_callback

        self.add_item(submit_btn)
        self.add_item(restart_btn)

    async def submit_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This is not your character session.", ephemeral=True)
            return

        positive = build_positive_prompt(self.data)
        negative = build_negative_prompt(self.data)

        # Call your AI API here
        # image_bytes = await venice_generate(...)

        await interaction.response.send_message(
            f"🎨 Character submitted!\n\n**Positive Prompt:**\n```{positive}```\n**Negative Prompt:**\n```{negative}```\n(Insert API call here.)",
            ephemeral=True
        )

    async def restart_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This is not your character session.", ephemeral=True)
            return

        # start a new session
        self.wizard.active_sessions[interaction.user.id] = {}
        self.wizard.user_step[interaction.user.id] = 0

        first_question = self.wizard.steps[0][1]
        await interaction.response.send_message(f"🔄 Restarting character creation...\n\n{first_question}", ephemeral=True)

# ---------------- Setup ----------------
async def setup(bot):
    await bot.add_cog(CharacterCreator(bot))
