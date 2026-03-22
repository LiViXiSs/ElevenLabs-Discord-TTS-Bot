import json
import asyncio
from pathlib import Path

import discord
from discord.ext import commands
import requests

# =========================
# CONFIG
# =========================
DISCORD_BOT_TOKEN = "token here"
ELEVENLABS_API_KEY = "eleven labs api key here"

PREFIX = "e!"
VOICE_ALIASES_FILE = "voice_aliases.json"
VOICE_SETTINGS_FILE = "voice_settings.json"

ELEVEN_MODEL_ID = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"
MAX_TTS_LENGTH = 400
VOICES_PER_PAGE = 10

# remembers the last page each guild/user viewed
voice_page_state = {}


# =========================
# DISCORD SETUP
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


# =========================
# HELPERS
# =========================
def load_aliases() -> dict:
    path = Path(VOICE_ALIASES_FILE)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_aliases(data: dict) -> None:
    Path(VOICE_ALIASES_FILE).write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def default_voice_settings() -> dict:
    return {
        "stability": 0.75,
        "similarity_boost": 0.75,
        "style": 0.0,
        "speed": 1.0,
        "use_speaker_boost": True
    }


def load_voice_settings() -> dict:
    path = Path(VOICE_SETTINGS_FILE)
    if not path.exists():
        return default_voice_settings()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "stability": float(data.get("stability", 0.75)),
            "similarity_boost": float(data.get("similarity_boost", 0.75)),
            "style": float(data.get("style", 0.0)),
            "speed": float(data.get("speed", 1.0)),
            "use_speaker_boost": bool(data.get("use_speaker_boost", True))
        }
    except Exception:
        return default_voice_settings()


def save_voice_settings(data: dict) -> None:
    Path(VOICE_SETTINGS_FILE).write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def get_elevenlabs_headers() -> dict:
    return {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }


def fetch_voices() -> list[dict]:
    url = "https://api.elevenlabs.io/v1/voices"
    response = requests.get(
        url,
        headers={"xi-api-key": ELEVENLABS_API_KEY},
        timeout=30
    )
    response.raise_for_status()
    data = response.json()
    return data.get("voices", [])


def get_paginated_account_voices(page: int, page_size: int = VOICES_PER_PAGE):
    voices = fetch_voices()
    total = len(voices)

    if total == 0:
        return [], 1, 1, total

    max_page = (total + page_size - 1) // page_size
    page = max(1, min(page, max_page))

    start = (page - 1) * page_size
    end = start + page_size
    sliced = voices[start:end]

    return sliced, page, max_page, total


def get_custom_library_voices() -> list[dict]:
    voices = fetch_voices()

    custom_categories = {
        "cloned",
        "generated",
        "professional",
        "premade",
        "library",
        "ivc",
        "pvc",
    }

    filtered = []
    for v in voices:
        category = str(v.get("category", "")).lower()
        labels = v.get("labels", {}) or {}

        if (
            category in custom_categories
            or v.get("fine_tuning") is not None
            or labels.get("use_case")
            or labels.get("description")
        ):
            filtered.append(v)

    if not filtered:
        filtered = voices

    return filtered


def generate_tts_bytes(text: str, voice_id: str) -> bytes:
    settings = load_voice_settings()

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format={OUTPUT_FORMAT}"
    payload = {
        "text": text,
        "model_id": ELEVEN_MODEL_ID,
        "voice_settings": {
            "stability": settings["stability"],
            "similarity_boost": settings["similarity_boost"],
            "style": settings["style"],
            "speed": settings["speed"],
            "use_speaker_boost": settings["use_speaker_boost"]
        }
    }

    response = requests.post(
        url,
        headers=get_elevenlabs_headers(),
        json=payload,
        timeout=60
    )
    response.raise_for_status()
    return response.content


async def ensure_voice(ctx: commands.Context) -> discord.VoiceClient | None:
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("You need to be in a voice channel first.")
        return None

    user_channel = ctx.author.voice.channel

    if ctx.voice_client:
        if ctx.voice_client.channel != user_channel:
            await ctx.voice_client.move_to(user_channel)
        return ctx.voice_client

    return await user_channel.connect()


