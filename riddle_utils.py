# riddle_utils.py

riddle_cache = {}

DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"

async def close_riddle_with_winner(bot, riddle_id, winner, solution_text):
    from discord import Embed, File
    import json
    from datetime import datetime

    riddle_data = riddle_cache.pop(riddle_id, None)
    if not riddle_data:
        return

    # ✅ 1. Original-Embed aktualisieren
    embed = Embed(
        title="🎉 Riddle Solved!",
        description=f"**Riddle:** {riddle_data['text']}\n\n✅ **Correct Answer:** {solution_text}\n👑 **Winner:** {winner.mention}",
        color=0x00FF00,
        timestamp=datetime.utcnow()
    )

    channel = bot.get_channel(riddle_data['channel_id'])
    if channel:
        try:
            message = await channel.fetch_message(riddle_data['message_id'])
            await message.edit(embed=embed, view=None)
        except:
            pass

    # ✅ 2. Neuer "Gewinner"-Post
    try:
        mention_text = riddle_data.get("mentions", "")

        final_embed = Embed(
            title="🎯 Rätsel gelöst!",
            description=f"**Rätsel:**\n{riddle_data['text']}\n\n"
                        f"✅ **Eingereichte Lösung:**\n{solution_text}\n\n"
                        f"📜 **Offizielle Lösung:**\n{riddle_data.get('solution', 'Nicht definiert.')}\n\n"
                        f"👑 **Gewinner:** {winner.mention}",
            color=0xFFD700,
            timestamp=datetime.utcnow()
        )

        final_embed.set_author(name=winner.display_name, icon_url=winner.display_avatar.url)

        # Bild einfügen (lösungsbild > standardbild)
        image_url = riddle_data.get("solution_image") or DEFAULT_IMAGE_URL
        final_embed.set_image(url=image_url)
        final_embed.set_footer(text="🏆 Glückwunsch zum Knacken des Rätsels!")

        await channel.send(content=mention_text, embed=final_embed)

    except Exception as e:
        print(f"Fehler beim Senden des Gewinner-Posts: {e}")

    # ✅ 3. riddles.json aktualisieren
    with open("riddles.json", "w", encoding="utf-8") as f:
        json.dump(riddle_cache, f, ensure_ascii=False, indent=2)

