"""Background image settings dialog."""
from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from typing import Any, Callable

import ttkbootstrap as ttkb
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, X

from .settings import Settings


class BackgroundDialog:
    def __init__(self, parent: tk.Misc, settings: Settings,
                 on_apply: Callable[[Settings], None]) -> None:
        self._on_apply = on_apply
        self._settings = settings
        self._vars: dict[str, tk.Variable] = {}

        self.win = ttkb.Toplevel(parent)
        self.win.title("背景图设置")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()
        self.win.deiconify()

        frame = ttkb.Frame(self.win, padding=15)
        frame.pack(fill=BOTH, expand=True)
        frame.columnconfigure(1, weight=1)

        # Path row (label + entry + browse button)
        ttkb.Label(frame, text="图片路径").grid(row=0, column=0, sticky="w", pady=2)
        path_var = tk.StringVar(value=settings.background_image_path)
        self._vars["background_image_path"] = path_var
        path_entry = ttkb.Entry(frame, textvariable=path_var)
        path_entry.grid(row=0, column=1, sticky="ew", pady=2, padx=(0, 5))
        ttkb.Button(frame, text="浏览…", command=self._browse,
                    bootstyle="secondary").grid(row=0, column=2, sticky="e", pady=2)

        # Numeric rows (label + entry)
        rows = [
            ("background_origin_x",  "中心 X (in)",      settings.background_origin_x),
            ("background_origin_y",  "中心 Y (in)",      settings.background_origin_y),
            ("background_scale",     "缩放 (in/px)",     settings.background_scale),
            ("background_rotation",  "旋转 (°)",         settings.background_rotation),
        ]
        for i, (key, label, value) in enumerate(rows, start=1):
            ttkb.Label(frame, text=label).grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=str(value))
            self._vars[key] = var
            ttkb.Entry(frame, textvariable=var, width=14).grid(
                row=i, column=1, sticky="ew", pady=2)

        # Opacity slider row
        opacity_row = len(rows) + 1
        ttkb.Label(frame, text="透明度").grid(
            row=opacity_row, column=0, sticky="w", pady=2)
        op_var = tk.DoubleVar(value=settings.background_opacity)
        self._vars["background_opacity"] = op_var
        op_scale = ttkb.Scale(frame, from_=0.0, to=1.0, variable=op_var,
                              command=lambda _v: None)
        op_scale.grid(row=opacity_row, column=1, sticky="ew", pady=2)

        # Visible checkbox
        vis_row = opacity_row + 1
        vis_var = tk.BooleanVar(value=settings.background_visible)
        self._vars["background_visible"] = vis_var
        ttkb.Checkbutton(frame, text="显示背景", variable=vis_var,
                         bootstyle="round-toggle").grid(
            row=vis_row, column=1, sticky="w", pady=(4, 2))

        # Recommended scale hint (recomputed when path changes)
        hint_row = vis_row + 1
        self._hint_label = ttkb.Label(frame, text="", bootstyle="secondary")
        self._hint_label.grid(row=hint_row, column=0, columnspan=3, sticky="w", pady=(8, 0))
        path_var.trace_add("write", lambda *_a: self._refresh_hint())
        self._refresh_hint()

        # Button bar
        btn_bar = ttkb.Frame(self.win, padding=(15, 0, 15, 15))
        btn_bar.pack(fill=X)
        ttkb.Button(btn_bar, text="默认", command=self._restore_defaults,
                    bootstyle="secondary").pack(side=LEFT)
        ttkb.Button(btn_bar, text="取消", command=self.win.destroy,
                    bootstyle="secondary").pack(side=RIGHT, padx=(5, 0))
        ttkb.Button(btn_bar, text="应用", command=self._apply,
                    bootstyle="primary").pack(side=RIGHT)

        # Auto-fit to content
        self.win.update_idletasks()
        req_w = self.win.winfo_reqwidth()
        req_h = self.win.winfo_reqheight()
        self.win.geometry(f"{max(req_w, 380)}x{req_h}")
        self.win.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")

    def _browse(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择背景图",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                       ("所有文件", "*.*")],
        )
        if path:
            self._vars["background_image_path"].set(path)

    def _refresh_hint(self) -> None:
        path = self._vars["background_image_path"].get()
        if not path or not Path(path).is_file():
            self._hint_label.configure(text="")
            return
        try:
            from PIL import Image
            with Image.open(path) as img:
                w, h = img.size
        except Exception:
            self._hint_label.configure(text="(无法识别图片)")
            return
        # VEX field is 144 × 144 inches (12 ft). For an exact-fit, scale
        # would be 144 / max(w, h). Show this as a recommendation.
        rec = 144.0 / max(w, h)
        self._hint_label.configure(
            text=f"图片 {w}×{h}px  ·  若要填满 144in 场地, 推荐缩放 ≈ {rec:.4f} in/px")

    def _restore_defaults(self) -> None:
        s = Settings()
        for k, v in self._vars.items():
            if isinstance(v, tk.BooleanVar):
                v.set(getattr(s, k))
            elif isinstance(v, tk.DoubleVar):
                v.set(getattr(s, k))
            else:
                v.set(str(getattr(s, k)))

    def _apply(self) -> None:
        new = Settings()
        new.background_image_path = self._vars["background_image_path"].get().strip()
        for k in ("background_origin_x", "background_origin_y",
                  "background_scale", "background_rotation"):
            try:
                setattr(new, k, float(self._vars[k].get()))
            except ValueError:
                setattr(new, k, getattr(self._settings, k))
        try:
            op = float(self._vars["background_opacity"].get())
            new.background_opacity = max(0.0, min(1.0, op))
        except (ValueError, tk.TclError):
            new.background_opacity = self._settings.background_opacity
        new.background_visible = bool(self._vars["background_visible"].get())
        self._on_apply(new)
        self.win.destroy()