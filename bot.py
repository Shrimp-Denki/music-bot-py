from __future__ import annotations
import os, asyncio, logging, contextlib, sqlite3, re
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse

import random
import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

# ╭─ ENV ─╮
load_dotenv()
PREFIX           = "h."
BOT_TOKENS       = [t.strip() for t in os.getenv("BOT_TOKENS", "").split(',') if t.strip()]
CLUSTER_ID       = int(os.getenv("CLUSTER_ID", "0"))
TOTAL_CLUSTERS   = int(os.getenv("TOTAL_CLUSTERS", "1"))

if not BOT_TOKENS: raise SystemExit("❌  Chưa thiết lập BOT_TOKENS")
if CLUSTER_ID >= len(BOT_TOKENS): raise SystemExit("❌  CLUSTER_ID vượt quá số token")
TOKEN = BOT_TOKENS[CLUSTER_ID]

# ╭─ LOG ─╮
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(f"cluster-{CLUSTER_ID}")

# ╭─ DB Owner ─╮
conn = sqlite3.connect("bot.db", check_same_thread=False)
with conn:
    conn.execute("""CREATE TABLE IF NOT EXISTS owners
                    (channel_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL);""")
def set_owner(cid:int, uid:int):   conn.execute("REPLACE INTO owners VALUES (?,?)", (cid, uid))
def get_owner(cid:int)->int|None:  r=conn.execute("SELECT owner_id FROM owners WHERE channel_id=?", (cid,)).fetchone(); return r[0] if r else None
def clear_owner(cid:int):          conn.execute("DELETE FROM owners WHERE channel_id=?", (cid,))

# ╭─ yt-dlp & FFmpeg ─╮
YTDL = yt_dlp.YoutubeDL({
    "format": "bestaudio/best",
    "quiet": True,
    "default_search": "scsearch",
    # "geo_bypass": True, 
    # "geo_bypass_country": "VN",
    "extractflat": False,
    "extract_flat": False
})
FFMPEG_OPTS = {"before_options":"-nostdin -reconnect 1 -reconnect_delay_max 5",
               "options":"-vn -loglevel error"}

# ╭─ Music Platform Configuration ─╮
PLATFORM_CONFIG = {
    'youtube': {
        'search': 'ytsearch',
        'domains': ['youtube.com', 'youtu.be', 'music.youtube.com'],
        'emoji': '📺',
        'name': 'YouTube',
        'extractors': ['youtube', 'youtube:search']
    },
    'soundcloud': {
        'search': 'scsearch',
        'domains': ['soundcloud.com'],
        'emoji': '🟠',
        'name': 'SoundCloud',
        'extractors': ['soundcloud', 'soundcloud:search']
    },
    'spotify': {
        'search': 'ytsearch',  # Spotify tracks will be searched on YouTube
        'domains': ['spotify.com', 'open.spotify.com'],
        'emoji': '🟢',
        'name': 'Spotify',
        'extractors': ['spotify']
    },
    'applemusic': {
        'search': 'ytsearch',  # Apple Music tracks will be searched on YouTube
        'domains': ['music.apple.com', 'itunes.apple.com'],
        'emoji': '🍎',
        'name': 'Apple Music',
        'extractors': ['applemusic']
    },
    'deezer': {
        'search': 'ytsearch',  # Deezer tracks will be searched on YouTube
        'domains': ['deezer.com'],
        'emoji': '🔵',
        'name': 'Deezer',
        'extractors': ['deezer']
    },
    'yandex': {
        'search': 'ytsearch',  # Yandex Music tracks will be searched on YouTube
        'domains': ['music.yandex.ru', 'music.yandex.com'],
        'emoji': '🔴',
        'name': 'Yandex Music',
        'extractors': ['yandexmusic']
    }
}

