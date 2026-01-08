# viewport.py
import time
import math
import tkinter as tk
from tkinter import ttk
from typing import Optional, Tuple, List

from PIL import Image, ImageTk


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


class ViewportCanvas(ttk.Frame):
    """
    Smooth pan/zoom viewport with a 2-layer drag system:

      - SHARP layer: world-anchored tile renderer
      - PREVIEW layer (proxy): screen-pinned viewport image during drag

    The preview layer avoids blank areas during drag and reduces perceived lag
    by updating a cheaper proxy while limiting expensive sharp rebuilds.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        # Canvas + scrollbars
        self.canvas = tk.Canvas(self, bg="#202020", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self._xsb = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self._ysb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self._xsb.grid(row=1, column=0, sticky="ew")
        self._ysb.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=self._on_xscroll, yscrollcommand=self._on_yscroll)

        # Source image + zoom
        self._pil: Optional[Image.Image] = None
        self._zoom = 4.0

        # Pyramid levels: (scale_relative_to_original, image)
        self._pyr: List[Tuple[float, Image.Image]] = []

        # -------------------------
        # Canvas image items
        # -------------------------
        # PREVIEW layer (screen pinned)
        self._preview_id: Optional[int] = None
        self._preview_tk: Optional[ImageTk.PhotoImage] = None
        self._preview_visible = False

        # SHARP layer (world anchored)
        self._canvas_image_id: Optional[int] = None
        self._tk_image: Optional[ImageTk.PhotoImage] = None

        # Interaction state
        self._is_dragging = False
        self._user_panned = False

        # Scheduling (sharp)
        self._view_after_id = None
        self._force_next_draw = False

        # Deferred HQ redraw after pan release
        self._hq_after_id = None
        self._hq_delay_ms = 120

        # Cache keys / tile coverage in ORIGINAL image coords
        self._last_draw_key = None
        self._tile_box: Optional[Tuple[int, int, int, int]] = None  # (l,t,r,b) in original coords

        # Track whether last SHARP render was "preview" (drag/NEAREST)
        self._last_was_preview = False

        # Space around image in scrollregion
        self._pad_mode = "auto"  # "auto" or int pixels

        # -------------------------
        # Base tuning (screen space)
        # -------------------------
        self._idle_margin_screen = 160
        self._base_edge_debounce_ms = 18
        self._inner_pad_screen_px = 140

        # edge trigger used only if freeze disabled
        self._edge_trigger_screen_px = 140

        # -------------------------
        # Zoom curve ranges
        # -------------------------
        self._zoom_low = 2.5
        self._zoom_high = 10.0

        # -------------------------
        # DRAG TILE SIZE CONTROL
        # -------------------------
        self._drag_margin_screen_low_zoom = 260
        self._drag_margin_screen_high_zoom = 420

        self._drag_quant_screen_low_zoom_px = 32
        self._drag_quant_screen_high_zoom_px = 64

        # -------------------------
        # 2-LAYER DRAG (FREEZE + ESCAPE HATCH)
        # -------------------------
        self._drag_freeze_enabled = True

        # escape threshold in screen px
        self._drag_freeze_escape_screen_px = 130

        # cap how often we allow an escape sharp redraw during drag (ms)
        self._drag_freeze_escape_min_interval_ms_low_zoom = 0
        self._drag_freeze_escape_min_interval_ms_high_zoom = 75

        # only redraw SHARP during drag if explicitly allowed
        self._drag_escape_pending = False
        self._drag_last_escape_redraw_ms = 0.0

        # -------------------------
        # PREVIEW (proxy) layer tuning
        # -------------------------
        self._preview_enabled = True
        self._preview_min_interval_ms = 18  # throttle proxy rebuilds
        self._preview_last_ms = 0.0
        self._preview_after_id = None

        # push preview to smaller pyramid levels
        self._preview_bias_down_extra = 0.95
        self._preview_resample = Image.BILINEAR

        # -------------------------
        # Non-freeze drag throttle (fallback)
        # -------------------------
        self._drag_min_interval_ms_low_zoom = 0
        self._drag_min_interval_ms_high_zoom = 55
        self._drag_outside_tile_min_interval_ms_high_zoom = 70
        self._drag_last_redraw_ms = 0.0

        # -------------------------
        # Pyramid settings
        # -------------------------
        self._pyr_min_dim = 256
        self._pyr_levels = 5
        self._pyr_downsample_resample = Image.BILINEAR
        self._downscale_resample = Image.BILINEAR
        self._upscale_resample = Image.NEAREST
        self._pyr_bias_down = 0.55  # try 0.8 if wheel zoom spikes a lot

        # bindings
        self.canvas.bind("<Configure>", self._on_configure)

    # -----------------------------
    # Public API
    # -----------------------------
    def set_image(self, pil, *, recenter=True, force=True):
        self._pil = pil
        self._build_pyramid(pil)
        if recenter:
            self._user_panned = False
        self.invalidate_cache()
        self.schedule_redraw(0, force=force)

    def set_zoom(self, z: float, *, recenter=False, force=False):
        z = float(z or 1.0)
        z = max(0.5, min(16.0, z))
        if abs(z - self._zoom) < 1e-9 and not force:
            return
        self._zoom = z
        if recenter:
            self._user_panned = False
        self.invalidate_cache()
        self.schedule_redraw(0, force=True)

    def get_zoom(self) -> float:
        return float(self._zoom)

    def zoom_fit(self):
        pil = self._pil
        if pil is None:
            return
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        iw, ih = pil.size
        if iw <= 0 or ih <= 0:
            return
        z = min(cw / iw, ch / ih)
        z = max(0.5, min(16.0, z))
        self._zoom = z
        self._user_panned = False
        self.invalidate_cache()
        self.schedule_redraw(0, force=True)

    def wheel_zoom(self, canvas_x: int, canvas_y: int, delta):
        old_z = float(self._zoom or 1.0)
        try:
            d = float(delta)
        except Exception:
            d = 0.0
        if abs(d) < 1e-9:
            return

        # Linux (+1/-1)
        if abs(d) == 1.0:
            d = 120.0 if d > 0 else -120.0

        base = 1.125
        new_z = max(0.5, min(16.0, old_z * (base ** (d / 120.0))))
        if abs(new_z - old_z) < 1e-9:
            return

        pil = self._pil
        if pil is None:
            self._zoom = new_z
            self.invalidate_cache()
            self.schedule_redraw(0, force=True)
            return

        wx = float(self.canvas.canvasx(canvas_x))
        wy = float(self.canvas.canvasy(canvas_y))

        self._zoom = new_z
        self.invalidate_cache()
        self._ensure_scrollregion(pil)

        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        pad = self._compute_pad(cw, ch)
        img_x = pad
        img_y = pad

        ix = (wx - img_x) / old_z
        iy = (wy - img_y) / old_z

        wx_new = img_x + ix * new_z
        wy_new = img_y + iy * new_z

        left_new = wx_new - canvas_x
        top_new = wy_new - canvas_y

        scroll_w, scroll_h = self._scrollregion_wh(pil, pad)
        self.canvas.xview_moveto(self._clamp01(left_new / max(1.0, float(scroll_w))))
        self.canvas.yview_moveto(self._clamp01(top_new / max(1.0, float(scroll_h))))

        self._user_panned = True
        self.schedule_redraw(0, force=True)

    # -----------------------------
    # Pan
    # -----------------------------
    def pan_begin(self, x: int, y: int):
        if self._pil is None:
            return

        # cancel deferred HQ from previous release
        if self._hq_after_id is not None:
            try:
                self.after_cancel(self._hq_after_id)
            except Exception:
                pass
            self._hq_after_id = None

        # cancel pending preview job
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None

        self._is_dragging = True
        self._user_panned = True
        self.canvas.scan_mark(x, y)

        if self._preview_enabled:
            self._preview_show()

    def pan_move(self, x: int, y: int):
        if self._pil is None or not self._is_dragging:
            return

        z = float(self._zoom or 1.0)

        self._user_panned = True
        self.canvas.scan_dragto(x, y, gain=1)

        # Update PREVIEW (proxy) frequently (throttled)
        if self._preview_enabled:
            self._schedule_preview_redraw()

        # If no tile yet, allow one sharp draw soon
        if self._tile_box is None:
            self._drag_escape_pending = True
            self._schedule_drag_escape_redraw(z)
            return

        outside = self._viewport_outside_tile()

        if self._drag_freeze_enabled:
            if outside:
                over = self._outside_amount_screen_px(z)
                if over >= float(self._drag_freeze_escape_screen_px):
                    self._drag_escape_pending = True
                    self._schedule_drag_escape_redraw(z)
                return
            else:
                return

        # fallback path if freeze disabled
        if outside:
            self._schedule_drag_redraw(z, outside_tile=True)
            return

        if self._near_tile_edge():
            self._schedule_drag_redraw(z, outside_tile=False)

    def pan_end(self):
        if not self._is_dragging:
            return
        self._is_dragging = False
        self._drag_escape_pending = False

        # cancel pending sharp redraw
        if self._view_after_id is not None:
            try:
                self.after_cancel(self._view_after_id)
            except Exception:
                pass
            self._view_after_id = None

        # cancel pending preview
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None

        # cancel pending HQ
        if self._hq_after_id is not None:
            try:
                self.after_cancel(self._hq_after_id)
            except Exception:
                pass
            self._hq_after_id = None

        # Force SHARP redraw so viewport is covered
        self.schedule_redraw(0, force=True)

        # deferred HQ (only if last sharp was preview)
        self._hq_after_id = self.after(self._hq_delay_ms, self._hq_redraw_now)

        if self._preview_enabled:
            self._preview_hide()

    # -----------------------------
    # Scheduling (SHARP)
    # -----------------------------
    def invalidate_cache(self):
        self._last_draw_key = None
        self._tile_box = None

    def schedule_redraw(self, delay_ms: int = 0, *, force: bool = False):
        self._force_next_draw = force
        if self._view_after_id is not None:
            try:
                self.after_cancel(self._view_after_id)
            except Exception:
                pass
        self._view_after_id = self.after(delay_ms, self._do_redraw)

    def _do_redraw(self):
        self._view_after_id = None

        # HARD FREEZE: ignore SHARP redraws during drag unless forced or escape permitted
        if (
            self._is_dragging
            and self._drag_freeze_enabled
            and (not self._force_next_draw)
            and (not self._drag_escape_pending)
        ):
            self._force_next_draw = False
            return

        self._draw_viewport_only(recenter=(not self._user_panned), force=self._force_next_draw)
        self._force_next_draw = False
        self._drag_escape_pending = False

    def _hq_redraw_now(self):
        self._hq_after_id = None
        if self._is_dragging:
            return
        if not self._last_was_preview:
            return
        self.schedule_redraw(0, force=False)

    # -----------------------------
    # Scheduling (PREVIEW)
    # -----------------------------
    def _preview_rect_and_offsets(self):
        """
        Returns:
          (l0, t0, r0, b0, cw, ch, dx_px, dy_px, crop_l, crop_t, crop_r, crop_b)

        Where:
          - (l0,t0,r0,b0) is the ideal viewport rect in image coords (may extend outside image)
          - (crop_l..crop_b) is the clipped crop rect in image coords (inside image)
          - (dx_px, dy_px) is where the cropped content should be pasted in the viewport image
        """
        pil = self._pil
        if pil is None:
            return None

        z = float(self._zoom or 1.0)
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        pad = self._compute_pad(cw, ch)
        img_x = pad
        img_y = pad

        left_w0 = float(self.canvas.canvasx(0))
        top_w0 = float(self.canvas.canvasy(0))

        want_w = cw / z
        want_h = ch / z

        # ideal (unclipped) viewport rect in image coords
        l0 = (left_w0 - img_x) / z
        t0 = (top_w0 - img_y) / z
        r0 = l0 + want_w
        b0 = t0 + want_h

        # clipped crop rect inside image
        crop_l = max(0.0, min(float(pil.width), l0))
        crop_t = max(0.0, min(float(pil.height), t0))
        crop_r = max(0.0, min(float(pil.width), r0))
        crop_b = max(0.0, min(float(pil.height), b0))

        # if completely outside
        if crop_r <= crop_l or crop_b <= crop_t:
            dx_px = dy_px = 0
            return (l0, t0, r0, b0, cw, ch, dx_px, dy_px, crop_l, crop_t, crop_r, crop_b)

        # where to paste the clipped crop in viewport px
        dx_px = int(round((crop_l - l0) * z))
        dy_px = int(round((crop_t - t0) * z))

        return (l0, t0, r0, b0, cw, ch, dx_px, dy_px, crop_l, crop_t, crop_r, crop_b)

    def _schedule_preview_redraw(self):
        now = _now_ms()
        elapsed = now - self._preview_last_ms
        if elapsed >= self._preview_min_interval_ms:
            self._preview_last_ms = now
            self._draw_preview_now()
        else:
            delay = int(max(1.0, self._preview_min_interval_ms - elapsed))
            if self._preview_after_id is None:
                self._preview_after_id = self.after(delay, self._preview_after_fire)

    def _preview_after_fire(self):
        self._preview_after_id = None
        self._preview_last_ms = _now_ms()
        self._draw_preview_now()

    def _draw_preview_now(self):
        if not self._preview_enabled or self._pil is None:
            return
        if not self._is_dragging:
            return

        pil = self._pil
        z = float(self._zoom or 1.0)
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())

        self._ensure_scrollregion(pil)

        info = self._preview_rect_and_offsets()
        if info is None:
            return

        (l0, t0, r0, b0, cw, ch, dx_px, dy_px,
         crop_l, crop_t, crop_r, crop_b) = info

        # background (match canvas bg "#202020")
        bg = Image.new("RGB", (cw, ch), (0x20, 0x20, 0x20))

        # If nothing intersects the image, just show bg
        if crop_r <= crop_l or crop_b <= crop_t:
            self._preview_tk = ImageTk.PhotoImage(bg)
            x0 = float(self.canvas.canvasx(0))
            y0 = float(self.canvas.canvasy(0))
            if self._preview_id is None:
                self._preview_id = self.canvas.create_image(x0, y0, anchor="nw", image=self._preview_tk)
            else:
                self.canvas.coords(self._preview_id, x0, y0)
                self.canvas.itemconfig(self._preview_id, image=self._preview_tk)
            self._preview_visible = True
            return

        # choose a more-downsampled pyramid for preview
        lvl_scale, lvl_img, _rel = self._pick_pyr_level_preview(z)

        # crop bounds in pyramid coords (clipped to lvl)
        l2 = int(round(crop_l * lvl_scale))
        t2 = int(round(crop_t * lvl_scale))
        r2 = int(round(crop_r * lvl_scale))
        b2 = int(round(crop_b * lvl_scale))

        l2 = max(0, min(lvl_img.width - 1, l2))
        t2 = max(0, min(lvl_img.height - 1, t2))
        r2 = max(l2 + 1, min(lvl_img.width, r2))
        b2 = max(t2 + 1, min(lvl_img.height, b2))

        cropped = lvl_img.crop((l2, t2, r2, b2))

        # On-screen size of the *clipped* crop portion
        crop_w_px = max(1, int(round((crop_r - crop_l) * z)))
        crop_h_px = max(1, int(round((crop_b - crop_t) * z)))

        try:
            scaled = cropped.resize((crop_w_px, crop_h_px), resample=self._preview_resample)
        except Exception:
            return

        try:
            bg.paste(scaled, (dx_px, dy_px))
        except Exception:
            return

        self._preview_tk = ImageTk.PhotoImage(bg)

        # Screen-pinned placement
        x0 = float(self.canvas.canvasx(0))
        y0 = float(self.canvas.canvasy(0))

        if self._preview_id is None:
            self._preview_id = self.canvas.create_image(x0, y0, anchor="nw", image=self._preview_tk)
        else:
            self.canvas.coords(self._preview_id, x0, y0)
            self.canvas.itemconfig(self._preview_id, image=self._preview_tk)

        # keep preview below sharp
        if self._canvas_image_id is not None:
            try:
                self.canvas.tag_lower(self._preview_id, self._canvas_image_id)
            except Exception:
                pass

        self._preview_visible = True

    def _preview_show(self):
        if self._preview_id is not None:
            try:
                self.canvas.itemconfigure(self._preview_id, state="normal")
            except Exception:
                pass
        self._preview_visible = True

    def _preview_hide(self):
        if self._preview_id is not None:
            try:
                self.canvas.itemconfigure(self._preview_id, state="hidden")
            except Exception:
                pass
        self._preview_visible = False

    # -----------------------------
    # Scrollbar callbacks
    # -----------------------------
    def _on_xscroll(self, lo, hi):
        self._xsb.set(lo, hi)
        if not self._is_dragging:
            self.schedule_redraw(16, force=False)

    def _on_yscroll(self, lo, hi):
        self._ysb.set(lo, hi)
        if not self._is_dragging:
            self.schedule_redraw(16, force=False)

    def _on_configure(self, event):
        self.schedule_redraw(0, force=True)
        if self._is_dragging and self._preview_enabled:
            self._draw_preview_now()

    # -----------------------------
    # Zoom-scaled knobs
    # -----------------------------
    def _zoom_t(self, z: float) -> float:
        if z <= self._zoom_low:
            return 0.0
        if z >= self._zoom_high:
            return 1.0
        t = (z - self._zoom_low) / (self._zoom_high - self._zoom_low)
        return t * t * (3.0 - 2.0 * t)

    def _scaled_drag_params(self, z: float) -> Tuple[int, int, int]:
        t = self._zoom_t(z)

        margin = int(round(
            self._drag_margin_screen_low_zoom +
            (self._drag_margin_screen_high_zoom - self._drag_margin_screen_low_zoom) * t
        ))

        quant = int(round(
            self._drag_quant_screen_low_zoom_px +
            (self._drag_quant_screen_high_zoom_px - self._drag_quant_screen_low_zoom_px) * t
        ))

        debounce = int(round(self._base_edge_debounce_ms * (1.0 + 1.2 * t)))

        margin = max(80, min(900, margin))
        quant = max(8, min(256, quant))
        debounce = max(8, min(200, debounce))

        return margin, quant, debounce

    # -----------------------------
    # Drag redraw throttle (fallback)
    # -----------------------------
    def _schedule_drag_redraw(self, z: float, *, outside_tile: bool):
        now = _now_ms()
        t = self._zoom_t(z)
        base_min = self._drag_min_interval_ms_low_zoom + (self._drag_min_interval_ms_high_zoom - self._drag_min_interval_ms_low_zoom) * t
        if outside_tile:
            base_min = max(base_min, self._drag_outside_tile_min_interval_ms_high_zoom * t)

        elapsed = now - self._drag_last_redraw_ms
        if elapsed >= base_min:
            self._drag_last_redraw_ms = now
            self.schedule_redraw(0, force=False)
        else:
            delay = int(max(0.0, base_min - elapsed))
            self.schedule_redraw(delay, force=False)

    # -----------------------------
    # Drag escape throttle (freeze hatch)
    # -----------------------------
    def _schedule_drag_escape_redraw(self, z: float):
        now = _now_ms()
        t = self._zoom_t(z)
        base_min = self._drag_freeze_escape_min_interval_ms_low_zoom + (
            self._drag_freeze_escape_min_interval_ms_high_zoom - self._drag_freeze_escape_min_interval_ms_low_zoom
        ) * t

        elapsed = now - self._drag_last_escape_redraw_ms
        if elapsed >= base_min:
            self._drag_last_escape_redraw_ms = now
            self.schedule_redraw(0, force=False)
        else:
            delay = int(max(0.0, base_min - elapsed))
            self.schedule_redraw(delay, force=False)

    # -----------------------------
    # Pyramid
    # -----------------------------
    def _build_pyramid(self, pil: Image.Image):
        self._pyr = []
        if pil is None:
            return
        self._pyr.append((1.0, pil))

        w, h = pil.size
        scale = 1.0
        cur = pil
        for _ in range(max(0, int(self._pyr_levels) - 1)):
            if min(w, h) <= self._pyr_min_dim:
                break
            w = max(1, w // 2)
            h = max(1, h // 2)
            scale *= 0.5
            cur = cur.resize((w, h), resample=self._pyr_downsample_resample)
            self._pyr.append((scale, cur))

    def _pick_pyr_level(self, zoom: float) -> Tuple[float, Image.Image, float]:
        if not self._pyr:
            return 1.0, self._pil, float(zoom)

        best = None
        for scale, img in self._pyr:
            rel = zoom / scale
            score = abs(math.log(rel, 2)) if rel > 0 else 1e9
            bias = self._pyr_bias_down * (-math.log(scale, 2))
            score -= bias
            if best is None or score < best[0]:
                best = (score, scale, img, rel)

        _, scale, img, rel = best
        return scale, img, rel

    def _pick_pyr_level_preview(self, zoom: float) -> Tuple[float, Image.Image, float]:
        if not self._pyr:
            return 1.0, self._pil, float(zoom)

        best = None
        for scale, img in self._pyr:
            rel = zoom / scale
            score = abs(math.log(rel, 2)) if rel > 0 else 1e9
            bias = (self._pyr_bias_down + self._preview_bias_down_extra) * (-math.log(scale, 2))
            score -= bias
            if best is None or score < best[0]:
                best = (score, scale, img, rel)

        _, scale, img, rel = best
        return scale, img, rel

    # -----------------------------
    # Geometry helpers
    # -----------------------------
    def _ensure_scrollregion(self, pil):
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        pad = self._compute_pad(cw, ch)
        scroll_w, scroll_h = self._scrollregion_wh(pil, pad)
        self.canvas.configure(scrollregion=(0, 0, scroll_w, scroll_h))

    def _scrollregion_wh(self, pil, pad: int):
        z = float(self._zoom or 1.0)
        zw = max(1, int(pil.width * z))
        zh = max(1, int(pil.height * z))
        return zw + 2 * pad, zh + 2 * pad

    def _compute_pad(self, canvas_w: int, canvas_h: int) -> int:
        if self._pad_mode == "auto":
            return max(canvas_w, canvas_h)
        try:
            return int(self._pad_mode)
        except Exception:
            return max(canvas_w, canvas_h)

    def _screen_to_image_px(self, px_screen: int, z: float) -> int:
        return max(1, int(px_screen / max(1e-9, z)))

    def _quantize_floor(self, v: int, q: int) -> int:
        return (v // q) * q

    def _quantize_ceil(self, v: int, q: int) -> int:
        return ((v + q - 1) // q) * q

    def _visible_rect_image_coords(self):
        pil = self._pil
        if pil is None:
            return None

        z = float(self._zoom or 1.0)
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        pad = self._compute_pad(cw, ch)
        img_x = pad
        img_y = pad

        left_w0 = float(self.canvas.canvasx(0))
        top_w0 = float(self.canvas.canvasy(0))
        right_w0 = float(self.canvas.canvasx(cw))
        bot_w0 = float(self.canvas.canvasy(ch))

        vis_l = (left_w0 - img_x) / z
        vis_t = (top_w0 - img_y) / z
        vis_r = (right_w0 - img_x) / z
        vis_b = (bot_w0 - img_y) / z

        vis_l = max(0.0, min(float(pil.width), vis_l))
        vis_r = max(0.0, min(float(pil.width), vis_r))
        vis_t = max(0.0, min(float(pil.height), vis_t))
        vis_b = max(0.0, min(float(pil.height), vis_b))
        return vis_l, vis_t, vis_r, vis_b, cw, ch

    def _viewport_outside_tile(self) -> bool:
        if self._tile_box is None:
            return True
        r = self._visible_rect_image_coords()
        if r is None:
            return True
        vis_l, vis_t, vis_r, vis_b, _, _ = r
        tl, tt, tr, tb = self._tile_box
        return (vis_l < tl) or (vis_t < tt) or (vis_r > tr) or (vis_b > tb)

    def _outside_amount_screen_px(self, z: float) -> float:
        if self._tile_box is None:
            return float("inf")
        r = self._visible_rect_image_coords()
        if r is None:
            return float("inf")
        vis_l, vis_t, vis_r, vis_b, _, _ = r
        tl, tt, tr, tb = self._tile_box

        over_l = max(0.0, float(tl) - vis_l)
        over_t = max(0.0, float(tt) - vis_t)
        over_r = max(0.0, vis_r - float(tr))
        over_b = max(0.0, vis_b - float(tb))
        over_img = max(over_l, over_t, over_r, over_b)
        return over_img * float(z)

    def _near_tile_edge(self) -> bool:
        if self._tile_box is None:
            return True
        r = self._visible_rect_image_coords()
        if r is None:
            return True

        vis_l, vis_t, vis_r, vis_b, _, _ = r
        z = float(self._zoom or 1.0)
        edge_img = self._screen_to_image_px(self._edge_trigger_screen_px, z)
        tl, tt, tr, tb = self._tile_box
        return (
            (vis_l < tl + edge_img) or
            (vis_t < tt + edge_img) or
            (vis_r > tr - edge_img) or
            (vis_b > tb - edge_img)
        )

    # -----------------------------
    # Core render (SHARP)
    # -----------------------------
    def _draw_viewport_only(self, recenter: bool, force: bool):
        pil = self._pil
        if pil is None:
            return

        z = float(self._zoom or 1.0)

        r = self._visible_rect_image_coords()
        if r is None:
            return
        vis_l, vis_t, vis_r, vis_b, cw, ch = r

        pad = self._compute_pad(cw, ch)
        img_x = pad
        img_y = pad

        self._ensure_scrollregion(pil)
        scroll_w, scroll_h = self._scrollregion_wh(pil, pad)

        if recenter:
            zw = max(1, int(pil.width * z))
            zh = max(1, int(pil.height * z))
            center_x = img_x + (zw / 2.0)
            center_y = img_y + (zh / 2.0)
            target_left = center_x - (cw / 2.0)
            target_top = center_y - (ch / 2.0)
            self.canvas.xview_moveto(self._clamp01(target_left / max(1.0, float(scroll_w))))
            self.canvas.yview_moveto(self._clamp01(target_top / max(1.0, float(scroll_h))))
            r2 = self._visible_rect_image_coords()
            if r2 is None:
                return
            vis_l, vis_t, vis_r, vis_b, cw, ch = r2

        # Skip if cached tile still covers view
        if not force and self._tile_box is not None:
            tl, tt, tr, tb = self._tile_box
            inner_img = self._screen_to_image_px(self._inner_pad_screen_px, z)
            if (vis_l >= tl + inner_img and vis_t >= tt + inner_img and
                vis_r <= tr - inner_img and vis_b <= tb - inner_img):
                return

        # margins / quant
        if self._is_dragging:
            margin_screen, quant_screen, _ = self._scaled_drag_params(z)
        else:
            margin_screen = self._idle_margin_screen
            quant_screen = 1

        # World rect
        left_w0 = float(self.canvas.canvasx(0))
        top_w0 = float(self.canvas.canvasy(0))
        right_w0 = float(self.canvas.canvasx(cw))
        bot_w0 = float(self.canvas.canvasy(ch))

        left_w = left_w0 - margin_screen
        top_w = top_w0 - margin_screen
        right_w = right_w0 + margin_screen
        bot_w = bot_w0 + margin_screen

        l_ix = (left_w - img_x) / z
        r_ix = (right_w - img_x) / z
        t_iy = (top_w - img_y) / z
        b_iy = (bot_w - img_y) / z

        l_ix = max(0.0, min(float(pil.width), l_ix))
        r_ix = max(0.0, min(float(pil.width), r_ix))
        t_iy = max(0.0, min(float(pil.height), t_iy))
        b_iy = max(0.0, min(float(pil.height), b_iy))
        if r_ix <= l_ix or b_iy <= t_iy:
            return

        quant_img = self._screen_to_image_px(int(quant_screen), z)

        crop_l = int(l_ix)
        crop_t = int(t_iy)
        crop_r = int(r_ix + 0.999)
        crop_b = int(b_iy + 0.999)

        # Quantize BOTH sides for stability
        if quant_img > 1:
            crop_l = self._quantize_floor(crop_l, quant_img)
            crop_t = self._quantize_floor(crop_t, quant_img)
            crop_r = self._quantize_ceil(crop_r, quant_img)
            crop_b = self._quantize_ceil(crop_b, quant_img)

        crop_r = max(crop_l + 1, min(pil.width, crop_r))
        crop_b = max(crop_t + 1, min(pil.height, crop_b))

        self._tile_box = (crop_l, crop_t, crop_r, crop_b)

        # Pyramid level
        lvl_scale, lvl_img, rel = self._pick_pyr_level(z)

        l2 = int(crop_l * lvl_scale)
        t2 = int(crop_t * lvl_scale)
        r2 = max(l2 + 1, int(math.ceil(crop_r * lvl_scale)))
        b2 = max(t2 + 1, int(math.ceil(crop_b * lvl_scale)))

        r2 = min(lvl_img.width, r2)
        b2 = min(lvl_img.height, b2)

        # Target size in screen px
        target_w = max(1, int((crop_r - crop_l) * z))
        target_h = max(1, int((crop_b - crop_t) * z))

        draw_key = (
            id(pil), z,
            crop_l, crop_t, crop_r, crop_b,
            lvl_scale, l2, t2, r2, b2,
            target_w, target_h,
            bool(self._is_dragging),
            int(margin_screen), int(quant_screen),
        )
        if draw_key == self._last_draw_key and self._tk_image is not None and not force:
            return

        cropped = lvl_img.crop((l2, t2, r2, b2))

        if self._is_dragging:
            resample = Image.NEAREST
            self._last_was_preview = True
        else:
            resample = self._downscale_resample if rel < 1.0 else self._upscale_resample
            self._last_was_preview = False

        scaled = cropped.resize((target_w, target_h), resample=resample)
        self._tk_image = ImageTk.PhotoImage(scaled)

        world_draw_x = int(round(img_x + crop_l * z))
        world_draw_y = int(round(img_y + crop_t * z))

        if self._canvas_image_id is None:
            self._canvas_image_id = self.canvas.create_image(
                world_draw_x, world_draw_y, anchor="nw", image=self._tk_image
            )
        else:
            self.canvas.coords(self._canvas_image_id, world_draw_x, world_draw_y)
            self.canvas.itemconfig(self._canvas_image_id, image=self._tk_image)

        # keep sharp above preview
        if self._preview_id is not None:
            try:
                self.canvas.tag_raise(self._canvas_image_id, self._preview_id)
            except Exception:
                pass

        self._last_draw_key = draw_key

    # -----------------------------
    # Misc helpers
    # -----------------------------
    @staticmethod
    def _clamp01(v: float) -> float:
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v
