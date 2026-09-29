; Instalador de Music Downloader (Inno Setup 6). No se compila a mano:
; tools/build_installer.ps1 prepara build\stage y llama a ISCC con /DAppVersion.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Music Downloader"
; Igual que SetCurrentProcessExplicitAppUserModelID en main.py: así la ventana y el
; acceso directo son la misma app en la barra de tareas (anclar funciona bien)
#define AppUserModelID "LeanDoliri.MusicDownloader"

[Setup]
AppId={{6C1D2B7E-3F4A-4E8B-9A51-2D7C0F3B8E41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Leandro Doliri
AppPublisherURL=https://github.com/LeanDoliri/music-downloader
; Carpeta del usuario, sin permisos de administrador: así pip puede actualizar
; yt-dlp/spotDL dentro de la instalación desde el botón de la app
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=..\dist
OutputBaseFilename=MusicDownloaderSetup-{#AppVersion}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\icon.ico
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Al actualizar, reemplazar el Python entero (evita paquetes viejos mezclados con los nuevos)
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "..\build\stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Sin lanzador propio: un .exe sin firmar lo bloquea el Control inteligente de aplicaciones
; de Windows 11. pythonw.exe está firmado por la Python Software Foundation.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\main.py"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\main.py"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\main.py"""; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; pip crea archivos después de instalar (actualizaciones, __pycache__): borrar todo
Type: filesandordirs; Name: "{app}"