def parse_query(query: str) -> tuple[str, str]:
    """Parse query to extract platform and search term"""
    # Check for platform prefix (e.g., "soundcloud:phép màu")
    if ':' in query:
        parts = query.split(':', 1)
        if len(parts) == 2:
            platform = parts[0].lower().strip()
            search_term = parts[1].strip()
            
            # Map platform aliases
            platform_map = {
                'yt': 'youtube',
                'sc': 'soundcloud',
                'apple': 'applemusic',
                'am': 'applemusic',
                'yandex': 'yandex',
                'ym': 'yandex'
            }
            
            platform = platform_map.get(platform, platform)
            
            if platform in PLATFORM_CONFIG:
                return platform, search_term
    
    # No platform specified, detect from URL
    query_lower = query.lower()
    for platform, config in PLATFORM_CONFIG.items():
        for domain in config['domains']:
            if domain in query_lower:
                return platform, query
    
    # Default to YouTube
    return 'youtube', query

def detect_platform_from_url(url: str) -> str:
    """Detect platform from URL"""
    url_lower = url.lower()
    for platform, config in PLATFORM_CONFIG.items():
        for domain in config['domains']:
            if domain in url_lower:
                return platform
    return 'youtube'

def _blocking_fetch(q: str):
    """Fetch music info with platform detection"""
    platform = 'youtube'  # Default platform
    search_term = q  # Default search term
    
    try:
        platform, search_term = parse_query(q)
        # Use the log variable from the main module
        import logging
        log = logging.getLogger(f"cluster-{os.getenv('CLUSTER_ID', '0')}")
        log.info(f"Platform: {platform}, Search: {search_term}")
        
        # Get YTDL from main module - we'll need to pass it as parameter or make it global
        # For now, create a local instance
        import yt_dlp
        
        ytdl_base_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "default_search": "ytsearch",
            "extractflat": False,
            "extract_flat": False,
            "no_warnings": True,
            "ignoreerrors": True
        }
        
        # Configure yt-dlp based on platform
        ytdl_opts = ytdl_base_opts.copy()
        
        # Set the correct default_search for each platform
        if platform == 'soundcloud':
            ytdl_opts['default_search'] = 'scsearch'
        elif platform == 'youtube':
            ytdl_opts['default_search'] = 'ytsearch'
        else:
            ytdl_opts['default_search'] = 'scsearch'
            #taserandom = random.randint(1,2)
            #if taserandom == 1:
            #    ytdl_opts['default_search'] = 'scsearch'
            #else:
            #    ytdl_opts['default_search'] = 'ytsearch'
        
        # Create temporary YTDL instance with updated options
        temp_ytdl = yt_dlp.YoutubeDL(ytdl_opts)
        
        # Handle special platforms that need metadata extraction
        if platform in ['spotify', 'applemusic', 'deezer', 'yandex'] and search_term.startswith(('http://', 'https://')):
            try:
                # Try to extract metadata from the URL
                metadata = temp_ytdl.extract_info(search_term, download=False)
                if metadata:
                    # Create search query from metadata
                    title = metadata.get('title', '')
                    artist = metadata.get('artist', '') or metadata.get('uploader', '')
                    album = metadata.get('album', '')
                    
                    # Build search query
                    search_parts = [title]
                    if artist:
                        search_parts.append(artist)
                    if album and len(search_parts) < 2:
                        search_parts.append(album)
                    
                    # Use ytsearch for these platforms
                    search_query = ' '.join(search_parts)
                    log.info(f"Converted {platform} URL to search: {search_query}")
                    
                    # Search on YouTube with ytsearch
                    youtube_ytdl = yt_dlp.YoutubeDL({**ytdl_base_opts, 'default_search': 'ytsearch'})
                    data = youtube_ytdl.extract_info(search_query, download=False)
                else:
                    # Fallback to original query
                    data = temp_ytdl.extract_info(search_term, download=False)
            except Exception as e:
                log.warning(f"Failed to extract from {platform}: {e}")
                # Fallback to YouTube search
                youtube_ytdl = yt_dlp.YoutubeDL({**ytdl_base_opts, 'default_search': 'ytsearch'})
                data = youtube_ytdl.extract_info(search_term, download=False)
        else:
            # For direct search queries or URLs
            if search_term.startswith(('http://', 'https://')):
                # It's a URL, extract directly
                data = temp_ytdl.extract_info(search_term, download=False)
            else:
                # It's a search term, use the configured search prefix
                data = temp_ytdl.extract_info(search_term, download=False)
        
        # Process results
        if data and "entries" in data:
            results = [entry for entry in data["entries"] if entry]
            # Tag results with platform info
            for result in results:
                if result:
                    result['detected_platform'] = platform
            return results
        elif data:
            data['detected_platform'] = platform
            return [data]
        
    except Exception as e:
        # Handle the case where log might not be defined
        try:
            log.error(f"Error fetching from {platform}: {e}")
        except:
            print(f"Error fetching from {platform}: {e}")
        
        try:
            # Fallback to default YouTube search
            search_term_fallback = search_term
            # Remove any platform prefix from the fallback search
            if ':' in search_term_fallback and not search_term_fallback.startswith(('http://', 'https://')):
                search_term_fallback = search_term_fallback.split(':', 1)[1]
            
            fallback_ytdl = yt_dlp.YoutubeDL({
                "format": "bestaudio/best",
                "quiet": True,
                "default_search": "scsearch",
                "extractflat": False,
                "extract_flat": False,
                "no_warnings": True,
                "ignoreerrors": True
            })
            data = fallback_ytdl.extract_info(search_term_fallback, download=False)
            if data and "entries" in data:
                results = [entry for entry in data["entries"] if entry]
                for result in results:
                    if result:
                        result['detected_platform'] = 'youtube'
                return results
            elif data:
                data['detected_platform'] = 'youtube'
                return [data]
        except Exception as fallback_error:
            try:
                log.error(f"Fallback search also failed: {fallback_error}")
            except:
                print(f"Fallback search also failed: {fallback_error}")
    
    return []

