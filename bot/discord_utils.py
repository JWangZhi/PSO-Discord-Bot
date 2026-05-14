"""Discord message helpers."""

import discord

from settings import app as app_settings


async def send_long_message(interaction: discord.Interaction, text: str):
    """Split a long string into Discord follow-up chunks."""
    limit = app_settings.DISCORD_MESSAGE_LIMIT
    if len(text) <= limit:
        await interaction.followup.send(text)
        return

    lines = text.split("\n")
    current_chunk = ""

    for line in lines:
        while len(line) > limit:
            if current_chunk:
                await interaction.followup.send(current_chunk)
                current_chunk = ""
            await interaction.followup.send(line[:limit])
            line = line[limit:]

        if len(current_chunk) + len(line) + 1 > limit:
            await interaction.followup.send(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk.strip():
        await interaction.followup.send(current_chunk)


async def reply_long(message: discord.Message, text: str):
    """Split a long reply into Discord message chunks."""
    limit = app_settings.DISCORD_MESSAGE_LIMIT
    if len(text) <= limit:
        await message.reply(text)
        return

    lines = text.split("\n")
    current_chunk = ""
    first = True

    for line in lines:
        while len(line) > limit:
            if current_chunk:
                if first:
                    await message.reply(current_chunk)
                    first = False
                else:
                    await message.channel.send(current_chunk)
                current_chunk = ""
            if first:
                await message.reply(line[:limit])
                first = False
            else:
                await message.channel.send(line[:limit])
            line = line[limit:]

        if len(current_chunk) + len(line) + 1 > limit:
            if first:
                await message.reply(current_chunk)
                first = False
            else:
                await message.channel.send(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk.strip():
        if first:
            await message.reply(current_chunk)
        else:
            await message.channel.send(current_chunk)
