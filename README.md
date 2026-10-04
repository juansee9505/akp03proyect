# AKP03 Controller

Programa propio para controlar el **Ajazz AKP03** (y clones Mirabox N3 / AKP03E / AKP03R)
como **control de música**, sin necesidad del software oficial.

- Muestra **qué está sonando**: título, artista, app, carátula y barra de progreso.
  Funciona con **Spotify, YouTube (Chrome, Edge, Firefox), Música de Windows, VLC…**:
  cualquier app que aparezca en el panel multimedia de Windows.
- **Anterior / Play-Pausa / Siguiente** en las teclas.
- **Volumen** con la perilla (girar = subir/bajar, presionar = silenciar), y aviso en
  pantalla con el porcentaje.
- Cada tecla se puede **personalizar con tu propia imagen o animación GIF**.
- Ventana con **vista previa en vivo** de las teclas y toda la configuración.
- Se queda en la **bandeja del sistema** y puede **iniciar con Windows**.

## Distribución por defecto

```
 ┌──────────┬─────────────────────────┐
 │ Carátula │ Título · Artista · App  │   ← panel de 2 teclas, el texto se desplaza
 │          │ ▬▬▬▬▬▬▬▬ 1:23 / 3:45    │
 ├──────────┼──────────┬──────────────┤
 │    ⏮     │    ⏯     │     ⏭        │
 └──────────┴──────────┴──────────────┘
  Perilla 1: volumen / silenciar
  Perilla 2: cambiar canción / play-pausa
  Perilla 3: brillo
  Botones 1-3: anterior / play-pausa / siguiente
```

Todo se puede cambiar desde la ventana.

## Instalación (Windows 10/11)

1. **Cierra el software oficial de Ajazz / Stream Dock** (sólo un programa puede usar el
   dispositivo a la vez; desactiva también su inicio automático).
2. Descarga `AKP03Controller-Setup.exe` (instalador) o `AKP03Controller.exe` (portable) desde
   la pestaña **Actions** (artefacto `AKP03Controller-windows`) o desde **Releases** del repositorio.
3. Ejecútalo. No requiere drivers ni permisos de administrador.

> Windows puede mostrar el aviso de SmartScreen porque el ejecutable no está firmado:
> «Más información» → «Ejecutar de todas formas».

### Compilar tú mismo el .exe

Con Python 3.10+ para Windows instalado:

```bat
packaging\build_windows.bat
```

Genera `dist\AKP03Controller.exe` (y `dist\AKP03Controller-Setup.exe` si tienes
[Inno Setup 6](https://jrsoftware.org/isinfo.php)).

Cada vez que se sube un cambio a GitHub, el workflow `.github/workflows/build.yml` compila el
ejecutable y el instalador automáticamente. Si creas una etiqueta `v1.0.0` publica una Release.

## Personalizar

En la ventana:

- **Clic** en una tecla de la vista previa → la editas en la pestaña *Teclas*:
  - *Mostrar*: carátula, título/artista, anterior, play/pausa, siguiente, volumen, silenciar,
    reloj, imagen/animación propia o vacía.
  - *Imagen / GIF*: tu imagen (PNG, JPG, GIF animado, WebP). Se copia a la carpeta de la
    app, así que puedes borrar el original.
  - *Dibujar el icono encima*: desactívalo si tu imagen ya es el botón completo.
  - *Al pulsar*: acción de la tecla (también «Abrir programa / URL»).
- **Clic derecho** en una tecla de la vista previa → simula pulsarla.
- *Perillas y botones*: qué hace girar / presionar cada perilla y cada botón.
- *Apariencia*: brillo, color de acento, color de fondo, velocidad del texto, FPS.

Dos o más teclas seguidas con «Título y artista» se unen en un panel ancho.

La configuración se guarda en `%APPDATA%\AKP03Controller\config.json` (Linux:
`~/.config/akp03controller/config.json`) junto con un registro `akp03.log`.

## Si algo no coincide con tu unidad

El protocolo del AKP03 no está documentado oficialmente; los valores provienen de proyectos
de código abierto (mirajazz, opendeck-akp03). Hay revisiones de hardware distintas, así que
el programa trae herramientas para ajustarlo:

| Problema | Solución |
|---|---|
| «No se encontró ningún AKP03» | Pestaña *Dispositivo* → «Ver dispositivos HID», busca tu unidad y escribe su VID/PID. Comprueba que el software oficial está cerrado. |
| Las imágenes salen en otra tecla | «Identificar teclas» muestra 1-6; ajusta «Orden de teclas (IDs)». |
| Las imágenes salen giradas/espejadas | Cambia «Rotación» / «Espejar» hasta que la barra de color quede arriba. |
| No se ven imágenes | Prueba «Tamaño de paquete» = 1024 (firmwares nuevos). |
| Una tecla/perilla no hace nada | Mira «Últimas entradas recibidas». Si sale «unknown (código 0x..)», añade el código a `device.input_map` en `config.json`, p. ej. `"input_map": {"0x26": "button1"}`. Nombres válidos: `key1`-`key6`, `button1`-`button3`, `knob1+`/`knob1-`/`knob1press` (igual para 2 y 3). |

Desde la línea de comandos:

```bash
python -m akp03 --list-devices   # lista los dispositivos HID
python -m akp03 --debug-input    # muestra el código de cada tecla/perilla
python -m akp03 --identify       # numera las teclas al iniciar
python -m akp03 --demo --no-device  # prueba la interfaz con música simulada
python -m akp03 --headless       # sin ventana
```

## Ejecutar desde el código

```bash
pip install -r requirements.txt
python -m akp03
```

### Linux

Necesita `playerctl` (información de MPRIS: Spotify, navegadores…) y `wpctl` o `pactl`
para el volumen. Para acceder al dispositivo sin root:

```bash
sudo cp packaging/99-akp03.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

## Estructura

| Archivo | Qué hace |
|---|---|
| `akp03/device.py` | Protocolo USB-HID del AKP03 (imágenes, brillo, lectura de teclas/perillas) |
| `akp03/media/windows.py` | Lo que suena en Windows (GlobalSystemMediaTransportControls) + volumen (pycaw) |
| `akp03/media/linux.py` | Lo mismo en Linux (playerctl / wpctl) |
| `akp03/render.py`, `akp03/icons.py` | Dibujo de cada tecla, texto desplazable, GIF animados |
| `akp03/controller.py` | Une todo: entradas → acciones, estado → imágenes, reconexión automática |
| `akp03/gui.py` | Ventana de configuración y vista previa |
| `packaging/` | Compilación del .exe e instalador |

## Pruebas

```bash
pip install pytest
python -m pytest
```
