import gradio as gr
import os, re, tempfile, subprocess, urllib.request, time
from pathlib import Path

GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
RAPIDAPI_KEY   = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST  = "instagram-reels-downloader-api.p.rapidapi.com"
INSTAGRAM_COOKIES = os.environ.get("INSTAGRAM_COOKIES", "")

def _instagram_cookies_file():
    """Escribe las cookies de Instagram (secret INSTAGRAM_COOKIES) a un archivo temporal para yt-dlp."""
    if not INSTAGRAM_COOKIES.strip():
        return None
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, dir=tempfile.gettempdir())
    f.write(INSTAGRAM_COOKIES.strip())
    f.close()
    return f.name

import yt_dlp

def dl_instagram_rapidapi(url, tmpdir):
    """Descarga via RapidAPI Instagram Reels Downloader — siempre funciona."""
    import json, urllib.parse

    endpoint = f"https://{RAPIDAPI_HOST}/download"
    params = urllib.parse.urlencode({"url": url})
    req = urllib.request.Request(
        f"{endpoint}?{params}",
        headers={
            "X-RapidAPI-Key":  RAPIDAPI_KEY,
            "X-RapidAPI-Host": RAPIDAPI_HOST,
            "User-Agent": "Mozilla/5.0",
        }
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())

    print("RapidAPI response keys:", list(data.keys()) if isinstance(data, dict) else type(data))

    # Extraer URL de vídeo — varios formatos posibles
    video_url = None
    title = "Vídeo de Instagram"

    if isinstance(data, dict):
        d = data.get("data", data)
        title = (d.get("title", "") or "").replace("\n", " ").strip()[:80] or "Vídeo de Instagram"

        # La URL está en data.medias — buscar el item de tipo video
        for item in d.get("medias", []):
            if isinstance(item, dict) and item.get("type") == "video":
                v = item.get("url", "")
                if v:
                    video_url = v
                    break
        # Fallback: cualquier media con url que tenga mp4/fbcdn
        if not video_url:
            for item in d.get("medias", []):
                if isinstance(item, dict):
                    v = item.get("url", "")
                    if v and ("mp4" in v or "fbcdn" in v or "cdninstagram" in v):
                        video_url = v
                        break

    if not video_url:
        raise RuntimeError(f"RapidAPI no devolvió URL de vídeo. Respuesta: {str(data)[:300]}")

    mp4 = os.path.join(tmpdir, "video.mp4")
    req2 = urllib.request.Request(video_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req2, timeout=60) as r2:
        with open(mp4, "wb") as f:
            f.write(r2.read())

    mp3 = os.path.join(tmpdir, "audio.mp3")
    to_mp3(mp4, mp3)
    return (mp3 if os.path.exists(mp3) and os.path.getsize(mp3) > 1000 else mp4), title


def detect_platform(url):
    u = url.lower()
    if "instagram.com" in u or "instagr.am" in u: return "instagram"
    if "tiktok.com" in u or "vm.tiktok.com" in u: return "tiktok"
    if "youtube.com" in u or "youtu.be" in u:      return "youtube"
    if "twitter.com" in u or "x.com" in u:         return "twitter"
    return "unknown"

def find_audio(directory):
    for ext in ["mp3","m4a","wav","ogg","webm","mp4","opus"]:
        for f in Path(directory).rglob(f"*.{ext}"):
            if f.stat().st_size > 2000: return str(f)
    files = [f for f in Path(directory).rglob("*") if f.is_file() and f.stat().st_size > 2000]
    return str(files[0]) if files else None

def to_mp3(src, dst):
    subprocess.run(["ffmpeg","-i",src,"-q:a","5","-map","a",dst,"-y"], capture_output=True, timeout=60)

def transcribe_groq(audio_path, language):
    from groq import Groq
    lang = None if language == "auto" else language

    # Comprimir si supera 25MB
    final_path = audio_path
    if os.path.getsize(audio_path) / 1_000_000 > 24:
        compressed = audio_path + "_c.mp3"
        subprocess.run(["ffmpeg","-i",audio_path,"-b:a","48k",compressed,"-y"], capture_output=True)
        if os.path.exists(compressed): final_path = compressed

    client = Groq(api_key=GROQ_API_KEY)
    with open(final_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(os.path.basename(final_path), f),
            model="whisper-large-v3",
            language=lang or "es",
            response_format="text",
        )
    return result if isinstance(result, str) else result.text

def dl_ytdlp(url, tmpdir, platform, cookies_file=None):
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
        "postprocessors": [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"96"}],
        "quiet": True, "no_warnings": True, "socket_timeout": 10, "retries": 1, "nocheckcertificate": True,
    }
    if platform == "instagram":
        opts["http_headers"] = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"}
        if cookies_file: opts["cookiefile"] = cookies_file
    elif platform == "tiktok":
        opts["http_headers"] = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36", "Referer": "https://www.tiktok.com/"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = (info or {}).get("title", "Sin título")
    audio = find_audio(tmpdir)
    if not audio: raise RuntimeError("Archivo no encontrado")
    return audio, title

