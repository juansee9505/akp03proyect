; Instalador de AKP03 Controller (Inno Setup 6)
#define AppName "AKP03 Controller"
#define AppVersion GetEnv("AKP03_VERSION")
#if AppVersion == ""
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{8C1B7F0E-3D7A-4C55-9B1E-5A0C3F7D2E61}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=AKP03 Controller
DefaultDirName={localappdata}\Programs\AKP03Controller
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=AKP03Controller-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\AKP03Controller.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"
Name: "autostart"; Description: "Iniciar con Windows"; GroupDescription: "Inicio:"

[Files]
Source: "..\dist\AKP03Controller.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\AKP03Controller.exe"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\AKP03Controller.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "AKP03Controller"; ValueData: """{app}\AKP03Controller.exe"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\AKP03Controller.exe"; Description: "Abrir {#AppName}"; Flags: nowait postinstall skipifsilent
