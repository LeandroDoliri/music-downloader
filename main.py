import webview
import subprocess
import threading
import os
import re
import sys
import json
import shutil
import time
import hashlib
import zipfile
import webbrowser
import urllib.request
from importlib import metadata as pkg_metadata
from importlib.util import find_spec

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# La versión instalada trae ffmpeg y deno en bin\ (ver tools/build_installer.ps1). Se anteponen
# al PATH para que shutil.which y los subprocesos (yt-dlp, spotDL) usen esos antes que los del sistema.
_BUNDLED_BIN = os.path.join(APP_DIR, 'bin')
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
# yt-dlp reciente agrega el título después del id ("<id>: <título> has already…")
ARCHIVED_LINE = re.compile(r'^\[download\] (\S+): (?:.* )?has already been recorded in the archive')
# Omitido por yt-dlp porque el archivo ya existe en la carpeta (aunque no esté en el historial)
EXISTING_LINE = re.compile(r'^\[download\] (.+) has already been downloaded$')
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


def _read_text(path, default=''):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return default


# Versión de la app: el archivo VERSION, el mismo que usa tools/build_installer.ps1
APP_VERSION = _read_text(os.path.join(APP_DIR, 'VERSION'), '0.0.0')
# Generación del Python/ffmpeg/deno que trae la instalación (ver RUNTIME en build_installer.ps1).
# Sin el archivo es la primera, la del instalador 1.0.0.
try:
    APP_RUNTIME = int(_read_text(os.path.join(APP_DIR, 'RUNTIME'), '1'))
except ValueError:
    APP_RUNTIME = 1
# API de releases de GitHub. La variable de entorno permite probar contra un servidor local.
UPDATE_API = os.environ.get('MUSIC_DOWNLOADER_UPDATE_URL',
                            'https://api.github.com/repos/LeandroDoliri/music-downloader').rstrip('/')
UPDATE_ASSET = re.compile(r'^MusicDownloader-update-[\d.]+\.zip$')
# Lo que reemplaza una actualización; el resto de la carpeta (python\, bin\) es del instalador
APP_FILES = ('main.py', 'icon.ico', 'VERSION', 'requirements.txt', 'web')
# Carpeta de trabajo de la actualización (descarga, extracción y copia de seguridad)
UPDATE_WORK = os.path.join(APP_DIR, '_update')


def _is_git_checkout():
    # Desde el repo solo se avisa: los archivos los maneja git, no el actualizador
    return os.path.exists(os.path.join(APP_DIR, '.git'))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _http_get(url, timeout):
    # GitHub rechaza pedidos sin User-Agent
    headers = {'User-Agent': f'MusicDownloader/{APP_VERSION}', 'Accept': 'application/vnd.github+json'}
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def _latest_release():
    """El último release publicado, o None si no hay red o la respuesta no sirve."""
    try:
        with _http_get(f'{UPDATE_API}/releases/latest', timeout=6) as r:
            release = json.load(r)
        version = release['tag_name'].lstrip('vV')
        if not re.fullmatch(r'\d+(\.\d+)*', version):
            return None
        asset = next((a for a in release.get('assets') or [] if UPDATE_ASSET.match(a.get('name', ''))), None)
        return {'version': version, 'notes': release.get('body') or '',
                'page': release.get('html_url') or '', 'asset': asset}
    except Exception:
        return None