def dl_instagram_embed(url, tmpdir):
    m = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_\-]+)", url)
    if not m: raise RuntimeError("Shortcode no encontrado")
    req = urllib.request.Request(f"https://www.instagram.com/p/{m.group(1)}/embed/captioned/",
        headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)", "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=20) as r: html = r.read().decode("utf-8", errors="ignore")
    vm = re.search(r'"video_url":"(https://[^"]+)"', html)
    if not vm: raise RuntimeError("video_url no encontrado")
    video_url = vm.group(1).replace("\\u0026", "&")
    mp4 = os.path.join(tmpdir, "video.mp4")
    with urllib.request.urlopen(urllib.request.Request(video_url,
        headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.instagram.com/"}), timeout=40) as r2:
        with open(mp4,"wb") as f: f.write(r2.read())
    mp3 = os.path.join(tmpdir, "audio.mp3"); to_mp3(mp4, mp3)
    return (mp3 if os.path.exists(mp3) and os.path.getsize(mp3)>1000 else mp4), "Vídeo de Instagram"

def dl_instaloader(url, tmpdir):
    import instaloader
    m = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_\-]+)", url)
    if not m: raise RuntimeError("Shortcode no encontrado")
    L = instaloader.Instaloader(download_videos=True, download_video_thumbnails=False,
        download_geotags=False, download_comments=False, save_metadata=False,
        post_metadata_txt_pattern="", dirname_pattern=tmpdir, quiet=True)
    post = instaloader.Post.from_shortcode(L.context, m.group(1))
    L.download_post(post, target=tmpdir)
    audio = find_audio(tmpdir)
    if not audio: raise RuntimeError("Vídeo no encontrado")
    return audio, post.caption[:80] if post.caption else "Vídeo de Instagram"

LANG_NAMES = {
    "auto":"Auto detectar","es":"Español","en":"Inglés","fr":"Francés",
    "de":"Alemán","it":"Italiano","pt":"Portugués","zh":"Chino",
    "ja":"Japonés","ko":"Coreano","ar":"Árabe","ru":"Ruso",
    "nl":"Holandés","pl":"Polaco","tr":"Turco","hi":"Hindi",
}
LANG_CODES = {v: k for k, v in LANG_NAMES.items()}
EMOJI = {"youtube":"📺","instagram":"📸","tiktok":"🎵","twitter":"🐦"}

