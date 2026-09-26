import webview
import subprocess
import threading
import os
import re
import sys
import json
import shutil
import time
import urllib.request
from importlib import metadata as pkg_metadata
from importlib.util import find_spec

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

# Las herramientas se ejecutan como módulos del mismo Python que corre la app, así la
# versión que se chequea/actualiza es exactamente la que se usa. Con pythonw se prefiere
# python.exe (misma instalación) para que pip y los módulos escriban bien en stdout.
PYTHON = sys.executable
if os.path.basename(PYTHON).lower() == 'pythonw.exe':
    _console_python = os.path.join(os.path.dirname(PYTHON), 'python.exe')
    if os.path.exists(_console_python):
        PYTHON = _console_python

# paquete pip -> módulo
TOOLS = {'yt-dlp': 'yt_dlp', 'spotdl': 'spotdl'}
# Extras de pip: yt-dlp[default] trae yt-dlp-ejs (necesario para YouTube) y mutagen (portadas)
PIP_SPECS = ['yt-dlp[default]', 'spotdl']

LOSSLESS = ('flac', 'wav')
THUMBNAIL_AUDIO = ('mp3', 'm4a', 'opus', 'flac')  # WAV no admite portada: yt-dlp falla
THUMBNAIL_VIDEO = ('mp4', 'mkv')                   # WEBM no admite portada
# Solo subtítulos en español e inglés (manuales o automáticos del idioma original).
# Patrones abiertos como "en.*" también atrapan decenas de traducciones automáticas
# ("en-de" = inglés traducido del alemán) y YouTube responde 429.
SUB_LANGS = 'es(-419|-ES|-MX)?,en(-US|-GB)?,-live_chat'
# Recorta la miniatura 16:9 de YouTube a una portada cuadrada
SQUARE_COVER = "ThumbnailsConvertor+FFmpeg_o:-c:v mjpeg -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\""
# "Artista - Canción" en el título -> tags artist/title, solo si el video no trae artista
# propio (YouTube Music y canales oficiales sí lo traen). Si hay artista, el "§" inicial
# impide que la regex coincida.
ARTIST_FROM_TITLE = '%(artist&§|)s%(title)s:^(?P<artist>[^§]+?) - (?P<title>[^§]+)$'
# Subcarpeta y numeración solo cuando se descarga una playlist
OUTPUT_TEMPLATE = '%(playlist_title|.)s/%(playlist_index&{:02d} - |)s%(title)s.%(ext)s'

ERROR_LINE = re.compile(r'^(ERROR\b|\w+Error\b)')
SUCCESS_LINE = re.compile(r'^\[download\] 100%|has already been downloaded|^\[(ExtractAudio|Merger)\]|^Downloaded "|^Skipping ')


def build_ytdlp_command(o):
    fmt, quality = o['advFormat'], o['quality']
    cmd = [PYTHON, '-m', 'yt_dlp', o['url'], '--newline', '--ignore-errors',
           '--yes-playlist' if o.get('playlist') else '--no-playlist']

    if o['mainFormat'] == 'mp4':  # Video
        res = '' if quality == 'best' else f':{quality}'
        if fmt == 'mp4':
            # AV1/VP9 en MP4 no abren en muchos reproductores: priorizar H.264 (máx. 1080p en YouTube)
            cmd += ['-f', 'bv*+ba/b', '-S', f'vcodec:h264,res{res},acodec:m4a']
        elif fmt == 'webm':
            cmd += ['-f', 'bv*[ext=webm]+ba[ext=webm]/b[ext=webm]/b', '-S', f'res{res}']
        else:  # mkv acepta cualquier codec: máxima calidad
            cmd += ['-f', 'bv*+ba/b', '-S', f'res{res}']
        cmd += ['--merge-output-format', fmt]
        if o.get('metadata'):
            cmd.append('--embed-metadata')
            if fmt in THUMBNAIL_VIDEO:
                cmd.append('--embed-thumbnail')
        if o.get('subs'):
            cmd += ['--embed-subs', '--write-auto-subs', '--sub-langs', SUB_LANGS]
    else:  # Audio
        # Elegir la pista original del mismo codec evita recodificar M4A (AAC) y OPUS
        source = {'m4a': 'ba[acodec^=mp4a]/ba/b', 'opus': 'ba[acodec=opus]/ba/b'}.get(fmt, 'ba/b')
        cmd += ['-f', source, '-x', '--audio-format', fmt]
        if fmt not in LOSSLESS:
            if quality != 'best':
                cmd += ['--audio-quality', quality.upper()]
            elif fmt == 'mp3':
                cmd += ['--audio-quality', '0']  # el default de yt-dlp es 5 (~128 kbps)
            # m4a/opus con 'best': sin --audio-quality se copia el audio sin recodificar
        if o.get('metadata'):
            cmd += ['--embed-metadata', '--parse-metadata', ARTIST_FROM_TITLE]
            if fmt in THUMBNAIL_AUDIO:
                cmd += ['--embed-thumbnail', '--convert-thumbnails', 'jpg', '--ppa', SQUARE_COVER]

    if o.get('sponsorblock'):
        cmd += ['--sponsorblock-remove', 'music_offtopic']

    cmd += ['-o', os.path.join(o['folder'], OUTPUT_TEMPLATE)]
    return cmd


