import discord
from discord.ext import commands
from discord.ui import Button, View
import json
import os

# File to store interaction data (for persistent button interactions)
INTERACTIONS_FILE = 'button_interactions.json'

class TextEmbedView(View):
    def __init__(self, full_text, shortened_text):
        super().__init__()
        self.full_text = full_text
        self.shortened_text = shortened_text

    @discord.ui.button(label="Show more", style=discord.ButtonStyle.primary, custom_id="more_text_button")
    async def show_full_text(self, interaction: discord.Interaction, button: Button):
        # Display full text when the button is pressed
        await interaction.response.edit_message(content=self.full_text, embed=None, view=None)

class TextEmbedCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.interactions = self.load_interactions()

    def load_interactions(self):
        """Load saved interactions if they exist."""
        if os.path.exists(INTERACTIONS_FILE):
            try:
                with open(INTERACTIONS_FILE, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                print(f"Error loading JSON data: {e}")
                return {}
        return {}

    def save_interactions(self):
        """Save interactions to file."""
        try:
            with open(INTERACTIONS_FILE, 'w') as f:
                json.dump(self.interactions, f)
        except IOError as e:
            print(f"Error saving interactions: {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        # Prevent the bot from responding to its own messages or processing commands here
        if message.author.id == self.bot.user.id:
            return

        # Check if the message starts with 'Prompt:' (case-sensitive)
        if message.content.startswith('Prompt:'):
            prompt = message.content  # Get the text from the message
            max_characters = 100
            shortened_text = prompt[:max_characters] + "..."

            # If the text is longer than 100 characters, add the button
            if len(prompt) > max_characters:
                embed = discord.Embed(title="Generated Image", description=shortened_text)
                embed.set_footer(text="Prompt shortened. Click 'Show more' for the full text.")
                view = TextEmbedView(prompt, shortened_text)
            else:
                embed = discord.Embed(title="Generated Image", description=prompt)
                view = None

            # Send the message in the same channel with the shortened text and button
            await message.channel.send(embed=embed, view=view)

            # Save the full text for later retrieval when the button is pressed
            if len(prompt) > max_characters:
                self.interactions[message.id] = {'full_text': prompt}
                self.save_interactions()

        # Continue processing other bot commands
        await self.bot.process_commands(message)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        # When the button is pressed, find the saved interaction and update the message
        if interaction.data['custom_id'] == 'more_text_button':
            message_id = interaction.message.id
            if message_id in self.interactions:
                full_text = self.interactions[message_id]['full_text']
                await interaction.response.edit_message(content=full_text, embed=None, view=None)
            else:
                await interaction.response.send_message("Data not found!", ephemeral=True)

    async def cog_load(self):
        """Load interactions when the cog is loaded."""
        self.interactions = self.load_interactions()

    async def cog_unload(self):
        """Save interactions when the cog is unloaded."""
        self.save_interactions()

def setup(bot):
    bot.add_cog(TextEmbedCog(bot))
