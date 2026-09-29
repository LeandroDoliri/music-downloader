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

# La versión instalada trae ffmpeg y deno en bin\ (ver tools/build_installer.ps1). Se anteponen
# al PATH para que shutil.which y los subprocesos (yt-dlp, spotDL) usen esos antes que los del sistema.
_BUNDLED_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin')
if os.path.isdir(_BUNDLED_BIN):
    os.environ['PATH'] = _BUNDLED_BIN + os.pathsep + os.environ.get('PATH', '')

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
# Errores frecuentes de yt-dlp/spotDL -> explicación en español (se muestra una vez por descarga)
KNOWN_ERRORS = [
    (re.compile(r'only available to Music Premium members', re.I),
     "Explicación: YouTube restringió este contenido a miembros de YouTube Music Premium. "
     "Si tienes Premium, conecta tu cuenta en Opciones avanzadas y activa «Usar mi cuenta»."),
    (re.compile(r'cookies are no longer valid', re.I),
     "Explicación: la sesión de YouTube guardada caducó. Vuelve a conectar la cuenta en Opciones avanzadas."),
    (re.compile(r'Sign in to confirm you.re not a bot', re.I),
     "Explicación: YouTube pide verificar que no eres un bot. Suele pasar tras muchas descargas seguidas: "
     "espera un rato y vuelve a intentar."),
    (re.compile(r'Sign in to confirm your age|age.restricted', re.I),
     "Explicación: el video tiene restricción de edad y YouTube solo lo muestra con una sesión iniciada."),
    (re.compile(r'Private video|members-only|Join this channel', re.I),
     "Explicación: el video es privado o exclusivo para miembros del canal."),
    (re.compile(r'Video unavailable|This video is not available|removed', re.I),
     "Explicación: el video ya no está disponible (fue borrado o bloqueado en tu país)."),
    (re.compile(r'HTTP Error 429|Too Many Requests', re.I),
     "Explicación: YouTube está limitando las solicitudes. Espera unos minutos antes de reintentar."),
    (re.compile(r'No results found for song', re.I),
     "Explicación: spotDL no encontró esta canción en YouTube, así que no se pudo descargar."),
]
SUCCESS_LINE = re.compile(r'^\[download\] 100%|has already been downloaded|^\[(ExtractAudio|Merger)\]|^Downloaded "|^Skipping ')

# Sesión de YouTube del usuario (para contenido Premium). Se importa una vez desde el
# navegador y se guarda fuera del repo; solo se conservan cookies de YouTube/Google.
APP_DATA = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'MusicDownloader')
COOKIES_FILE = os.path.join(APP_DATA, 'youtube_cookies.txt')
# youtube.com y sus subdominios; de Google solo lo que usa la sesión de YouTube
# (no Gmail, Drive, Docs, etc.)
COOKIE_DOMAINS = ('youtube.com',)
COOKIE_EXACT_DOMAINS = ('google.com', 'accounts.google.com')
# Historial de descargas de yt-dlp (extractor, id, tipo, título, ruta final), para
# detectar lo que el usuario ya tiene aunque esté en otra carpeta u otro formato
HISTORY_FILE = os.path.join(APP_DATA, 'history.tsv')
# Lista de omitidos que se regenera antes de cada descarga, solo con archivos que siguen existiendo
SKIP_ARCHIVE = os.path.join(APP_DATA, 'skip_archive.txt')
ARCHIVED_LINE = re.compile(r'^\[download\] (\S+): has already been recorded in the archive')
# Con --print-errors spotDL repite al final cada canción que falló, con su URL de Spotify
SPOTDL_NOT_FOUND = re.compile(r'^(https://open\.spotify\.com/track/\S+) - LookupError')
# Marca en el nombre de archivo de las canciones bajadas sin filtro (pueden ser otra versión)
REVIEW_TAG = '[revisar]'
# Cookies que solo existen con una sesión iniciada
LOGIN_COOKIES = ('SAPISID', '__Secure-3PAPISID', 'LOGIN_INFO')
_LOCAL = os.environ.get('LOCALAPPDATA', '')
_ROAMING = os.environ.get('APPDATA', '')
# Navegadores que yt-dlp sabe leer, con la carpeta que indica que están instalados
BROWSERS = {
    'vivaldi': ('Vivaldi', os.path.join(_LOCAL, 'Vivaldi', 'User Data')),
    'firefox': ('Firefox', os.path.join(_ROAMING, 'Mozilla', 'Firefox', 'Profiles')),
    'brave': ('Brave', os.path.join(_LOCAL, 'BraveSoftware', 'Brave-Browser', 'User Data')),
    'opera': ('Opera', os.path.join(_ROAMING, 'Opera Software', 'Opera Stable')),
    'chrome': ('Chrome', os.path.join(_LOCAL, 'Google', 'Chrome', 'User Data')),
    'edge': ('Edge', os.path.join(_LOCAL, 'Microsoft', 'Edge', 'User Data')),
}


