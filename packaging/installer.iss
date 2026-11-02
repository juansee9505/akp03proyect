; Instalador de AKP03 Controller (Inno Setup 6)
#define AppName "AKP03 Controller"
#define AppVersion GetEnv("AKP03_VERSION")
#if AppVersion == ""
  #define AppVersion "0.2.1"
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
; Si el programa está abierto al actualizar, se cierra solo (sale de forma
; ordenada al recibir el aviso de Windows) y se vuelve a abrir al terminar.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"
Name: "autostart"; Description: "Iniciar con Windows"; GroupDescription: "Inicio:"

[InstallDelete]
; Restos de versiones anteriores (antes era un único .exe; ahora es una carpeta).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\AKP03Controller\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\AKP03Controller.exe"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\AKP03Controller.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "AKP03Controller"; ValueData: """{app}\AKP03Controller.exe"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\AKP03Controller.exe"; Description: "Abrir {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Si el usuario activó «Iniciar con Windows» desde la ventana del programa, el
// desinstalador también lo quita (si no, Windows intentaría abrir un .exe borrado).
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'AKP03Controller');
end;