def build_spotdl_command(o):
    fmt, quality = o['advFormat'], o['quality']
    if fmt not in ('mp3', 'm4a', 'opus', 'flac', 'wav'):
        fmt = 'mp3'
    if fmt in LOSSLESS:
        bitrate = 'disable'
    elif quality == 'best':
        # 'disable' en m4a/opus evita la conversión; en mp3 'auto' usa el bitrate de la fuente
        bitrate = 'disable' if fmt in ('m4a', 'opus') else 'auto'
    else:
        bitrate = quality

    cmd = [PYTHON, '-m', 'spotdl', 'download', o['url'],
           '--format', fmt, '--bitrate', bitrate,
           '--audio', 'youtube-music', 'youtube',
           '--print-errors']
    if o.get('sponsorblock'):
        cmd.append('--sponsor-block')
    return cmd


def _version_tuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', v or ''))


def _latest_pypi_version(package):
    try:
        with urllib.request.urlopen(f'https://pypi.org/pypi/{package}/json', timeout=8) as r:
            return json.load(r)['info']['version']
    except Exception:
        return None  # sin internet: no se puede comparar


def _max_version_allowed_by_spotdl(package):
    """spotDL fija un techo para yt-dlp (p. ej. 'yt-dlp<2027'). Devuelve ese techo o None."""
    try:
        for req in pkg_metadata.requires('spotdl') or []:
            if re.match(rf'{re.escape(package)}\b', req, re.I):
                m = re.search(r'<\s*([\d.]+)', req)
                return m.group(1) if m else None
    except pkg_metadata.PackageNotFoundError:
        pass
    return None


