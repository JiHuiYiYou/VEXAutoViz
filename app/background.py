"""Background image manager — draws a top-down field photo behind the trajectory.

Anchored in world coordinates: `background_origin_x/y` is the image's
center, `background_scale` is inches per image pixel. `view_rotation`
(viewport compass) does NOT affect the image — only the trajectory
rotates, so the photo stays a fixed "field reference".

Renders only happen on state changes (path / mtime / rotation / opacity /
view_scale). Pan and window resize use the cached PhotoImage unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageTk

from .settings import Settings


class BackgroundManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._raw_image: Image.Image | None = None
        self._source_signature: tuple[str, float, int] | None = None
        # (path, mtime, size) — invalidates cache if file changes
        self._tk_image: Any = None
        self._cache_key: tuple | None = None

    # ---- public ----

    def draw(self, canvas: Any, to_canvas: Callable[[float, float],
                                                    tuple[float, float]],
             view_scale: float) -> None:
        """Render the background behind everything else.

        Called once per `_render()`. Cheap when nothing changed (just
        `create_image` at the new anchor point).
        """
        s = self.settings
        if not s.background_visible or not s.background_image_path:
            return
        path = s.background_image_path
        if not Path(path).is_file():
            return

        try:
            st = os.stat(path)
        except OSError:
            return
        signature = (path, st.st_mtime, st.st_size)
        if self._source_signature != signature:
            try:
                self._raw_image = Image.open(path)
                self._raw_image.load()  # force decode now, fail fast
            except (OSError, Image.UnidentifiedImageError, ValueError):
                self._raw_image = None
                self._source_signature = signature
                return
            self._source_signature = signature

        if self._raw_image is None:
            return

        cache_key = (
            signature,
            float(s.background_rotation),
            float(s.background_opacity),
            float(view_scale),
        )
        if self._cache_key != cache_key or self._tk_image is None:
            self._tk_image = self._render_image(view_scale)
            self._cache_key = cache_key
            if self._tk_image is None:
                return

        # Anchor at the world-space center. to_canvas already applies
        # view_rotation, _scale, and view_offset.
        cx, cy = to_canvas(s.background_origin_x, s.background_origin_y)
        canvas.create_image(cx, cy, image=self._tk_image, anchor="center",
                            tags=("background",))

    # ---- internal ----

    def _render_image(self, view_scale: float) -> Any | None:
        s = self.settings
        img = self._raw_image
        if img is None:
            return None
        # Work in RGBA so we can apply opacity via the alpha channel.
        rgba = img.convert("RGBA")
        # Canvas pixel size: image px × inches-per-pixel × world→canvas scale.
        # background_scale is inches per image pixel; view_scale is
        # canvas pixels per world inch. So size_px = px * bg_scale * view_scale.
        scale_px = max(s.background_scale, 1e-9) * view_scale
        target_w = max(1, int(round(rgba.width * scale_px)))
        target_h = max(1, int(round(rgba.height * scale_px)))
        if target_w < 1 or target_h < 1:
            return None
        if (target_w, target_h) != rgba.size:
            rgba = rgba.resize((target_w, target_h), Image.LANCZOS)

        # Rotation (degrees, CW). expand=True keeps the full rotated image.
        if abs(s.background_rotation) > 1e-6:
            rgba = rgba.rotate(-s.background_rotation, resample=Image.BICUBIC,
                               expand=True)

        # Opacity: scale alpha channel by `opacity` (0..1).
        op = max(0.0, min(1.0, float(s.background_opacity)))
        if op < 0.999:
            alpha = rgba.split()[3]
            alpha = alpha.point(lambda v: int(v * op))
            rgba.putalpha(alpha)

        return ImageTk.PhotoImage(rgba)