async def fetch_info(q:str): 
    result = await asyncio.get_running_loop().run_in_executor(None, _blocking_fetch, q)
    return result if isinstance(result, list) else [result] if result else []

# ╭─ STATE ─╮
queues:dict[int,deque]={}
history:dict[int,deque]={}
now_playing:dict[int,dict]={}
last_use:dict[int,datetime]={}
idle_timers:dict[int,asyncio.Task]={}
IDLE_TIMEOUT=60  # 1 minute of no music playing
VOICE_TIMEOUT=1800  # 30 minutes of no activity

def _key(ctx): return ctx.author.voice.channel.id if ctx.author.voice else ctx.guild.id
def _queue(k): return queues.setdefault(k,deque())
def _history(k): return history.setdefault(k,deque(maxlen=20))

# ╭─ Auto-disconnect when idle ─╮
async def idle_disconnect(key: int, delay: int = IDLE_TIMEOUT):
    await asyncio.sleep(delay)
    vc = discord.utils.get(bot.voice_clients, channel__id=key)
    if vc and not vc.is_playing() and not vc.is_paused():
        try:
            await vc.disconnect(force=True)
            queues.pop(key, None)
            history.pop(key, None)
            now_playing.pop(key, None)
            last_use.pop(key, None)
            clear_owner(key)
            log.info(f"Auto-disconnected from channel {key} due to inactivity")
        except:
            pass
    idle_timers.pop(key, None)

def start_idle_timer(key: int):
    # Cancel existing timer
    if key in idle_timers:
        idle_timers[key].cancel()
    # Start new timer
    idle_timers[key] = bot.loop.create_task(idle_disconnect(key))

def cancel_idle_timer(key: int):
    if key in idle_timers:
        idle_timers[key].cancel()
        idle_timers.pop(key, None)

