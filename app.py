# app.py
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

import actions
import ui_controls
from input_controller import InputController

from viewport import ViewportCanvas

from timlib import (
    TimImage, TimClut,
    parse_tim, extract_cluts_from_raw_block,
    render_tim_to_image,
    auto_detect_frames, slice_frames_fixed,
)


class TimViewerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TIM Loader / CLUT Swapper / Image Exporter (Viewport Zoom)")
        self.geometry("1300x780")

        self.tim_files: List[TimImage] = []
        self.all_cluts: List[TimClut] = []
        self.current_tim: Optional[TimImage] = None

        self.current_sheet_pil = None
        self.current_frames_pil = []
        self.current_frame_idx: int = 0

        self._anim_after_id: Optional[str] = None
        self._controls_win: Optional[tk.Toplevel] = None


        # Build UI (widgets + viewport)
        ui_controls.build_ui(self)

        # Install input controller (all bindings live there)
        self.input = InputController(self, self.viewport)
        self.input.install()
        

    # -------------------------------------------------
    # Viewport creation hook (used by ui_controls)
    # -------------------------------------------------
    def _create_viewport(self, parent):
        return ViewportCanvas(parent)

    # -----------------------------
    # Actions (delegated)
    # -----------------------------
    def export_image(self):
        actions.export_image(self)

    def export_indices(self):
        actions.export_indices(self)

    def import_indices_resize(self):
        actions.import_indices_resize(self)

    def save_tim_as(self):
        actions.save_tim_as(self)

    def show_controls(self):
        # Re-focus existing window if already open
        if self._controls_win is not None:
            try:
                if self._controls_win.winfo_exists():
                    self._controls_win.deiconify()
                    self._controls_win.lift()
                    self._controls_win.focus_force()
                    return
            except Exception:
                pass
            self._controls_win = None

        win = tk.Toplevel(self)
        self._controls_win = win
        win.title("Controls")
        win.resizable(False, False)
        win.transient(self)

        def _on_close():
            try:
                win.destroy()
            except Exception:
                pass
            self._controls_win = None

        win.protocol("WM_DELETE_WINDOW", _on_close)

        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Controls", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Separator(frm).pack(fill="x", pady=(8, 10))

        # NOTE: If you want this to be perfectly accurate, we can later
        # have InputController provide a list of bindings. For now, keep it simple.
        lines = [
            "Pan: Left-click + drag",
            "Zoom: Mouse wheel",
            "Fit: Fit button (top bar)",
            "Animation: Use Animate / Play / Pause and the scrub bar",
        ]
        ttk.Label(frm, text="\n".join(lines), justify="left").pack(anchor="w")

        ttk.Separator(frm).pack(fill="x", pady=(10, 10))

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Close", command=_on_close).pack(side="right")

        # Position near the main window
        try:
            self.update_idletasks()
            x = self.winfo_rootx() + 40
            y = self.winfo_rooty() + 40
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass

        win.lift()
        win.focus_force()

    # -----------------------------
    # Status helper
    # -----------------------------
    def set_status(self, extra: str = ""):
        if self.current_tim is None:
            self.info_var.set("Load TIMs to begin.")
            return

        base = os.path.basename(self.current_tim.path)
        bpp_name = {0: "4bpp", 1: "8bpp", 2: "16bpp", 3: "24bpp"}.get(self.current_tim.bpp_mode, f"mode{self.current_tim.bpp_mode}")
        pal = self.current_tim.applied_clut.label if self.current_tim.applied_clut else "(no CLUT)"
        frames = f" | frames {len(self.current_frames_pil)}" if (self.anim_enable.get() and self.current_frames_pil) else ""
        self.info_var.set(f"{base} | {bpp_name} | {self.current_tim.pixel_width()}×{self.current_tim.img_h}{frames} | CLUT: {pal}{extra}")

    # -----------------------------
    # Load / select
    # -----------------------------
    def load_tims(self):
        paths = filedialog.askopenfilenames(
            title="Select TIM files",
            filetypes=[("PlayStation TIM", "*.tim"), ("All files", "*.*")],
        )
        if not paths:
            return

        loaded: List[TimImage] = []
        cluts: List[TimClut] = []
        errors: List[str] = []

        for p in paths:
            try:
                t = parse_tim(p)
                loaded.append(t)
                cluts.extend(extract_cluts_from_raw_block(t))
            except Exception as e:
                errors.append(f"{os.path.basename(p)}: {e}")

        if not loaded:
            messagebox.showerror("No TIMs loaded", "Could not load any TIM files.\n\n" + "\n".join(errors[:20]))
            return

        self.tim_files = loaded
        self.all_cluts = cluts

        self.files_list.delete(0, tk.END)
        for t in self.tim_files:
            base = os.path.basename(t.path)
            bpp_name = {0: "4bpp", 1: "8bpp", 2: "16bpp", 3: "24bpp"}.get(t.bpp_mode, f"mode{t.bpp_mode}")
            self.files_list.insert(tk.END, f"{base} [{bpp_name}]")

        self.clut_list.delete(0, tk.END)
        for c in self.all_cluts:
            self.clut_list.insert(tk.END, c.label)

        self.files_list.selection_clear(0, tk.END)
        self.files_list.selection_set(0)
        self.files_list.event_generate("<<ListboxSelect>>")

        msg = f"Loaded {len(self.tim_files)} TIM(s), {len(self.all_cluts)} CLUT row(s)."
        if errors:
            msg += f"  ({len(errors)} failed)"
        self.info_var.set(msg)

        if errors:
            messagebox.showwarning("Some files failed", "\n".join(errors[:25]))

    def on_select_file(self, _evt=None):
        sel = self.files_list.curselection()
        if not sel:
            return
        idx = sel[0]
        self.current_tim = self.tim_files[idx]

        if self.current_tim.bpp_mode in (0, 1) and self.current_tim.applied_clut is None:
            own = extract_cluts_from_raw_block(self.current_tim)
            if own:
                self.current_tim.applied_clut = own[0]

        self.rebuild_sheet_and_frames(auto=True)
        self.set_status()

    def on_select_clut(self, _evt=None):
        if self.current_tim is None:
            return
        sel = self.clut_list.curselection()
        if not sel:
            return
        c = self.all_cluts[sel[0]]

        if self.current_tim.bpp_mode not in (0, 1):
            messagebox.showinfo("No CLUT needed", "This TIM is 16bpp (direct color). CLUTs do not apply.")
            return

        self.current_tim.applied_clut = c
        self.rebuild_sheet_and_frames(auto=False)
        self.set_status()

    # -----------------------------
    # Animation + rendering state
    # -----------------------------
    def on_anim_toggle(self):
        if not self.anim_enable.get():
            self.pause_anim()
        self._push_current_pil_to_viewport(recenter=False, force=True)
        self.set_status()

    def rebuild_sheet_and_frames(self, auto: bool):
        self.pause_anim()
        if self.current_tim is None:
            return

        try:
            sheet = render_tim_to_image(self.current_tim, self.current_tim.applied_clut)
        except Exception as e:
            messagebox.showerror("Render error", str(e))
            return

        self.current_sheet_pil = sheet

        if auto:
            fw, fh, direction, count = auto_detect_frames(sheet.width, sheet.height)
            if count > 1 or self.frame_w_var.get() == 0 or self.frame_h_var.get() == 0:
                self.frame_w_var.set(fw)
                self.frame_h_var.set(fh)
                self.dir_var.set(direction)
                self.scrub_var.set(0)

        self.rebuild_frames()

    def rebuild_frames(self):
        if self.current_sheet_pil is None:
            self.current_frames_pil = []
            self.current_frame_idx = 0
            self.scrub.configure(to=0)
            self.scrub_var.set(0)
            self._push_current_pil_to_viewport(recenter=True, force=True)
            return

        sheet = self.current_sheet_pil
        fw = int(self.frame_w_var.get() or 0)
        fh = int(self.frame_h_var.get() or 0)
        direction = self.dir_var.get()

        if fw <= 0 or fh <= 0:
            fw, fh, direction, _ = auto_detect_frames(sheet.width, sheet.height)

        frames = slice_frames_fixed(sheet, fw, fh, direction)
        self.current_frames_pil = frames
        self.current_frame_idx = max(0, min(self.current_frame_idx, len(frames) - 1)) if frames else 0

        max_idx = max(0, len(frames) - 1)
        self.scrub.configure(to=max_idx)
        self.scrub_var.set(self.current_frame_idx)

        self._push_current_pil_to_viewport(recenter=True, force=True)
        self.set_status()

    def play_anim(self):
        if not self.anim_enable.get():
            self.anim_enable.set(True)
            self.set_status()
        self._tick_anim()

    def pause_anim(self):
        if self._anim_after_id is not None:
            try:
                self.after_cancel(self._anim_after_id)
            except Exception:
                pass
            self._anim_after_id = None

    def _tick_anim(self):
        if not (self.anim_enable.get() and self.current_frames_pil):
            self._anim_after_id = None
            return

        fps = float(self.fps_var.get() or 8.0)
        fps = max(0.5, min(60.0, fps))
        delay = int(round(1000.0 / fps))

        nxt = self.current_frame_idx + 1
        if nxt >= len(self.current_frames_pil):
            if self.loop_var.get():
                nxt = 0
            else:
                self.pause_anim()
                return

        self.current_frame_idx = nxt
        self.scrub_var.set(self.current_frame_idx)
        self._push_current_pil_to_viewport(recenter=False, force=True)

        self._anim_after_id = self.after(delay, self._tick_anim)

    def on_scrub(self):
        if not self.current_frames_pil:
            return
        try:
            idx = int(float(self.scrub_var.get()))
        except Exception:
            idx = 0
        idx = max(0, min(len(self.current_frames_pil) - 1, idx))
        self.current_frame_idx = idx
        self._push_current_pil_to_viewport(recenter=False, force=True)

    # -----------------------------
    # Viewport integration
    # -----------------------------
    def _current_pil_for_view(self):
        if self.anim_enable.get() and self.current_frames_pil:
            return self.current_frames_pil[self.current_frame_idx]
        return self.current_sheet_pil

    def _push_current_pil_to_viewport(self, *, recenter: bool, force: bool):
        # Keep viewport zoom synced to slider value
        self.viewport.set_zoom(float(self.zoom_var.get() or 1.0), recenter=False, force=False)
        self.viewport.set_image(self._current_pil_for_view(), recenter=recenter, force=force)

    def _on_zoom_slider(self):
        # slider controls zoom but does not recenter
        self.viewport.set_zoom(float(self.zoom_var.get() or 1.0), recenter=False, force=False)

    def zoom_fit(self):
        self.viewport.zoom_fit()
        self.zoom_var.set(self.viewport.get_zoom())


def main():
    app = TimViewerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