def build_voice_embed(voices: list[dict], page: int, max_page: int, total: int, title: str):
    embed = discord.Embed(title=title, color=discord.Color.green())

    if not voices:
        embed.description = "No voices found."
        return embed

    lines = []
    for idx, voice in enumerate(voices, start=1 + (page - 1) * VOICES_PER_PAGE):
        name = voice.get("name", "Unknown")
        voice_id = voice.get("voice_id", "Unknown ID")
        category = voice.get("category", "unknown")
        labels = voice.get("labels", {}) or {}

        extra = []
        if labels.get("accent"):
            extra.append(f"accent: {labels['accent']}")
        if labels.get("age"):
            extra.append(f"age: {labels['age']}")
        if labels.get("gender"):
            extra.append(f"gender: {labels['gender']}")

        extra_text = f"\n*{' | '.join(extra)}*" if extra else ""
        lines.append(
            f"**{idx}. {name}**\n"
            f"`{voice_id}`\n"
            f"Category: `{category}`{extra_text}"
        )

    embed.description = "\n\n".join(lines)
    embed.set_footer(text=f"Page {page}/{max_page} • Total voices: {total}")
    return embed


# =========================
# EVENTS
# =========================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is ready.")


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing command arguments. Use `e!help` to see command usage.")
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send("Invalid command argument. Use `e!help` to see the correct format.")
        return

    print(f"Unhandled command error: {error}")
    await ctx.send(f"Error: `{error}`")


# =========================
# HELP
# =========================
@bot.command(name="help")
async def help_command(ctx: commands.Context):
    embed = discord.Embed(
        title="ElevenLabs TTS Bot Help",
        description="Text-to-speech bot with voice aliases, paging, custom voices, and voice tuning controls.",
        color=discord.Color.blurple()
    )

    embed.add_field(name="e!help", value="Show this help menu.", inline=False)
    embed.add_field(name="e!voices [page]", value="Show all account voices by page.\nExample: `e!voices 2`", inline=False)
    embed.add_field(name="e!nextvoices", value="Go to the next voices page.", inline=False)
    embed.add_field(name="e!prevvoices", value="Go to the previous voices page.", inline=False)
    embed.add_field(name="e!myvoices", value="Show your custom/library voices.", inline=False)
    embed.add_field(name="e!setvoice <alias> <voice_id>", value="Save a voice alias.", inline=False)
    embed.add_field(name="e!aliases", value="Show saved aliases.", inline=False)
    embed.add_field(name="e!delvoice <alias>", value="Delete a saved alias.", inline=False)
    embed.add_field(name="e!tts <alias> <text>", value="Play TTS in your voice channel.", inline=False)
    embed.add_field(name="e!join", value="Join your current voice channel.", inline=False)
    embed.add_field(name="e!leave", value="Leave the voice channel.", inline=False)
    embed.add_field(name="e!stop", value="Stop current playback.", inline=False)
    embed.add_field(name="e!ttssettings", value="Show current TTS tuning settings.", inline=False)
    embed.add_field(
        name="e!settts <setting> <value>",
        value="Change a TTS setting.\nSettings: `stability`, `similarity`, `style`, `speed`, `speaker_boost`",
        inline=False
    )
    embed.add_field(
        name="e!ttspreset <name>",
        value="Apply a preset.\nPresets: `natural`, `clear`, `expressive`, `calm`",
        inline=False
    )

    embed.set_footer(text="Prefix: e!")
    await ctx.send(embed=embed)


# =========================
# VOICE BROWSING
# =========================
@bot.command(name="voices")
async def voices_command(ctx: commands.Context, page: int = 1):
    try:
        async with ctx.typing():
            voices, page, max_page, total = await asyncio.to_thread(get_paginated_account_voices, page)
    except requests.HTTPError as e:
        try:
            err_text = e.response.text
        except Exception:
            err_text = str(e)
        await ctx.send(f"Failed to fetch voices from ElevenLabs:\n```{err_text[:1500]}```")
        return
    except Exception as e:
        await ctx.send(f"Unexpected error fetching voices: `{e}`")
        return

    guild_key = ctx.guild.id if ctx.guild else ctx.author.id
    voice_page_state[guild_key] = page

    embed = build_voice_embed(
        voices=voices,
        page=page,
        max_page=max_page,
        total=total,
        title="Available ElevenLabs Voices"
    )
    await ctx.send(embed=embed)


@bot.command(name="nextvoices")
async def nextvoices_command(ctx: commands.Context):
    guild_key = ctx.guild.id if ctx.guild else ctx.author.id
    current_page = voice_page_state.get(guild_key, 1)
    await voices_command(ctx, current_page + 1)


