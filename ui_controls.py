# ui_controls.py
import tkinter as tk
from tkinter import ttk


def build_ui(app):
    app.columnconfigure(1, weight=1)
    app.rowconfigure(0, weight=1)

    sidebar = ttk.Frame(app, padding=8)
    sidebar.grid(row=0, column=0, sticky="nsw")
    sidebar.rowconfigure(2, weight=1)

    main = ttk.Frame(app, padding=8)
    main.grid(row=0, column=1, sticky="nsew")
    main.rowconfigure(2, weight=1)
    main.columnconfigure(0, weight=1)

    # top buttons
    btn_row = ttk.Frame(sidebar)
    btn_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    for i in range(2):
        btn_row.columnconfigure(i, weight=1)

    ttk.Button(btn_row, text="Load TIMs…", command=app.load_tims).grid(row=0, column=0, sticky="ew", padx=(0, 4))
    ttk.Button(btn_row, text="Export Image…", command=app.export_image).grid(row=0, column=1, sticky="ew", padx=(4, 0))

    edit_row = ttk.Frame(sidebar)
    edit_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
    for i in range(3):
        edit_row.columnconfigure(i, weight=1)

    ttk.Button(edit_row, text="Export Indices…", command=app.export_indices).grid(row=0, column=0, sticky="ew", padx=(0, 4))
    ttk.Button(edit_row, text="Import Indices (Resize)…", command=app.import_indices_resize).grid(row=0, column=1, sticky="ew", padx=(4, 4))
    ttk.Button(edit_row, text="Save TIM As…", command=app.save_tim_as).grid(row=0, column=2, sticky="ew", padx=(4, 0))

    nb = ttk.Notebook(sidebar)
    nb.grid(row=2, column=0, sticky="nsew")

    files_tab = ttk.Frame(nb, padding=6)
    cluts_tab = ttk.Frame(nb, padding=6)
    nb.add(files_tab, text="Files")
    nb.add(cluts_tab, text="CLUTs")

    files_tab.rowconfigure(0, weight=1)
    files_tab.columnconfigure(0, weight=1)
    app.files_list = tk.Listbox(files_tab, height=20)
    app.files_list.grid(row=0, column=0, sticky="nsew")
    app.files_list.bind("<<ListboxSelect>>", app.on_select_file)

    cluts_tab.rowconfigure(0, weight=1)
    cluts_tab.columnconfigure(0, weight=1)
    app.clut_list = tk.Listbox(cluts_tab, height=20)
    app.clut_list.grid(row=0, column=0, sticky="nsew")
    app.clut_list.bind("<<ListboxSelect>>", app.on_select_clut)

    app.info_var = tk.StringVar(value="Load TIMs to begin.")
    ttk.Label(sidebar, textvariable=app.info_var, wraplength=320).grid(row=3, column=0, sticky="ew", pady=(8, 0))

    # Zoom bar
    topbar = ttk.Frame(main)
    topbar.grid(row=0, column=0, sticky="ew")
    topbar.columnconfigure(1, weight=1)

    app.zoom_var = tk.DoubleVar(value=4.0)
    ttk.Label(topbar, text="Zoom").grid(row=0, column=0, sticky="w")
    ttk.Scale(
        topbar,
        from_=0.5,
        to=16.0,
        variable=app.zoom_var,
        command=lambda _e: app._on_zoom_slider(),
    ).grid(row=0, column=1, sticky="ew", padx=8)
    ttk.Button(topbar, text="Fit", command=app.zoom_fit).grid(row=0, column=2, sticky="e")
    ttk.Button(topbar, text="Controls", command=app.show_controls).grid(row=0, column=3, sticky="e")

    # Animation controls
    animbar = ttk.Frame(main)
    animbar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    animbar.columnconfigure(13, weight=1)

    app.anim_enable = tk.BooleanVar(value=False)
    ttk.Checkbutton(animbar, text="Animate", variable=app.anim_enable, command=app.on_anim_toggle).grid(row=0, column=0, padx=(0, 8))

    ttk.Label(animbar, text="Frame W").grid(row=0, column=1, sticky="e")
    app.frame_w_var = tk.IntVar(value=0)
    ttk.Entry(animbar, width=6, textvariable=app.frame_w_var).grid(row=0, column=2, padx=(4, 12))

    ttk.Label(animbar, text="Frame H").grid(row=0, column=3, sticky="e")
    app.frame_h_var = tk.IntVar(value=0)
    ttk.Entry(animbar, width=6, textvariable=app.frame_h_var).grid(row=0, column=4, padx=(4, 12))

    ttk.Label(animbar, text="Dir").grid(row=0, column=5, sticky="e")
    app.dir_var = tk.StringVar(value="horizontal")
    ttk.Combobox(animbar, width=10, textvariable=app.dir_var, values=["horizontal", "vertical"], state="readonly").grid(row=0, column=6, padx=(4, 12))

    ttk.Label(animbar, text="FPS").grid(row=0, column=7, sticky="e")
    app.fps_var = tk.DoubleVar(value=8.0)
    ttk.Entry(animbar, width=6, textvariable=app.fps_var).grid(row=0, column=8, padx=(4, 12))

    app.loop_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(animbar, text="Loop", variable=app.loop_var).grid(row=0, column=9, padx=(0, 12))

    ttk.Button(animbar, text="Apply", command=app.rebuild_frames).grid(row=0, column=10, padx=(0, 6))
    ttk.Button(animbar, text="Play", command=app.play_anim).grid(row=0, column=11, padx=(0, 6))
    ttk.Button(animbar, text="Pause", command=app.pause_anim).grid(row=0, column=12, padx=(0, 10))

    app.scrub_var = tk.IntVar(value=0)
    app.scrub = ttk.Scale(animbar, from_=0, to=0, variable=app.scrub_var, command=lambda _e: app.on_scrub())
    app.scrub.grid(row=0, column=13, sticky="ew")

    # Viewport
    app.viewport = app._create_viewport(main)
    app.viewport.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