def _filter_cookies(src, dst):
    """Copia a dst solo las cookies de YouTube/Google (formato Netscape). Devuelve si hay sesión."""
    logged_in = False
    kept = ['# Netscape HTTP Cookie File']
    with open(src, encoding='utf-8', errors='replace') as f:
        for line in f:
            fields = line.rstrip('\n').split('\t')
            if len(fields) != 7:
                continue
            domain = fields[0].removeprefix('#HttpOnly_').lstrip('.')
            if domain in COOKIE_EXACT_DOMAINS or any(domain == d or domain.endswith('.' + d) for d in COOKIE_DOMAINS):
                kept.append(line.rstrip('\n'))
                if domain.endswith('youtube.com') and fields[5] in LOGIN_COOKIES:
                    logged_in = True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    # Se lee todo antes de escribir, así src y dst pueden ser el mismo archivo
    with open(dst, 'w', encoding='utf-8') as f:
        f.write('\n'.join(kept) + '\n')
    return logged_in


def load_history(kind):
    """{(extractor, id): (título, ruta)} de las descargas de ese tipo cuyo archivo todavía existe."""
    known = {}
    try:
        with open(HISTORY_FILE, encoding='utf-8') as f:
            for line in f:
                fields = line.rstrip('\n').split('\t')
                if len(fields) == 5 and fields[2] == kind and os.path.exists(fields[4]):
                    known[(fields[0].lower(), fields[1])] = (fields[3], fields[4])
    except FileNotFoundError:
        pass
    return known


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
    if o.get('useAccount') and os.path.exists(COOKIES_FILE):
        cmd += ['--cookies', COOKIES_FILE]

    # Registrar cada archivo terminado en el historial (after_move = ruta final ya convertida)
    kind = 'video' if o['mainFormat'] == 'mp4' else 'audio'
    cmd += ['--print-to-file', f'after_move:%(extractor_key)s\t%(id)s\t{kind}\t%(title)s\t%(filepath)s', HISTORY_FILE]
    if o.get('archiveFile'):
        cmd += ['--download-archive', o['archiveFile']]

    cmd += ['-o', os.path.join(o['folder'], OUTPUT_TEMPLATE)]
    return cmd


