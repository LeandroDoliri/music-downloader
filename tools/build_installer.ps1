<#
Genera dist\MusicDownloaderSetup-<versión>.exe.

Uso (desde la raíz del repo):
    powershell -ExecutionPolicy Bypass -File tools\build_installer.ps1 [-Version 1.2.0] [-Clean]

Arma build\stage con todo lo que se instala:
    python\    Python "embeddable" oficial + pip + requirements.txt (pip sigue funcionando
               para que el botón de actualizar de la app pueda subir yt-dlp/spotDL)
    bin\       ffmpeg.exe, ffprobe.exe y deno.exe (main.py los antepone al PATH)
    main.py, web\, icon.ico, VERSION, RUNTIME, requirements.txt
y lo empaqueta con Inno Setup (installer\MusicDownloader.iss). Los accesos directos abren
python\pythonw.exe main.py: no hay lanzador .exe propio porque, sin firma digital, el Control
inteligente de aplicaciones de Windows 11 lo bloquea. Por la misma razón, en una PC con ese
control activado el Setup tampoco arranca: ahí se usa tools\install_local.ps1.

El .zip es el paquete que la app baja del release de GitHub para actualizarse sola (ver
apply_app_update en main.py): solo los archivos de la app, más un manifest.json con la versión,
el sha256 de cada archivo y min_runtime (el valor de RUNTIME). RUNTIME se sube a mano cuando
cambian Python, ffmpeg o deno de forma que la app nueva los necesite: las instalaciones con un
RUNTIME menor no se actualizan desde el .zip y la app les pide el instalador completo.

Necesita Inno Setup 6 (winget install JRSoftware.InnoSetup). Las descargas se guardan en
build\downloads y se reutilizan; -Clean las borra para bajar versiones nuevas.
#>
param(
    [string]$Version,   # por defecto, el contenido de VERSION
    [string]$PythonVersion = '3.14.6',
    [switch]$Clean,
    [switch]$NoSetup   # solo armar build\stage (para tools\install_local.ps1), sin Inno Setup
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest es muy lento con la barra de progreso

$root = Split-Path $PSScriptRoot -Parent
$build = Join-Path $root 'build'
$downloads = Join-Path $build 'downloads'
$stage = Join-Path $build 'stage'
if (-not $Version) { $Version = (Get-Content "$root\VERSION" -Raw).Trim() }

if ($Clean -and (Test-Path $build)) { Remove-Item $build -Recurse -Force }
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $downloads, $stage, "$stage\bin" | Out-Null

function Get-File($url, $name) {
    $path = Join-Path $downloads $name
    if (-not (Test-Path $path)) {
        Write-Host "Descargando $url"
        Invoke-WebRequest $url -OutFile "$path.part"
        Move-Item "$path.part" $path
    }
    return $path
}

function Expand-Zip($zip, $name) {
    $dest = Join-Path $build "extract\$name"
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    Expand-Archive $zip $dest
    return $dest
}

function Find-Iscc {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $found) { throw 'No se encontró Inno Setup 6. Instálalo con: winget install JRSoftware.InnoSetup' }
    return $found
}
if (-not $NoSetup) { $iscc = Find-Iscc }

# --- Python embebido + dependencias ---
Write-Host "== Python $PythonVersion"
$pyZip = Get-File "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" "python-$PythonVersion-embed-amd64.zip"
Expand-Archive $pyZip "$stage\python"
# El Python embebido ignora site-packages salvo que se active "import site" en su ._pth
$pth = Get-ChildItem "$stage\python" -Filter 'python*._pth' | Select-Object -First 1
(Get-Content $pth.FullName) -replace '^#\s*import site', 'import site' | Set-Content $pth.FullName -Encoding ascii