def _verify_update(folder, expected_version):
    """Compara el paquete extraído con su manifest.json. Devuelve el manifest o lanza ValueError."""
    try:
        with open(os.path.join(folder, 'manifest.json'), encoding='utf-8') as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        raise ValueError('el paquete no trae un manifest.json válido')
    files = manifest.get('files') or {}
    if manifest.get('version') != expected_version:
        raise ValueError(f"el paquete dice ser la versión {manifest.get('version')}, no la {expected_version}")
    for required in ('main.py', 'VERSION', 'requirements.txt', 'web/index.html'):
        if required not in files:
            raise ValueError(f'al paquete le falta {required}')
    # Solo archivos de la app: un paquete no puede tocar python\ ni bin\
    if any(rel.split('/')[0] not in APP_FILES for rel in files):
        raise ValueError('el paquete trae archivos fuera de la app')
    present = set()
    for root, _, names in os.walk(folder):
        for name in names:
            rel = os.path.relpath(os.path.join(root, name), folder).replace(os.sep, '/')
            if rel != 'manifest.json':
                present.add(rel)
    if present != set(files):
        raise ValueError('los archivos del paquete no coinciden con el manifest')
    for rel, digest in files.items():
        if _sha256(os.path.join(folder, rel)) != str(digest).lower():
            raise ValueError(f'{rel} está dañado (el sha256 no coincide)')
    return manifest


