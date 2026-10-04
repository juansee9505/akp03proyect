"""Interfaz gráfica (tkinter): vista previa en vivo y configuración."""

from __future__ import annotations

import copy
import logging
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageTk

from . import APP_NAME, __version__, autostart
from .config import (ACTIONS, KEY_TYPES, KNOB_TURN_ACTIONS, NUM_BUTTONS, NUM_KEYS, NUM_KNOBS,
                     import_media_file, normalize)
from .controller import Controller
from .device import InputEvent
from .icons import icon
from .render import hex_color

log = logging.getLogger(__name__)

KEY_TYPE_LABELS = {
    "cover": "Carátula del álbum / video",
    "now_playing": "Título y artista (panel)",
    "previous": "Canción anterior",
    "play_pause": "Play / Pausa",
    "next": "Canción siguiente",
    "volume": "Indicador de volumen",
    "volume_up": "Subir volumen",
    "volume_down": "Bajar volumen",
    "mute": "Silenciar",
    "clock": "Reloj",
    "image": "Imagen / animación propia",
    "none": "Vacía",
}
ACTION_LABELS = {
    "play_pause": "Play / Pausa",
    "next": "Canción siguiente",
    "previous": "Canción anterior",
    "volume_up": "Subir volumen",
    "volume_down": "Bajar volumen",
    "mute": "Silenciar / activar sonido",
    "brightness_up": "Subir brillo",
    "brightness_down": "Bajar brillo",
    "open": "Abrir programa / URL",
    "none": "Nada",
}
KNOB_LABELS = {
    "volume": "Volumen",
    "track": "Cambiar canción",
    "seek": "Adelantar / retroceder",
    "brightness": "Brillo de la pantalla",
    "none": "Nada",
}
AUTO_ACTION = "Automática (según el tipo)"
PREVIEW_SCALE = 1.6
IMAGE_TYPES = [("Imágenes y GIF", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"), ("Todos", "*.*")]


def _inverse(d: dict) -> dict:
    return {v: k for k, v in d.items()}


class _Tray:
    """Icono en la bandeja del sistema (opcional, requiere pystray)."""

    def __init__(self, image: Image.Image, on_open: Callable, on_quit: Callable):
        import pystray  # type: ignore

        menu = pystray.Menu(
            pystray.MenuItem("Abrir", lambda *_: on_open(), default=True),
            pystray.MenuItem("Salir", lambda *_: on_quit()),
        )
        self.icon = pystray.Icon("akp03", image, APP_NAME, menu)
        self.icon.run_detached()

    def stop(self):
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            pass


class App:
    def __init__(self, controller: Controller, start_minimized: bool = False):
        self.ctrl = controller
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {__version__}")
        self.root.minsize(860, 560)
        self._photos: list[Optional[ImageTk.PhotoImage]] = [None] * NUM_KEYS
        self._last_frame_id = -1
        self._selected = 0
        self._loading = False
        self._save_job = None
        self._tray: Optional[_Tray] = None
        self._last_look_change = 0.0

        self._app_image = self._make_app_icon()
        self._app_icon_tk = ImageTk.PhotoImage(self._app_image)
        self.root.iconphoto(True, self._app_icon_tk)

        self._build()
        self._load_key_editor()
        self._setup_tray()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if start_minimized:
            if self._tray:
                self.root.withdraw()
            else:
                self.root.iconify()
        self.root.after(100, self._tick)

    # ================================================================ utilidades
    @property
    def cfg(self) -> dict:
        return self.ctrl.config

    def _make_app_icon(self) -> Image.Image:
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, size - 1, size - 1), radius=14,
                            fill=hex_color(self.cfg["display"]["theme_color"]) + (255,))
        img.alpha_composite(icon("music", 44, (15, 15, 15, 255)), (10, 10))
        return img

    def _update(self, fn: Callable[[dict], None]) -> None:
        """Modifica una copia de la configuración, la aplica y la guarda."""
        if self._loading:
            return
        cfg = copy.deepcopy(self.ctrl.config)
        fn(cfg)
        self.ctrl.apply_config(normalize(cfg))
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(800, self._save)

    def _save(self):
        self._save_job = None
        self.ctrl._save()
        self.saved_var.set("Configuración guardada ✓")
        self.root.after(2500, lambda: self.saved_var.set(""))

    # ================================================================ construcción
    def _build(self):
        root = self.root
        style = ttk.Style(root)
        try:
            style.theme_use("vista" if "vista" in style.theme_names() else "clam")
        except tk.TclError:
            pass

        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        # ---------------- izquierda: vista previa
        left = ttk.Frame(main)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 12))

        self.status_var = tk.StringVar(value=self.ctrl.status)
        ttk.Label(left, textvariable=self.status_var, foreground="#666").pack(anchor="w")

        np = ttk.LabelFrame(left, text="Sonando ahora", padding=8)
        np.pack(fill="x", pady=(6, 8))
        self.title_var = tk.StringVar()
        self.artist_var = tk.StringVar()
        self.app_var = tk.StringVar()
        ttk.Label(np, textvariable=self.title_var, font=("Segoe UI", 12, "bold"),
                  wraplength=330).pack(anchor="w")
        ttk.Label(np, textvariable=self.artist_var, wraplength=330).pack(anchor="w")
        ttk.Label(np, textvariable=self.app_var, foreground="#888").pack(anchor="w")
        ctl = ttk.Frame(np)
        ctl.pack(anchor="w", pady=(6, 0))
        for text, action in (("⏮", "previous"), ("⏯", "play_pause"), ("⏭", "next"),
                             ("🔉", "volume_down"), ("🔊", "volume_up"), ("🔇", "mute")):
            ttk.Button(ctl, text=text, width=4,
                       command=lambda a=action: self.ctrl.do_action(a)).pack(side="left", padx=1)

        pv = ttk.LabelFrame(left, text="Vista previa del AKP03 (clic = editar, clic derecho = pulsar)",
                            padding=8)
        pv.pack(fill="x")
        ks = int(self.cfg["device"]["key_size"] * PREVIEW_SCALE)
        grid = tk.Frame(pv, bg="#222", padx=6, pady=6)
        grid.pack()
        self.key_labels: list[tk.Label] = []
        self._blank_photo = ImageTk.PhotoImage(Image.new("RGB", (ks, ks), (0, 0, 0)))
        for i in range(NUM_KEYS):
            lbl = tk.Label(grid, bg="#000", image=self._blank_photo, bd=0, highlightthickness=3,
                           highlightbackground="#222", highlightcolor="#222", cursor="hand2")
            lbl.grid(row=i // 3, column=i % 3, padx=4, pady=4)
            lbl.bind("<Button-1>", lambda e, i=i: self._select_key(i))
            lbl.bind("<Button-3>", lambda e, i=i: self._simulate(InputEvent("key", i, "press")))
            self.key_labels.append(lbl)

        knobs = ttk.Frame(pv)
        knobs.pack(pady=(8, 0))
        for k in range(NUM_KNOBS):
            f = ttk.Frame(knobs)
            f.grid(row=0, column=k, padx=6)
            ttk.Label(f, text=f"Perilla {k + 1}").pack()
            row = ttk.Frame(f)
            row.pack()
            ttk.Button(row, text="⟲", width=3,
                       command=lambda k=k: self._simulate(InputEvent("knob", k, "turn", -1))).pack(side="left")
            ttk.Button(row, text="●", width=3,
                       command=lambda k=k: self._simulate(InputEvent("knob", k, "press"))).pack(side="left")
            ttk.Button(row, text="⟳", width=3,
                       command=lambda k=k: self._simulate(InputEvent("knob", k, "turn", 1))).pack(side="left")
        btns = ttk.Frame(pv)
        btns.pack(pady=(6, 0))
        for b in range(NUM_BUTTONS):
            ttk.Button(btns, text=f"Botón {b + 1}", width=9,
                       command=lambda b=b: self._simulate(InputEvent("button", b, "press"))).pack(side="left", padx=4)

        # ---------------- derecha: configuración
        right = ttk.Notebook(main)
        right.grid(row=0, column=1, sticky="nsew")
        right.add(self._build_keys_tab(right), text="Teclas")
        right.add(self._build_controls_tab(right), text="Perillas y botones")
        right.add(self._build_look_tab(right), text="Apariencia")
        right.add(self._build_device_tab(right), text="Dispositivo")
        right.add(self._build_general_tab(right), text="General")

        bottom = ttk.Frame(main)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.saved_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.saved_var, foreground="#2a7").pack(side="left")
        ttk.Button(bottom, text="Salir", command=self.quit).pack(side="right")

    # ---------------------------------------------------------------- pestaña teclas
    def _build_keys_tab(self, parent):
        f = ttk.Frame(parent, padding=12)
        f.columnconfigure(1, weight=1)
        self.key_title = ttk.Label(f, font=("Segoe UI", 11, "bold"))
        self.key_title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(f, text="Mostrar:").grid(row=1, column=0, sticky="w")
        self.key_type_var = tk.StringVar()
        cb = ttk.Combobox(f, textvariable=self.key_type_var, state="readonly",
                          values=[KEY_TYPE_LABELS[t] for t in KEY_TYPES])
        cb.grid(row=1, column=1, columnspan=2, sticky="ew", pady=3)
        cb.bind("<<ComboboxSelected>>", lambda e: self._key_changed())

        ttk.Label(f, text="Imagen / GIF:").grid(row=2, column=0, sticky="w")
        self.key_image_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.key_image_var, state="readonly").grid(row=2, column=1, sticky="ew", pady=3)
        bf = ttk.Frame(f)
        bf.grid(row=2, column=2, sticky="e")
        ttk.Button(bf, text="Elegir…", command=self._choose_image).pack(side="left", padx=2)
        ttk.Button(bf, text="Quitar", command=self._clear_image).pack(side="left")

        self.key_overlay_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Dibujar el icono encima de mi imagen", variable=self.key_overlay_var,
                        command=self._key_changed).grid(row=3, column=1, columnspan=2, sticky="w", pady=3)

        ttk.Label(f, text="Al pulsar:").grid(row=4, column=0, sticky="w")
        self.key_action_var = tk.StringVar()
        cb = ttk.Combobox(f, textvariable=self.key_action_var, state="readonly",
                          values=[AUTO_ACTION] + [ACTION_LABELS[a] for a in ACTIONS])
        cb.grid(row=4, column=1, columnspan=2, sticky="ew", pady=3)
        cb.bind("<<ComboboxSelected>>", lambda e: self._key_changed())

        ttk.Label(f, text="Programa / URL:").grid(row=5, column=0, sticky="w")
        self.key_target_var = tk.StringVar()
        e = ttk.Entry(f, textvariable=self.key_target_var)
        e.grid(row=5, column=1, columnspan=2, sticky="ew", pady=3)
        e.bind("<FocusOut>", lambda ev: self._key_changed())
        e.bind("<Return>", lambda ev: self._key_changed())

        ttk.Button(f, text="Probar esta tecla",
                   command=lambda: self._simulate(InputEvent("key", self._selected, "press"))).grid(
            row=6, column=1, sticky="w", pady=(10, 0))

        help_text = (
            "Consejos:\n"
            "• Dos o más teclas seguidas con «Título y artista» forman un panel ancho "
            "con el texto desplazándose.\n"
            "• En «Carátula», tu imagen se usa cuando la canción no trae carátula.\n"
            "• En «Imagen / animación propia» puedes usar GIF animados.\n"
            "• En el panel, tu imagen sustituye al fondo difuminado de la carátula."
        )
        ttk.Label(f, text=help_text, foreground="#666", wraplength=420, justify="left").grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(16, 0))
        return f

    def _select_key(self, i: int):
        self._selected = i
        self._load_key_editor()

    def _load_key_editor(self):
        self._loading = True
        key = self.cfg["keys"][self._selected]
        self.key_title.configure(text=f"Tecla {self._selected + 1}")
        self.key_type_var.set(KEY_TYPE_LABELS[key["type"]])
        self.key_image_var.set(key.get("image") or "")
        self.key_overlay_var.set(bool(key.get("overlay", True)))
        action = key.get("action")
        self.key_action_var.set(ACTION_LABELS[action] if action else AUTO_ACTION)
        self.key_target_var.set(key.get("target") or "")
        for i, lbl in enumerate(self.key_labels):
            color = "#1DB954" if i == self._selected else "#222"
            lbl.configure(highlightbackground=color, highlightcolor=color)
        self._loading = False

    def _key_changed(self):
        types = _inverse(KEY_TYPE_LABELS)
        actions = _inverse(ACTION_LABELS)
        i = self._selected

        def fn(cfg):
            key = cfg["keys"][i]
            key["type"] = types.get(self.key_type_var.get(), key["type"])
            key["overlay"] = bool(self.key_overlay_var.get())
            key["action"] = actions.get(self.key_action_var.get())
            key["target"] = self.key_target_var.get().strip() or None
            key["image"] = self.key_image_var.get() or None

        self._update(fn)

    def _choose_image(self):
        path = filedialog.askopenfilename(title="Elige una imagen o GIF", filetypes=IMAGE_TYPES)
        if not path:
            return
        try:
            stored = import_media_file(path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"No se pudo copiar la imagen:\n{exc}")
            return
        self.key_image_var.set(stored)
        self._key_changed()

    def _clear_image(self):
        self.key_image_var.set("")
        self._key_changed()

    # ---------------------------------------------------------------- perillas y botones
    def _build_controls_tab(self, parent):
        f = ttk.Frame(parent, padding=12)
        f.columnconfigure(1, weight=1)
        f.columnconfigure(2, weight=1)
        ttk.Label(f, text="Al girar").grid(row=0, column=1, sticky="w")
        ttk.Label(f, text="Al presionar").grid(row=0, column=2, sticky="w")
        self.knob_vars = []
        for k in range(NUM_KNOBS):
            ttk.Label(f, text=f"Perilla {k + 1}").grid(row=k + 1, column=0, sticky="w", pady=3)
            tv, pv = tk.StringVar(), tk.StringVar()
            cb1 = ttk.Combobox(f, textvariable=tv, state="readonly",
                               values=[KNOB_LABELS[a] for a in KNOB_TURN_ACTIONS])
            cb2 = ttk.Combobox(f, textvariable=pv, state="readonly",
                               values=[ACTION_LABELS[a] for a in ACTIONS if a != "open"])
            cb1.grid(row=k + 1, column=1, sticky="ew", padx=3)
            cb2.grid(row=k + 1, column=2, sticky="ew", padx=3)
            for cb in (cb1, cb2):
                cb.bind("<<ComboboxSelected>>", lambda e: self._controls_changed())
            knob = self.cfg["knobs"][k]
            tv.set(KNOB_LABELS[knob["turn"]])
            pv.set(ACTION_LABELS[knob["press"]])
            self.knob_vars.append((tv, pv))

        ttk.Separator(f).grid(row=5, column=0, columnspan=3, sticky="ew", pady=12)
        ttk.Label(f, text="Acción").grid(row=6, column=1, sticky="w")
        ttk.Label(f, text="Programa / URL (si la acción es «Abrir»)").grid(row=6, column=2, sticky="w")
        self.button_vars = []
        for b in range(NUM_BUTTONS):
            ttk.Label(f, text=f"Botón {b + 1}").grid(row=b + 7, column=0, sticky="w", pady=3)
            av, tv = tk.StringVar(), tk.StringVar()
            cb = ttk.Combobox(f, textvariable=av, state="readonly",
                              values=[ACTION_LABELS[a] for a in ACTIONS])
            cb.grid(row=b + 7, column=1, sticky="ew", padx=3)
            cb.bind("<<ComboboxSelected>>", lambda e: self._controls_changed())
            e = ttk.Entry(f, textvariable=tv)
            e.grid(row=b + 7, column=2, sticky="ew", padx=3)
            e.bind("<FocusOut>", lambda ev: self._controls_changed())
            e.bind("<Return>", lambda ev: self._controls_changed())
            btn = self.cfg["buttons"][b]
            av.set(ACTION_LABELS[btn["action"]])
            tv.set(btn.get("target") or "")
            self.button_vars.append((av, tv))

        ttk.Separator(f).grid(row=10, column=0, columnspan=3, sticky="ew", pady=12)
        self.vol_step_var = tk.IntVar(value=self.cfg["volume_step"])
        ttk.Label(f, text="Paso de volumen por clic (%)").grid(row=11, column=0, columnspan=2, sticky="w")
        sp = ttk.Spinbox(f, from_=1, to=20, textvariable=self.vol_step_var, width=6,
                         command=self._controls_changed)
        sp.grid(row=11, column=2, sticky="w")
        sp.bind("<FocusOut>", lambda e: self._controls_changed())
        return f

    def _controls_changed(self):
        turns = _inverse(KNOB_LABELS)
        actions = _inverse(ACTION_LABELS)

        def fn(cfg):
            for k, (tv, pv) in enumerate(self.knob_vars):
                cfg["knobs"][k]["turn"] = turns.get(tv.get(), "none")
                cfg["knobs"][k]["press"] = actions.get(pv.get(), "none")
            for b, (av, tv) in enumerate(self.button_vars):
                cfg["buttons"][b]["action"] = actions.get(av.get(), "none")
                cfg["buttons"][b]["target"] = tv.get().strip() or None
            try:
                cfg["volume_step"] = max(1, min(20, int(self.vol_step_var.get())))
            except (tk.TclError, ValueError):
                pass

        self._update(fn)

    # ---------------------------------------------------------------- apariencia
    def _build_look_tab(self, parent):
        f = ttk.Frame(parent, padding=12)
        f.columnconfigure(1, weight=1)
        disp = self.cfg["display"]

        ttk.Label(f, text="Brillo").grid(row=0, column=0, sticky="w")
        self.brightness_var = tk.IntVar(value=self.cfg["device"]["brightness"])
        ttk.Scale(f, from_=0, to=100, variable=self.brightness_var, orient="horizontal",
                  command=lambda v: self._look_changed()).grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(f, text="Color de acento").grid(row=1, column=0, sticky="w")
        self.theme_btn = tk.Button(f, width=8, bg=disp["theme_color"], relief="groove",
                                   command=lambda: self._pick_color("theme_color", self.theme_btn))
        self.theme_btn.grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(f, text="Color de fondo").grid(row=2, column=0, sticky="w")
        self.bg_btn = tk.Button(f, width=8, bg=disp["background_color"], relief="groove",
                                command=lambda: self._pick_color("background_color", self.bg_btn))
        self.bg_btn.grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(f, text="Velocidad del texto").grid(row=3, column=0, sticky="w")
        self.scroll_var = tk.IntVar(value=int(disp["scroll_speed"]))
        ttk.Scale(f, from_=5, to=80, variable=self.scroll_var, orient="horizontal",
                  command=lambda v: self._look_changed()).grid(row=3, column=1, sticky="ew", pady=4)

        ttk.Label(f, text="Cuadros por segundo").grid(row=4, column=0, sticky="w")
        self.fps_var = tk.IntVar(value=disp["fps"])
        sp = ttk.Spinbox(f, from_=1, to=30, textvariable=self.fps_var, width=6, command=self._look_changed)
        sp.grid(row=4, column=1, sticky="w", pady=4)
        sp.bind("<FocusOut>", lambda e: self._look_changed())

        self.dim_var = tk.BooleanVar(value=disp["dim_cover_when_paused"])
        ttk.Checkbutton(f, text="Oscurecer la carátula cuando está en pausa", variable=self.dim_var,
                        command=self._look_changed).grid(row=5, column=0, columnspan=2, sticky="w", pady=4)

        ttk.Label(f, text=("Más cuadros por segundo = animaciones y texto más fluidos, pero más "
                           "tráfico USB. 10-15 es un buen valor."),
                  foreground="#666", wraplength=420).grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))
        return f

    def _pick_color(self, field: str, button: tk.Button):
        current = self.cfg["display"][field]
        result = colorchooser.askcolor(color=current, title="Elige un color")
        if not result or not result[1]:
            return
        button.configure(bg=result[1])
        self._update(lambda cfg: cfg["display"].__setitem__(field, result[1]))

    def _look_changed(self):
        self._last_look_change = time.monotonic()

        def fn(cfg):
            try:
                cfg["device"]["brightness"] = int(float(self.brightness_var.get()))
                cfg["display"]["scroll_speed"] = int(float(self.scroll_var.get()))
                cfg["display"]["fps"] = int(self.fps_var.get())
            except (tk.TclError, ValueError):
                pass
            cfg["display"]["dim_cover_when_paused"] = bool(self.dim_var.get())

        self._update(fn)

    # ---------------------------------------------------------------- dispositivo
    def _build_device_tab(self, parent):
        f = ttk.Frame(parent, padding=12)
        f.columnconfigure(1, weight=1)
        dev = self.cfg["device"]

        def fmt(v):
            return "" if v is None else (v if isinstance(v, str) else f"0x{v:04x}")

        ttk.Label(f, text="VID (vacío = automático)").grid(row=0, column=0, sticky="w")
        self.vid_var = tk.StringVar(value=fmt(dev["vid"]))
        ttk.Entry(f, textvariable=self.vid_var, width=10).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(f, text="PID (vacío = automático)").grid(row=1, column=0, sticky="w")
        self.pid_var = tk.StringVar(value=fmt(dev["pid"]))
        ttk.Entry(f, textvariable=self.pid_var, width=10).grid(row=1, column=1, sticky="w", pady=3)

        ttk.Label(f, text="Tamaño de paquete").grid(row=2, column=0, sticky="w")
        self.packet_var = tk.StringVar(value=str(dev["packet_size"]))
        ttk.Combobox(f, textvariable=self.packet_var, values=["512", "1024"], width=8,
                     state="readonly").grid(row=2, column=1, sticky="w", pady=3)

        ttk.Label(f, text="Rotación de imagen").grid(row=3, column=0, sticky="w")
        self.rot_var = tk.StringVar(value=str(dev["rotation"]))
        ttk.Combobox(f, textvariable=self.rot_var, values=["0", "90", "180", "270"], width=8,
                     state="readonly").grid(row=3, column=1, sticky="w", pady=3)

        self.flip_var = tk.BooleanVar(value=dev["flip"])
        ttk.Checkbutton(f, text="Espejar imagen", variable=self.flip_var).grid(
            row=4, column=1, sticky="w", pady=3)

        ttk.Label(f, text="Orden de teclas (IDs)").grid(row=5, column=0, sticky="w")
        self.ids_var = tk.StringVar(value=",".join(str(x) for x in dev["image_key_ids"]))
        ttk.Entry(f, textvariable=self.ids_var, width=18).grid(row=5, column=1, sticky="w", pady=3)

        row = ttk.Frame(f)
        row.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(row, text="Aplicar", command=self._device_changed).pack(side="left")
        ttk.Button(row, text="Identificar teclas", command=lambda: self.ctrl.identify()).pack(side="left", padx=6)
        ttk.Button(row, text="Ver dispositivos HID", command=self._show_hid).pack(side="left")

        ttk.Label(f, text=("«Identificar teclas» muestra 1-6 en el dispositivo durante 8 s. Si el orden "
                           "no coincide, cambia «Orden de teclas»; si los números salen girados, "
                           "cambia la rotación (la barra de color debe quedar arriba)."),
                  foreground="#666", wraplength=440, justify="left").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(10, 6))

        ttk.Label(f, text="Últimas entradas recibidas:").grid(row=8, column=0, columnspan=2, sticky="w")
        self.input_list = tk.Listbox(f, height=7)
        self.input_list.grid(row=9, column=0, columnspan=2, sticky="nsew")
        f.rowconfigure(9, weight=1)
        return f

    def _device_changed(self):
        def parse(s):
            s = s.strip()
            return int(s, 0) if s else None

        try:
            vid, pid = parse(self.vid_var.get()), parse(self.pid_var.get())
            ids = [int(x, 0) for x in self.ids_var.get().replace(" ", "").split(",") if x]
            if len(ids) != NUM_KEYS:
                raise ValueError(f"Se necesitan {NUM_KEYS} IDs separados por comas")
        except ValueError as exc:
            messagebox.showerror(APP_NAME, f"Valor inválido: {exc}")
            return

        def fn(cfg):
            d = cfg["device"]
            d["vid"], d["pid"] = vid, pid
            d["packet_size"] = int(self.packet_var.get())
            d["rotation"] = int(self.rot_var.get())
            d["flip"] = bool(self.flip_var.get())
            d["image_key_ids"] = ids

        self._update(fn)

    def _show_hid(self):
        from .device import DeviceError, describe_hid_devices

        try:
            text = describe_hid_devices()
        except DeviceError as exc:
            text = str(exc)
        win = tk.Toplevel(self.root)
        win.title("Dispositivos HID")
        t = tk.Text(win, width=110, height=24)
        t.insert("1.0", text)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)

    # ---------------------------------------------------------------- general
    def _build_general_tab(self, parent):
        f = ttk.Frame(parent, padding=12)
        app = self.cfg["app"]
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        cb = ttk.Checkbutton(f, text="Iniciar con Windows", variable=self.autostart_var,
                             command=self._autostart_changed)
        cb.pack(anchor="w", pady=3)
        if not autostart.supported():
            cb.state(["disabled"])
        self.min_var = tk.BooleanVar(value=app["start_minimized"])
        ttk.Checkbutton(f, text="Iniciar minimizado", variable=self.min_var,
                        command=self._general_changed).pack(anchor="w", pady=3)
        self.tray_var = tk.BooleanVar(value=app["minimize_to_tray"])
        ttk.Checkbutton(f, text="Al cerrar la ventana, seguir funcionando en la bandeja del sistema",
                        variable=self.tray_var, command=self._general_changed).pack(anchor="w", pady=3)
        ttk.Label(f, text=(
            "Importante: cierra el software oficial de Ajazz / Stream Dock, porque sólo un "
            "programa a la vez puede controlar el dispositivo.\n\n"
            f"Configuración guardada en:\n{self.ctrl.config_path or '(por defecto)'}"),
            foreground="#666", wraplength=440, justify="left").pack(anchor="w", pady=(16, 0))
        return f

    def _autostart_changed(self):
        try:
            autostart.set_enabled(bool(self.autostart_var.get()))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"No se pudo cambiar el inicio automático:\n{exc}")
            self.autostart_var.set(autostart.is_enabled())

    def _general_changed(self):
        def fn(cfg):
            cfg["app"]["start_minimized"] = bool(self.min_var.get())
            cfg["app"]["minimize_to_tray"] = bool(self.tray_var.get())

        self._update(fn)

    # ================================================================ ciclo
    def _simulate(self, ev: InputEvent):
        self.ctrl.inject(ev)

    def _tick(self):
        try:
            self._refresh()
        except Exception:  # noqa: BLE001
            log.exception("Error actualizando la interfaz")
        self.root.after(80, self._tick)

    def _refresh(self):
        self.status_var.set(self.ctrl.status)
        st = self.ctrl.state
        if st.has_media:
            self.title_var.set(st.title or "—")
            self.artist_var.set(st.artist)
            vol = "" if st.volume is None else f" · Volumen {st.volume}%" + (" (silenciado)" if st.muted else "")
            estado = {"playing": "Reproduciendo", "paused": "En pausa"}.get(st.status, st.status)
            self.app_var.set(f"{st.app} · {estado}{vol}")
        else:
            self.title_var.set("No se está reproduciendo nada")
            self.artist_var.set("")
            self.app_var.set("" if st.volume is None else f"Volumen {st.volume}%")

        if self.ctrl.frame_id != self._last_frame_id and self.ctrl.last_frame:
            self._last_frame_id = self.ctrl.frame_id
            ks = int(self.cfg["device"]["key_size"] * PREVIEW_SCALE)
            for i, img in enumerate(self.ctrl.last_frame[:NUM_KEYS]):
                photo = ImageTk.PhotoImage(img.resize((ks, ks), Image.LANCZOS))
                self._photos[i] = photo
                self.key_labels[i].configure(image=photo, width=ks, height=ks)

        items = self.ctrl.input_log
        if self.input_list.size() != len(items) or (items and self.input_list.get("end") != items[-1]):
            self.input_list.delete(0, "end")
            for it in items:
                self.input_list.insert("end", it)
            self.input_list.see("end")

        bright = self.cfg["device"]["brightness"]
        # El brillo también cambia desde la perilla: sincroniza el control deslizante.
        if (int(float(self.brightness_var.get())) != bright
                and time.monotonic() - self._last_look_change > 1.5):
            self.brightness_var.set(bright)

    # ================================================================ bandeja y cierre
    def _setup_tray(self):
        try:
            self._tray = _Tray(self._app_image, self._from_tray_open, self._from_tray_quit)
        except Exception as exc:  # noqa: BLE001
            log.info("Bandeja del sistema no disponible: %s", exc)
            self._tray = None

    def _from_tray_open(self):
        self.root.after(0, self._show)

    def _from_tray_quit(self):
        self.root.after(0, self.quit)

    def _show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_close(self):
        if self._tray and self.cfg["app"]["minimize_to_tray"]:
            self.root.withdraw()
        else:
            self.quit()

    def quit(self):
        """Cierra la ventana; quien llamó a run() detiene el controlador."""
        if self._save_job:
            self.root.after_cancel(self._save_job)
            self._save()
        if self._tray:
            self._tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