# ╭─ Queue Pagination ─╮
class QueueView(discord.ui.View):
    def __init__(self, queue_list: list, key: int, per_page: int = 15):
        super().__init__(timeout=300)
        self.queue_list = queue_list
        self.key = key
        self.per_page = per_page
        self.current_page = 0
        self.max_page = (len(queue_list) - 1) // per_page
        
        # Update button states
        self.update_buttons()
    
    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.max_page
    
    def get_embed(self):
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_items = self.queue_list[start:end]
        
        embed = discord.Embed(
            title="🎵 Hàng chờ phát nhạc",
            color=0x0061ff,
            description=f"Trang {self.current_page + 1}/{self.max_page + 1} | Tổng: {len(self.queue_list)} bài"
        )
        
        if not page_items:
            embed.add_field(name="Trống", value="Không có bài nào trong hàng chờ", inline=False)
        else:
            queue_text = ""
            for i, track in enumerate(page_items, start + 1):
                duration = int(track.get('duration', 0))
                m, s = divmod(duration, 60)
                duration_str = f"{m}:{s:02d}" if duration > 0 else "N/A"
                queue_text += f"`{i}.` **{track['title'][:50]}{'...' if len(track['title']) > 50 else ''}**\n"
                queue_text += f"    👤 {track.get('uploader', 'Unknown')[:30]} | ⏱ {duration_str}\n\n"
            
            embed.description += f"\n\n{queue_text}"
        
        return embed
    
    @discord.ui.button(label="◀️ Trước", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
    
    @discord.ui.button(label="▶️ Sau", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

# ╭─ Discord UI Buttons ─╮
class MusicControls(discord.ui.View):
    def __init__(self, key:int, *, timeout:float|None=1800, persistent=False):
        super().__init__(timeout=None if persistent else timeout)
        self.key=key; self.paused=False; self.loop=False

    @discord.ui.button(label="⏸️", style=discord.ButtonStyle.secondary, custom_id="btn_pause")
    async def pause_btn(self, intr:discord.Interaction, btn:discord.ui.Button):
        vc=intr.guild.voice_client
        if not vc: return await intr.response.defer()
        if self.paused: 
            vc.resume(); btn.label="⏸️"
            cancel_idle_timer(self.key)
        else: 
            vc.pause(); btn.label="▶️"
            start_idle_timer(self.key)
        self.paused=not self.paused
        await intr.response.edit_message(view=self)

    @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary, custom_id="btn_prev")
    async def prev_btn(self, intr, _):
        hist=_history(self.key)
        if not hist: return await intr.response.defer()
        _queue(self.key).appendleft(hist.pop())
        intr.guild.voice_client.stop(); await intr.response.defer()

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary, custom_id="btn_skip")
    async def skip_btn(self, intr,_):
        intr.guild.voice_client and intr.guild.voice_client.stop()
        await intr.response.defer()

    @discord.ui.button(label="⏹️", style=discord.ButtonStyle.danger, custom_id="btn_stop")
    async def stop_btn(self, intr,_):
        vc = intr.guild.voice_client
        if vc:
            await vc.disconnect(force=True)
            key = self.key
            queues.pop(key, None)
            history.pop(key, None)
            now_playing.pop(key, None)
            last_use.pop(key, None)
            cancel_idle_timer(key)
            clear_owner(key)
        await intr.response.defer()

    @discord.ui.button(label="🔁", style=discord.ButtonStyle.secondary, custom_id="btn_loop")
    async def loop_btn(self, intr, btn):
        self.loop=not self.loop
        btn.style = discord.ButtonStyle.success if self.loop else discord.ButtonStyle.secondary
        await intr.response.edit_message(view=self)

# ╭─ Bot subclass (to add persistent view) ─╮
class MyBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(MusicControls(key=0, persistent=True))

intents=discord.Intents.default(); intents.message_content=True; intents.guilds=True; intents.voice_states=True
bot=MyBot(command_prefix=PREFIX, intents=intents)
if CLUSTER_ID: bot.help_command=None

# ╭─ helpers ─╮
async def _ensure_vc(ctx):
    if not ctx.author.voice: return await ctx.reply("🔈 Vào voice channel trước!")
    vc = ctx.voice_client or await ctx.author.voice.channel.connect(timeout=10)
    if vc.channel != ctx.author.voice.channel: await vc.move_to(ctx.author.voice.channel)
    return vc

async def _send_np(ctx, info, key):
    dur=int(info.get('duration',0)); m,s=divmod(dur,60)
    
    # Detect platform
    url = info.get("webpage_url", "")
    platform = "🎵"
    if "youtube.com" in url or "youtu.be" in url:
        platform = "📺 YouTube"
    elif "soundcloud.com" in url:
        platform = "🟠 SoundCloud"
    elif "spotify.com" in url:
        platform = "🟢 Spotify"
    
    emb = (discord.Embed(title="Đang phát",url=info.get("webpage_url"),
            description=f"**{info['title']}**\n👤 {info.get('uploader','?')}\n{platform}",
            color=0x0061ff)
            .set_thumbnail(url=info.get("thumbnail"))
            .add_field(name="⏱ Thời lượng", value=f"{m}:{s:02d}"))
    
    queue_size = len(_queue(key))
    if queue_size > 0:
        emb.add_field(name="📋 Hàng chờ", value=f"{queue_size} bài", inline=True)
    
    await ctx.send(embed=emb, view=MusicControls(key))

