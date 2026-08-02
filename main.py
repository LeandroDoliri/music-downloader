import webview
import subprocess
import threading
import os
import json
import tkinter as tk
from tkinter import filedialog

class Api:
    def __init__(self):
        self.window = None

    def set_window(self, window):
        self.window = window

    def choose_directory(self):
        try:
            # Correr tkinter en un proceso separado para evitar deadlocks de hilo con pywebview
            script = "import tkinter as tk, tkinter.filedialog as fd; root=tk.Tk(); root.withdraw(); root.attributes('-topmost', True); print(fd.askdirectory())"
            
            creationflags = 0
            if os.name == 'nt':
                creationflags = subprocess.CREATE_NO_WINDOW
                
            result = subprocess.run(['pythonw', '-c', script], capture_output=True, text=True, creationflags=creationflags)
            folder = result.stdout.strip()
            return folder if folder else ""
        except Exception as e:
            self._log(f"Error al abrir selector: {str(e)}")
            return ""

    def download(self, tool, options):
        # Correr en un hilo separado para no bloquear la UI
        thread = threading.Thread(target=self._run_download, args=(tool, options))
        thread.start()
        return True

    def _run_download(self, tool, options):
        try:
            folder = options.get('folder', '')
            url = options.get('url', '')
            main_format = options.get('mainFormat', 'mp3')
            adv_format = options.get('advFormat', 'mp3')
            quality = options.get('quality', '320k')
            playlist = options.get('playlist', False)
            metadata = options.get('metadata', True)
            subs = options.get('subs', False)

            if not folder or not os.path.exists(folder):
                self._log("Error: La carpeta destino no existe o es inválida.")
                self._finish_download(False)
                return

            self._log(f"Iniciando descarga en: {folder}")

            cmd = []
            if tool == 'yt-dlp':
                cmd = ['yt-dlp', url]
                
                # Playlist
                if playlist:
                    cmd.append('--yes-playlist')
                else:
                    cmd.append('--no-playlist')
                    
                # Format & Quality
                if main_format == 'mp4': # Video
                    if quality == 'best':
                        v_qual = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
                    else:
                        v_qual = f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/best[height<={quality}][ext=mp4]/best'
                    cmd.extend(['-f', v_qual])
                    
                    if adv_format != 'mp4':
                        cmd.extend(['--merge-output-format', adv_format])
                else: # Audio
                    cmd.extend(['-x', '--audio-format', adv_format])
                    if quality != 'best': # audio quality map
                        # yt-dlp uses 0 for best, 5 for mid, 9 for worst. 
                        # We just pass the quality string directly for specific bitrates
                        cmd.extend(['--audio-quality', quality.replace('k', 'K')]) 
                
                # Metadata
                if metadata:
                    cmd.append('--embed-metadata')
                    if main_format != 'mp4':
                        cmd.append('--embed-thumbnail')
                
                # Subs
                if subs and main_format == 'mp4':
                    cmd.extend(['--write-subs', '--embed-subs', '--sub-langs', 'all'])
                    
                cmd.extend(['-o', f"{folder}/%(title)s.%(ext)s"])

            elif tool == 'spotdl':
                cmd = ['spotdl', url]
                # Format
                if main_format == 'mp4':
                    self._log("Advertencia: spotDL no soporta video. Descargando en MP3.")
                    adv_format = 'mp3'
                
                cmd.extend(['--format', adv_format])
                cmd.extend(['--bitrate', quality])
                
                if not metadata:
                    # spotdl embeds by default. Can't easily turn off without hack, just skip
                    pass
            else:
                self._log("Herramienta no soportada.")
                self._finish_download(False)
                return

            self._log(f"Ejecutando: {' '.join(cmd)}")
            
            # spotDL necesita que el cwd sea folder para guardar allí
            cwd = folder if tool == 'spotdl' else None

            creationflags = 0
            if os.name == 'nt':
                creationflags = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
                creationflags=creationflags,
                encoding='utf-8',
                errors='replace'
            )

            import time
            last_time = 0
            for line in process.stdout:
                if line:
                    now = time.time()
                    if (now - last_time) > 0.1 or 'error' in line.lower():
                        self._log(line.strip())
                        last_time = now

            process.wait()
            
            if process.returncode == 0:
                self._log("¡Descarga completada con éxito!")
                self._finish_download(True)
            else:
                self._log(f"Error: El proceso terminó con código {process.returncode}")
                self._finish_download(False)
                
        except Exception as e:
            self._log(f"Excepción: {str(e)}")
            self._finish_download(False)

    def _log(self, message):
        if self.window:
            safe_message = json.dumps(message)
            self.window.evaluate_js(f"appendLog({safe_message})")

    def _finish_download(self, success):
        if self.window:
            success_str = "true" if success else "false"
            self.window.evaluate_js(f"onDownloadComplete({success_str})")

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
        height=750,
        background_color='#f3f4f6'
    )
    
    api.set_window(window)
    webview.start(icon='icon.ico')