def _swap_app_files(new, app_dir, backup):
    """Pone los archivos de `new` en app_dir y guarda los anteriores en `backup`.
    Si algo falla a mitad de camino, vuelve a dejar los anteriores y relanza el error."""
    os.makedirs(backup)
    moved, placed = [], []
    try:
        for name in APP_FILES:
            src, dst = os.path.join(new, name), os.path.join(app_dir, name)
            if os.path.exists(dst):
                os.replace(dst, os.path.join(backup, name))
                moved.append(name)
            if os.path.exists(src):
                os.replace(src, dst)
                placed.append(name)
    except Exception:
        for name in placed:
            dst = os.path.join(app_dir, name)
            shutil.rmtree(dst) if os.path.isdir(dst) else os.remove(dst)
        for name in moved:
            os.replace(os.path.join(backup, name), os.path.join(app_dir, name))
        raise


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
        self._release_page = ''  # página del último release (ver check_app_update)

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

    # --- Actualización de la app (releases de GitHub) ---

    def check_app_update(self):
        """Compara VERSION con el último release. Sin red o con error devuelve available=False.
        reason: '' (se puede actualizar desde la app), 'git' (checkout del repo: solo avisar) o
        'installer' (el release no trae el paquete .zip: hay que bajar el instalador)."""
        result = {'current': APP_VERSION, 'latest': None, 'available': False, 'notes': '',
                  'can_apply': False, 'reason': ''}
        release = _latest_release()
        if not release:
            return result
        self._release_page = release['page']
        result.update(latest=release['version'], notes=release['notes'],
                      available=_version_tuple(release['version']) > _version_tuple(APP_VERSION))
        if _is_git_checkout():
            result['reason'] = 'git'
        elif not release['asset']:
            result['reason'] = 'installer'
        else:
            result['can_apply'] = True
        return result

    def open_release_page(self):
        # Solo la página que devolvió la API, nunca una URL que mande JS
        if self._release_page.startswith(('https://', 'http://127.0.0.1')):
            webbrowser.open(self._release_page)
        return True

    def apply_app_update(self):
        """Baja el paquete del último release, reemplaza los archivos de la app y la reinicia.
        Devuelve {'ok': bool, 'error': str}. Con ok la ventana se cierra enseguida."""
        if _is_git_checkout():
            return {'ok': False, 'error': 'Estás usando el código del repositorio: actualiza con git pull.'}
        with self._lock:
            if self._busy:
                return {'ok': False, 'error': 'Espera a que termine la descarga en curso.'}
            self._busy = True
            self._cancelled = False
        restarting = False
        try:
            release = _latest_release()
            if not release:
                return {'ok': False, 'error': 'No se pudo consultar GitHub. Revisa tu conexión a internet.'}
            if _version_tuple(release['version']) <= _version_tuple(APP_VERSION):
                return {'ok': False, 'error': 'Ya tienes la última versión.'}
            self._release_page = release['page']
            asset = release['asset']
            if not asset:
                self.open_release_page()
                return {'ok': False, 'error': 'Este release no trae el paquete de actualización: '
                                              'descarga el instalador desde la página que se abrió.'}

            shutil.rmtree(UPDATE_WORK, ignore_errors=True)
            os.makedirs(UPDATE_WORK)
            package = os.path.join(UPDATE_WORK, 'update.zip')
            self._log(f"Descargando Music Downloader {release['version']}…")
            with _http_get(asset['browser_download_url'], timeout=30) as r, open(package, 'wb') as f:
                shutil.copyfileobj(r, f)
            # GitHub publica el sha256 de cada asset ("sha256:<hex>"); los releases viejos no lo traen
            digest = asset.get('digest') or ''
            if digest.startswith('sha256:') and _sha256(package) != digest[7:].lower():
                raise ValueError('la descarga llegó dañada (el sha256 no coincide con el de GitHub)')

            new = os.path.join(UPDATE_WORK, 'new')
            with zipfile.ZipFile(package) as z:
                for name in z.namelist():
                    parts = name.replace('\\', '/').split('/')
                    if name.startswith(('/', '\\')) or ':' in name or '..' in parts:
                        raise ValueError(f'el paquete trae una ruta no permitida: {name}')
                z.extractall(new)
            manifest = _verify_update(new, release['version'])
            self._log('Paquete verificado: todos los archivos coinciden con su sha256.')

            if int(manifest.get('min_runtime') or 1) > APP_RUNTIME:
                self.open_release_page()
                return {'ok': False, 'error': f"La versión {release['version']} cambia Python, ffmpeg o deno y "
                                              'necesita el instalador completo. Se abrió la página del release: '
                                              'descarga MusicDownloaderSetup.exe e instálalo encima (no se pierde nada).'}

            # Las instalaciones 1.0.0 no tienen requirements.txt: ahí se corre siempre
            new_reqs = os.path.join(new, 'requirements.txt')
            if _read_text(os.path.join(APP_DIR, 'requirements.txt'), None) != _read_text(new_reqs):
                cmd = [PYTHON, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
                       '--no-warn-script-location', '--no-build-isolation', '-r', new_reqs]
                self._log('Instalando las dependencias de la versión nueva…')
                if self._stream(cmd) != 0:
                    raise ValueError('pip no pudo instalar las dependencias nuevas')

            try:
                _swap_app_files(new, APP_DIR, os.path.join(UPDATE_WORK, 'backup'))
            except PermissionError as e:
                raise ValueError(f'un archivo de la app está en uso ({e.filename}); '
                                 'cierra otras ventanas de Music Downloader y vuelve a intentar')
            self._log(f"Music Downloader {release['version']} instalado. Reiniciando…")
            self._restart()
            restarting = True
            return {'ok': True, 'error': ''}
        except Exception as e:
            self._log(f'Error al actualizar: {e}')
            return {'ok': False, 'error': f'No se pudo actualizar: {e}. La app quedó como estaba.'}
        finally:
            with self._lock:
                self._process = None
                self._busy = restarting  # si se reinicia, no aceptar descargas mientras se cierra
            shutil.rmtree(UPDATE_WORK, ignore_errors=True)

    def _restart(self):
        """Abre la versión nueva en otro proceso y cierra esta ventana."""
        # pythonw.exe (firmado) y no un .exe propio: ver Control inteligente de aplicaciones en CLAUDE.md
        pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
        exe = pythonw if os.path.exists(pythonw) else sys.executable
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        subprocess.Popen([exe, os.path.join(APP_DIR, 'main.py')], cwd=APP_DIR, creationflags=flags, close_fds=True)
        window = self._window
        # Un instante de margen para que JS reciba la respuesta antes de que se cierre la ventana
        if window:
            threading.Timer(1.0, window.destroy).start()

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
            if (m := EXISTING_LINE.match(line)):
                # Mismo formato que el caso del historial (SUCCESS_LINE ya lo contó como éxito).
                # yt-dlp igual lo anota en el historial, así que la próxima vez se detecta desde cualquier carpeta.
                path = m.group(1)
                self._log(f"Ya lo tienes: {os.path.splitext(os.path.basename(path))[0]} → {path}")
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

    current_dir = APP_DIR
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
