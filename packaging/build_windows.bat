@echo off
REM Compila AKP03 Controller para Windows:
REM   dist\AKP03Controller\              versión en carpeta (la que usa el instalador:
REM                                      arranca más rápido y el instalador puede cerrarla)
REM   dist\AKP03Controller-portable.exe  un solo archivo, sin instalar
REM   dist\AKP03Controller-Setup.exe     instalador (si Inno Setup está instalado)
REM Requisitos: Python 3.10+ para Windows (64 bits).
setlocal
cd /d "%~dp0\.."

python -m pip install --upgrade pip || goto :error
python -m pip install -r requirements-dev.txt || goto :error
python -m pytest -q || goto :error
python packaging\make_icon.py || goto :error

if "%AKP03_VERSION%"=="" (
  for /f %%v in ('python -c "import akp03; print(akp03.__version__)"') do set AKP03_VERSION=%%v
)
echo Version %AKP03_VERSION%

set PYI_OPTS=--noconfirm --windowed --icon packaging\icon.ico ^
  --collect-submodules winrt --collect-submodules pycaw ^
  --collect-data sv_ttk --hidden-import sv_ttk ^
  --hidden-import pystray._win32 --hidden-import websocket

python -m PyInstaller %PYI_OPTS% --clean --onedir --name AKP03Controller run.py || goto :error
python -m PyInstaller %PYI_OPTS% --onefile --name AKP03Controller-portable run.py || goto :error

echo.
echo Listo: dist\AKP03Controller\AKP03Controller.exe y dist\AKP03Controller-portable.exe

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  %ISCC% packaging\installer.iss || goto :error
  echo Instalador listo: dist\AKP03Controller-Setup.exe
) else (
  echo Inno Setup no encontrado: se omite el instalador ^(el portable funciona solo^).
)
exit /b 0

:error
echo Error en la compilacion.
exit /b 1
