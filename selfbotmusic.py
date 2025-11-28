import asyncio
import discord
import os
import logging
import yt_dlp as youtube_dl
from threading import Thread
from flask import Flask
from discord.ext import commands

# ========== CONFIGURATION ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Options YouTube-DL
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': True
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# ========== SERVEUR WEB KEEP-ALIVE ==========
app = Flask(__name__)

@app.route('/')
def home():
    return "🎵 Music Selfbot actif !"

def run_flask():
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()
    logger.info("🌐 Serveur web démarré sur le port 8080")

# ========== SOURCE AUDIO ==========
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

# ========== MUSIC SELFBOT ==========
class MusicSelfbot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents, self_bot=True)
        
        self.queue = []
        self.current_song = None
        self.voice_client = None
        self.loop_mode = False
        # 🔧 CHANGE TON PREFIX ICI 🔧
        self.prefix = os.getenv("BOT_PREFIX", "!")  # Par défaut "!" si pas défini

    async def on_ready(self):
        logger.info(f"✅ Connecté en tant que {self.user} (ID: {self.user.id})")
        logger.info(f"🎵 Commandes: {self.prefix}play, {self.prefix}skip, {self.prefix}stop, {self.prefix}queue, {self.prefix}loop")

    async def on_message(self, message):
        # Ignore les messages qui ne sont pas de l'utilisateur
        if message.author != self.user:
            return

        content = message.content.strip()
        
        # Commande !play <url ou recherche>
        if content.startswith(f"{self.prefix}play "):
            query = content[len(f"{self.prefix}play "):]
            await self.play_music(message, query)
        
        # Commande !join - Rejoint le canal vocal
        elif content == f"{self.prefix}join":
            await self.join_voice(message)
        
        # Commande !leave - Quitte le canal vocal
        elif content == f"{self.prefix}leave":
            await self.leave_voice(message)
        
        # Commande !skip - Passe à la chanson suivante
        elif content == f"{self.prefix}skip":
            await self.skip_song(message)
        
        # Commande !stop - Arrête la musique
        elif content == f"{self.prefix}stop":
            await self.stop_music(message)
        
        # Commande !queue - Affiche la file d'attente
        elif content == f"{self.prefix}queue":
            await self.show_queue(message)
        
        # Commande !loop - Active/désactive la boucle
        elif content == f"{self.prefix}loop":
            await self.toggle_loop(message)
        
        # Commande !volume <0-100>
        elif content.startswith(f"{self.prefix}volume "):
            try:
                volume = int(content.split()[1])
                await self.set_volume(message, volume)
            except (IndexError, ValueError):
                await message.edit(content="❌ Usage: !volume <0-100>")
                await asyncio.sleep(3)
                await message.delete()
        
        # Commande !help
        elif content == f"{self.prefix}help":
            await self.show_help(message)

    async def join_voice(self, message):
        """Rejoint le canal vocal de l'utilisateur"""
        if message.guild and message.author.voice:
            channel = message.author.voice.channel
            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.move_to(channel)
            else:
                self.voice_client = await channel.connect()
            await message.edit(content=f"✅ Connecté à **{channel.name}**")
            await asyncio.sleep(2)
            await message.delete()
        else:
            await message.edit(content="❌ Tu dois être dans un canal vocal !")
            await asyncio.sleep(3)
            await message.delete()

    async def leave_voice(self, message):
        """Quitte le canal vocal"""
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect()
            self.voice_client = None
            self.queue = []
            self.current_song = None
            await message.edit(content="👋 Déconnecté du canal vocal")
            await asyncio.sleep(2)
            await message.delete()
        else:
            await message.edit(content="❌ Pas connecté à un canal vocal")
            await asyncio.sleep(3)
            await message.delete()

    async def play_music(self, message, query):
        """Joue une chanson depuis YouTube"""
        if not message.guild or not message.author.voice:
            await message.edit(content="❌ Tu dois être dans un canal vocal !")
            await asyncio.sleep(3)
            await message.delete()
            return

        # Rejoindre le canal si pas déjà connecté
        if not self.voice_client or not self.voice_client.is_connected():
            await self.join_voice(message)

        try:
            await message.edit(content=f"🔍 Recherche: **{query}**...")
            
            # Télécharger les infos
            player = await YTDLSource.from_url(query, loop=self.loop, stream=True)
            
            # Ajouter à la queue
            self.queue.append(player)
            
            if not self.voice_client.is_playing():
                await message.edit(content=f"🎵 Lecture: **{player.title}**")
                await asyncio.sleep(3)
                await message.delete()
                await self.play_next()
            else:
                await message.edit(content=f"➕ Ajouté à la queue: **{player.title}**")
                await asyncio.sleep(3)
                await message.delete()
                
        except Exception as e:
            logger.error(f"Erreur lecture: {e}")
            await message.edit(content=f"❌ Erreur: {str(e)}")
            await asyncio.sleep(5)
            await message.delete()

    async def play_next(self):
        """Joue la prochaine chanson dans la queue"""
        if not self.queue:
            self.current_song = None
            return

        self.current_song = self.queue.pop(0)
        
        def after_playing(error):
            if error:
                logger.error(f"Erreur playback: {error}")
            
            # Si mode loop, remettre la chanson dans la queue
            if self.loop_mode and self.current_song:
                self.queue.insert(0, self.current_song)
            
            # Jouer la suivante
            asyncio.run_coroutine_threadsafe(self.play_next(), self.loop)

        self.voice_client.play(self.current_song, after=after_playing)
        logger.info(f"🎵 Lecture: {self.current_song.title}")

    async def skip_song(self, message):
        """Passe à la chanson suivante"""
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()
            await message.edit(content="⏭️ Chanson passée")
            await asyncio.sleep(2)
            await message.delete()
        else:
            await message.edit(content="❌ Aucune musique en cours")
            await asyncio.sleep(3)
            await message.delete()

    async def stop_music(self, message):
        """Arrête la musique et vide la queue"""
        if self.voice_client:
            self.queue = []
            self.current_song = None
            self.voice_client.stop()
            await message.edit(content="⏹️ Musique arrêtée")
            await asyncio.sleep(2)
            await message.delete()
        else:
            await message.edit(content="❌ Aucune musique en cours")
            await asyncio.sleep(3)
            await message.delete()

    async def show_queue(self, message):
        """Affiche la file d'attente"""
        if not self.queue and not self.current_song:
            await message.edit(content="📭 La queue est vide")
            await asyncio.sleep(3)
            await message.delete()
            return

        queue_text = "🎵 **File d'attente:**\n\n"
        
        if self.current_song:
            queue_text += f"▶️ En cours: **{self.current_song.title}**\n\n"
        
        if self.queue:
            for i, song in enumerate(self.queue[:10], 1):
                queue_text += f"{i}. {song.title}\n"
            
            if len(self.queue) > 10:
                queue_text += f"\n... et {len(self.queue) - 10} autres"
        
        await message.edit(content=queue_text)
        await asyncio.sleep(10)
        await message.delete()

    async def toggle_loop(self, message):
        """Active/désactive le mode boucle"""
        self.loop_mode = not self.loop_mode
        status = "activé ✅" if self.loop_mode else "désactivé ❌"
        await message.edit(content=f"🔁 Mode loop {status}")
        await asyncio.sleep(3)
        await message.delete()

    async def set_volume(self, message, volume):
        """Change le volume (0-100)"""
        if not 0 <= volume <= 100:
            await message.edit(content="❌ Volume doit être entre 0 et 100")
            await asyncio.sleep(3)
            await message.delete()
            return

        if self.voice_client and self.voice_client.source:
            self.voice_client.source.volume = volume / 100
            await message.edit(content=f"🔊 Volume: {volume}%")
            await asyncio.sleep(2)
            await message.delete()
        else:
            await message.edit(content="❌ Aucune musique en cours")
            await asyncio.sleep(3)
            await message.delete()

    async def show_help(self, message):
        """Affiche l'aide"""
        help_text = """
🎵 **Commandes du Music Selfbot:**

`!play <url/recherche>` - Joue une chanson
`!join` - Rejoint ton canal vocal
`!leave` - Quitte le canal vocal
`!skip` - Passe la chanson
`!stop` - Arrête la musique
`!queue` - Affiche la file d'attente
`!loop` - Active/désactive la boucle
`!volume <0-100>` - Change le volume
`!help` - Affiche cette aide
"""
        await message.edit(content=help_text)
        await asyncio.sleep(15)
        await message.delete()


# ========== MAIN ==========
async def main():
    token = os.getenv("DISCORD_TOKEN")
    
    if not token:
        logger.error("❌ Variable DISCORD_TOKEN manquante")
        logger.info("💡 Ajoute ton token dans les Secrets")
        return

    logger.warning("⚠️ AVERTISSEMENT: Les selfbots violent les CGU de Discord")
    logger.warning("⚠️ Ton compte peut être BANNI définitivement")
    logger.info("💡 Pour un usage légitime, utilise un bot officiel")
    
    keep_alive()
    
    client = MusicSelfbot()
    
    try:
        await client.start(token)
    except KeyboardInterrupt:
        logger.info("⏹️ Arrêt demandé")
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Programme arrêté")
