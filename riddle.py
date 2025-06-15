# riddle.py

import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta
from riddle_view import RiddleSubmitView, end_riddle

RIDDLE_GROUP_ID = 1380610400416043089
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"

def load_json(path):
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump({}, f)
    with open(path, "r") as f:
        return json.load(f)

def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)

class Riddle(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.riddles = load_json("riddles.json")
        self.user_data = load_json("user_data.json")
        self.check_expired_riddles.start()

    def cog_unload(self):
        self.check_expired_riddles.cancel()

    def setup_persistent_views(self):
        for riddle_id, data in self.riddles.items():
            view = RiddleSubmitView(riddle_id, data["text"], data["creator_id"])
            self.bot.add_view(view)

    @tasks.loop(minutes=1)
    async def check_expired_riddles(self):
        now = datetime.utcnow()
        to_close = []
        for riddle_id, data in self.riddles.items():
            expiry = datetime.fromisoformat(data["expires_at"])
            if now >= expiry:
                to_close.append(riddle_id)
        for riddle_id in to_close:
            await end_riddle(self.bot, riddle_id)

    @app_commands.command(name="riddle_add", description="Add a new riddle")
    async def riddle_add(self, interaction: discord.Interaction,
                         text: str,
                         solution: str,
                         channel_name: str,
                         image_url: str = None,
                         mention_group1: discord.Role = None,
                         mention_group2: discord.Role = None,
                         solution_image: str = None,
                         length: int = 1,
                         award: str = None):

        if RIDDLE_GROUP_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

        channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)
        if not channel:
            await interaction.response.send_message("❌ Could not find the specified channel.", ephemeral=True)
            return

        riddle_id = str(uuid.uuid4())[:8]
        expires_at = datetime.utcnow() + timedelta(days=length)

        mention_text = f"<@&{RIDDLE_GROUP_ID}>"
        if mention_group1:
            mention_text += f" {mention_group1.mention}"
        if mention_group2:
            mention_text += f" {mention_group2.mention}"

        image_url = image_url or DEFAULT_IMAGE_URL
        text = text.replace("\\n", "\n")

        embed = discord.Embed(
            title=f"Goon Hut Riddle (Created: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')})",
            description=text,
            color=discord.Color.gold()
        )
        embed.set_image(url=image_url)
        embed.set_footer(text=f"By {interaction.user.display_name} • Closes in {length} day(s)")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url)
        if award:
            embed.add_field(name="🏆 Award", value=award, inline=False)

        view = RiddleSubmitView(riddle_id, text, interaction.user.id)
        message = await channel.send(content=mention_text, embed=embed, view=view)

        self.riddles[riddle_id] = {
            "text": text,
            "solution": solution,
            "channel_id": str(channel.id),
            "message_id": str(message.id),
            "creator_id": interaction.user.id,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": expires_at.isoformat(),
            "image_url": image_url,
            "solution_image": solution_image,
            "award": award,
            "mention_text": mention_text
        }

        write_json("riddles.json", self.riddles)

        user_id = str(interaction.user.id)
        self.user_data.setdefault(user_id, {"riddles_created": 0, "riddles_solved": 0})
        self.user_data[user_id]["riddles_created"] += 1
        write_json("user_data.json", self.user_data)

        await interaction.response.send_message(f"✅ Riddle posted in {channel.mention}!", ephemeral=True)

    @app_commands.command(name="riddle_list", description="List and manage active riddles")
    async def riddle_list(self, interaction: discord.Interaction):
        if not self.riddles:
            await interaction.response.send_message("There are no active riddles.", ephemeral=True)
            return

        options = []
        for riddle_id, data in self.riddles.items():
            label = f"{riddle_id} | {data['created_at'][:10]}"
            options.append(discord.SelectOption(label=label, value=riddle_id))

        async def select_callback(select_interaction):
            rid = select_interaction.data["values"][0]
            data = self.riddles[rid]

            embed = discord.Embed(
                title=f"Riddle #{rid}",
                description=f"**Text:** {data['text']}\n\n**Solution:** {data['solution']}",
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"By <@{data['creator_id']}>")

            class ManageView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=60)

                @discord.ui.button(label="Close with Winner", style=discord.ButtonStyle.success)
                async def close_with_winner(self, i, _):
                    await i.response.send_modal(CloseWithWinnerModal(rid))

                @discord.ui.button(label="Close without Winner", style=discord.ButtonStyle.secondary)
                async def close_without_winner(self, i, _):
                    await end_riddle(self.bot, rid)
                    await i.response.send_message("Riddle closed without a winner.", ephemeral=True)

                @discord.ui.button(label="Delete Riddle", style=discord.ButtonStyle.danger)
                async def delete_riddle(self, i, _):
                    await self.delete_riddle_data(rid)
                    await i.response.send_message("Riddle deleted.", ephemeral=True)

                async def delete_riddle_data(self, rid):
                    try:
                        ch = self.bot.get_channel(int(self.riddles[rid]["channel_id"]))
                        msg = await ch.fetch_message(int(self.riddles[rid]["message_id"]))
                        await msg.delete()
                    except Exception:
                        pass
                    self.riddles.pop(rid, None)
                    write_json("riddles.json", self.riddles)

            await select_interaction.response.send_message(embed=embed, ephemeral=True, view=ManageView())

        select = discord.ui.Select(placeholder="Select a riddle", options=options)
        select.callback = select_callback

        view = discord.ui.View(timeout=30)
        view.add_item(select)
        await interaction.response.send_message("Select a riddle to manage:", view=view, ephemeral=True)

class CloseWithWinnerModal(discord.ui.Modal, title="Close Riddle with Winner"):
    def __init__(self, riddle_id):
        super().__init__(timeout=60)
        self.riddle_id = riddle_id

        self.user_input = discord.ui.TextInput(
            label="Winner (mention or ID)",
            placeholder="Enter the user ID or mention of the winner",
            required=True
        )
        self.add_item(self.user_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user_input.value.strip("<@!> "))
            self_riddles = load_json("riddles.json")
            riddle = self_riddles.get(self.riddle_id)
            if not riddle:
                await interaction.response.send_message("Riddle not found or already closed.", ephemeral=True)
                return
            riddle["winner_id"] = user_id
            write_json("riddles.json", self_riddles)

            user_data = load_json("user_data.json")
            user_data.setdefault(str(user_id), {"riddles_created": 0, "riddles_solved": 0})
            user_data[str(user_id)]["riddles_solved"] += 1
            write_json("user_data.json", user_data)

            await end_riddle(interaction.client, self.riddle_id)
            await interaction.response.send_message("✅ Riddle closed with winner!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message("❌ Error closing riddle: " + str(e), ephemeral=True)

async def setup(bot):
    await bot.add_cog(Riddle(bot))
