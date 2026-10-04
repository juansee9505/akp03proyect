@echo off
REM Compila AKP03Controller.exe (y el instalador si Inno Setup está instalado).
REM Requisitos: Python 3.10+ para Windows (64 bits).
setlocal
cd /d "%~dp0\.."

python -m pip install --upgrade pip || goto :error
python -m pip install -r requirements-dev.txt || goto :error
python -m pytest -q || goto :error
python packaging\make_icon.py || goto :error

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name AKP03Controller ^
  --icon packaging\icon.ico ^
  --collect-submodules winrt ^
  --collect-submodules pycaw ^
  --hidden-import pystray._win32 ^
  --hidden-import websocket ^
  run.py || goto :error

echo.
echo Ejecutable listo: dist\AKP03Controller.exe

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  %ISCC% packaging\installer.iss || goto :error
  echo Instalador listo: dist\AKP03Controller-Setup.exe
) else (
  echo Inno Setup no encontrado: se omite el instalador ^(el .exe funciona solo^).
)
exit /b 0

:error
echo Error en la compilacion.
exit /b 1