class Api:
    def __init__(self):
        self._window = None
        self._process = None
        self._cancelled = False
        self._busy = False
        self._lock = threading.Lock()

    def set_window(self, window):
        self._window = window

    def choose_directory(self):
        try:
            if not self._window:
                return ""
            dialog_type = getattr(webview.FileDialog, 'FOLDER', webview.FOLDER_DIALOG) if hasattr(webview, 'FileDialog') else webview.FOLDER_DIALOG
            result = self._window.create_file_dialog(dialog_type)
            if result and len(result) > 0:
                folder = result[0]
                return folder if os.path.exists(folder) else ""
            return ""
        except Exception as e:
            self._log(f"Error al abrir selector: {str(e)}")
            return ""

    # --- Dependencias y actualizaciones ---

    def check_environment(self):
        """Versiones instaladas vs. últimas en PyPI, y programas externos necesarios."""
        packages = []
        for name in TOOLS:
            try:
                installed = pkg_metadata.version(name)
            except pkg_metadata.PackageNotFoundError:
                installed = None
            latest = _latest_pypi_version(name)
            ceiling = _max_version_allowed_by_spotdl(name) if name != 'spotdl' else None
            outdated = bool(installed and latest and _version_tuple(latest) > _version_tuple(installed))
            # Si la última versión supera el techo de spotDL, pip no la va a instalar
            blocked = bool(outdated and ceiling and _version_tuple(latest) >= _version_tuple(ceiling))
            packages.append({'name': name, 'installed': installed, 'latest': latest,
                             'outdated': outdated and not blocked, 'blocked': blocked})
        return {
            'packages': packages,
            'ffmpeg': bool(shutil.which('ffmpeg')),
            # yt-dlp necesita un runtime de JavaScript (deno) para descifrar YouTube;
            # sin él solo ve algunos formatos y muchas descargas fallan
            'deno': bool(shutil.which('deno')),
        }

    def update_tools(self):
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._cancelled = False
        try:
            cmd = [PYTHON, '-m', 'pip', 'install', '--upgrade', *PIP_SPECS]
            self._log(f"Ejecutando: {' '.join(cmd[2:])}")
            return self._stream(cmd) == 0
        except Exception as e:
            self._log(f"Excepción: {e}")
            return False
        finally:
            with self._lock:
                self._busy = False

    # --- Descargas ---

    def download(self, tool, options):
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._cancelled = False
        # Correr en un hilo separado para no bloquear la UI
        thread = threading.Thread(target=self._run_download, args=(tool, options), daemon=True)
        thread.start()
        return True

    def cancel(self):
        with self._lock:
            self._cancelled = True
            process = self._process
        if process and process.poll() is None:
            self._kill(process)
        return True

    def shutdown(self):
        # Al cerrar la ventana: no volver a llamar a JS y matar la descarga en curso
        self._window = None
        self.cancel()

    @staticmethod
    def _kill(process):
        if os.name == 'nt':
            # /T mata también a los hijos (ffmpeg), que terminate() dejaría huérfanos
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                           capture_output=True, creationflags=NO_WINDOW)
        else:
            process.terminate()

    def _run_download(self, tool, options):
        try:
            folder = options.get('folder', '')
            if not folder or not os.path.exists(folder):
                self._log("Error: La carpeta destino no existe o es inválida.")
                self._finish_download('error')
                return

            if tool not in TOOLS:
                self._log("Herramienta no soportada.")
                self._finish_download('error')
                return
            if not find_spec(TOOLS[tool]):
                self._log(f"Error: {tool} no está instalado. Ejecuta: pip install -r requirements.txt")
                self._finish_download('error')
                return
            if not shutil.which('ffmpeg'):
                self._log("Error: No se encontró ffmpeg en el PATH del sistema.")
                self._finish_download('error')
                return

            if tool == 'yt-dlp' and not shutil.which('deno'):
                self._log("Advertencia: no se encontró deno. Sin él yt-dlp no ve todos los formatos de YouTube (winget install DenoLand.Deno).")

            if tool == 'spotdl':
                if options.get('mainFormat') == 'mp4':
                    self._log("Advertencia: spotDL no soporta video. Descargando audio.")
                    options = dict(options, mainFormat='mp3', advFormat='mp3')
                cmd = build_spotdl_command(options)
            else:
                cmd = build_ytdlp_command(options)

            self._log(f"Iniciando descarga en: {folder}")
            self._log(f"Ejecutando: {tool} {' '.join(cmd[4:] if tool == 'spotdl' else cmd[3:])}")

            # spotDL guarda en el directorio de trabajo
            cwd = folder if tool == 'spotdl' else None
            stats = {'errors': 0, 'successes': 0}
            returncode = self._stream(cmd, cwd=cwd, stats=stats)

            if self._cancelled:
                self._finish_download('cancelled')
            elif returncode == 0 and stats['errors'] == 0:
                self._finish_download('success')
            elif stats['successes'] > 0:
                # Con --ignore-errors una playlist sigue aunque fallen algunos elementos
                self._finish_download('partial')
            else:
                if returncode != 0:
                    self._log(f"Error: El proceso terminó con código {returncode}")
                self._finish_download('error')

        except Exception as e:
            self._log(f"Excepción: {str(e)}")
            self._finish_download('error')
        finally:
            with self._lock:
                self._process = None
                self._busy = False

    def _stream(self, cmd, cwd=None, stats=None):
        """Ejecuta cmd, manda su salida al log y devuelve el código de salida."""
        env = dict(os.environ,
                   # Forzar UTF-8 para no romper títulos con acentos o caracteres no latinos
                   PYTHONIOENCODING='utf-8', PYTHONUTF8='1',
                   # spotDL (rich) corta las líneas a 80 columnas si no hay terminal
                   COLUMNS='200')

        with self._lock:
            if self._cancelled:
                return -1
            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
                creationflags=NO_WINDOW,
                encoding='utf-8',
                errors='replace'
            )
            self._process = process

        # Solo se limitan las líneas de progreso (muy frecuentes); el resto se muestra siempre
        last_progress = 0
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            if stats is not None:
                if ERROR_LINE.match(line):
                    stats['errors'] += 1
                if SUCCESS_LINE.search(line):
                    stats['successes'] += 1
            if '%' in line and not ERROR_LINE.match(line) and not line.startswith('[download] 100%'):
                now = time.time()
                if now - last_progress < 0.1:
                    continue
                last_progress = now
            self._log(line)

        process.wait()
        return process.returncode

    def _eval(self, script):
        window = self._window
        if window:
            try:
                window.evaluate_js(script)
            except Exception:
                pass  # la ventana se cerró mientras se descargaba

    def _log(self, message):
        self._eval(f"appendLog({json.dumps(message)})")

    def _finish_download(self, status):
        """status: 'success' | 'partial' | 'error' | 'cancelled'"""
        if self._cancelled:
            status = 'cancelled'
        self._eval(f"onDownloadComplete({json.dumps(status)})")


if __name__ == '__main__':
    api = Api()

    current_dir = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(current_dir, 'web')
    index_path = os.path.join(web_dir, 'index.html')

    window = webview.create_window(
        'Music Downloader',
        url=f'file://{index_path}',
        js_api=api,
        width=540,
        height=800,
        min_size=(420, 560),
        background_color='#eef0f4'
    )

    api.set_window(window)
    webview.start(icon=os.path.join(current_dir, 'icon.ico'))
    api.shutdown()