async def _next(key:int, ctx):
    q=_queue(key)
    if not q:
        start_idle_timer(key)
        return
    
    cancel_idle_timer(key)
    info=q.popleft(); info["url"]=info.get("url") or info["webpage_url"]
    vc=ctx.voice_client; src=discord.FFmpegPCMAudio(info["url"],**FFMPEG_OPTS)
    if key in now_playing: _history(key).append(now_playing[key])
    now_playing[key]=info; last_use[key]=datetime.now(timezone.utc)
    vc.play(src, after=lambda _:
        ctx.bot.loop.call_soon_threadsafe(asyncio.create_task,_next(key,ctx)))
    await _send_np(ctx, info, key)

# ╭─ idle worker (for empty voice channels) ─╮
async def idle_worker():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now=datetime.now(timezone.utc)
        for k,t in list(last_use.items()):
            vc=discord.utils.get(bot.voice_clients, channel__id=k)
            if vc and len(vc.channel.members)<=1:
                with contextlib.suppress(discord.DiscordException): 
                    await vc.disconnect(force=True)
                queues.pop(k,None); 
                history.pop(k,None)
                now_playing.pop(k,None)
                last_use.pop(k,None); 
                cancel_idle_timer(k)
                clear_owner(k); 
                continue
            if (now-t).total_seconds()>VOICE_TIMEOUT:
                with contextlib.suppress(discord.DiscordException): 
                    await vc.disconnect(force=True)
                queues.pop(k,None)
                history.pop(k,None)
                now_playing.pop(k,None)
                last_use.pop(k,None)
                cancel_idle_timer(k)
                clear_owner(k)
        await asyncio.sleep(30)

@bot.event
async def on_ready():
    if not hasattr(bot,"idle_task"): bot.idle_task=bot.loop.create_task(idle_worker())
    log.info("Cluster %s/%s online as %s", CLUSTER_ID, TOTAL_CLUSTERS-1, bot.user)

# ╭─ cluster helper ─╮
def cluster_check(ctx): return _key(ctx)%TOTAL_CLUSTERS==CLUSTER_ID

# ╭─ Commands ─╮
@bot.command(help="Phát bài (từ khoá/link) - Hỗ trợ YouTube, SoundCloud, Spotify")
async def play(ctx, *, query:str):
    if not cluster_check(ctx): return
    vc=await _ensure_vc(ctx)
    if not vc: 
        await ctx.reply("❌ Không join voice.")
        return
    
    # Show loading message for playlists
    loading_msg = None
    if any(platform in query.lower() for platform in ['playlist', 'album', 'set']):
        loading_msg = await ctx.reply("🔄 Đang tải playlist...")
    
    try:
        tracks = await fetch_info(query)
        if not tracks:
            if loading_msg: await loading_msg.edit(content="❌ Không tìm thấy.")
            else: await ctx.reply("❌ Không tìm thấy.")
            return
        
        key=_key(ctx)
        queue_obj = _queue(key)
        added_count = 0
        
        for track in tracks:
            if track:
                queue_obj.append(track)
                added_count += 1
        
        set_owner(key,ctx.author.id)
        last_use[key]=datetime.now(timezone.utc)
        
        if added_count == 1:
            if loading_msg: 
                await loading_msg.edit(content=f"✅ Đã thêm **{tracks[0]['title']}**.")
            else:
                await ctx.reply(f"✅ Đã thêm **{tracks[0]['title']}**.")
        else:
            if loading_msg:
                await loading_msg.edit(content=f"✅ Đã thêm {added_count} bài vào hàng chờ.")
            else:
                await ctx.reply(f"✅ Đã thêm {added_count} bài vào hàng chờ.")
        
        if not vc.is_playing() and not vc.is_paused(): 
            await _next(key, ctx)
            
    except Exception as e:
        log.error(f"Error in play command: {e}")
        if loading_msg: await loading_msg.edit(content="❌ Có lỗi xảy ra khi tải nhạc.")
        else: await ctx.reply("❌ Có lỗi xảy ra khi tải nhạc.")