@bot.command(name="prevvoices")
async def prevvoices_command(ctx: commands.Context):
    guild_key = ctx.guild.id if ctx.guild else ctx.author.id
    current_page = voice_page_state.get(guild_key, 1)
    await voices_command(ctx, max(1, current_page - 1))


@bot.command(name="myvoices")
async def myvoices_command(ctx: commands.Context):
    try:
        async with ctx.typing():
            voices = await asyncio.to_thread(get_custom_library_voices)
    except requests.HTTPError as e:
        try:
            err_text = e.response.text
        except Exception:
            err_text = str(e)
        await ctx.send(f"Failed to fetch your custom voices:\n```{err_text[:1500]}```")
        return
    except Exception as e:
        await ctx.send(f"Unexpected error fetching custom voices: `{e}`")
        return

    if not voices:
        await ctx.send("No custom/library voices found on your account.")
        return

    embed = discord.Embed(
        title="Your Custom / Library Voices",
        color=discord.Color.orange()
    )

    lines = []
    for voice in voices[:20]:
        name = voice.get("name", "Unknown")
        voice_id = voice.get("voice_id", "Unknown ID")
        category = voice.get("category", "unknown")
        lines.append(f"**{name}**\n`{voice_id}`\nCategory: `{category}`")

    embed.description = "\n\n".join(lines)

    if len(voices) > 20:
        embed.set_footer(text=f"Showing 20 of {len(voices)} voices")

    await ctx.send(embed=embed)


# =========================
# ALIAS COMMANDS
# =========================
@bot.command(name="setvoice")
async def setvoice_command(ctx: commands.Context, alias: str, voice_id: str):
    aliases = load_aliases()
    alias = alias.lower()

    aliases[alias] = voice_id
    save_aliases(aliases)

    embed = discord.Embed(
        title="Voice Alias Saved",
        description=f"**{alias}** → `{voice_id}`",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)


@bot.command(name="aliases")
async def aliases_command(ctx: commands.Context):
    aliases = load_aliases()

    if not aliases:
        await ctx.send("No saved voice aliases yet.")
        return

    embed = discord.Embed(
        title="Saved Voice Aliases",
        color=discord.Color.blurple()
    )
    embed.description = "\n".join(
        f"**{alias}** → `{voice_id}`" for alias, voice_id in aliases.items()
    )
    await ctx.send(embed=embed)


@bot.command(name="delvoice")
async def delvoice_command(ctx: commands.Context, alias: str):
    aliases = load_aliases()
    alias = alias.lower()

    if alias not in aliases:
        await ctx.send(f"No saved alias found for **{alias}**.")
        return

    del aliases[alias]
    save_aliases(aliases)

    embed = discord.Embed(
        title="Voice Alias Deleted",
        description=f"Removed alias **{alias}**.",
        color=discord.Color.orange()
    )
    await ctx.send(embed=embed)


# =========================
# TTS SETTINGS COMMANDS
# =========================
@bot.command(name="ttssettings")
async def ttssettings_command(ctx: commands.Context):
    settings = load_voice_settings()

    embed = discord.Embed(
        title="Current TTS Voice Settings",
        color=discord.Color.blurple()
    )
    embed.add_field(name="Stability", value=str(settings["stability"]), inline=True)
    embed.add_field(name="Similarity", value=str(settings["similarity_boost"]), inline=True)
    embed.add_field(name="Style", value=str(settings["style"]), inline=True)
    embed.add_field(name="Speed", value=str(settings["speed"]), inline=True)
    embed.add_field(name="Speaker Boost", value=str(settings["use_speaker_boost"]), inline=True)
    embed.set_footer(text="Use e!settts <setting> <value> to change them.")
    await ctx.send(embed=embed)


