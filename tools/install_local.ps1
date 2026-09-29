<#
Instala (o desinstala) Music Downloader en esta PC sin el Setup de Inno.

Hace falta en PCs con el Control inteligente de aplicaciones de Windows 11 activado: ese control
bloquea los .exe sin firma digital, incluido el Setup, pero no Python (firmado) ni este script.
Instala lo mismo que el Setup, en la misma carpeta, y lo registra en "Agregar o quitar programas".

Uso (desde la raíz del repo, después de tools\build_installer.ps1):
    powershell -ExecutionPolicy Bypass -File tools\install_local.ps1 [-Version 1.0.0] [-NoDesktop]
    powershell -ExecutionPolicy Bypass -File tools\install_local.ps1 -Uninstall
#>
param(
    [string]$Version = '1.0.0',
    [switch]$NoDesktop,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'

$name = 'Music Downloader'
$dest = Join-Path $env:LOCALAPPDATA "Programs\$name"
$regKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MusicDownloader'
$shortcuts = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) "$name.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "$name.lnk")
)

function Stop-App {
    # pythonw/python de la instalación (la app o una descarga en curso)
    Get-Process python, pythonw -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($dest, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force
    Start-Sleep -Milliseconds 500
}

if ($Uninstall) {
    Stop-App
    $shortcuts | Where-Object { Test-Path $_ } | Remove-Item -Force
    if (Test-Path $regKey) { Remove-Item $regKey -Force }
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    Write-Host "$name desinstalado. Tus preferencias y el historial (%APPDATA%\MusicDownloader) se conservan."
    return
}

$stage = Join-Path (Split-Path $PSScriptRoot -Parent) 'build\stage'
if (-not (Test-Path "$stage\python\pythonw.exe")) {
    throw "No existe build\stage. Genera primero: powershell -ExecutionPolicy Bypass -File tools\build_installer.ps1"
}

Stop-App
# Python se reemplaza entero (igual que [InstallDelete] del Setup) para no mezclar paquetes viejos
if (Test-Path "$dest\python") { Remove-Item "$dest\python" -Recurse -Force }
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item "$stage\*" $dest -Recurse -Force
Copy-Item $PSCommandPath "$dest\uninstall.ps1" -Force

# Accesos directos a pythonw.exe (firmado), con el ícono de la app
$shell = New-Object -ComObject WScript.Shell
foreach ($path in $shortcuts) {
    if ($NoDesktop -and $path -like "$([Environment]::GetFolderPath('Desktop'))*") { continue }
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = "$dest\python\pythonw.exe"
    $lnk.Arguments = "`"$dest\main.py`""
    $lnk.WorkingDirectory = $dest
    $lnk.IconLocation = "$dest\icon.ico,0"
    $lnk.Description = 'Descarga música y videos de YouTube y Spotify'
    $lnk.Save()
}

# Entrada en "Agregar o quitar programas"
New-Item $regKey -Force | Out-Null
$size = [int]((Get-ChildItem $dest -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
$values = @{
    DisplayName     = $name
    DisplayVersion  = $Version
    Publisher       = 'Leandro Doliri'
    DisplayIcon     = "$dest\icon.ico"
    InstallLocation = $dest
    UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$dest\uninstall.ps1`" -Uninstall"
}
foreach ($k in $values.Keys) { Set-ItemProperty $regKey $k $values[$k] }
Set-ItemProperty $regKey EstimatedSize $size -Type DWord
Set-ItemProperty $regKey NoModify 1 -Type DWord
Set-ItemProperty $regKey NoRepair 1 -Type DWord

Write-Host "$name $Version instalado en $dest"
