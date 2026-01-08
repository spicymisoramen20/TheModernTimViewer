# input_controller.py
class InputController:
    """
    Dumb input layer:
      - Detects input gestures / gating (Space-to-pan)
      - Forwards raw events to ViewportCanvas methods
      - Syncs app.zoom_var from viewport.get_zoom()

    No math here. All pan/zoom behavior is in viewport.py.
    """

    def __init__(self, app, viewport):
        self.app = app
        self.viewport = viewport
        self.canvas = viewport.canvas

        self._space_down = False
        self._pan_active = False

    def install(self):
        # Space gating (bind on app so it works even when canvas isn't focused)
        self.app.bind_all("<KeyPress-space>", self._on_space_down)
        self.app.bind_all("<KeyRelease-space>", self._on_space_up)

        # Mouse drag panning (only when space held)
        self.canvas.bind("<ButtonPress-1>", self._on_pan_press)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_pan_release)

        # Wheel zoom (always)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel_zoom)  # Windows/macOS
        self.canvas.bind("<Button-4>", self._on_linux_wheel_up)     # Linux
        self.canvas.bind("<Button-5>", self._on_linux_wheel_down)   # Linux

        # Make sure canvas can receive events
        self.canvas.bind("<Enter>", lambda e: self.canvas.focus_set())
        self.canvas.bind("<Button-1>", lambda e: self.canvas.focus_set(), add=True)

    # -----------------------------
    # Space gate
    # -----------------------------
    def _on_space_down(self, _e=None):
        self._space_down = True
        try:
            self.canvas.configure(cursor="fleur")
        except Exception:
            pass
        return "break"

    def _on_space_up(self, _e=None):
        self._space_down = False
        self._pan_active = False
        self.viewport.pan_end()
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass
        return "break"

    # -----------------------------
    # Panning
    # -----------------------------
    def _on_pan_press(self, e):
        if not self._space_down:
            return
        self._pan_active = True
        self.viewport.pan_begin(e.x, e.y)

    def _on_pan_move(self, e):
        if not (self._space_down and self._pan_active):
            return
        self.viewport.pan_move(e.x, e.y)

    def _on_pan_release(self, _e):
        self._pan_active = False
        self.viewport.pan_end()

    # -----------------------------
    # Wheel zoom
    # -----------------------------
    def _on_linux_wheel_up(self, e):
        self.viewport.wheel_zoom(e.x, e.y, delta=+1)
        self.app.zoom_var.set(self.viewport.get_zoom())

    def _on_linux_wheel_down(self, e):
        self.viewport.wheel_zoom(e.x, e.y, delta=-1)
        self.app.zoom_var.set(self.viewport.get_zoom())

    def _on_mousewheel_zoom(self, e):
        # e.delta is typically +/-120 on Windows, small on macOS trackpads
        self.viewport.wheel_zoom(e.x, e.y, delta=e.delta)
        self.app.zoom_var.set(self.viewport.get_zoom())