@bot.command(name="settts")
async def settts_command(ctx: commands.Context, setting: str, value: str):
    settings = load_voice_settings()
    setting = setting.lower()

    try:
        if setting == "stability":
            settings["stability"] = clamp(float(value), 0.0, 1.0)

        elif setting in ("similarity", "similarity_boost"):
            settings["similarity_boost"] = clamp(float(value), 0.0, 1.0)

        elif setting == "style":
            settings["style"] = clamp(float(value), 0.0, 1.0)

        elif setting == "speed":
            settings["speed"] = clamp(float(value), 0.7, 1.2)

        elif setting in ("speaker_boost", "boost"):
            if value.lower() in ("true", "on", "yes", "1"):
                settings["use_speaker_boost"] = True
            elif value.lower() in ("false", "off", "no", "0"):
                settings["use_speaker_boost"] = False
            else:
                await ctx.send("Speaker boost must be `true` or `false`.")
                return
        else:
            await ctx.send(
                "Unknown setting. Use one of: `stability`, `similarity`, `style`, `speed`, `speaker_boost`."
            )
            return

        save_voice_settings(settings)

        embed = discord.Embed(
            title="TTS Setting Updated",
            description=f"**{setting}** set to `{value}`",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    except ValueError:
        await ctx.send("That value is not valid for this setting.")


@bot.command(name="ttspreset")
async def ttspreset_command(ctx: commands.Context, preset: str):
    preset = preset.lower()

    presets = {
        "natural": {
            "stability": 0.45,
            "similarity_boost": 0.80,
            "style": 0.15,
            "speed": 0.96,
            "use_speaker_boost": True
        },
        "clear": {
            "stability": 0.70,
            "similarity_boost": 0.85,
            "style": 0.05,
            "speed": 1.00,
            "use_speaker_boost": True
        },
        "expressive": {
            "stability": 0.35,
            "similarity_boost": 0.75,
            "style": 0.40,
            "speed": 1.00,
            "use_speaker_boost": True
        },
        "calm": {
            "stability": 0.65,
            "similarity_boost": 0.80,
            "style": 0.05,
            "speed": 0.92,
            "use_speaker_boost": True
        }
    }

    if preset not in presets:
        await ctx.send("Available presets: `natural`, `clear`, `expressive`, `calm`")
        return

    save_voice_settings(presets[preset])

    embed = discord.Embed(
        title="TTS Preset Applied",
        description=f"Applied preset **{preset}**.",
        color=discord.Color.orange()
    )
    await ctx.send(embed=embed)


# =========================
# VOICE CHANNEL COMMANDS
# =========================
@bot.command(name="join")
async def join_command(ctx: commands.Context):
    vc = await ensure_voice(ctx)
    if vc:
        await ctx.send(f"Joined **{vc.channel.name}**.")


@bot.command(name="leave")
async def leave_command(ctx: commands.Context):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected from the voice channel.")
    else:
        await ctx.send("I'm not in a voice channel.")


@bot.command(name="stop")
async def stop_command(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("Stopped playback.")
    else:
        await ctx.send("Nothing is playing right now.")


# =========================
# TTS
# =========================
@bot.command(name="tts")
async def tts_command(ctx: commands.Context, alias: str, *, text: str):
    alias = alias.lower()
    aliases = load_aliases()

    if alias not in aliases:
        await ctx.send(
            f"Unknown voice alias: **{alias}**\n"
            f"Use `e!myvoices` or `e!voices` to find a voice ID, then save it with `e!setvoice {alias} <voice_id>`."
        )
        return

    if len(text) > MAX_TTS_LENGTH:
        await ctx.send(f"Text is too long. Keep it under {MAX_TTS_LENGTH} characters.")
        return

    vc = await ensure_voice(ctx)
    if vc is None:
        return

    if vc.is_playing():
        vc.stop()

    voice_id = aliases[alias]

    try:
        async with ctx.typing():
            audio_bytes = await asyncio.to_thread(generate_tts_bytes, text, voice_id)
    except requests.HTTPError as e:
        try:
            err_text = e.response.text
        except Exception:
            err_text = str(e)
        await ctx.send(f"ElevenLabs API error:\n```{err_text[:1500]}```")
        return
    except Exception as e:
        await ctx.send(f"Unexpected TTS error: `{e}`")
        return

    temp_file = Path(f"tts_{ctx.guild.id if ctx.guild else 'dm'}.mp3")
    temp_file.write_bytes(audio_bytes)

    audio_source = discord.FFmpegPCMAudio(
        executable="ffmpeg",
        source=str(temp_file),
        options="-vn"
    )

    def after_playing(error):
        try:
            if error:
                print(f"Playback error: {error}")
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
        except Exception as cleanup_error:
            print(f"Cleanup error: {cleanup_error}")

    vc.play(audio_source, after=after_playing)
    await ctx.send(f"Speaking with **{alias}**.")


# =========================
# START
# =========================
if __name__ == "__main__":
    if DISCORD_BOT_TOKEN == "YOUR_DISCORD_BOT_TOKEN" or ELEVENLABS_API_KEY == "YOUR_ELEVENLABS_API_KEY":
        raise RuntimeError("Set your DISCORD_BOT_TOKEN and ELEVENLABS_API_KEY in the script first.")

    bot.run(DISCORD_BOT_TOKEN)