def process(url, cookies_text, language_label, progress=gr.Progress(track_tqdm=True)):
    url = (url or "").strip()
    if not url: return "❌ Introduce una URL.", "", "", None, ""
    platform = detect_platform(url)
    if platform == "unknown": return "❌ URL no reconocida. Soportamos YouTube, Instagram y TikTok.", "", "", None, ""

    language = LANG_CODES.get(language_label, "es")
    emoji = EMOJI.get(platform, "🎬")
    cookies_file = None
    if platform == "instagram":
        if cookies_text and cookies_text.strip():
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, dir=tempfile.gettempdir())
            f.write(cookies_text.strip()); f.close()
            cookies_file = f.name
        else:
            cookies_file = _instagram_cookies_file()
    t0 = time.time()

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path, title, errors = None, "Sin título", []

            if platform == "instagram":
                progress(0.1, "📸 Descargando de Instagram…")
                if RAPIDAPI_KEY:
                    try: audio_path, title = dl_instagram_rapidapi(url, tmpdir)
                    except Exception as e: errors.append(f"rapidapi: {e}"); print(f"RapidAPI falló: {e}")

                if not audio_path:
                    progress(0.3, "📸 Método alternativo (yt-dlp + cookies)…")
                    try: audio_path, title = dl_ytdlp(url, tmpdir, platform, cookies_file=cookies_file)
                    except Exception as e: errors.append(f"yt-dlp: {e}")

                if not audio_path:
                    progress(0.4, "📸 Método alternativo (embed)…")
                    try: audio_path, title = dl_instagram_embed(url, tmpdir)
                    except Exception as e: errors.append(f"embed: {e}")

                if not audio_path:
                    progress(0.5, "📸 Método alternativo (instaloader)…")
                    try: audio_path, title = dl_instaloader(url, tmpdir)
                    except Exception as e: errors.append(f"instaloader: {e}")

            else:
                progress(0.15, f"{emoji} Descargando de {platform.capitalize()}…")
                try: audio_path, title = dl_ytdlp(url, tmpdir, platform)
                except Exception as e: errors.append(str(e))

            if not audio_path:
                msg = "❌ No se pudo descargar el vídeo.\n\n" + "\n".join(errors)
                if platform == "instagram": msg += "\n\n\U0001F4A1 " + ("Tus cookies de Instagram pueden haber caducado; actualiza el secret INSTAGRAM_COOKIES." if cookies_file else "Configura el secret INSTAGRAM_COOKIES en Settings para un respaldo mas fiable.")
                return msg, "", "", None, ""

            progress(0.6, "⚡ Transcribiendo con Groq Whisper large-v3…")
            full_text = ""
            if GROQ_API_KEY:
                try: full_text = transcribe_groq(audio_path, language)
                except Exception as e: print(f"Groq error: {e}")

            if not full_text:
                return "❌ Error: no se pudo transcribir con Groq.", "", "", None, ""

            progress(1.0, "✅ ¡Completado!")
            elapsed = round(time.time() - t0, 1)
            words = len(full_text.split())
            chars = len(full_text)

            status = f"✅ Completado en {elapsed}s  ·  {emoji} {platform.capitalize()}  ·  📝 {words} palabras  ·  {chars} caracteres"

            txt_path = os.path.join(tempfile.gettempdir(), "transcripcion.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"Título: {title}\nURL: {url}\n\n{full_text}")

            return full_text, title, status, txt_path, full_text

    except Exception as e:
        import traceback; traceback.print_exc()
        return f"❌ Error inesperado: {e}", "", "", None, ""
    finally:
        if cookies_file and os.path.exists(cookies_file): os.unlink(cookies_file)

# ─── UI ───────────────────────────────────────────────────────────────────────

CSS = """
.gradio-container { max-width: 1200px !important; margin: auto !important; }
#header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 16px; padding: 32px; text-align: center; margin-bottom: 20px; }
#header h1 { color: white; font-size: 2.2em; margin: 0 0 6px; }
#header p  { color: rgba(255,255,255,0.85); margin: 0; font-size: 1.05em; }
#transcribe-btn { background: linear-gradient(135deg, #667eea, #764ba2) !important; border: none !important; font-size: 1.1em !important; padding: 16px !important; border-radius: 10px !important; color: white !important; }
#status-box textarea { color: #22c55e !important; font-weight: 600 !important; font-size: 0.9em !important; }
#transcript-box textarea { font-size: 0.95em !important; line-height: 1.7 !important; }
.footer { text-align: center; color: #9ca3af; font-size: 0.82em; padding: 14px 0 2px; }
"""

LANG_LIST = list(LANG_NAMES.values())

with gr.Blocks(title="Transcriptor Pro", theme=gr.themes.Soft(), css=CSS) as demo:

    gr.HTML("""
    <div id="header">
        <h1>🎙️ Transcriptor Pro</h1>
        <p>Transcripción instantánea · YouTube · Instagram · TikTok · +99 idiomas · Powered by Groq</p>
    </div>
    """)

    with gr.Row(equal_height=False):
        with gr.Column(scale=1, min_width=320):
            gr.Markdown("### 🔗 Enlace del vídeo")
            url_input = gr.Textbox(
                label="",
                placeholder="https://www.instagram.com/reel/...\nhttps://www.youtube.com/watch?v=...\nhttps://www.tiktok.com/@.../video/...",
                lines=3,
            )
            gr.Markdown("### ⚙️ Opciones")
            language_select = gr.Dropdown(
                choices=LANG_LIST, value="Español",
                label="🌐 Idioma del vídeo",
            )

            with gr.Accordion("🍪 Cookies de Instagram (opcional)", open=False):
                cookies_input = gr.Textbox(
                    label="Cookies (formato Netscape, solo si el respaldo automatico falla)",
                    lines=6,
                    placeholder="# Netscape HTTP Cookie File",
                )

            transcribe_btn = gr.Button("🚀  Transcribir ahora", variant="primary", size="lg", elem_id="transcribe-btn")
            status_output  = gr.Textbox(label="Estado", interactive=False, lines=2,
                                        placeholder="Aquí verás el progreso…", elem_id="status-box")

            gr.Markdown("### 📥 Descargar transcripción")
            download_file = gr.File(label="Archivo .txt listo para descargar", interactive=False)

        with gr.Column(scale=1, min_width=420):
            title_output = gr.Textbox(label="📌 Título del vídeo", interactive=False)
            text_output  = gr.Textbox(
                label="📝 Transcripción completa",
                lines=30, interactive=True, show_copy_button=True,
                placeholder="La transcripción aparecerá aquí en segundos…",
                elem_id="transcript-box",
            )
            full_text_hidden = gr.Textbox(visible=False)

    gr.HTML('<div class="footer">⚡ Groq Whisper large-v3 · yt-dlp · instaloader · Nexpalmagency</div>')

    transcribe_btn.click(
        fn=process,
        inputs=[url_input, cookies_input, language_select],
        outputs=[text_output, title_output, status_output, download_file, full_text_hidden],
        show_progress="full",
    )

demo.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", 7860)), ssr_mode=False)
