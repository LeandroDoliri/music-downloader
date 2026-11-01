# Music Downloader

Una aplicación de escritorio minimalista y moderna para descargar música y videos de YouTube y Spotify con opciones avanzadas de calidad y formatos.

[![Descargar instalador para Windows](https://img.shields.io/badge/Descargar-instalador%20para%20Windows-4f46e5?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/LeandroDoliri/music-downloader/releases/latest/download/MusicDownloaderSetup.exe)

## Características
- **Soporte Universal**: Descarga desde YouTube o Spotify.
- **Interfaz Moderna**: Diseño minimalista inspirado en interfaces premium, con un tema claro y elegante.
- **Opciones Avanzadas**: Control total sobre el formato (MP3, FLAC, MKV, MP4) y calidad (desde 128kbps hasta lossless y 1080p).
- **Playlists y Metadatos**: Descarga playlists enteras e incrusta portadas y metadatos con un solo clic.

## Instalación rápida (Windows)

Descarga y ejecuta `MusicDownloaderSetup.exe` (botón de arriba, o desde [Releases](https://github.com/LeandroDoliri/music-downloader/releases/latest)). Trae todo lo necesario (Python, ffmpeg y deno), no pide permisos de administrador y crea accesos directos en el menú Inicio y, si quieres, en el escritorio. Se desinstala desde "Agregar o quitar programas".

La app se actualiza sola: al abrirla revisa si hay una versión nueva en Releases y, si la hay, muestra un aviso con el botón **Actualizar** (nunca actualiza sin preguntar). Las instalaciones de la versión 1.0.0 no tienen el actualizador: hay que instalar la 1.1.0 a mano una vez.

### Generar el instalador

```powershell
winget install JRSoftware.InnoSetup
powershell -ExecutionPolicy Bypass -File tools\build_installer.ps1
```

La versión sale del archivo `VERSION`. El resultado queda en `dist\`: el instalador y `MusicDownloader-update-<versión>.zip`, el paquete que usa la actualización desde la app.

### Publicar una versión

1. Sube el número en `VERSION` (y `RUNTIME` si la versión nueva necesita otro Python, ffmpeg o deno: así las instalaciones viejas piden el instalador completo en lugar de actualizarse desde el zip).
2. Ejecuta `tools\build_installer.ps1`.
3. Crea el release `vX.Y.Z` en GitHub con dos assets: el instalador renombrado a `MusicDownloaderSetup.exe` (el botón de descarga de arriba apunta a ese nombre) y `MusicDownloader-update-X.Y.Z.zip`.

> **Control inteligente de aplicaciones (Windows 11):** si está activado, Windows bloquea el Setup porque no tiene firma digital (falla sin mostrar nada). En esa PC, instala con el script, que deja lo mismo en la misma carpeta y se desinstala desde "Agregar o quitar programas":
>
> ```powershell
> powershell -ExecutionPolicy Bypass -File tools\build_installer.ps1 -NoSetup
> powershell -ExecutionPolicy Bypass -File tools\install_local.ps1
> ```

## Ejecutar desde el código

### Requisitos
- Python 3.x
- `yt-dlp`
- `spotdl`
- `ffmpeg` (en el PATH de sistema)

### Pasos

1. Clona el repositorio
2. Instala las dependencias de Python:
   ```bash
   pip install -r requirements.txt
   ```
3. Ejecuta la aplicación:
   ```bash
   pythonw main.py
   ```
