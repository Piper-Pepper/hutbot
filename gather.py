import discord
from discord import app_commands
from discord.ext import commands

TARGET_USER_ID = 1339242900906836090  # Nur Nachrichten dieses Users verschieben


class GatherCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="gather", description="Collect all posts from a specific user and move them to a thread.")
    @app_commands.describe(thread_id="The ID of the thread where the messages should be moved.")
    async def gather(self, interaction: discord.Interaction, thread_id: str):
        await interaction.response.defer(ephemeral=True)

        try:
            # Hole den Channel und den Thread
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                return await interaction.followup.send("❌ This command can only be used in a text channel.", ephemeral=True)

            thread = channel.get_thread(int(thread_id))
            if not thread:
                # Falls nicht gecached → vom Server fetchen
                try:
                    thread = await channel.fetch_thread(int(thread_id))
                except discord.NotFound:
                    return await interaction.followup.send("❌ Thread not found!", ephemeral=True)

            moved_count = 0
            async for message in channel.history(limit=None, oldest_first=True):
                if message.author.id == TARGET_USER_ID:
                    try:
                        await thread.send(
                            content=f"**From {message.author.mention}:**\n{message.content}",
                            files=[await attachment.to_file() for attachment in message.attachments]
                        )
                        moved_count += 1
                        # Optional: Originalnachricht löschen
                  
                    except discord.Forbidden:
                        await interaction.followup.send("⚠️ I don't have permission to send messages to that thread.", ephemeral=True)
                        return

            await interaction.followup.send(f"✅ Done! Moved {moved_count} messages to thread <#{thread.id}>.", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GatherCog(bot))
