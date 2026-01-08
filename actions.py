# actions.py
import os
from tkinter import filedialog, messagebox
from PIL import Image

from timlib import (
    render_tim_to_image,
    export_indices_png_and_meta,
    import_indices_from_png_resize_tim,
    build_tim_bytes,
)


def export_indices(app):
    if app.current_tim is None:
        messagebox.showinfo("No TIM selected", "Select a TIM first.")
        return
    if app.current_tim.bpp_mode not in (0, 1):
        messagebox.showinfo("Not indexed", "This TIM is not indexed (4bpp/8bpp).")
        return

    base = os.path.splitext(os.path.basename(app.current_tim.path))[0]
    suggested = base + "_index.png"
    out_path = filedialog.asksaveasfilename(
        title="Export Indices PNG",
        defaultextension=".png",
        initialfile=suggested,
        filetypes=[("PNG image", "*.png")],
    )
    if not out_path:
        return

    try:
        meta_path = export_indices_png_and_meta(app.current_tim, out_path)
    except Exception as e:
        messagebox.showerror("Export indices failed", str(e))
        return

    app.set_status(extra=" | Exported index+meta")
    messagebox.showinfo(
        "Exported",
        f"Saved:\n{out_path}\n{meta_path}\n\nEdit in indexed mode (P). You may upscale; import will resize TIM.",
    )


def import_indices_resize(app):
    if app.current_tim is None:
        messagebox.showinfo("No TIM selected", "Select a TIM first.")
        return
    if app.current_tim.bpp_mode not in (0, 1):
        messagebox.showinfo("Not indexed", "This TIM is not indexed (4bpp/8bpp).")
        return

    png_path = filedialog.askopenfilename(
        title="Select edited index PNG (can be uprezzed)",
        filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
    )
    if not png_path:
        return

    meta_guess = os.path.splitext(png_path)[0] + ".json"
    meta_path = meta_guess if os.path.exists(meta_guess) else None

    try:
        import_indices_from_png_resize_tim(app.current_tim, png_path, meta_path)
    except Exception as e:
        messagebox.showerror("Import indices failed", str(e))
        return

    app.rebuild_sheet_and_frames(auto=True)
    app.set_status(extra=" | Imported+resized")
    messagebox.showinfo(
        "Imported",
        "Imported indices and resized the TIM in memory.\nUse 'Save TIM As…' to write a new TIM file.",
    )


def save_tim_as(app):
    if app.current_tim is None:
        messagebox.showinfo("No TIM selected", "Select a TIM first.")
        return

    base = os.path.splitext(os.path.basename(app.current_tim.path))[0]
    suggested = base + "_edited.tim"
    out_path = filedialog.asksaveasfilename(
        title="Save TIM As",
        defaultextension=".tim",
        initialfile=suggested,
        filetypes=[("TIM", "*.tim"), ("All files", "*.*")],
    )
    if not out_path:
        return

    try:
        out_bytes = build_tim_bytes(app.current_tim)
        with open(out_path, "wb") as f:
            f.write(out_bytes)
    except Exception as e:
        messagebox.showerror("Save failed", str(e))
        return

    app.set_status(extra=" | Saved")
    messagebox.showinfo("Saved", f"Wrote:\n{out_path}")


def export_image(app):
    if app.current_tim is None:
        messagebox.showinfo("Nothing to export", "Load and select a TIM first.")
        return

    if app.anim_enable.get() and app.current_frames_pil:
        pil = app.current_frames_pil[app.current_frame_idx]
        suffix = f"_frame{app.current_frame_idx:02d}"
    else:
        if app.current_sheet_pil is None:
            app.current_sheet_pil = render_tim_to_image(app.current_tim, app.current_tim.applied_clut)
        pil = app.current_sheet_pil
        suffix = ""

    base = os.path.splitext(os.path.basename(app.current_tim.path))[0]
    suggested = base + suffix + ".png"

    out_path = filedialog.asksaveasfilename(
        title="Export Image",
        defaultextension=".png",
        initialfile=suggested,
        filetypes=[("PNG image", "*.png"), ("BMP image", "*.bmp")],
    )
    if not out_path:
        return

    ext = os.path.splitext(out_path)[1].lower().strip(".")
    try:
        if ext == "bmp":
            rgba = pil.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
            flat = Image.alpha_composite(bg, rgba).convert("RGB")
            flat.save(out_path, "BMP")
        else:
            pil.save(out_path, "PNG")
    except Exception as e:
        messagebox.showerror("Export failed", str(e))
        return

    app.set_status(extra=" | Exported image")
    messagebox.showinfo("Exported", f"Saved:\n{out_path}")
