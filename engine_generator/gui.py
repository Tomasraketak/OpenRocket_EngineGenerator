"""Grafické rozhraní generátoru .eng souborů pro OpenRocket / openMotor."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import curve, dataimport, engfile
from .config import Config, PresetStore, default_output_dir
from .plot import ThrustPlot, HAS_MATPLOTLIB

Point = Tuple[float, float]

APP_TITLE = "Generátor motorových souborů .eng pro OpenRocket"
SUPPORTED = [
    ("Podporovaná data", "*.xlsx *.xlsm *.csv *.txt *.tsv *.dat *.eng"),
    ("Excel", "*.xlsx *.xlsm"),
    ("Textová data", "*.csv *.txt *.tsv *.dat"),
    ("Motorový soubor", "*.eng"),
    ("Všechny soubory", "*.*"),
]


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(940, 620)

        self.cfg = Config()
        self.presets = PresetStore()

        self.workbook: Optional[dataimport.Workbook] = None
        self.raw_series: List[Point] = []   # naměřená data tak, jak přišla ze souboru
        self.display_raw: List[Point] = []  # naměřená data ve stejné ose jako křivka (šedě)
        self.points: List[Point] = []       # výsledná křivka pro .eng

        self._build_vars()
        self._build_menu()
        self._build_layout()
        self._refresh_preset_list()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh()

    # ------------------------------------------------------------------ #
    # Proměnné formulářů
    # ------------------------------------------------------------------ #
    def _build_vars(self) -> None:
        self.var_path = tk.StringVar()
        self.var_sheet = tk.StringVar()
        self.var_time_col = tk.StringVar()
        self.var_thrust_col = tk.StringVar()
        self.var_time_unit = tk.StringVar(value="sekundy")

        self.var_mode = tk.StringVar(value="raw")
        self.var_step = tk.IntVar(value=int(self.cfg["step_ms"]))
        self.var_max_points = tk.IntVar(value=32)
        self.var_threshold = tk.DoubleVar(value=5.0)
        self.var_smooth = tk.IntVar(value=1)
        self.var_baseline = tk.BooleanVar(value=True)
        self.var_trim = tk.BooleanVar(value=True)
        self.var_shift = tk.BooleanVar(value=True)
        self.var_preserve = tk.BooleanVar(value=True)

        self.var_cut_start = tk.DoubleVar(value=0.0)
        self.var_cut_end = tk.DoubleVar(value=0.0)
        self._cut_limits = (0.0, 0.0)
        self._updating_cut = False

        self.var_manual_step = tk.IntVar(value=int(self.cfg["step_ms"]))
        self.var_manual_duration = tk.DoubleVar(value=3.0)

        self.motor_vars: Dict[str, tk.StringVar] = {
            key: tk.StringVar() for key in
            ("name", "manufacturer", "diameter_mm", "length_mm", "delays",
             "propellant_g", "total_g", "comment")
        }
        self.motor_vars["delays"].set("P")

        self.var_preset = tk.StringVar()
        self.var_output_dir = tk.StringVar(value=self.cfg["output_dir"])
        self.var_filename = tk.StringVar()
        self.var_open_folder = tk.BooleanVar(value=bool(self.cfg["open_folder_after_export"]))
        self.var_overwrite = tk.BooleanVar(value=bool(self.cfg["overwrite_without_asking"]))
        self.var_status = tk.StringVar(value="Připraveno.")
        self.var_target_hint = tk.StringVar()
        self.var_cut_hint = tk.StringVar()
        self._filename_edited = False
        self.var_output_dir.trace_add("write", lambda *_: self._refresh_target_hint())

        for var in list(self.motor_vars.values()):
            var.trace_add("write", lambda *_: self._refresh_stats())

    # ------------------------------------------------------------------ #
    # Menu a rozvržení
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="Otevřít data…", command=self.on_browse_input)
        file_menu.add_command(label="Exportovat .eng", command=self.on_export)
        file_menu.add_command(label="Exportovat jako…", command=self.on_export_as)
        file_menu.add_separator()
        file_menu.add_command(label="Konec", command=self._on_close)
        menu.add_cascade(label="Soubor", menu=file_menu)

        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="Nápověda", command=self._show_help)
        menu.add_cascade(label="Nápověda", menu=help_menu)
        self.config(menu=menu)

    def _build_layout(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        left = ttk.Frame(paned, width=470)
        right = ttk.Frame(paned)
        paned.add(left, weight=0)
        paned.add(right, weight=1)

        notebook = ttk.Notebook(left)
        notebook.pack(fill="both", expand=True)
        notebook.add(self._build_import_tab(notebook), text="1 · Import dat")
        notebook.add(self._build_manual_tab(notebook), text="2 · Ruční křivka")
        notebook.add(self._build_motor_tab(notebook), text="3 · Motor")
        notebook.add(self._build_settings_tab(notebook), text="4 · Nastavení")

        export = ttk.Frame(right)
        export.pack(side="bottom", fill="x", pady=8)

        stats = ttk.LabelFrame(right, text="Souhrn křivky")
        stats.pack(side="bottom", fill="x", pady=(8, 0))

        self.cut_frame = ttk.LabelFrame(right, text="Ořez naměřené křivky")
        self._build_cut_controls(self.cut_frame)
        self.lbl_stats = ttk.Label(stats, justify="left", anchor="w")
        self.lbl_stats.pack(fill="x", padx=8, pady=6)
        self.txt_warnings = tk.Text(stats, height=4, wrap="word", relief="flat",
                                    background=self.cget("background"))
        self.txt_warnings.pack(fill="x", padx=8, pady=(0, 6))
        self.txt_warnings.configure(state="disabled")

        self.plot = ThrustPlot(right)
        self.plot.widget.pack(side="top", fill="both", expand=True)

        name_row = ttk.Frame(export)
        name_row.pack(fill="x")
        ttk.Label(name_row, text="Název souboru:").pack(side="left")
        entry = ttk.Entry(name_row, textvariable=self.var_filename)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Key>", lambda _e: setattr(self, "_filename_edited", True))

        button_row = ttk.Frame(export)
        button_row.pack(fill="x", pady=(6, 0))
        ttk.Button(button_row, text="Exportovat jako…", command=self.on_export_as).pack(side="right")
        ttk.Button(button_row, text="Vytvořit .eng", command=self.on_export).pack(side="right", padx=6)
        ttk.Label(button_row, textvariable=self.var_target_hint, foreground="#666666").pack(
            side="left", fill="x")

        status = ttk.Frame(self)
        status.pack(fill="x", side="bottom")
        ttk.Separator(status).pack(fill="x")
        ttk.Label(status, textvariable=self.var_status, anchor="w").pack(fill="x", padx=10, pady=4)

    def _build_cut_controls(self, parent: ttk.LabelFrame) -> None:
        """Posuvníky pro ruční zkrácení záznamu zleva a zprava."""
        body = ttk.Frame(parent)
        body.pack(fill="x", padx=8, pady=6)
        body.columnconfigure(1, weight=1)

        self.cut_scales = {}
        for row, (key, label, variable) in enumerate((
                ("start", "začátek:", self.var_cut_start),
                ("end", "konec:", self.var_cut_end))):
            ttk.Label(body, text=label, width=9).grid(row=row, column=0, sticky="w", pady=2)
            scale = ttk.Scale(body, orient="horizontal", variable=variable,
                              command=lambda _value, side=key: self._on_cut_change(side))
            scale.grid(row=row, column=1, sticky="ew", padx=6, pady=2)
            entry = ttk.Entry(body, width=8, justify="right")
            entry.grid(row=row, column=2, pady=2)
            entry.bind("<Return>", lambda _e, side=key: self._on_cut_typed(side))
            entry.bind("<FocusOut>", lambda _e, side=key: self._on_cut_typed(side))
            ttk.Label(body, text="s").grid(row=row, column=3, sticky="w", padx=(4, 0))
            self.cut_scales[key] = (scale, entry)

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Button(buttons, text="Zrušit ořez", command=self.on_reset_cut).pack(side="left")
        ttk.Label(buttons, textvariable=self.var_cut_hint, foreground="#666666").pack(
            side="left", padx=10)

    # ------------------------------------------------------------------ #
    # Záložka 1 - import
    # ------------------------------------------------------------------ #
    def _build_import_tab(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=10)

        source = ttk.LabelFrame(frame, text="Zdrojový soubor")
        source.pack(fill="x")
        row = ttk.Frame(source)
        row.pack(fill="x", padx=8, pady=8)
        ttk.Entry(row, textvariable=self.var_path).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Procházet…", command=self.on_browse_input).pack(side="left", padx=(6, 0))
        ttk.Label(source, text="Podporováno: .xlsx, .csv/.txt/.tsv a existující .eng",
                  foreground="#666666").pack(anchor="w", padx=8, pady=(0, 8))

        mapping = ttk.LabelFrame(frame, text="Sloupce")
        mapping.pack(fill="x", pady=(10, 0))
        grid = ttk.Frame(mapping)
        grid.pack(fill="x", padx=8, pady=8)
        grid.columnconfigure(1, weight=1)
        self.cmb_sheet = self._combo_row(grid, 0, "List:", self.var_sheet, self._on_sheet_change)
        self.cmb_time = self._combo_row(grid, 1, "Sloupec času:", self.var_time_col, self._on_map_change)
        self.cmb_thrust = self._combo_row(grid, 2, "Sloupec tahu:", self.var_thrust_col, self._on_map_change)
        self.cmb_unit = self._combo_row(grid, 3, "Jednotka času:", self.var_time_unit,
                                        self._on_map_change, values=["sekundy", "milisekundy"])

        options = ttk.LabelFrame(frame, text="Zpracování")
        options.pack(fill="x", pady=(10, 0))
        body = ttk.Frame(options)
        body.pack(fill="x", padx=8, pady=8)

        ttk.Radiobutton(body, text="Použít přesně naměřené body (doporučeno)", value="raw",
                        variable=self.var_mode, command=self._on_process_change).grid(
                            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text="do .eng jde každý naměřený vzorek; mění se jen to, co je zaškrtnuté níže",
                  foreground="#777777").grid(row=1, column=0, columnspan=2, sticky="w", padx=(20, 0))

        ttk.Radiobutton(body, text="Převzorkovat na pevný krok", value="step",
                        variable=self.var_mode, command=self._on_process_change).grid(
                            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        step_row = ttk.Frame(body)
        step_row.grid(row=3, column=0, columnspan=2, sticky="w", padx=(20, 0))
        ttk.Label(step_row, text="krok:").pack(side="left")
        step_combo = ttk.Combobox(step_row, textvariable=self.var_step, width=6, state="readonly",
                                  values=curve.STEP_CHOICES_MS)
        step_combo.pack(side="left", padx=4)
        step_combo.bind("<<ComboboxSelected>>", self._on_process_change)
        ttk.Label(step_row, text="ms").pack(side="left")

        ttk.Radiobutton(body, text="Zjednodušit na daný počet bodů", value="reduce",
                        variable=self.var_mode, command=self._on_process_change).grid(
                            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        points_row = ttk.Frame(body)
        points_row.grid(row=5, column=0, columnspan=2, sticky="w", padx=(20, 0))
        ttk.Label(points_row, text="max. bodů:").pack(side="left")
        spin = ttk.Spinbox(points_row, from_=4, to=200, textvariable=self.var_max_points, width=6,
                           command=self._on_process_change)
        spin.pack(side="left", padx=4)
        spin.bind("<Return>", self._on_process_change)

        extra = ttk.Frame(body)
        extra.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(extra, text="odečíst klidovou hodnotu siloměru", variable=self.var_baseline,
                        command=self._on_process_change).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(extra, text="oříznout na dobu hoření", variable=self.var_trim,
                        command=self._on_process_change).grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(extra, text="posunout zážeh na čas 0", variable=self.var_shift,
                        command=self._on_process_change).grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(extra, text="zachovat celkový impuls", variable=self.var_preserve,
                        command=self._on_process_change).grid(row=3, column=0, columnspan=3, sticky="w")

        ttk.Label(extra, text="práh hoření [% vrcholu]:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        threshold = ttk.Spinbox(extra, from_=0.0, to=50.0, increment=0.5, width=6,
                                textvariable=self.var_threshold, command=self._on_process_change)
        threshold.grid(row=4, column=1, sticky="w", padx=4, pady=(6, 0))
        threshold.bind("<Return>", self._on_process_change)

        ttk.Label(extra, text="vyhlazení [vzorků]:").grid(row=5, column=0, sticky="w")
        smooth = ttk.Spinbox(extra, from_=1, to=99, textvariable=self.var_smooth, width=6,
                             command=self._on_process_change)
        smooth.grid(row=5, column=1, sticky="w", padx=4)
        smooth.bind("<Return>", self._on_process_change)

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Přenést do ruční tabulky",
                   command=self.on_copy_to_manual).pack(side="left")
        ttk.Button(actions, text="Znovu načíst", command=self._reload_file).pack(side="left", padx=6)
        return frame

    def _combo_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.Variable,
                   handler, values: Sequence[str] = ()) -> ttk.Combobox:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        combo = ttk.Combobox(parent, textvariable=variable, state="readonly", values=list(values))
        combo.grid(row=row, column=1, sticky="ew", pady=3)
        combo.bind("<<ComboboxSelected>>", handler)
        return combo

    # ------------------------------------------------------------------ #
    # Záložka 2 - ruční zadání
    # ------------------------------------------------------------------ #
    def _build_manual_tab(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=10)

        top = ttk.LabelFrame(frame, text="Mřížka")
        top.pack(fill="x")
        row = ttk.Frame(top)
        row.pack(fill="x", padx=8, pady=8)
        ttk.Label(row, text="krok:").pack(side="left")
        combo = ttk.Combobox(row, textvariable=self.var_manual_step, width=6, state="readonly",
                             values=curve.STEP_CHOICES_MS)
        combo.pack(side="left", padx=4)
        ttk.Label(row, text="ms      doba hoření:").pack(side="left")
        ttk.Entry(row, textvariable=self.var_manual_duration, width=6).pack(side="left", padx=4)
        ttk.Label(row, text="s").pack(side="left")

        buttons = ttk.Frame(top)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(buttons, text="Vytvořit mřížku", command=self.on_make_grid).pack(side="left")
        ttk.Button(buttons, text="Přepočítat na krok", command=self.on_resnap).pack(side="left", padx=6)

        table = ttk.LabelFrame(frame, text="Body křivky (dvojklikem lze hodnotu přepsat)")
        table.pack(fill="both", expand=True, pady=(10, 0))
        holder = ttk.Frame(table)
        holder.pack(fill="both", expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(holder, columns=("time", "thrust"), show="headings", height=14)
        self.tree.heading("time", text="čas [s]")
        self.tree.heading("thrust", text="tah [N]")
        self.tree.column("time", width=110, anchor="e")
        self.tree.column("thrust", width=110, anchor="e")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", self._begin_edit)
        self.tree.bind("<Return>", self._begin_edit)
        self.tree.bind("<Delete>", lambda _e: self.on_delete_row())

        row_buttons = ttk.Frame(table)
        row_buttons.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(row_buttons, text="+ řádek", command=self.on_add_row).pack(side="left")
        ttk.Button(row_buttons, text="− řádek", command=self.on_delete_row).pack(side="left", padx=6)
        ttk.Button(row_buttons, text="Vynulovat tahy", command=self.on_clear_thrust).pack(side="left")
        ttk.Button(row_buttons, text="Smazat vše", command=self.on_clear_all).pack(side="right")
        return frame

    # ------------------------------------------------------------------ #
    # Záložka 3 - motor
    # ------------------------------------------------------------------ #
    def _build_motor_tab(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=10)

        fields = ttk.LabelFrame(frame, text="Údaje o motoru")
        fields.pack(fill="x")
        grid = ttk.Frame(fields)
        grid.pack(fill="x", padx=8, pady=8)
        grid.columnconfigure(1, weight=1)
        labels = [
            ("name", "Název / označení:", "např. H59 nebo Gragas_40mm"),
            ("manufacturer", "Výrobce:", "např. CRS"),
            ("diameter_mm", "Průměr [mm]:", "vnější průměr tělesa"),
            ("length_mm", "Délka [mm]:", "celková délka motoru"),
            ("delays", "Zpoždění [s]:", "P = plugged, jinak např. 5-7-10"),
            ("propellant_g", "Hmotnost paliva [g]:", "spálené palivo"),
            ("total_g", "Celková hmotnost [g]:", "motor včetně paliva"),
            ("comment", "Poznámka:", "zapíše se jako komentář do .eng"),
        ]
        for row, (key, label, hint) in enumerate(labels):
            ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(grid, textvariable=self.motor_vars[key]).grid(row=row, column=1, sticky="ew", pady=3)
            ttk.Label(grid, text=hint, foreground="#777777").grid(row=row, column=2, sticky="w", padx=(8, 0))

        ttk.Button(fields, text="Doplnit označení podle křivky",
                   command=self.on_fill_designation).pack(anchor="w", padx=8, pady=(0, 8))

        preset = ttk.LabelFrame(frame, text="Přednastavení")
        preset.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(preset)
        row.pack(fill="x", padx=8, pady=8)
        self.cmb_preset = ttk.Combobox(row, textvariable=self.var_preset, state="readonly")
        self.cmb_preset.pack(side="left", fill="x", expand=True)
        self.cmb_preset.bind("<<ComboboxSelected>>", lambda _e: self.on_load_preset())
        buttons = ttk.Frame(preset)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(buttons, text="Načíst", command=self.on_load_preset).pack(side="left")
        ttk.Button(buttons, text="Uložit jako…", command=self.on_save_preset).pack(side="left", padx=6)
        ttk.Button(buttons, text="Smazat", command=self.on_delete_preset).pack(side="left")
        return frame

    # ------------------------------------------------------------------ #
    # Záložka 4 - nastavení
    # ------------------------------------------------------------------ #
    def _build_settings_tab(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=10)

        output = ttk.LabelFrame(frame, text="Složka pro uložení .eng")
        output.pack(fill="x")
        row = ttk.Frame(output)
        row.pack(fill="x", padx=8, pady=8)
        ttk.Entry(row, textvariable=self.var_output_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Procházet…", command=self.on_browse_output).pack(side="left", padx=(6, 0))
        buttons = ttk.Frame(output)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(buttons, text="Výchozí složka OpenRocketu",
                   command=lambda: self.var_output_dir.set(default_output_dir())).pack(side="left")
        ttk.Button(buttons, text="Otevřít složku", command=self.on_open_output).pack(side="left", padx=6)
        ttk.Label(output, text="Nastavení se ukládá automaticky a platí i po zavření programu.",
                  foreground="#666666", wraplength=420, justify="left").pack(anchor="w", padx=8, pady=(0, 8))

        behaviour = ttk.LabelFrame(frame, text="Chování")
        behaviour.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(behaviour, text="po exportu otevřít složku",
                        variable=self.var_open_folder).pack(anchor="w", padx=8, pady=(8, 0))
        ttk.Checkbutton(behaviour, text="přepisovat existující soubor bez dotazu",
                        variable=self.var_overwrite).pack(anchor="w", padx=8, pady=(0, 8))

        info = ttk.LabelFrame(frame, text="Umístění konfigurace")
        info.pack(fill="x", pady=(10, 0))
        ttk.Label(info, text=self.cfg.path, wraplength=420, justify="left",
                  foreground="#555555").pack(anchor="w", padx=8, pady=(8, 0))
        ttk.Label(info, text=self.presets.path, wraplength=420, justify="left",
                  foreground="#555555").pack(anchor="w", padx=8, pady=(0, 8))
        if not HAS_MATPLOTLIB:
            ttk.Label(frame, text="matplotlib není nainstalován – graf se kreslí zjednodušeně.\n"
                                  "Volitelně: pip install matplotlib",
                      foreground="#a05000", justify="left").pack(anchor="w", pady=(10, 0))
        return frame

    # ------------------------------------------------------------------ #
    # Import dat
    # ------------------------------------------------------------------ #
    def on_browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Vyberte soubor s daty",
            initialdir=self.cfg["last_import_dir"] or os.path.expanduser("~"),
            filetypes=SUPPORTED)
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> None:
        try:
            if path.lower().endswith(".eng"):
                self._load_eng(path)
            else:
                self._load_table(path)
        except Exception as error:  # chyby souborů hlásíme uživateli, ne do konzole
            messagebox.showerror("Nepodařilo se načíst soubor", str(error), parent=self)
            return
        self.var_path.set(path)
        self.cfg["last_import_dir"] = os.path.dirname(path)
        self.cfg.save()

    def _load_eng(self, path: str) -> None:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            spec, points = engfile.parse_eng(fh.read())
        self.workbook = None
        self.raw_series = []          # u .eng není co zobrazovat na pozadí
        self.display_raw = []
        self._reset_cut_limits()
        self.points = list(points)
        self._apply_spec(spec)
        self.cmb_sheet.configure(values=[])
        self.var_status.set("Načten motorový soubor %s (%d bodů)." % (os.path.basename(path), len(points)))
        self._fill_table()
        self._refresh()

    def _load_table(self, path: str) -> None:
        workbook = dataimport.read_any(path)
        self.workbook = workbook
        names = [sheet.name for sheet in workbook.sheets]
        self.cmb_sheet.configure(values=names)
        best = dataimport.best_data_sheet(workbook) or (workbook.sheets[0] if workbook.sheets else None)
        if best is None:
            raise ValueError("Soubor neobsahuje žádný list s daty.")
        self.var_sheet.set(best.name)
        self._prefill_from_metadata(dataimport.scan_metadata(workbook))
        self._on_sheet_change()
        self.var_status.set("Načteno %s – list „%s“." % (os.path.basename(path), best.name))

    def _prefill_from_metadata(self, meta: Dict[str, Any]) -> None:
        """Předvyplní údaje o motoru z listu se souhrnem, pokud jsou prázdné."""
        mapping = {
            "name": "name",
            "manufacturer": "manufacturer",
            "diameter_mm": "diameter_mm",
            "length_mm": "length_mm",
        }
        for source, target in mapping.items():
            if source in meta and not self.motor_vars[target].get().strip():
                self.motor_vars[target].set(_pretty(meta[source]))
        if "propellant_kg" in meta and not self.motor_vars["propellant_g"].get().strip():
            self.motor_vars["propellant_g"].set(_pretty(float(meta["propellant_kg"]) * 1000.0))
        if "total_kg" in meta and not self.motor_vars["total_g"].get().strip():
            self.motor_vars["total_g"].set(_pretty(float(meta["total_kg"]) * 1000.0))

    def _current_sheet(self) -> Optional[dataimport.Sheet]:
        if not self.workbook:
            return None
        return self.workbook.by_name(self.var_sheet.get())

    def _on_sheet_change(self, _event: object = None) -> None:
        sheet = self._current_sheet()
        if sheet is None:
            return
        header = sheet.header()
        columns = dataimport.numeric_columns(sheet)
        labels = [self._column_label(header, index) for index in columns]
        self._column_index = {label: index for label, index in zip(labels, columns)}
        self.cmb_time.configure(values=labels)
        self.cmb_thrust.configure(values=labels)

        time_col, thrust_col, scale = dataimport.guess_columns(sheet)
        if time_col in columns:
            self.var_time_col.set(self._column_label(header, time_col))
        if thrust_col in columns:
            self.var_thrust_col.set(self._column_label(header, thrust_col))
        self.var_time_unit.set("milisekundy" if scale == 0.001 else "sekundy")
        self._on_map_change()

    @staticmethod
    def _column_label(header: Sequence[str], index: int) -> str:
        name = header[index] if index < len(header) else ""
        letter = chr(ord("A") + index) if index < 26 else "?"
        return "%s: %s" % (letter, name or "sloupec %d" % (index + 1))

    def _on_map_change(self, _event: object = None) -> None:
        sheet = self._current_sheet()
        if sheet is None:
            return
        mapping = getattr(self, "_column_index", {})
        time_col = mapping.get(self.var_time_col.get(), -1)
        thrust_col = mapping.get(self.var_thrust_col.get(), -1)
        if time_col < 0 or thrust_col < 0:
            return
        scale = 0.001 if self.var_time_unit.get().startswith("mili") else 1.0
        self.raw_series = dataimport.extract_series(sheet, time_col, thrust_col, scale)
        self._reset_cut_limits()
        self._on_process_change()

    # ------------------------------------------------------------------ #
    # Ruční ořez
    # ------------------------------------------------------------------ #
    def _reset_cut_limits(self) -> None:
        """Nastaví rozsah posuvníků podle načtených dat a zruší dosavadní ořez."""
        if len(self.raw_series) < 2:
            self._cut_limits = (0.0, 0.0)
            self.cut_frame.pack_forget()
            return
        low, high = self.raw_series[0][0], self.raw_series[-1][0]
        self._cut_limits = (low, high)
        self._updating_cut = True
        try:
            for key, (scale, _entry) in self.cut_scales.items():
                scale.configure(from_=low, to=high)
            self.var_cut_start.set(low)
            self.var_cut_end.set(high)
        finally:
            self._updating_cut = False
        self.cut_frame.pack(side="bottom", fill="x", pady=(8, 0), before=self.plot.widget)
        self._refresh_cut_labels()

    def on_reset_cut(self) -> None:
        low, high = self._cut_limits
        if high <= low:
            return
        self._updating_cut = True
        try:
            self.var_cut_start.set(low)
            self.var_cut_end.set(high)
        finally:
            self._updating_cut = False
        self._on_process_change()

    def _on_cut_change(self, side: str) -> None:
        """Posuvník se pohnul - pohlídat, aby se konce nepřekřížily."""
        if self._updating_cut or self._cut_limits[1] <= self._cut_limits[0]:
            return
        low, high = self._cut_limits
        gap = max((high - low) / 200.0, 1e-4)
        start, end = self.var_cut_start.get(), self.var_cut_end.get()
        self._updating_cut = True
        try:
            if side == "start" and start > end - gap:
                self.var_cut_start.set(max(low, end - gap))
            elif side == "end" and end < start + gap:
                self.var_cut_end.set(min(high, start + gap))
        finally:
            self._updating_cut = False
        self._on_process_change()

    def _on_cut_typed(self, side: str) -> str:
        """Hodnota zapsaná ručně do políčka vedle posuvníku."""
        _scale, entry = self.cut_scales[side]
        value = _safe_float(entry.get(), None)
        low, high = self._cut_limits
        if value is None or high <= low:
            self._refresh_cut_labels()
            return "break"
        variable = self.var_cut_start if side == "start" else self.var_cut_end
        self._updating_cut = True
        try:
            variable.set(min(max(value, low), high))
        finally:
            self._updating_cut = False
        self._on_cut_change(side)
        return "break"

    def _refresh_cut_labels(self) -> None:
        for key, (_scale, entry) in self.cut_scales.items():
            value = self.var_cut_start.get() if key == "start" else self.var_cut_end.get()
            if entry is not self.focus_get():
                entry.delete(0, "end")
                entry.insert(0, "%.3f" % value)
        low, high = self._cut_limits
        kept = self.var_cut_end.get() - self.var_cut_start.get()
        self.var_cut_hint.set("ponecháno %.3f s z %.3f s záznamu" % (max(kept, 0.0), high - low)
                              if high > low else "")

    def _reload_file(self) -> None:
        if self.var_path.get():
            self.load_file(self.var_path.get())

    def _on_process_change(self, _event: object = None) -> None:
        if not self.raw_series:
            self._refresh()
            return
        low, high = self._cut_limits
        cut_start = self.var_cut_start.get() if high > low else None
        cut_end = self.var_cut_end.get() if high > low else None
        options = curve.ProcessOptions(
            subtract_baseline=self.var_baseline.get(),
            trim_to_burn=self.var_trim.get(),
            threshold_pct=_safe_float(self.var_threshold.get(), 5.0),
            shift_to_zero=self.var_shift.get(),
            mode=self.var_mode.get(),
            step_ms=int(self.var_step.get() or 100),
            max_points=int(self.var_max_points.get() or 32),
            smooth_window=int(self.var_smooth.get() or 1),
            preserve_impulse=self.var_preserve.get(),
            cut_start_s=cut_start,
            cut_end_s=cut_end,
        )
        result = curve.process_detailed(self.raw_series, options)
        self.points = result.points
        # Šedě je vidět i kus záznamu za ořezem, ale ne celý - jinak by se osa
        # roztáhla přes několik sekund ticha před zážehem a po něm.
        self.display_raw = _around(result.baseline, self.points)
        self.cfg["step_ms"] = options.step_ms
        self._fill_table()
        self._refresh()

    def on_copy_to_manual(self) -> None:
        if not self.points:
            messagebox.showinfo("Není co přenést", "Nejprve načtěte data.", parent=self)
            return
        self.var_manual_duration.set(round(self.points[-1][0], 3))
        self._fill_table()
        self.var_status.set("Křivka je v ruční tabulce, můžete ji upravit.")

    # ------------------------------------------------------------------ #
    # Ruční tabulka
    # ------------------------------------------------------------------ #
    def _fill_table(self) -> None:
        focused = self.tree.index(self.tree.focus()) if self.tree.focus() else -1
        self.tree.delete(*self.tree.get_children())
        for time_s, thrust in self.points:
            self.tree.insert("", "end", values=("%.3f" % time_s, "%.2f" % thrust))
        children = self.tree.get_children()
        if 0 <= focused < len(children):
            self.tree.focus(children[focused])
            self.tree.selection_set(children[focused])

    def _read_table(self) -> List[Point]:
        points: List[Point] = []
        for item in self.tree.get_children():
            time_text, thrust_text = self.tree.item(item, "values")
            points.append((_safe_float(time_text, 0.0), _safe_float(thrust_text, 0.0)))
        return points

    def on_make_grid(self) -> None:
        step = int(self.var_manual_step.get() or 100)
        duration = _safe_float(self.var_manual_duration.get(), 3.0)
        if duration <= 0:
            messagebox.showwarning("Neplatná doba", "Doba hoření musí být větší než 0 s.", parent=self)
            return
        old = {round(t, 4): f for t, f in self.points}
        self.points = [(t, old.get(round(t, 4), 0.0)) for t, _ in curve.make_grid(step, duration)]
        self._fill_table()
        self._refresh()

    def on_resnap(self) -> None:
        if len(self.points) < 2:
            messagebox.showinfo("Prázdná křivka", "Nejprve vytvořte mřížku nebo načtěte data.", parent=self)
            return
        self.points = curve.resnap(self.points, int(self.var_manual_step.get() or 100))
        self._fill_table()
        self._refresh()

    def on_add_row(self) -> None:
        step = int(self.var_manual_step.get() or 100) / 1000.0
        last = self.points[-1][0] if self.points else -step
        self.points = self.points + [(round(last + step, 6), 0.0)]
        self._fill_table()
        self._refresh()

    def on_delete_row(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        indexes = {self.tree.index(item) for item in selection}
        self.points = [p for i, p in enumerate(self.points) if i not in indexes]
        self._fill_table()
        self._refresh()

    def on_clear_thrust(self) -> None:
        self.points = [(t, 0.0) for t, _ in self.points]
        self._fill_table()
        self._refresh()

    def on_clear_all(self) -> None:
        if self.points and not messagebox.askyesno("Smazat vše", "Opravdu smazat celou křivku?", parent=self):
            return
        self.points = []
        self.raw_series = []
        self.display_raw = []
        self._reset_cut_limits()
        self._fill_table()
        self._refresh()

    def _begin_edit(self, event: tk.Event) -> str:
        """Editace buňky přímo v tabulce."""
        item = self.tree.focus() if event.type == tk.EventType.KeyPress else self.tree.identify_row(event.y)
        column = "#2" if event.type == tk.EventType.KeyPress else self.tree.identify_column(event.x)
        if not item or column not in ("#1", "#2"):
            return "break"
        bbox = self.tree.bbox(item, column)
        if not bbox:
            return "break"
        x, y, width, height = bbox
        current = self.tree.item(item, "values")[0 if column == "#1" else 1]
        entry = ttk.Entry(self.tree, justify="right")
        entry.insert(0, current)
        entry.select_range(0, "end")
        entry.place(x=x, y=y, width=width, height=height)
        entry.focus_set()

        def commit(move_down: bool) -> None:
            value = _safe_float(entry.get(), None)
            entry.destroy()
            if value is None:
                return
            index = self.tree.index(item)
            time_s, thrust = self.points[index]
            if column == "#1":
                self.points[index] = (round(value, 6), thrust)
                self.points = curve.dedupe_times(sorted(self.points, key=lambda p: p[0]))
            else:
                self.points[index] = (time_s, max(0.0, round(value, 4)))
            self._fill_table()
            self._refresh()
            children = self.tree.get_children()
            if move_down and index + 1 < len(children):
                self.tree.selection_set(children[index + 1])
                self.tree.focus(children[index + 1])
                self.tree.see(children[index + 1])

        entry.bind("<Return>", lambda _e: commit(True))
        entry.bind("<FocusOut>", lambda _e: commit(False))
        entry.bind("<Escape>", lambda _e: entry.destroy())
        return "break"

    # ------------------------------------------------------------------ #
    # Motor a přednastavení
    # ------------------------------------------------------------------ #
    def _current_spec(self) -> engfile.MotorSpec:
        comment = self.motor_vars["comment"].get().strip()
        return engfile.MotorSpec(
            name=self.motor_vars["name"].get().strip(),
            diameter_mm=_safe_float(self.motor_vars["diameter_mm"].get(), 0.0),
            length_mm=_safe_float(self.motor_vars["length_mm"].get(), 0.0),
            delays=self.motor_vars["delays"].get().strip() or "P",
            propellant_kg=_safe_float(self.motor_vars["propellant_g"].get(), 0.0) / 1000.0,
            total_kg=_safe_float(self.motor_vars["total_g"].get(), 0.0) / 1000.0,
            manufacturer=self.motor_vars["manufacturer"].get().strip(),
            comments=[comment] if comment else [],
        )

    def _apply_spec(self, spec: engfile.MotorSpec) -> None:
        self.motor_vars["name"].set(spec.name)
        self.motor_vars["manufacturer"].set(spec.manufacturer)
        self.motor_vars["diameter_mm"].set(_pretty(spec.diameter_mm))
        self.motor_vars["length_mm"].set(_pretty(spec.length_mm))
        self.motor_vars["delays"].set(spec.delays or "P")
        self.motor_vars["propellant_g"].set(_pretty(spec.propellant_kg * 1000.0))
        self.motor_vars["total_g"].set(_pretty(spec.total_kg * 1000.0))
        self.motor_vars["comment"].set("; ".join(spec.comments))

    def on_fill_designation(self) -> None:
        if not self.points:
            return
        name = engfile.designation(self.points)
        if name:
            self.motor_vars["name"].set(name)

    def _refresh_preset_list(self) -> None:
        names = self.presets.names()
        self.cmb_preset.configure(values=names)
        last = self.cfg["last_preset"]
        if last in names:
            self.var_preset.set(last)
            self.on_load_preset(quiet=True)

    def on_load_preset(self, quiet: bool = False) -> None:
        name = self.var_preset.get()
        values = self.presets.get(name)
        if not values:
            if not quiet:
                messagebox.showinfo("Přednastavení", "Vyberte uložené přednastavení.", parent=self)
            return
        for key, var in self.motor_vars.items():
            if key in values:
                var.set(str(values[key]))
        self.cfg["last_preset"] = name
        self.cfg.save()
        self.var_status.set("Načteno přednastavení „%s“." % name)

    def on_save_preset(self) -> None:
        default = self.var_preset.get() or self.motor_vars["name"].get()
        name = simpledialog.askstring("Uložit přednastavení", "Název přednastavení:",
                                      initialvalue=default, parent=self)
        if not name:
            return
        self.presets.put(name.strip(), {key: var.get() for key, var in self.motor_vars.items()})
        self.cmb_preset.configure(values=self.presets.names())
        self.var_preset.set(name.strip())
        self.cfg["last_preset"] = name.strip()
        self.cfg.save()
        self.var_status.set("Přednastavení „%s“ uloženo." % name.strip())

    def on_delete_preset(self) -> None:
        name = self.var_preset.get()
        if not name or not messagebox.askyesno("Smazat", "Smazat přednastavení „%s“?" % name, parent=self):
            return
        self.presets.delete(name)
        self.cmb_preset.configure(values=self.presets.names())
        self.var_preset.set("")

    # ------------------------------------------------------------------ #
    # Nastavení a export
    # ------------------------------------------------------------------ #
    def on_browse_output(self) -> None:
        path = filedialog.askdirectory(title="Složka pro .eng soubory",
                                       initialdir=self.var_output_dir.get() or os.path.expanduser("~"))
        if path:
            self.var_output_dir.set(path)
            self._save_settings()

    def on_open_output(self) -> None:
        path = self.var_output_dir.get()
        if not os.path.isdir(path):
            messagebox.showwarning("Složka neexistuje", path, parent=self)
            return
        _open_in_file_manager(path)

    def on_export(self) -> None:
        directory = self.var_output_dir.get().strip()
        filename = self.var_filename.get().strip() or engfile.suggest_filename(self._current_spec(), self.points)
        if not filename.lower().endswith(".eng"):
            filename += ".eng"
        self._write(os.path.join(directory, filename))

    def on_export_as(self) -> None:
        spec = self._current_spec()
        path = filedialog.asksaveasfilename(
            title="Uložit .eng jako",
            defaultextension=".eng",
            initialdir=self.var_output_dir.get() or os.path.expanduser("~"),
            initialfile=self.var_filename.get().strip() or engfile.suggest_filename(spec, self.points),
            filetypes=[("Motorový soubor", "*.eng"), ("Všechny soubory", "*.*")])
        if path:
            self._write(path, ask_overwrite=False)

    def _write(self, path: str, ask_overwrite: bool = True) -> None:
        spec = self._current_spec()
        errors, warnings = engfile.validate(spec, self.points)
        if errors:
            messagebox.showerror("Soubor nelze vytvořit", "\n".join("• " + e for e in errors), parent=self)
            return
        if warnings and not messagebox.askyesno(
                "Zkontrolujte prosím", "\n".join("• " + w for w in warnings) + "\n\nPokračovat?", parent=self):
            return
        directory = os.path.dirname(path)
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Složku nelze vytvořit", str(error), parent=self)
            return
        if (ask_overwrite and os.path.exists(path) and not self.var_overwrite.get()
                and not messagebox.askyesno("Přepsat soubor",
                                            "Soubor už existuje:\n%s\n\nPřepsat?" % path, parent=self)):
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(engfile.build_eng_text(spec, self.points))
        except OSError as error:
            messagebox.showerror("Uložení selhalo", str(error), parent=self)
            return

        self.var_filename.set(os.path.basename(path))
        self._filename_edited = True
        self._save_settings()
        self.var_status.set("Uloženo: %s" % path)
        if self.var_open_folder.get():
            _open_in_file_manager(os.path.dirname(path))
        messagebox.showinfo("Hotovo",
                            "Soubor byl vytvořen:\n%s\n\nV OpenRocketu se motor objeví po restartu "
                            "programu." % path, parent=self)

    def _save_settings(self) -> None:
        self.cfg["output_dir"] = self.var_output_dir.get().strip() or default_output_dir()
        self.cfg["open_folder_after_export"] = bool(self.var_open_folder.get())
        self.cfg["overwrite_without_asking"] = bool(self.var_overwrite.get())
        self.cfg["step_ms"] = int(self.var_step.get() or 100)
        self.cfg.save()

    def _on_close(self) -> None:
        self._save_settings()
        self.destroy()

    # ------------------------------------------------------------------ #
    # Překreslení
    # ------------------------------------------------------------------ #
    def _refresh(self) -> None:
        self.plot.draw(self.points, self.display_raw,
                       self.motor_vars["name"].get() or "Tahová křivka")
        self._refresh_stats()
        self._refresh_cut_labels()
        if not self._filename_edited:
            self.var_filename.set(engfile.suggest_filename(self._current_spec(), self.points))
        self._refresh_target_hint()

    def _refresh_target_hint(self) -> None:
        directory = self.var_output_dir.get().strip()
        self.var_target_hint.set("uloží se do: %s" % (directory or "(nevybráno)"))

    def _refresh_stats(self) -> None:
        if not self.points:
            self.lbl_stats.configure(text="Zatím žádná křivka.")
            self._set_warnings([])
            return
        info = engfile.summary(self.points)
        self.lbl_stats.configure(text=(
            "Bodů: %(points)d      Doba hoření: %(burn_s).3f s      Vrcholový tah: %(peak_n).1f N\n"
            "Průměrný tah: %(average_n).1f N      Celkový impuls: %(impulse_ns).1f N·s\n"
            "Třída: %(class)s (%(class_pct).0f %% třídy)      Označení podle křivky: %(designation)s"
        ) % info)
        _errors, warnings = engfile.validate(self._current_spec(), self.points)
        self._set_warnings(warnings)

    def _set_warnings(self, warnings: Sequence[str]) -> None:
        self.txt_warnings.configure(state="normal")
        self.txt_warnings.delete("1.0", "end")
        if warnings:
            self.txt_warnings.insert("1.0", "\n".join("• " + w for w in warnings))
        self.txt_warnings.configure(state="disabled", foreground="#a05000")

    def _show_help(self) -> None:
        messagebox.showinfo("Nápověda", (
            "1) Import dat – načtěte .xlsx nebo .csv ze zkušebního stavu, zkontrolujte "
            "sloupce a zvolte krok převzorkování.\n\n"
            "2) Ruční křivka – nebo si křivku naklikejte sami: zvolte krok 100–1000 ms, "
            "dobu hoření, vytvořte mřížku a dvojklikem zapisujte tah v newtonech.\n\n"
            "3) Motor – vyplňte název, rozměry a hmotnosti; kombinaci lze uložit jako "
            "přednastavení pro příště.\n\n"
            "4) Nastavení – vyberte složku, kam se .eng ukládá (výchozí je složka "
            "ThrustCurves OpenRocketu). Volba se pamatuje.\n\n"
            "Nakonec klikněte na „Vytvořit .eng“."), parent=self)


# ---------------------------------------------------------------------- #
# Pomocné funkce
# ---------------------------------------------------------------------- #

def _safe_float(value: Any, fallback: Optional[float]) -> Any:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return fallback


def _around(background: Sequence[Point], points: Sequence[Point]) -> List[Point]:
    """Ořízne pozadí grafu na okolí výsledné křivky."""
    if not points or not background:
        return list(background)
    duration = points[-1][0] - points[0][0]
    margin = max(0.25 * duration, 0.5)
    low, high = points[0][0] - margin, points[-1][0] + margin
    return [(t, f) for t, f in background if low <= t <= high]


def _pretty(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return ("%.4f" % number).rstrip("0").rstrip(".") or "0"


def _open_in_file_manager(path: str) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass  # otevření průzkumníka není kritické


def main(argv: Sequence[str] = ()) -> int:
    app = App()
    if argv:
        app.load_file(argv[0])
    app.mainloop()
    return 0
