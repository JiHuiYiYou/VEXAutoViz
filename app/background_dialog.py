"""Background image settings dialog — WeChat/Photoshop transform style.

Layout:
    [path entry]    [Browse…]
    [preview canvas with 144×144in field outline + image + 4 corner handles]
    [↻ 90°]  [↺ 90°]   |   [重置变换]
    [对齐↖] [对齐↗] [对齐↙] [对齐↘]   |   [居中]
    透明度 [=================o==================] 50%
    [✓] 显示背景
                                [取消] [应用]
"""
from __future__ import annotations

import math
import os
import tkinter as tk
from pathlib import Path
from typing import Any, Callable

import ttkbootstrap as ttkb
from PIL import Image, ImageTk
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, X

from .settings import Settings


class BackgroundDialog:
    FIELD_SIZE = 144.0
    PREVIEW_W = 440
    PREVIEW_H = 380
    PREVIEW_PX_PER_IN = 2.5
    HANDLE_HALF = 6                 # half-side of corner handle square (px)
    HIT_RADIUS = 10                 # px radius for corner hit-test
    MIN_SCALE_FACTOR = 0.05         # floor on per-drag scale factor

    # image corner idx -> field corner world coord (inches)
    FIELD_CORNERS = {
        0: (-72.0,  72.0),   # TL
        1: ( 72.0,  72.0),   # TR
        2: ( 72.0, -72.0),   # BR
        3: (-72.0, -72.0),   # BL
    }

    def __init__(self, parent: tk.Misc, settings: Settings,
                 on_apply: Callable[[Settings], None],
                 on_change: Callable[[], None] | None = None,
                 view_rotation: float = 0.0) -> None:
        self._settings = settings
        self._on_apply = on_apply
        self._on_change = on_change or (lambda: None)
        # World view rotation (deg, CW from north) inherited from the main
        # canvas so mouse drag directions match what the user sees there.
        self._view_rotation = view_rotation

        self._raw_w = 0
        self._raw_h = 0
        self._tk_image: ImageTk.PhotoImage | None = None
        self._cache_key: tuple | None = None
        self._drag: dict | None = None
        self._corner_pixels: list[tuple[float, float]] = []

        self.win = ttkb.Toplevel(parent)
        self.win.title("背景图设置")
        self.win.transient(parent)
        self.win.grab_set()
        self.win.deiconify()

        frame = ttkb.Frame(self.win, padding=15)
        frame.pack(fill=BOTH, expand=True)
        frame.columnconfigure(0, weight=1)

        # ---- row 0: path display + Browse ----
        path_bar = ttkb.Frame(frame)
        path_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        path_bar.columnconfigure(1, weight=1)
        ttkb.Label(path_bar, text="图片路径").grid(row=0, column=0, sticky="w")
        self._path_var = tk.StringVar(value=settings.background_image_path)
        path_entry = ttkb.Entry(path_bar, textvariable=self._path_var,
                                state="readonly")
        path_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttkb.Button(path_bar, text="浏览…", command=self._browse,
                    bootstyle="secondary").grid(row=0, column=2, sticky="e")
        self._path_var.trace_add("write", lambda *_a: self._on_path_or_op_change())

        # ---- row 1: preview canvas ----
        self.canvas = tk.Canvas(
            frame, width=self.PREVIEW_W, height=self.PREVIEW_H,
            background="#1e1e1e", highlightthickness=1,
            highlightbackground="#3a3a3a",
        )
        self.canvas.grid(row=1, column=0, pady=(0, 10))
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        # ---- row 2: rotate + reset ----
        rot_bar = ttkb.Frame(frame)
        rot_bar.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ttkb.Button(rot_bar, text="↻ 90°", width=8, bootstyle="secondary",
                    command=self._rotate_cw).pack(side=LEFT, padx=(0, 4))
        ttkb.Button(rot_bar, text="↺ 90°", width=8, bootstyle="secondary",
                    command=self._rotate_ccw).pack(side=LEFT, padx=(0, 16))
        ttkb.Button(rot_bar, text="重置变换", bootstyle="secondary",
                    command=self._reset_transform).pack(side=LEFT)

        # ---- row 3: align + center ----
        align_bar = ttkb.Frame(frame)
        align_bar.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        for label, idx in [("对齐↖", 0), ("对齐↗", 1),
                           ("对齐↙", 3), ("对齐↘", 2)]:
            ttkb.Button(align_bar, text=label, width=6, bootstyle="secondary",
                        command=lambda i=idx: self._align_corner(i)
                        ).pack(side=LEFT, padx=(0, 4))
        ttkb.Button(align_bar, text="居中", bootstyle="secondary",
                    command=self._center).pack(side=LEFT, padx=(12, 0))

        # ---- row 4: opacity slider ----
        op_bar = ttkb.Frame(frame)
        op_bar.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        op_bar.columnconfigure(1, weight=1)
        ttkb.Label(op_bar, text="透明度").grid(row=0, column=0, sticky="w")
        self._op_var = tk.DoubleVar(value=settings.background_opacity)
        self._op_pct_var = tk.StringVar(
            value=f"{int(settings.background_opacity*100)}%")
        self._op_var.trace_add("write", lambda *_a: self._on_path_or_op_change())
        ttkb.Scale(op_bar, from_=0.0, to=1.0, variable=self._op_var,
                   command=lambda _v: None
                   ).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttkb.Label(op_bar, textvariable=self._op_pct_var, width=4,
                   anchor="e").grid(row=0, column=2, sticky="e")

        # ---- row 5: visibility ----
        self._vis_var = tk.BooleanVar(value=settings.background_visible)
        ttkb.Checkbutton(frame, text="显示背景", variable=self._vis_var,
                         bootstyle="round-toggle", command=self._on_vis_change
                         ).grid(row=5, column=0, sticky="w", pady=(0, 12))

        # ---- bottom: cancel + apply ----
        btn_bar = ttkb.Frame(self.win, padding=(15, 0, 15, 15))
        btn_bar.pack(fill=X)
        ttkb.Button(btn_bar, text="取消", command=self._cancel,
                    bootstyle="secondary").pack(side=RIGHT, padx=(5, 0))
        ttkb.Button(btn_bar, text="应用", command=self._apply,
                    bootstyle="primary").pack(side=RIGHT)

        # Auto-fit + center on parent
        self.win.update_idletasks()
        req_w = self.win.winfo_reqwidth()
        req_h = self.win.winfo_reqheight()
        self.win.geometry(f"{max(req_w, 460)}x{req_h}")
        self.win.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")

        self._refresh_dims()
        self._redraw_preview()

    # -------------------- coordinate helpers --------------------

    def _world_to_preview(self, wx: float, wy: float) -> tuple[float, float]:
        # Apply the same view rotation as the main canvas so mouse directions
        # match. Rotation is CW around the visual center; world +Y (north)
        # rotates by R.
        θ = math.radians(self._view_rotation)
        rx = wx * math.cos(θ) + wy * math.sin(θ)
        ry = -wx * math.sin(θ) + wy * math.cos(θ)
        return (self.PREVIEW_W / 2 + rx * self.PREVIEW_PX_PER_IN,
                self.PREVIEW_H / 2 - ry * self.PREVIEW_PX_PER_IN)

    def _preview_to_world(self, px_: float, py_: float) -> tuple[float, float]:
        θ = math.radians(self._view_rotation)
        rx = (px_ - self.PREVIEW_W / 2) / self.PREVIEW_PX_PER_IN
        ry = -(py_ - self.PREVIEW_H / 2) / self.PREVIEW_PX_PER_IN
        # Inverse rotation: undo the CW-by-R we applied in `_world_to_preview`.
        x = rx * math.cos(-θ) + ry * math.sin(-θ)
        y = -rx * math.sin(-θ) + ry * math.cos(-θ)
        return (x, y)

    def _image_corners_world(self) -> list[tuple[float, float]]:
        """Return [TL, TR, BR, BL] in world inches.

        `background_rotation` is in CW-screen degrees (matches `img.rotate(-R)`
        on screen and the +90/-90 toolbar buttons).

        PIL local convention: x grows right, y grows DOWN. World Y grows UP.

        PIL `rotate(angle)` applies a math-CCW-by-angle rotation matrix to
        PIL-local coordinates (Y-down). Empirically, `rotate(-R)` puts PIL
        local TL (-hw, -hh) at the top-right of the rendered image — which
        means `background_rotation = R` should use the math-CCW(R) matrix
        on PIL-local points, then flip PIL-y to world-y.

            M_CCWR = [[cos, -sin], [sin, cos]]
            new_pil_x = lx*cos - ly*sin
            new_pil_y = lx*sin + ly*cos
            world_x = new_pil_x
            world_y = -new_pil_y   (= -lx*sin - ly*cos)

        Then translate by origin.
        """
        if self._raw_w <= 0 or self._raw_h <= 0:
            return []
        s = self._settings
        θ = math.radians(s.background_rotation)
        cos, sin = math.cos(θ), math.sin(θ)
        ox, oy = s.background_origin_x, s.background_origin_y
        half_w = s.background_scale * self._raw_w / 2
        half_h = s.background_scale * self._raw_h / 2
        local = [(-half_w, -half_h), ( half_w, -half_h),    # PIL TL, TR
                 ( half_w,  half_h), (-half_w,  half_h)]    # PIL BR, BL
        return [(lx * cos - ly * sin + ox,
                 -lx * sin - ly * cos + oy)
                for lx, ly in local]

    # -------------------- callbacks --------------------

    def _on_path_or_op_change(self) -> None:
        """Path / opacity changed via trace_add; push into shared settings."""
        s = self._settings
        s.background_image_path = self._path_var.get()
        s.background_opacity = max(0.0, min(1.0, float(self._op_var.get())))
        self._op_pct_var.set(f"{int(s.background_opacity * 100)}%")
        self._refresh_dims()
        self._on_change()
        self._redraw_preview()

    def _on_vis_change(self) -> None:
        self._settings.background_visible = bool(self._vis_var.get())
        self._on_change()
        self._redraw_preview()

    def _browse(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择背景图",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                       ("所有文件", "*.*")],
        )
        if path:
            self._path_var.set(path)

    def _refresh_dims(self) -> None:
        s = self._settings
        if not s.background_image_path or not Path(s.background_image_path).is_file():
            self._raw_w = self._raw_h = 0
            return
        try:
            with Image.open(s.background_image_path) as img:
                self._raw_w, self._raw_h = img.size
        except Exception:
            self._raw_w = self._raw_h = 0

    # -------------------- rasterization (cached PhotoImage) --------------------

    def _maybe_rasterize(self) -> None:
        s = self._settings
        if self._raw_w <= 0 or self._raw_h <= 0:
            self._tk_image = None
            return
        try:
            st = os.stat(s.background_image_path)
            signature = (s.background_image_path, st.st_mtime, st.st_size)
        except OSError:
            self._tk_image = None
            return
        key = (signature,
               float(s.background_scale),
               float(s.background_rotation),
               float(s.background_opacity))
        if key == self._cache_key and self._tk_image is not None:
            return
        # Re-PIL
        img = Image.open(s.background_image_path).convert("RGBA")
        scale_px = max(s.background_scale, 1e-9) * self.PREVIEW_PX_PER_IN
        target_w = max(1, int(round(img.width * scale_px)))
        target_h = max(1, int(round(img.height * scale_px)))
        if (target_w, target_h) != img.size:
            img = img.resize((target_w, target_h), Image.LANCZOS)
        if abs(s.background_rotation) > 1e-6:
            img = img.rotate(-s.background_rotation, resample=Image.BICUBIC,
                             expand=True)
        op = max(0.0, min(1.0, float(s.background_opacity)))
        if op < 0.999:
            a = img.split()[3].point(lambda v: int(v * op))
            img.putalpha(a)
        self._tk_image = ImageTk.PhotoImage(img)
        self._cache_key = key

    # -------------------- draw --------------------

    def _redraw_preview(self) -> None:
        cv = self.canvas
        cv.delete("field", "bg", "bbox")

        # Field outline (144×144 in, dashed).
        fcx, fcy = self.PREVIEW_W / 2, self.PREVIEW_H / 2
        fpx = self.FIELD_SIZE * self.PREVIEW_PX_PER_IN  # 360 px
        cv.create_rectangle(fcx - fpx / 2, fcy - fpx / 2,
                            fcx + fpx / 2, fcy + fpx / 2,
                            outline="#666", dash=(4, 4), width=1,
                            tags=("field",))
        # Origin marker
        cv.create_line(fcx - 8, fcy, fcx + 8, fcy, fill="#666",
                       tags=("field",))
        cv.create_line(fcx, fcy - 8, fcx, fcy + 8, fill="#666",
                       tags=("field",))
        cv.create_text(fcx, fcy - fpx / 2 - 12,
                       text="144×144in 场地", fill="#888",
                       font=("TkDefaultFont", 8), tags=("field",))

        if self._raw_w <= 0 or self._raw_h <= 0:
            cv.create_text(fcx, fcy,
                           text="点击「浏览…」选择一张图片",
                           fill="#888", font=("TkDefaultFont", 10),
                           tags=("bg",))
            self._corner_pixels = []
            return

        # Image (always drawn in preview so the user sees current config,
        # even when main canvas is hidden — visibility affects main only).
        self._maybe_rasterize()
        if self._tk_image is not None:
            s = self._settings
            cx, cy = self._world_to_preview(s.background_origin_x,
                                            s.background_origin_y)
            cv.create_image(cx, cy, image=self._tk_image, anchor="center",
                            tags=("bg",))

        # Bbox + 4 corner handles
        corners = self._image_corners_world()
        if len(corners) == 4:
            cps = [self._world_to_preview(wx, wy) for wx, wy in corners]
            self._corner_pixels = cps
            poly = [c for p in cps for c in p]
            cv.create_polygon(*poly, outline="white", fill="",
                              dash=(4, 4), width=1, tags=("bbox",))
            for hx, hy in cps:
                cv.create_rectangle(hx - self.HANDLE_HALF,
                                    hy - self.HANDLE_HALF,
                                    hx + self.HANDLE_HALF,
                                    hy + self.HANDLE_HALF,
                                    fill="white", outline="black",
                                    width=1, tags=("bbox",))
        else:
            self._corner_pixels = []

    # -------------------- gesture handlers --------------------

    def _hit_test(self, px: float, py: float) -> str | None:
        """Returns 'corner:i' (i in 0..3) or 'body' or None."""
        for i, (hx, hy) in enumerate(self._corner_pixels):
            if abs(px - hx) <= self.HIT_RADIUS and abs(py - hy) <= self.HIT_RADIUS:
                return f"corner:{i}"
        if len(self._corner_pixels) == 4:
            if self._point_in_convex_polygon(px, py, self._corner_pixels):
                return "body"
        return None

    @staticmethod
    def _point_in_convex_polygon(px: float, py: float,
                                 poly: list[tuple[float, float]]) -> bool:
        n = len(poly)
        if n < 3:
            return False
        sign = 0
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            ex, ey = x2 - x1, y2 - y1
            vx, vy = px - x1, py - y1
            cross = ex * vy - ey * vx
            if cross > 0:
                cur = 1
            elif cross < 0:
                cur = -1
            else:
                continue
            if sign == 0:
                sign = cur
            elif sign != cur:
                return False
        return True

    def _on_press(self, event: tk.Event) -> None:
        hit = self._hit_test(event.x, event.y)
        if hit is None:
            return
        m_w = self._preview_to_world(event.x, event.y)
        s = self._settings
        if hit.startswith("corner"):
            idx = int(hit.split(":")[1])
            self._drag = {"kind": "corner", "idx": idx}
        else:
            self._drag = {
                "kind": "body",
                "origin_at_press": (s.background_origin_x,
                                    s.background_origin_y),
                "mouse_at_press": m_w,
            }

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        s = self._settings
        m_w = self._preview_to_world(event.x, event.y)
        if self._drag["kind"] == "corner":
            self._apply_corner_drag(self._drag["idx"], m_w)
        else:
            ox0, oy0 = self._drag["origin_at_press"]
            mx0, my0 = self._drag["mouse_at_press"]
            s.background_origin_x = ox0 + (m_w[0] - mx0)
            s.background_origin_y = oy0 + (m_w[1] - my0)
        self._on_change()
        self._redraw_preview()

    def _on_release(self, _event: tk.Event) -> None:
        self._drag = None

    def _apply_corner_drag(self, idx: int, m_w: tuple[float, float]) -> None:
        """Drag corner `idx`: opposite corner stays anchored, image scales
        proportionally + rotates so the dragged corner lands at the mouse.
        Aspect ratio is preserved.

        General solution (works for any of the 4 corners):
            Let O = world coords of opposite corner before drag.
            (dx, dy) = (mouse - O).
            After applying new_scale: hw = scale*w/2, hh = scale*h/2.
            PIL local of the 4 corners in (TL,TR,BR,BL) order:
                L0 = (-hw, -hh)  L1 = (+hw, -hh)
                L2 = (+hw, +hh)  L3 = (-hw, +hh)
            i = idx, j = (idx+2) % 4 (opposite).
            AL = Lx_i - Lx_j,  BL = Ly_i - Ly_j.

            `_image_corners_world` uses math-CCW(R) on PIL local, then
            flips PIL-y to world-y. So the corner-handle equation in world
            coords is:
                dx =  AL*cos - BL*sin
                dy = -AL*sin - BL*cos
            Solving for (cos, sin):
                cos = ( AL*dx - BL*dy) / (AL^2 + BL^2)
                sin = -(BL*dx + AL*dy) / (AL^2 + BL^2)
                R = atan2(sin, cos)
        """
        s = self._settings
        corners = self._image_corners_world()
        if len(corners) != 4:
            return
        opp_idx = (idx + 2) % 4
        O = corners[opp_idx]
        tl, _tr, br, _bl = corners

        diag_old = math.hypot(br[0] - tl[0], br[1] - tl[1])
        if diag_old < 1e-6:
            return
        diag_new = math.hypot(m_w[0] - O[0], m_w[1] - O[1])
        factor = diag_new / diag_old
        if factor < self.MIN_SCALE_FACTOR:
            factor = self.MIN_SCALE_FACTOR
        s.background_scale = max(s.background_scale * factor, 1e-6)

        new_scale = s.background_scale
        hw = new_scale * self._raw_w / 2
        hh = new_scale * self._raw_h / 2
        # PIL local Y-down, in (TL, TR, BR, BL) order.
        pil = [(-hw, -hh), ( hw, -hh), ( hw,  hh), (-hw,  hh)]
        ix, iy = pil[idx]
        jx, jy = pil[opp_idx]
        AL = ix - jx
        BL = iy - jy
        denom = AL * AL + BL * BL
        if denom < 1e-12:
            return
        dx = m_w[0] - O[0]
        dy = m_w[1] - O[1]
        cos_R = (AL * dx - BL * dy) / denom
        sin_R = -(BL * dx + AL * dy) / denom
        s.background_rotation = math.degrees(math.atan2(sin_R, cos_R)) % 360.0

        # Place origin so the OPPOSITE corner stays exactly at O (this is
        # the only correct anchor for non-square images — a "midpoint"
        # trick drifts when hw != hh).
        s.background_origin_x = O[0] - (jx * cos_R - jy * sin_R)
        s.background_origin_y = O[1] - (-jx * sin_R - jy * cos_R)

    # -------------------- action buttons --------------------

    def _rotate_cw(self) -> None:
        self._settings.background_rotation = (self._settings.background_rotation + 90.0) % 360.0
        self._on_change()
        self._redraw_preview()

    def _rotate_ccw(self) -> None:
        self._settings.background_rotation = (self._settings.background_rotation - 90.0) % 360.0
        self._on_change()
        self._redraw_preview()

    def _reset_transform(self) -> None:
        s = self._settings
        s.background_origin_x = 0.0
        s.background_origin_y = 0.0
        s.background_rotation = 0.0
        if self._raw_w > 0 and self._raw_h > 0:
            # Recommend scale that fits the image inside the 144in field.
            s.background_scale = 144.0 / max(self._raw_w, self._raw_h)
        else:
            s.background_scale = 0.1
        self._on_change()
        self._redraw_preview()

    def _align_corner(self, idx: int) -> None:
        s = self._settings
        corners = self._image_corners_world()
        if len(corners) != 4:
            return
        fx, fy = self.FIELD_CORNERS[idx]
        cur = corners[idx]
        s.background_origin_x += (fx - cur[0])
        s.background_origin_y += (fy - cur[1])
        self._on_change()
        self._redraw_preview()

    def _center(self) -> None:
        self._settings.background_origin_x = 0.0
        self._settings.background_origin_y = 0.0
        self._on_change()
        self._redraw_preview()

    def _cancel(self) -> None:
        self.win.destroy()

    def _apply(self) -> None:
        # Flush latest var values into the shared Settings object before
        # handing off to the main window for persistence + status.
        self._on_path_or_op_change()
        self._on_vis_change()
        self._on_apply(self._settings)
        self.win.destroy()