@bot.command(help="Hiển thị hàng chờ phát nhạc", aliases=["q"])
async def queue(ctx):
    if not cluster_check(ctx): return
    
    key = _key(ctx)
    queue_list = list(_queue(key))
    
    if not queue_list:
        embed = discord.Embed(
            title="🎵 Hàng chờ phát nhạc",
            description="Hàng chờ trống",
            color=0x0061ff
        )
        await ctx.send(embed=embed)
        return
    
    view = QueueView(queue_list, key)
    await ctx.send(embed=view.get_embed(), view=view)

@bot.command(help="Bỏ qua bài")
async def skip(ctx):
    if cluster_check(ctx) and ctx.voice_client: 
        ctx.voice_client.stop(); 
        await ctx.message.add_reaction("⏭️")

@bot.command(help="Bài trước")
async def previous(ctx):
    if not cluster_check(ctx): return
    key=_key(ctx); hist=_history(key)
    if not hist: return await ctx.reply("❌ Không có bài trước!")
    _queue(key).appendleft(hist.pop()); 
    if ctx.voice_client: ctx.voice_client.stop()

@bot.command(help="Tạm dừng")
async def pause(ctx):
    if cluster_check(ctx) and ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause(); 
        start_idle_timer(_key(ctx))
        await ctx.message.add_reaction("⏸️")

@bot.command(help="Tiếp tục")
async def resume(ctx):
    if cluster_check(ctx) and ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume(); 
        cancel_idle_timer(_key(ctx))
        await ctx.message.add_reaction("▶️")

@bot.command(help="Đang phát", aliases=["np"])
async def nowplaying(ctx):
    if cluster_check(ctx): 
        info=now_playing.get(_key(ctx))
        if info: 
            await _send_np(ctx,info,_key(ctx))
        else:
            await ctx.reply("❌ Không có bài nào đang phát.")

@bot.command(name="clear", help="Xoá hàng chờ")
async def clearqueue(ctx):
    if cluster_check(ctx):
        key = _key(ctx)
        _queue(key).clear()
        await ctx.reply("🗑️ Đã xoá hàng chờ.")

@bot.command(help="Ping")
async def ping(ctx): 
    await ctx.reply(f"{bot.latency*1000:.0f} ms")

@bot.command(name="commands", help="Hiển thị danh sách lệnh")
async def commands_list(ctx):
    if not cluster_check(ctx): return
    
    embed = discord.Embed(
        title="🎵 Danh sách lệnh",
        description="Prefix: `" + PREFIX + "`",
        color=0x0061ff
    )
    
    commands_list = [
        ("`play` - Phát nhạc (YouTube/SoundCloud/Spotify)", "🎵"),
        ("`queue`, `q` - Hiển thị hàng chờ", "📋"),
        ("`nowplaying`, `np` - Bài đang phát", "▶️"),
        ("`skip` - Bỏ qua bài", "⏭️"),
        ("`previous` - Bài trước", "⏮️"),
        ("`pause` - Tạm dừng", "⏸️"),
        ("`resume` - Tiếp tục", "▶️"),
        ("`clear` - Xóa hàng chờ", "🗑️"),
        ("`ping` - Kiểm tra độ trễ", "🏓"),
        ("`commands` - Hiển thị lệnh này", "📝")
    ]
    
    for cmd, emoji in commands_list:
        embed.add_field(name=f"{emoji} {cmd.split(' - ')[0]}", value=cmd.split(' - ')[1], inline=True)
    
    embed.add_field(
        name="ℹ️ Lưu ý", 
        value="• Bot tự động rời kênh sau 1 phút không phát nhạc\n• Hỗ trợ playlist từ tất cả 3 nền tảng\n• Hàng chờ hiển thị 15 bài/trang", 
        inline=False
    )
    
    await ctx.send(embed=embed)

# ╭─ RUN ─╮
if __name__=="__main__":
    bot.run(TOKEN, reconnect=True)
