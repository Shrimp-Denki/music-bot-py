from __future__ import annotations
import os, asyncio, logging, contextlib
from collections import deque
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp as youtube_dl

# ──────────────────────  ENV / CLUSTER  ──────────────────────
load_dotenv()
PREFIX          = os.getenv("PREFIX", "h.")
BOT_TOKENS      = [t.strip() for t in os.getenv("BOT_TOKENS", "").split(',') if t.strip()]
CLUSTER_ID      = int(os.getenv("CLUSTER_ID", "0"))
TOTAL_CLUSTERS  = int(os.getenv("TOTAL_CLUSTERS", "1"))

if not BOT_TOKENS:
    raise SystemExit("❌  BOT_TOKENS is empty – set it in .env or compose file")
if CLUSTER_ID >= len(BOT_TOKENS):
    raise SystemExit("❌  CLUSTER_ID out of range for BOT_TOKENS list")

TOKEN = BOT_TOKENS[CLUSTER_ID]

# ──────────────────────  LOGGING  ──────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(f"cluster-{CLUSTER_ID}")

# ──────────────────────  yt-dlp & ffmpeg  ──────────────────────
YTDL = youtube_dl.YoutubeDL({
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch",
    "ignoreerrors": True,
    "source_address": "0.0.0.0",
    "retries": 3,
})

FFMPEG_OPTS = {
    "before_options": "-nostdin -reconnect 1 -reconnect_delay_max 5",
    "options": "-vn -loglevel error",
}

async def fetch_info(query: str) -> dict | None:
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, lambda: YTDL.extract_info(query, download=False))
    if not info:
        return None
    if "entries" in info:
        info = next((e for e in info["entries"] if e), None)
    return info

# ──────────────────────  STATE  ──────────────────────
queues: dict[int, deque]       = {}
now_playing: dict[int, dict]   = {}
last_use: dict[int, datetime]  = {}
IDLE_TIMEOUT = 180  # seconds

# ──────────────────────  HELPERS  ──────────────────────

def _key(ctx: commands.Context) -> int:
    return ctx.author.voice.channel.id if ctx.author.voice else ctx.guild.id

def _queue(k: int) -> deque:
    return queues.setdefault(k, deque())

async def _ensure_vc(ctx: commands.Context) -> discord.VoiceClient | None:
    if not ctx.author.voice:
        await ctx.reply("🔈 Hãy tham gia voice channel trước!")
        return None
    vc = ctx.voice_client
    if not vc:
        vc = await ctx.author.voice.channel.connect(timeout=10)
    elif vc.channel != ctx.author.voice.channel:
        await vc.move_to(ctx.author.voice.channel)
    return vc

async def _send_now_playing(ctx: commands.Context, info: dict):
    embed = (
        discord.Embed(
            title="Now Playing",
            url=info.get("webpage_url"),
            description=f"**{info['title']}**\n👤 {info.get('uploader')}",
            color=0x00b0f4,
        )
        .set_thumbnail(url=info.get("thumbnail"))
        .add_field(name="⏱ Duration", value=f"{info['duration']//60}:{info['duration']%60:02d}")
    )
    await ctx.send(embed=embed)

async def _play_next(key: int, ctx: commands.Context):
    q = _queue(key)
    if not q:
        return
    info = q.popleft()
    if not info.get("url", "").startswith(("http", "https")):
        info["url"] = info["webpage_url"]
    src = discord.FFmpegPCMAudio(info["url"], **FFMPEG_OPTS)
    vc = ctx.voice_client
    if not vc:
        return
    now_playing[key] = info
    vc.play(src, after=lambda _: ctx.bot.loop.call_soon_threadsafe(asyncio.create_task, _play_next(key, ctx)))
    await _send_now_playing(ctx, info)

# ──────────────────────  BOT SETUP  ──────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)
# only cluster 0 retains help command
if CLUSTER_ID != 0:
    bot.help_command = None

# schedule idle worker
async def idle_worker():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.utcnow()
        for k, last in list(last_use.items()):
            if (now - last).total_seconds() > IDLE_TIMEOUT:
                vc = discord.utils.get(bot.voice_clients, channel__id=k)
                if vc:
                    with contextlib.suppress(discord.DiscordException):
                        await vc.disconnect(force=True)
                queues.pop(k, None)
                last_use.pop(k, None)
                log.info("Auto-disconnected idle channel %s", k)
        await asyncio.sleep(60)

@bot.event
async def on_ready():
    if not hasattr(bot, "idle_task"):
        bot.idle_task = bot.loop.create_task(idle_worker())
    log.info("Cluster %s/%s online as %s", CLUSTER_ID, TOTAL_CLUSTERS - 1, bot.user)

# ──────────────────────  COMMANDS  ──────────────────────

@bot.command(help="Phát bài hát hoặc URL")
async def play(ctx: commands.Context, *, query: str):
    if _key(ctx) % TOTAL_CLUSTERS != CLUSTER_ID:
        return
    vc = await _ensure_vc(ctx)
    if not vc:
        return
    info = await fetch_info(query)
    if not info:
        return await ctx.reply("❌ Không tìm thấy bài.")
    _queue(_key(ctx)).append(info)
    await ctx.reply(f"✅ Đã thêm **{info['title']}**.")
    last_use[_key(ctx)] = datetime.utcnow()
    if not vc.is_playing():
        await _play_next(_key(ctx), ctx)

@bot.command(help="Bỏ qua bài hiện tại")
async def skip(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.message.add_reaction("⏭️")
    else:
        await ctx.reply("⏹ Không có gì để skip.")

@bot.command(help="Tạm dừng phát")
async def pause(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.message.add_reaction("⏸️")
    else:
        await ctx.reply("❌ Không có gì đang phát.")

@bot.command(help="Tiếp tục phát")
async def resume(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.message.add_reaction("▶️")
    else:
        await ctx.reply("❌ Nhạc chưa bị tạm dừng.")

@bot.command(aliases=["np"], help="Hiển thị bài đang phát")
async def nowplaying(ctx: commands.Context):
    if _key(ctx) % TOTAL_CLUSTERS != CLUSTER_ID:
        return
    info = now_playing.get(_key(ctx))
    if not info:
        return await ctx.reply("🙅‍♂️ Không có bài nào đang phát.")
    await _send_now_playing(ctx, info)

@bot.command(name="clear", help="Xoá hàng chờ và dừng phát")
async def clearqueue(ctx: commands.Context):
    q = _queue(_key(ctx))
    q.clear()
    if ctx.voice_client:
        ctx.voice_client.stop()
    await ctx.reply("🗑️ Đã xoá hàng chờ.")

@bot.command(help="Rời voice và xoá queue")
async def leave(ctx: commands.Context):
    if ctx.voice_client:
        await ctx.voice_client.disconnect(force=True)
    queues.pop(_key(ctx), None)
    await ctx.message.add_reaction("👋")

# ──────────────────────  RUN BOT  ──────────────────────
if __name__ == "__main__":
    bot.run(TOKEN, reconnect=True)
