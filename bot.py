from __future__ import annotations
import os
import asyncio
import logging

import nextcord
from nextcord.ext import commands
from dotenv import load_dotenv
import mafic

load_dotenv()

PREFIX = os.getenv("PREFIX", "h.")
BOT_TOKENS = [t.strip() for t in os.getenv("BOT_TOKENS", "").split(',') if t.strip()]
CLUSTER_ID = int(os.getenv("CLUSTER_ID", "0"))

if not BOT_TOKENS:
    raise SystemExit("BOT_TOKENS not configured")
if CLUSTER_ID >= len(BOT_TOKENS):
    raise SystemExit("CLUSTER_ID out of range")
TOKEN = BOT_TOKENS[CLUSTER_ID]

LAVALINK_HOST = os.getenv("LAVALINK_HOST", "localhost")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
LAVALINK_SECURE = os.getenv("LAVALINK_SECURE", "false").lower() == "true"

logging.basicConfig(level=logging.INFO)

intents = nextcord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)


class QueuePlayer(mafic.Player):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.queue: asyncio.Queue[mafic.Track] = asyncio.Queue()

    async def queue_next(self):
        if self.queue.empty():
            return
        track = await self.queue.get()
        await self.play(track)

    async def on_track_end(self, event: mafic.TrackEndEvent):
        await self.queue_next()


@bot.event
async def on_ready():
    if not mafic.NodePool.nodes:
        mafic.NodePool.create_node(
            bot=bot,
            host=LAVALINK_HOST,
            port=LAVALINK_PORT,
            password=LAVALINK_PASSWORD,
            https=LAVALINK_SECURE,
            label="main",
            default_player=QueuePlayer,
        )
    logging.info("Logged in as %s", bot.user)


def get_player(guild_id: int) -> QueuePlayer | None:
    node = mafic.NodePool.get_node()
    return node.get_player(guild_id)


async def ensure_player(ctx: commands.Context) -> QueuePlayer | None:
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("You must join a voice channel first.")
        return None

    player = get_player(ctx.guild.id)
    if not player:
        await ctx.author.voice.channel.connect(cls=QueuePlayer)
        player = get_player(ctx.guild.id)
    return player


@bot.command(help="Play a track or search query")
async def play(ctx: commands.Context, *, query: str):
    player = await ensure_player(ctx)
    if not player:
        return

    tracks = await mafic.NodePool.get_node().fetch_tracks(query)
    if not tracks:
        await ctx.send("No tracks found.")
        return

    track = tracks[0]
    if player.current:
        await player.queue.put(track)
        await ctx.send(f"Queued: {track.title}")
    else:
        await player.play(track)
        await ctx.send(f"Now playing: {track.title}")


@bot.command(help="Skip the current track")
async def skip(ctx: commands.Context):
    player = get_player(ctx.guild.id)
    if player:
        await player.stop()
        await ctx.message.add_reaction("⏭️")


@bot.command(help="Pause playback")
async def pause(ctx: commands.Context):
    player = get_player(ctx.guild.id)
    if player and not player.paused:
        await player.set_pause(True)
        await ctx.message.add_reaction("⏸️")


@bot.command(help="Resume playback")
async def resume(ctx: commands.Context):
    player = get_player(ctx.guild.id)
    if player and player.paused:
        await player.set_pause(False)
        await ctx.message.add_reaction("▶️")


@bot.command(help="Leave the voice channel")
async def leave(ctx: commands.Context):
    player = get_player(ctx.guild.id)
    if player:
        await player.disconnect()
    if ctx.voice_client:
        await ctx.voice_client.disconnect(force=True)


@bot.command(help="Show the queue")
async def queue(ctx: commands.Context):
    player = get_player(ctx.guild.id)
    if not player:
        await ctx.send("Not connected.")
        return

    description = ""
    if player.current:
        description += f"Now: {player.current.title}\n"
    if player.queue.empty():
        description += "Queue empty."
    else:
        for idx, item in enumerate(list(player.queue._queue), start=1):
            description += f"{idx}. {item.title}\n"
    embed = nextcord.Embed(title="Queue", description=description)
    await ctx.send(embed=embed)


@bot.command(help="Bot latency")
async def ping(ctx: commands.Context):
    await ctx.send(f"{bot.latency*1000:.0f} ms")


if __name__ == "__main__":
    bot.run(TOKEN)
