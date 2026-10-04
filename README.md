# AKP03 Controller

Programa propio para controlar el **Ajazz AKP03** (y clones Mirabox N3 / AKP03E / AKP03R)
como **control de música**, sin necesidad del software oficial.

- Muestra **qué está sonando**: título, artista, app, carátula y barra de progreso.
  Funciona con **Spotify, YouTube (Chrome, Edge, Firefox), Música de Windows, VLC…**:
  cualquier app que aparezca en el panel multimedia de Windows.
- **Anterior / Play-Pausa / Siguiente** en las teclas.
- **Volumen sólo de la música** (la app que está sonando: Spotify, o el navegador con
  YouTube) con una rueda, sin tocar el volumen del resto del PC.
- **Volumen general de Windows** con otra rueda; al presionarla **silencia el micrófono**.
- **OBS Studio**: grabar, transmitir, pausar, cambiar de escena, silenciar fuentes,
  volumen de fuentes, guardar repetición, cámara virtual.
- Avisos en las teclas: volumen, «Mic OFF», «Grabando», escena actual…
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
  Perilla 1: volumen de la música (app que suena) · presionar = play/pausa
  Perilla 2: volumen general del PC              · presionar = silenciar micrófono
  Perilla 3: OBS, cambiar de escena               · presionar = grabar/detener
  Botones 1-3: OBS grabar / transmitir / pausar grabación
```

¿Cuál es la rueda grande? En la pestaña **Perillas y botones**, gira una rueda y se marca
«Perilla N ◀». Si la grande no es la 1, intercambia las opciones ahí mismo.

Notas:
- El volumen de la música cambia el de la **aplicación completa** en el mezclador de
  Windows: con YouTube en Chrome, cambia el volumen de Chrome (todas sus pestañas).
- Si no suena nada (o la app no aparece en el mezclador), esa rueda cambia el volumen general.
- «Silenciar micrófono» silencia el micrófono predeterminado de Windows, así que afecta a
  todas las apps (OBS, Discord, etc.).

## OBS

1. En OBS (28 o superior): **Herramientas → Ajustes del servidor WebSocket** → marca
   **Habilitar servidor WebSocket**.
2. Pulsa **Mostrar información de conexión** y copia la contraseña.
3. En AKP03 Controller, pestaña **OBS**: pega la contraseña y pulsa **Guardar y probar
   conexión**. Verás tus escenas y fuentes.
4. Las acciones con «…» (ir a escena, silenciar fuente, volumen de fuente) usan la columna
   **Fuente OBS / Escena** de la pestaña *Perillas y botones*: tras probar la conexión
   puedes elegirlas de la lista.

Si OBS está cerrado, las teclas muestran «OBS · Cerrado» y el programa vuelve a conectar
solo cuando lo abras.

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
- *OBS*: conexión con OBS Studio.
- *Apariencia*: brillo, color de acento, color de fondo, velocidad del texto, FPS.

Dos o más teclas seguidas con «Título y artista» se unen en un panel ancho.

La configuración se guarda en `%APPDATA%\AKP03Controller\config.json` (Linux:
`~/.config/akp03controller/config.json`) junto con un registro `akp03.log`.

## Si algo no coincide con tu unidad

El protocolo del AKP03 no está documentado oficialmente; los valores siguen a los proyectos
de código abierto [mirajazz](https://github.com/4ndv/mirajazz) y
[opendeck-akp03](https://github.com/4ndv/opendeck-akp03). El programa detecta el modelo por
su VID/PID y elige solo el tamaño de paquete, de tecla y la rotación (pestaña *Dispositivo* →
«Modelo detectado»). Modelos conocidos: AKP03, AKP03E y AKP03R (y sus revisiones 2,
PID 3002/3003), Mirabox N3, Soomfon SE, Mars Gaming MSD-TWO, TreasLin N3, Redragon SS-551.

Si aun así algo no cuadra:

| Problema | Solución |
|---|---|
| «No se encontró ningún AKP03» | Pestaña *Dispositivo* → «Ver dispositivos HID», busca tu unidad y escribe su VID/PID. Comprueba que el software oficial está cerrado. |
| Las imágenes salen en otra tecla | «Identificar teclas» muestra 1-6; ajusta «Orden de teclas (IDs)». |
| Las imágenes salen giradas/espejadas | Cambia «Rotación» / «Espejar» hasta que la barra de color quede arriba. |
| No se ven imágenes | Deja «Tamaño de paquete» en Automático (1024). Si no, prueba 512. |
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
| `akp03/media/windows.py` | Lo que suena en Windows (GlobalSystemMediaTransportControls) + volumen general, por app y micrófono (pycaw) |
| `akp03/media/linux.py` | Lo mismo en Linux (playerctl / wpctl) |
| `akp03/render.py`, `akp03/icons.py` | Dibujo de cada tecla, texto desplazable, GIF animados |
| `akp03/obs.py` | Cliente de obs-websocket v5 y acciones de OBS |
| `akp03/controller.py` | Une todo: entradas → acciones, estado → imágenes, reconexión automática |
| `akp03/gui.py` | Ventana de configuración y vista previa |
| `packaging/` | Compilación del .exe e instalador |

## Pruebas

```bash
pip install pytest
python -m pytest
```