def build_spotdl_command(o, urls=None, relaxed=False):
    """urls: lista de canciones a bajar en lugar de o['url'] (para el reintento).
    relaxed: sin el filtro de coincidencias de spotDL; el archivo lleva REVIEW_TAG."""
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

    cmd = [PYTHON, '-m', 'spotdl', 'download', *(urls or [o['url']]),
           '--format', fmt, '--bitrate', bitrate,
           '--audio', 'youtube-music', 'youtube',
           '--print-errors']
    if relaxed:
        cmd += ['--dont-filter-results', '--output', f'{{artists}} - {{title}} {REVIEW_TAG}.{{output-ext}}']
    if o.get('sponsorblock'):
        cmd.append('--sponsor-block')
    if o.get('useAccount') and os.path.exists(COOKIES_FILE):
        # Con Premium, spotDL baja el audio de YouTube Music a 256 kbps en lugar de 128
        cmd += ['--cookie-file', COOKIES_FILE]
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
        self._known = {}  # historial cargado para la descarga actual (ver load_history)

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

    # --- Cuenta de YouTube ---

    def account_status(self):
        meta = {}
        try:
            with open(COOKIES_FILE + '.json', encoding='utf-8') as f:
                meta = json.load(f)
        except Exception:
            pass
        return {
            'connected': os.path.exists(COOKIES_FILE),
            'browser': meta.get('browser'),
            'date': meta.get('date'),
            'browsers': [[key, label] for key, (label, path) in BROWSERS.items() if os.path.isdir(path)],
        }

    def connect_account(self, browser):
        """Importa la sesión de YouTube del navegador. Devuelve {'ok': bool, 'error': str}."""
        if browser not in BROWSERS:
            return {'ok': False, 'error': 'Navegador no soportado.'}
        with self._lock:
            if self._busy:
                return {'ok': False, 'error': 'Espera a que termine la descarga en curso.'}
            self._busy = True
        label = BROWSERS[browser][0]
        # yt-dlp lee las cookies del navegador y, al terminar, las vuelca en `tmp`
        # (todas las del navegador). Se filtran enseguida y `tmp` se borra siempre.
        tmp = COOKIES_FILE + '.tmp'
        try:
            os.makedirs(APP_DATA, exist_ok=True)
            cmd = [PYTHON, '-m', 'yt_dlp', '--cookies-from-browser', browser, '--cookies', tmp,
                   '--simulate', '--quiet', '--no-warnings', 'https://www.youtube.com/watch?v=jNQXAC9IVRw']
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    creationflags=NO_WINDOW, timeout=120)
            output = result.stdout + result.stderr
            if 'Could not copy' in output:
                return {'ok': False, 'error': f'{label} está abierto y bloquea sus cookies. Ciérralo por completo '
                                              '(también desde el ícono junto al reloj) y vuelve a intentar.'}
            if 'decrypt' in output.lower():
                return {'ok': False, 'error': f'{label} cifra sus cookies de una forma que yt-dlp no puede leer. '
                                              'Prueba con otro navegador donde tengas la sesión iniciada.'}
            if not os.path.exists(tmp):
                last = next((l for l in reversed(output.splitlines()) if l.strip()), 'error desconocido')
                return {'ok': False, 'error': f'No se pudo leer la sesión: {last}'}
            if not _filter_cookies(tmp, COOKIES_FILE):
                os.remove(COOKIES_FILE)
                return {'ok': False, 'error': f'No hay una sesión de YouTube iniciada en {label}. '
                                              'Inicia sesión en youtube.com, cierra el navegador y reintenta.'}
            with open(COOKIES_FILE + '.json', 'w', encoding='utf-8') as f:
                json.dump({'browser': label, 'date': time.strftime('%d/%m/%Y')}, f)
            return {'ok': True, 'error': ''}
        except subprocess.TimeoutExpired:
            return {'ok': False, 'error': 'yt-dlp tardó demasiado en leer las cookies.'}
        except Exception as e:
            return {'ok': False, 'error': f'Error inesperado: {e}'}
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
            with self._lock:
                self._busy = False

    def disconnect_account(self):
        for path in (COOKIES_FILE, COOKIES_FILE + '.json'):
            if os.path.exists(path):
                os.remove(path)
        return True

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

            os.makedirs(APP_DATA, exist_ok=True)
            self._known = {}
            if tool == 'spotdl':
                if options.get('mainFormat') == 'mp4':
                    self._log("Advertencia: spotDL no soporta video. Descargando audio.")
                    options = dict(options, mainFormat='mp3', advFormat='mp3')
                # spotDL ya omite por su cuenta las canciones que existen en la carpeta
                cmd = build_spotdl_command(options)
            else:
                if options.get('skipExisting'):
                    kind = 'video' if options.get('mainFormat') == 'mp4' else 'audio'
                    self._known = load_history(kind)
                    with open(SKIP_ARCHIVE, 'w', encoding='utf-8') as f:
                        f.writelines(f'{extractor} {vid}\n' for extractor, vid in self._known)
                    options = dict(options, archiveFile=SKIP_ARCHIVE)
                cmd = build_ytdlp_command(options)

            self._log(f"Iniciando descarga en: {folder}")
            self._log(f"Ejecutando: {tool} {' '.join(cmd[4:] if tool == 'spotdl' else cmd[3:])}")

            # spotDL guarda en el directorio de trabajo
            cwd = folder if tool == 'spotdl' else None
            stats = {'errors': 0, 'successes': 0, 'not_found': []}
            returncode = self._stream(cmd, cwd=cwd, stats=stats)

            # Segundo intento sin filtro para las canciones que spotDL no encontró
            if tool == 'spotdl' and stats['not_found'] and not self._cancelled:
                failed = stats['not_found']
                self._log(f"Reintentando {len(failed)} canción(es) con búsqueda flexible…")
                retry = {'errors': 0, 'successes': 0, 'not_found': []}
                self._stream(build_spotdl_command(options, urls=failed, relaxed=True), cwd=cwd, stats=retry)
                stats['errors'] = max(0, stats['errors'] - len(failed) + retry['errors'])
                stats['successes'] += retry['successes']
                if retry['successes']:
                    self._log(f"Aviso: {retry['successes']} canción(es) se bajaron sin filtro y llevan "
                              f"«{REVIEW_TAG}» en el nombre: podrían ser otra versión, revísalas.")
                    returncode = 0 if stats['errors'] <= 0 else returncode

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
        explained = set()
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            if stats is not None:
                if ERROR_LINE.match(line):
                    stats['errors'] += 1
                if SUCCESS_LINE.search(line):
                    stats['successes'] += 1
                if (m := SPOTDL_NOT_FOUND.match(line)) and m.group(1) not in stats['not_found']:
                    stats['not_found'].append(m.group(1))
            if (m := ARCHIVED_LINE.match(line)):
                # Omitido porque ya está en el historial: decir dónde está
                title, path = next(((t, p) for (_, vid), (t, p) in self._known.items() if vid == m.group(1)),
                                   (m.group(1), ''))
                self._log(f"Ya lo tienes: {title}" + (f" → {path}" if path else ''))
                if stats is not None:
                    stats['successes'] += 1
                continue
            if '%' in line and not ERROR_LINE.match(line) and not line.startswith('[download] 100%'):
                now = time.time()
                if now - last_progress < 0.1:
                    continue
                last_progress = now
            self._log(line)
            if ERROR_LINE.match(line):
                for pattern, explanation in KNOWN_ERRORS:
                    if pattern.search(line) and explanation not in explained:
                        explained.add(explanation)
                        self._log(explanation)
                        break

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
    if os.name == 'nt':
        # Identidad propia en la barra de tareas: sin esto Windows agrupa la ventana
        # con pythonw.exe y puede mostrar el ícono de Python en lugar de icon.ico
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('LeanDoliri.MusicDownloader')
        except Exception:
            pass

    api = Api()

    current_dir = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(current_dir, 'web')
    index_path = os.path.join(web_dir, 'index.html')

    window = webview.create_window(
        'Music Downloader',
        url=f'file://{index_path}',
        js_api=api,
        # Apaisada: a partir de 860 px de ancho la UI usa dos columnas (ver style.css)
        width=1000,
        height=760,
        min_size=(440, 560),
        background_color='#eef0f4'
    )

    api.set_window(window)
    webview.start(icon=os.path.join(current_dir, 'icon.ico'))
    api.shutdown()