$py = "$stage\python\python.exe"
$getPip = Get-File 'https://bootstrap.pypa.io/get-pip.py' 'get-pip.py'
& $py $getPip --no-warn-script-location --disable-pip-version-check
if ($LASTEXITCODE) { throw 'Falló la instalación de pip' }
# Algunas dependencias solo vienen como código fuente (p. ej. proxy_tools, de pywebview). El ._pth
# impide que pip vea el entorno de compilación aislado, así que se compilan con setuptools local.
& $py -m pip install --no-warn-script-location --disable-pip-version-check setuptools wheel
if ($LASTEXITCODE) { throw 'Falló la instalación de setuptools' }
& $py -m pip install --no-warn-script-location --disable-pip-version-check --no-build-isolation -r "$root\requirements.txt"
if ($LASTEXITCODE) { throw 'Falló pip install -r requirements.txt' }
& $py -c "import webview, yt_dlp, spotdl; print('dependencias OK')"
if ($LASTEXITCODE) { throw 'Las dependencias no importan en el Python embebido' }

# --- ffmpeg (compilación "essentials" de gyan.dev, la misma que instala winget) ---
Write-Host '== ffmpeg'
$ffZip = Get-File 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' 'ffmpeg-release-essentials.zip'
$ffDir = Expand-Zip $ffZip 'ffmpeg'
Get-ChildItem $ffDir -Recurse -Include 'ffmpeg.exe', 'ffprobe.exe' | Copy-Item -Destination "$stage\bin"

# --- deno (runtime de JavaScript que yt-dlp usa para YouTube) ---
Write-Host '== deno'
$denoZip = Get-File 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' 'deno-x86_64-pc-windows-msvc.zip'
Expand-Archive $denoZip "$stage\bin" -Force

# --- La app ---
Write-Host '== App'
Copy-Item "$root\main.py", "$root\icon.ico", "$root\VERSION", "$root\RUNTIME", "$root\requirements.txt" $stage
Copy-Item "$root\web" "$stage\web" -Recurse

# --- Paquete de actualización (se sube al release junto con el Setup) ---
# Se arma con System.IO.Compression y no con Compress-Archive: el de PowerShell 5.1 guarda las
# rutas con "\" y el manifest las nombra con "/". RUNTIME no va: describe la instalación, no la app.
Write-Host '== Paquete de actualización'
New-Item -ItemType Directory -Force "$root\dist" | Out-Null
$files = @('main.py', 'icon.ico', 'VERSION', 'requirements.txt') +
    (Get-ChildItem "$stage\web" -Recurse -File | ForEach-Object { 'web/' + $_.FullName.Substring("$stage\web\".Length).Replace('\', '/') })
$hashes = [ordered]@{}
foreach ($f in $files) { $hashes[$f] = (Get-FileHash (Join-Path $stage $f) -Algorithm SHA256).Hash.ToLower() }
$manifest = [ordered]@{
    version     = $Version
    min_runtime = [int](Get-Content "$root\RUNTIME" -Raw).Trim()
    files       = $hashes
} | ConvertTo-Json -Depth 3
$updateZip = "$root\dist\MusicDownloader-update-$Version.zip"
if (Test-Path $updateZip) { Remove-Item $updateZip -Force }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($updateZip, 'Create')
try {
    foreach ($f in $files) {
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (Join-Path $stage $f), $f) | Out-Null
    }
    $writer = New-Object System.IO.StreamWriter($zip.CreateEntry('manifest.json').Open(), (New-Object System.Text.UTF8Encoding $false))
    $writer.Write($manifest)
    $writer.Dispose()
} finally {
    $zip.Dispose()
}
Write-Host "Listo: $updateZip"

# Precompilar ahorra tiempo en el primer arranque
& $py -m compileall -q "$stage\python\Lib\site-packages" "$stage\main.py" | Out-Null

if ($NoSetup) { Write-Host "Listo: $stage"; return }

# --- Instalador ---
Write-Host '== Inno Setup'
& $iscc /Q "/DAppVersion=$Version" "$root\installer\MusicDownloader.iss"
if ($LASTEXITCODE) { throw 'Falló Inno Setup' }

$setup = Get-Item "$root\dist\MusicDownloaderSetup-$Version.exe"
Write-Host ("Listo: {0} ({1:N0} MB)" -f $setup.FullName, ($setup.Length / 1MB))
