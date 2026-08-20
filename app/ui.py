"""Main window for VEXAutoViz."""
from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox
from typing import Any

import ttkbootstrap as ttkb
from ttkbootstrap.constants import (
    BOTH, BOTTOM, END, HORIZONTAL, LEFT, RIGHT, TOP, X, Y,
)

from . import drawing
from .background import BackgroundManager
from .background_dialog import BackgroundDialog
from .commands import ChassisCommand, CommandKind, TrajectorySegment
from .detail_panel import DetailPanel
from .highlight import HighlightMap
from .parser import CppParser
from .settings import Settings, load_settings, save_settings
from .simulator import Simulator


CANVAS_BG = "#1e1e1e"
GRID_MAJOR = "#2e2e2e"
START_COLOR = "#88ff88"
START_RADIUS = 9
HIGHLIGHT_COLOR = "#ffeb3b"
HIGHLIGHT_WIDTH_BOOST = 3

# Endpoint marker (user-selected "key points") — cyan diamond, distinct
# from the green start dot and the magenta/cyan turn wedges.
MARKER_COLOR = "#00d4ff"
MARKER_OUTLINE = "#0a3a4a"
MARKER_HALF = 9          # half side length of the diamond (canvas px)
# Drawing-mode overlay (polyline in progress).
DRAW_COLOR = "#00d4ff"
DRAW_POINT_RADIUS = 5
# Snap radius in world inches — clicks within this distance of a marked
# endpoint snap to that endpoint.
SNAP_RADIUS_IN = 3.0

COMPASS_RADIUS = 32
COMPASS_HIT_RADIUS = 44
COMPASS_ANCHOR = "tr"     # top-right corner

KIND_LABEL = {
    CommandKind.DRIVE: "drive  ",
    CommandKind.TURN: "turn   ",
    CommandKind.TURN_LR: "turnLR",
    CommandKind.STOP: "stop   ",
}

LISTBOX_BG = "#1e1e1e"
LISTBOX_FG = "#d4d4d4"
LISTBOX_SEL_BG = "#264f78"
LISTBOX_SEL_FG = "#ffffff"


def _format_row(cmd: ChassisCommand) -> str:
    label = KIND_LABEL.get(cmd.kind, "?     ")
    if cmd.kind == CommandKind.DRIVE:
        d = cmd.args.get("distance", 0)
        h = cmd.args.get("heading", 0)
        v = cmd.args.get("drive_max_voltage", 0)
        h_str = "gyro" if isinstance(h, str) else f"{h:.0f}°"
        return f"L{cmd.line:<3} · {label} · {d:+6.1f}in  →  {h_str:>4} @ {v}V"
    if cmd.kind in (CommandKind.TURN, CommandKind.TURN_LR):
        t = cmd.args.get("target", 0)
        t_str = "gyro" if isinstance(t, str) else f"{t:.0f}°"
        return f"L{cmd.line:<3} · {label} · → {t_str:>4}"
    if cmd.kind == CommandKind.STOP:
        m = cmd.args.get("mode", "")
        return f"L{cmd.line:<3} · {label} · {m}"
    return f"L{cmd.line} · {cmd.kind.value}"


def _row_color(cmd: ChassisCommand) -> str:
    """Foreground color for the listbox row (kind-themed)."""
    if cmd.kind == CommandKind.DRIVE:
        return "#3b8edb"
    if cmd.kind == CommandKind.TURN:
        return "#d65bce"
    if cmd.kind == CommandKind.TURN_LR:
        return "#5bd0ce"
    if cmd.kind == CommandKind.STOP:
        m = str(cmd.args.get("mode", ""))
        if m == "brake":
            return "#ff5252"
        if m == "hold":
            return "#ffd54f"
        return "#9e9e9e"
    return LISTBOX_FG


class MainWindow:
    def __init__(self, root: ttkb.Window) -> None:
        self.root = root
        self.root.title("VEX Auto Visualizer")
        self.root.geometry("1200x800")
        self.root.minsize(800, 500)

        self.parser = CppParser()
        self.settings: Settings = load_settings()
        self.simulator = self._make_simulator()
        self.background = BackgroundManager(self.settings)
        self.commands: list[ChassisCommand] = []
        self.segments: list[TrajectorySegment] = []
        self.highlight = HighlightMap([], [])
        self.current_path: str | None = None
        self._scale = self.settings.pixels_per_inch or 3.0
        self._highlighted_lines: set[int] = set()
        self.view_rotation: float = self.settings.view_rotation
        self.view_offset_x: float = 0.0
        self.view_offset_y: float = 0.0
        self._auto_scale: bool = True
        self._dragging_compass = False
        self._pending_click = False
        self._pan_active = False
        self._press_x = 0
        self._press_y = 0
        self._last_drag_x = 0
        self._last_drag_y = 0
        # playback state
        self._t_current: float = 0.0
        self._is_playing: bool = False
        self._play_after_id: str | None = None
        self._t_x: float = self.settings.initial_x
        self._t_y: float = self.settings.initial_y
        self._t_h: float = self.settings.initial_heading
        # Session-only set of `line` numbers whose segments are hidden.
        # Hidden segments are skipped when running the simulator, so later
        # segments chain directly off the previous visible one's end.
        self.hidden_lines: set[int] = set()
        # Session-only set of `line` numbers whose END waypoint is marked
        # as a "key point" by the user. Drawn as cyan diamonds on the canvas.
        # Used both as a visual hint and as snap targets in drawing mode.
        self.marked_lines: set[int] = set()
        # Absolute world (x, y) at the moment the marker was created. Frozen
        # so editing / hiding / deleting upstream segments doesn't drift the
        # marker's visible position. Keyed by line number, same as above.
        self.marker_positions: dict[int, tuple[float, float]] = {}
        # Drawing-mode state. Activated by toolbar [📐 绘制] or `D` key.
        self._drawing_mode: bool = False
        self._drawing_start_line: int | None = None
        self._drawing_start_world: tuple[float, float] | None = None
        self._drawing_polyline: list[tuple[float, float]] = []
        self._draw_btn: Any = None

        self._build_toolbar()
        self._build_panes()
        self._build_playback_bar()
        self._build_status_bar()

        self.canvas.bind("<Configure>", lambda _e: self._render())
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<MouseWheel>", self._on_canvas_wheel)
        self.canvas.bind("<Button-3>", self._on_canvas_right_click)

        # Key bindings — bound at root so they work regardless of focus.
        self.root.bind("<KeyPress-d>",   lambda _e: self._toggle_drawing())
        self.root.bind("<KeyPress-D>",   lambda _e: self._toggle_drawing())
        self.root.bind("<Escape>",       lambda _e: self._on_escape())
        self.root.bind("<Delete>",       lambda _e: self._on_delete_key())
        self.root.bind("<BackSpace>",    lambda _e: self._on_delete_key())

    def _make_simulator(self) -> Simulator:
        return Simulator(self.settings)

    def _build_toolbar(self) -> None:
        bar = ttkb.Frame(self.root, padding=5)
        bar.pack(side=TOP, fill=X)

        ttkb.Button(bar, text="打开 Auto.cpp", command=self.open_file,
                    bootstyle="primary").pack(side=LEFT, padx=(0, 5))
        ttkb.Button(bar, text="重新加载", command=self.reload,
                    bootstyle="secondary").pack(side=LEFT, padx=(0, 5))
        ttkb.Button(bar, text="设置", command=self.open_settings,
                    bootstyle="secondary").pack(side=LEFT, padx=(0, 5))
        ttkb.Button(bar, text="背景", command=self.open_background,
                    bootstyle="secondary").pack(side=LEFT, padx=(0, 5))
        # Toggle button — `bootstyle="info-outline"` flips to `info` when
        # drawing mode is active. See `_set_drawing_mode` for the toggle.
        self._draw_btn = ttkb.Button(bar, text="📐 绘制", command=self._toggle_drawing,
                                     bootstyle="info-outline")
        self._draw_btn.pack(side=LEFT, padx=(0, 5))
        ttkb.Button(bar, text="复位视角", command=self.reset_view,
                    bootstyle="secondary").pack(side=LEFT, padx=(0, 5))

        self.path_label = ttkb.Label(bar, text="未选择文件", anchor="w",
                                     bootstyle="secondary")
        self.path_label.pack(side=LEFT, fill=X, expand=True, padx=10)

    def _build_panes(self) -> None:
        self.panes = ttkb.Panedwindow(self.root, orient=HORIZONTAL)
        self.panes.pack(fill=BOTH, expand=True, padx=5, pady=(0, 0))

        # Left: command listbox
        left = ttkb.Frame(self.panes)
        self.panes.add(left, weight=2)

        mono = font.nametofont("TkFixedFont")
        mono.configure(size=11)
        self.listbox = tk.Listbox(
            left, font=mono, background=LISTBOX_BG, foreground=LISTBOX_FG,
            selectbackground=LISTBOX_SEL_BG, selectforeground=LISTBOX_SEL_FG,
            borderwidth=0, highlightthickness=0,
            activestyle="none",
        )
        self.listbox.pack(fill=BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_listbox_select)
        self.listbox.bind("<Button-3>", self._on_listbox_right_click)
        # Bind arrow keys to root so they work regardless of focus (canvas,
        # toolbar, etc.). _select_adjacent returns "break" to suppress the
        # Tk Listbox's default Up/Down multi-select / anchor behavior.
        self.root.bind("<Up>",    lambda _e: self._select_adjacent(-1))
        self.root.bind("<Down>",  lambda _e: self._select_adjacent(1))
        self.root.bind("<Left>",  lambda _e: self._select_adjacent(-1))
        self.root.bind("<Right>", lambda _e: self._select_adjacent(1))
        self.root.bind("<space>", lambda _e: self._toggle_play())

        # Middle: detail panel (selected command's args + raw + context)
        middle = ttkb.Frame(self.panes)
        self.panes.add(middle, weight=3)
        self.detail = DetailPanel(
            middle,
            on_dirty=self._on_detail_dirty,
            on_save=self._on_detail_saved,
            on_discard=self._on_detail_discarded,
        )
        self.detail.pack(fill=BOTH, expand=True)

        # Right: trajectory canvas
        right = ttkb.Frame(self.panes)
        self.panes.add(right, weight=3)

        self.canvas = tk.Canvas(right, background=CANVAS_BG, highlightthickness=0)
        self.canvas.pack(fill=BOTH, expand=True)

    def _build_status_bar(self) -> None:
        self.status = ttkb.Label(self.root, text="就绪", anchor="w",
                                 bootstyle="secondary", padding=(5, 2))
        self.status.pack(side=BOTTOM, fill=X)

    def _build_playback_bar(self) -> None:
        bar = ttkb.Frame(self.root, padding=(5, 2))
        bar.pack(side=BOTTOM, fill=X)
        self.play_btn = ttkb.Button(bar, text="▶", width=3,
                                    command=self._toggle_play, bootstyle="secondary")
        self.play_btn.pack(side=LEFT)
        self.time_label = ttkb.Label(bar, text="0.00 / 0.00s", width=14, anchor="w",
                                     bootstyle="secondary")
        self.time_label.pack(side=LEFT, padx=(5, 5))
        self.t_slider = ttkb.Scale(bar, from_=0, to=1, value=0,
                                   command=self._on_slider_change)
        self.t_slider.pack(side=LEFT, fill=X, expand=True)
        ttkb.Button(bar, text="⟲", width=3, command=self._reset_playback,
                    bootstyle="secondary").pack(side=LEFT, padx=(5, 0))

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 Auto.cpp",
            filetypes=[("C++ source", "*.cpp *.cc *.cxx"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        self._load(path)

    def reload(self) -> None:
        if self.current_path:
            self._load(self.current_path)

    def reset_view(self) -> None:
        self._highlighted_lines.clear()
        self.listbox.selection_clear(0, END)
        self.view_rotation = 0.0
        self.view_offset_x = 0.0
        self.view_offset_y = 0.0
        self._auto_scale = True
        self.hidden_lines.clear()
        # Markers are session-only visual annotations; reset wipes them.
        self.marked_lines.clear()
        self.marker_positions.clear()
        self._reset_playback()
        if self.commands:
            self._refresh_segments()
        else:
            self._render()

    def open_settings(self) -> None:
        SettingsDialog(self.root, self.settings, on_apply=self._apply_settings)

    def open_background(self) -> None:
        BackgroundDialog(self.root, self.settings,
                         on_apply=self._apply_settings,
                         on_change=self._background_changed)

    def _background_changed(self) -> None:
        """Live update from BackgroundDialog — main canvas reflects mid-edit."""
        self.background.invalidate_cache()
        self._render()

    def _apply_settings(self, new_settings: Settings) -> None:
        self.settings = new_settings
        save_settings(new_settings)
        self.view_rotation = new_settings.view_rotation
        self.simulator = self._make_simulator()
        self.background.invalidate_cache()
        if self.commands:
            self.segments = self.simulator.run(self.commands)
            self.highlight = HighlightMap(self.commands, self.segments)
        self._highlighted_lines.clear()
        self.listbox.selection_clear(0, END)
        self._reset_playback()
        self._render()
        self._set_status("设置已应用")

    def _load(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            self._set_status(f"读取失败: {exc}")
            return

        self.current_path = path
        self.path_label.configure(text=path)
        self.root.title(f"VEX Auto Visualizer — {path}")

        self.commands = self.parser.parse(content)
        self.hidden_lines.clear()
        # Drop markers whose lines no longer exist after an external edit to
        # Auto.cpp. Internal `_delete_segment` already cleaned up its own
        # line first, but for symmetry we still drop any orphans here.
        # IMPORTANT: `marker_positions` is NOT cleared — frozen positions
        # survive even when the line that originally produced them is gone,
        # so the user can keep visually anchoring a "key point" after
        # deleting the segment that used to land on it.
        valid_lines = {c.line for c in self.commands}
        self.marked_lines &= valid_lines
        self.segments = self.simulator.run(self.commands)
        self.highlight = HighlightMap(self.commands, self.segments)
        self._highlighted_lines.clear()
        self.view_offset_x = 0.0
        self.view_offset_y = 0.0
        self._auto_scale = True
        self._reset_playback()

        self._populate_listbox()
        # Detail panel: feed file lines + per-line command index.
        file_lines = content.split("\n")
        # Drop the trailing empty string that comes from a trailing "\n",
        # so 1-based line numbers match what the parser emitted.
        if file_lines and file_lines[-1] == "":
            file_lines = file_lines[:-1]
        commands_by_line = {c.line: c for c in self.commands}
        self.detail.set_file(path, file_lines, commands_by_line)
        self.detail.show(self.commands[0] if self.commands else None)

        self._render()
        self._update_pose_status()

    def _populate_listbox(self) -> None:
        self.listbox.delete(0, END)
        for i, cmd in enumerate(self.commands):
            text = _format_row(cmd)
            color = _row_color(cmd)
            if cmd.line in self.hidden_lines:
                text = "[隐藏] " + text
                color = "#666666"
            self.listbox.insert(END, text)
            self.listbox.itemconfigure(i, foreground=color)

    def _render(self) -> None:
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 50 or h < 50:
            return

        self._autoscale(w, h)
        # Cache the playback-frame pose so the marker is drawn from current time,
        # not from the simulator's final state.
        self._t_x, self._t_y, self._t_h = self.simulator.pose_at(self._t_current)
        self._draw_grid(w, h)
        # Background image sits above the grid, below the trajectory.
        self.background.draw(self.canvas, self._to_canvas, self._scale)

        for seg in self.segments:
            self._draw_segment(seg)
        self._draw_start_marker()
        self._draw_robot_marker()
        # User-marked endpoint diamonds — drawn after the trajectory so they
        # stay visible, but before the compass so HUD never gets obscured.
        # Iteration is over `marker_positions` (not `marked_lines`), so a
        # marker stays on the canvas even after its original segment was
        # deleted — that's the "key point survives segment deletion"
        # semantic the user asked for.
        for line, world_xy in self.marker_positions.items():
            self._draw_endpoint_marker(world_xy, line)
        if self._drawing_mode and self._drawing_polyline:
            self._draw_polyline_overlay()
        self._draw_compass(w, h)

    def _draw_grid(self, w: int, h: int) -> None:
        step_px = 12 * self._scale
        if step_px < 6:
            return
        # Screen-space grid: lines stay axis-aligned regardless of view rotation.
        # Anchor the grid to world (0,0) so the "axes through origin" still line up.
        origin_x = w / 2 + self.view_offset_x
        origin_y = h / 2 + self.view_offset_y
        # First line to the left/top of origin, step back until off-screen.
        x = origin_x
        while x > 0:
            x -= step_px
        while x < w:
            xi = int(round(x))
            self.canvas.create_line(xi, 0, xi, h, fill=GRID_MAJOR, tags=("grid",))
            x += step_px
        y = origin_y
        while y > 0:
            y -= step_px
        while y < h:
            yi = int(round(y))
            self.canvas.create_line(0, yi, w, yi, fill=GRID_MAJOR, tags=("grid",))
            y += step_px
        # axes through the origin
        ax_color = "#444"
        self.canvas.create_line(0, origin_y, w, origin_y,
                                fill=ax_color, tags=("grid",))
        self.canvas.create_line(origin_x, 0, origin_x, h,
                                fill=ax_color, tags=("grid",))

    def _autoscale(self, w: int, h: int) -> None:
        if not self._auto_scale:
            return
        if self.settings.pixels_per_inch > 0:
            self._scale = self.settings.pixels_per_inch
            self.view_offset_x = 0.0
            self.view_offset_y = 0.0
            return
        if not self.segments:
            return
        max_abs = 1.0
        for seg in self.segments:
            if seg.kind != CommandKind.DRIVE:
                continue
            for x, y in seg.waypoints:
                max_abs = max(max_abs, abs(x), abs(y))
        usable = min(w, h) / 2 - 20
        self._scale = min(8.0, max(1.0, usable / max_abs))
        self.view_offset_x = 0.0
        self.view_offset_y = 0.0

    def _to_canvas(self, x: float, y: float) -> tuple[float, float]:
        """World-frame (x, y) inches → canvas pixels with view rotation+offset+scale applied."""
        θ = math.radians(self.view_rotation)
        rx = x * math.cos(θ) + y * math.sin(θ)
        ry = -x * math.sin(θ) + y * math.cos(θ)
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        return (w / 2 + rx * self._scale + self.view_offset_x,
                h / 2 - ry * self._scale + self.view_offset_y)

    def _draw_segment(self, seg: TrajectorySegment) -> None:
        is_hl = seg.line in self._highlighted_lines
        if is_hl:
            fill = HIGHLIGHT_COLOR
            width = seg.width + HIGHLIGHT_WIDTH_BOOST
        else:
            fill = seg.color
            width = seg.width

        if seg.kind == CommandKind.DRIVE and len(seg.waypoints) >= 2:
            (x0, y0), (x1, y1) = seg.waypoints
            cx0, cy0 = self._to_canvas(x0, y0)
            cx1, cy1 = self._to_canvas(x1, y1)
            self.canvas.create_line(cx0, cy0, cx1, cy1,
                                    fill=fill, width=width, arrow="last",
                                    arrowshape=(10, 12, 3),
                                    tags=("drive", seg.tag))
        elif seg.kind in (CommandKind.TURN, CommandKind.TURN_LR):
            self._draw_turn_dot(seg, fill, width)
        elif seg.kind == CommandKind.STOP:
            self._draw_stop_dot(seg, fill, width)

    def _draw_stop_dot(self, seg: TrajectorySegment, fill: str, width: int) -> None:
        x, y = seg.waypoints[0]
        cx, cy = self._to_canvas(x, y)
        r = 8 if seg.line not in self._highlighted_lines else 11
        outline = "white" if seg.line not in self._highlighted_lines else HIGHLIGHT_COLOR
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                fill=fill, outline=outline, width=width,
                                tags=("stop", seg.tag))

    def _draw_turn_dot(self, seg: TrajectorySegment, fill: str, width: int) -> None:
        x, y = seg.waypoints[0]
        cx, cy = self._to_canvas(x, y)
        is_hl = seg.line in self._highlighted_lines
        r = 11 if not is_hl else 15
        inner_r = r - 3
        outline = "white" if not is_hl else HIGHLIGHT_COLOR
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                fill=fill, outline=outline, width=width,
                                tags=("turn", seg.tag))
        self.canvas.create_oval(cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r,
                                fill=CANVAS_BG, outline="",
                                tags=("turn", seg.tag))

    def _draw_start_marker(self) -> None:
        cx, cy = self._to_canvas(0, 0)
        self.canvas.create_oval(cx - START_RADIUS, cy - START_RADIUS,
                                cx + START_RADIUS, cy + START_RADIUS,
                                fill=START_COLOR, outline="", tags=("start",))
        self.canvas.create_text(cx, cy + START_RADIUS + 12, text="起点",
                                fill=START_COLOR, font=("Arial", 9),
                                tags=("start",))

    def _draw_robot_marker(self) -> None:
        """Forward-pointing arrow marker at the playback-frame pose.

        Drawn in screen space so size is constant regardless of zoom;
        direction follows world heading + view_rotation.
        """
        cx, cy = self._to_canvas(self._t_x, self._t_y)
        h = math.radians(self._t_h + self.view_rotation)
        R = 18   # px — overall screen-space size (fixed)
        # Local-frame polygon (forward = +y in local frame).
        pts_local = [
            (0.0,      R),         # 0 front tip
            (-R*0.55,  R*0.45),    # 1 head left shoulder
            (-R*0.50, -R*0.55),    # 2 tail left
            (0.0,     -R*0.30),    # 3 tail center notch
            (R*0.50,  -R*0.55),    # 4 tail right
            (R*0.55,   R*0.45),    # 5 head right shoulder
        ]
        def to_screen(lx: float, ly: float) -> tuple[float, float]:
            sx = cx + lx * math.cos(h) + ly * math.sin(h)
            sy = cy + lx * math.sin(h) - ly * math.cos(h)
            return (sx, sy)
        body_pts: list[float] = []
        for idx in (0, 1, 2, 3, 4, 5):
            body_pts.extend(to_screen(*pts_local[idx]))
        self.canvas.create_polygon(
            *body_pts, fill="#ffd54f", outline="black", width=2,
            tags=("robot",))
        # Red front-tip triangle — distinct front so direction is unambiguous.
        head_local = [
            (0.0,       R * 0.92),
            (-R * 0.32, R * 0.45),
            (R * 0.32,  R * 0.45),
        ]
        head_pts: list[float] = []
        for lx, ly in head_local:
            head_pts.extend(to_screen(lx, ly))
        self.canvas.create_polygon(
            *head_pts, fill="#ff5252", outline="black", width=1,
            tags=("robot",))

    def _compass_center(self, w: int, h: int) -> tuple[int, int]:
        return (w - 50, 50)

    def _is_in_compass(self, x: int, y: int) -> bool:
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 100 or h < 100:
            return False
        cx, cy = self._compass_center(w, h)
        return (x - cx) ** 2 + (y - cy) ** 2 <= COMPASS_HIT_RADIUS ** 2

    def _draw_compass(self, w: int, h: int) -> None:
        if w < 100 or h < 100:
            return
        cx, cy = self._compass_center(w, h)
        r = COMPASS_RADIUS

        # Background circle
        bg = "#2a2a2a" if not self._dragging_compass else "#3a3a3a"
        self.canvas.create_oval(cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4,
                                fill=bg, outline="#666", width=1,
                                tags=("compass",))
        # Outer ring
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                fill="", outline="#555", width=1,
                                tags=("compass",))

        # Compute screen direction of world Y (north) and world X (east).
        θ = math.radians(self.view_rotation)
        # World-Y tip in screen: at angle θ CW from up.
        # World-X tip in screen: at angle (θ + 90) CW from up.
        n_tip_x = cx + r * math.sin(θ)
        n_tip_y = cy - r * math.cos(θ)
        e_tip_x = cx + r * math.cos(θ)
        e_tip_y = cy + r * math.sin(θ)

        # East axis (red, X)
        self.canvas.create_line(cx, cy, e_tip_x, e_tip_y,
                                fill="#ff5252", width=2, arrow="last",
                                arrowshape=(8, 10, 4),
                                tags=("compass",))
        # North axis (green, Y)
        self.canvas.create_line(cx, cy, n_tip_x, n_tip_y,
                                fill="#5bd0ce", width=2, arrow="last",
                                arrowshape=(8, 10, 4),
                                tags=("compass",))
        # Center dot
        self.canvas.create_oval(cx - 2, cy - 2, cx + 2, cy + 2,
                                fill="#cccccc", outline="", tags=("compass",))
        # Labels at tips
        lbl_offset = 4
        self.canvas.create_text(e_tip_x + lbl_offset, e_tip_y + lbl_offset,
                                text="E", fill="#ff5252", font=("Arial", 8, "bold"),
                                tags=("compass",))
        self.canvas.create_text(n_tip_x, n_tip_y - lbl_offset * 2,
                                text="N", fill="#5bd0ce", font=("Arial", 8, "bold"),
                                tags=("compass",))
        # Rotation value below
        self.canvas.create_text(cx, cy + r + 14,
                                text=f"{self.view_rotation:+.0f}°  拖动旋转",
                                fill="#aaaaaa", font=("Arial", 8),
                                tags=("compass",))

    # ---- click handlers ----

    def _on_canvas_press(self, event: Any) -> None:
        if self._is_in_compass(event.x, event.y):
            self._dragging_compass = True
            self._update_rotation_from_event(event)
            return
        if self._drawing_mode:
            # In drawing mode, left-click adds a polyline point (after start).
            # Right-click is handled separately in `_on_canvas_right_click`.
            self._on_drawing_click(event)
            return
        self._press_x = event.x
        self._press_y = event.y
        self._last_drag_x = event.x
        self._last_drag_y = event.y
        self._pending_click = True
        self._pan_active = False

    def _on_canvas_drag(self, event: Any) -> None:
        if self._dragging_compass:
            self._update_rotation_from_event(event)
            return
        total_dx = event.x - self._press_x
        total_dy = event.y - self._press_y
        if not self._pan_active and (total_dx ** 2 + total_dy ** 2 > 25):
            self._pan_active = True
            self._pending_click = False
            self._auto_scale = False
        if self._pan_active:
            self.view_offset_x += event.x - self._last_drag_x
            self.view_offset_y += event.y - self._last_drag_y
            self._render()
        self._last_drag_x = event.x
        self._last_drag_y = event.y

    def _on_canvas_release(self, event: Any) -> None:
        if self._dragging_compass:
            self._dragging_compass = False
            # Persist the new rotation so it survives restarts.
            self.settings.view_rotation = self.view_rotation
            save_settings(self.settings)
            self._render()
            return
        if self._pending_click and not self._pan_active:
            self._on_canvas_click(event)
        self._pending_click = False
        self._pan_active = False

    def _update_rotation_from_event(self, event: Any) -> None:
        cx, cy = self._compass_center(self.canvas.winfo_width(),
                                       self.canvas.winfo_height())
        dx = event.x - cx
        dy = event.y - cy
        # Capture the world point currently at the visual center so we can
        # keep it fixed when the view rotates (no swing if (0,0) is far away).
        θ_old = math.radians(self.view_rotation)
        rx_c = -self.view_offset_x / self._scale
        ry_c = self.view_offset_y / self._scale
        cx_w = rx_c * math.cos(θ_old) - ry_c * math.sin(θ_old)
        cy_w = rx_c * math.sin(θ_old) + ry_c * math.cos(θ_old)
        # angle CW from screen-up, in degrees
        self.view_rotation = math.degrees(math.atan2(dx, -dy))
        # Recompute offset so the same world point stays at the visual center.
        θ_new = math.radians(self.view_rotation)
        rx_n = cx_w * math.cos(θ_new) + cy_w * math.sin(θ_new)
        ry_n = -cx_w * math.sin(θ_new) + cy_w * math.cos(θ_new)
        self.view_offset_x = -rx_n * self._scale
        self.view_offset_y = ry_n * self._scale
        self._render()

    def _on_canvas_wheel(self, event: Any) -> None:
        factor = 1.1 if event.delta > 0 else 1 / 1.1
        self._zoom_at(event.x, event.y, factor)

    def _zoom_at(self, mx: int, my: int, factor: float) -> None:
        new_scale = max(0.5, min(50.0, self._scale * factor))
        real_factor = new_scale / self._scale
        self._auto_scale = False
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        pre_dx = mx - w / 2 - self.view_offset_x
        pre_dy = my - h / 2 - self.view_offset_y
        self._scale = new_scale
        self.view_offset_x = mx - pre_dx * real_factor - w / 2
        self.view_offset_y = my - pre_dy * real_factor - h / 2
        self._render()

    def _on_listbox_select(self, _event: Any) -> None:
        sel = self.listbox.curselection()
        if not sel:
            self.detail.show(None)
            return
        lines = {self.commands[i].line for i in sel}
        self._highlighted_lines = lines
        if len(sel) == 1:
            self.detail.show(self.commands[sel[0]])
        self._render()

    def _on_detail_dirty(self) -> None:
        # Args edited in the detail panel affect the trajectory. Re-run the
        # simulator against the current commands so the canvas reflects
        # the in-progress edit. Save-to-disk is still gated by the user
        # clicking "保存到文件".
        self.segments = self.simulator.run(self.commands)
        self.highlight = HighlightMap(self.commands, self.segments)
        self._t_current = min(self._t_current, self.simulator.total_duration)
        self._render()
        self._update_pose_status()

    def _on_detail_saved(self) -> None:
        # Re-read the file from disk and reparse so commands reflect the
        # saved state (preserves any edits to nearby context lines).
        if not self.current_path:
            return
        self._load(self.current_path)
        self._set_status("已保存到文件")

    def _on_detail_discarded(self) -> None:
        self._set_status("已撤销改动")

    def _on_listbox_right_click(self, event: Any) -> None:
        row = self.listbox.nearest(event.y)
        if row < 0 or row >= len(self.commands):
            return
        self.listbox.selection_clear(0, END)
        self.listbox.selection_set(row)
        self.listbox.activate(row)
        line = self.commands[row].line
        is_hidden = line in self.hidden_lines

        menu = tk.Menu(self.root, tearoff=0)
        if is_hidden:
            menu.add_command(label="取消隐藏此段",
                             command=lambda ln=line: self._unhide_line(ln))
        else:
            menu.add_command(label="隐藏此段",
                             command=lambda ln=line: self._hide_line(ln))
        # Bulk: hide all DRIVE segments whose voltage is at or below the
        # configured threshold. Always offer this option; the threshold lives
        # in settings so users can tune it.
        if not is_hidden:
            thr = self.settings.hide_voltage_threshold
            menu.add_command(
                label=f"隐藏所有 ≤{thr:.1f}v 的 DRIVE 段",
                command=lambda t=thr: self._hide_below_voltage(t))
        menu.add_separator()
        menu.add_command(label="全部取消隐藏",
                         command=self._unhide_all)
        # Marker toggle (per-line).
        if line in self.marked_lines:
            menu.add_command(label="取消标记此段终点 🔖",
                             command=lambda ln=line: (
                                 self.marked_lines.discard(ln),
                                 self._render()))
        else:
            menu.add_command(label="标记此段终点 🔖",
                             command=lambda ln=line: self._toggle_marker(ln))
        menu.add_separator()
        # Delete (destructive — confirm before splicing the file).
        menu.add_command(label="删除此段 (会改 Auto.cpp)",
                         command=lambda ln=line: self._prompt_and_delete(ln))
        menu.tk_popup(event.x_root, event.y_root)

    def _hide_line(self, line: int) -> None:
        self.hidden_lines.add(line)
        self._refresh_segments()

    def _unhide_line(self, line: int) -> None:
        self.hidden_lines.discard(line)
        self._refresh_segments()

    def _hide_below_voltage(self, threshold: float) -> None:
        for cmd in self.commands:
            if cmd.kind != CommandKind.DRIVE:
                continue
            try:
                v = float(cmd.args.get("drive_max_voltage", 0))
            except (TypeError, ValueError):
                continue
            if 0 < v <= threshold:
                self.hidden_lines.add(cmd.line)
        self._refresh_segments()

    def _unhide_all(self) -> None:
        if not self.hidden_lines:
            return
        self.hidden_lines.clear()
        self._refresh_segments()

    def _refresh_segments(self) -> None:
        """Re-run the simulator against visible commands only, then redraw."""
        visible_cmds = [c for c in self.commands if c.line not in self.hidden_lines]
        self.segments = self.simulator.run(visible_cmds)
        self.highlight = HighlightMap(visible_cmds, self.segments)
        # Clamp playback time so a hide that shortens the trail doesn't leave
        # the slider pointing past the end.
        self._t_current = min(self._t_current, self.simulator.total_duration)
        self._populate_listbox()
        self._sync_slider_and_highlight()

    def _select_adjacent(self, delta: int) -> str:
        """Single-row nav: delta=-1 for prev (←/↑), +1 for next (→/↓).

        Also jumps the playback time to the selected segment's start so a
        mid-playback arrow-key press "restarts" at the new segment.
        """
        n = len(self.commands)
        if n == 0:
            return "break"
        sel = self.listbox.curselection()
        if not sel:
            if delta < 0:
                return "break"
            new = 0
        else:
            new = sel[0] + delta
        if new < 0 or new >= n:
            return "break"
        # Jump playback to this segment's start; _sync_slider_and_highlight
        # below will keep the slider / label / highlight in sync.
        segs = self.simulator._segments_cache
        if new < len(segs):
            self._t_current = segs[new].t_start
        self.listbox.selection_clear(0, END)
        self.listbox.selection_set(new)
        self.listbox.activate(new)
        self.listbox.see(new)
        # selection_clear + selection_set back-to-back inside one callback
        # can collapse <<ListboxSelect>> to a no-op; force the highlight refresh
        # explicitly so the canvas tracks the new selection.
        self._sync_slider_and_highlight()
        self._on_listbox_select(None)
        return "break"

    def _on_canvas_click(self, event: Any) -> None:
        # find_closest returns nearest item including grid; find closest seg-tagged item
        x, y = event.x, event.y
        item = self.canvas.find_closest(x, y)
        if not item:
            return
        tags = self.canvas.gettags(item)
        seg_tag = next((t for t in tags if t.startswith("seg_")), None)
        if not seg_tag:
            return
        try:
            seg_idx = int(seg_tag.split("_")[1])
        except (ValueError, IndexError):
            return
        line = self.highlight.line_for_segment(seg_idx)
        if line is None:
            return
        # Jump playback time to this segment's start so a mid-playback click
        # "restarts" at the new segment.
        segs = self.simulator._segments_cache
        if seg_idx < len(segs):
            self._t_current = segs[seg_idx].t_start
        # find listbox row for this line
        for i, cmd in enumerate(self.commands):
            if cmd.line == line:
                self.listbox.selection_clear(0, END)
                self.listbox.selection_set(i)
                self.listbox.activate(i)
                self.listbox.see(i)
                self._highlighted_lines = {line}
                self.detail.show(cmd)
                self._render()
                return

    # ---- markers / delete / drawing ----

    def _draw_endpoint_marker(self, world_xy: tuple[float, float],
                              line: int) -> None:
        """Cyan diamond at the END waypoint of the marked segment."""
        cx, cy = self._to_canvas(*world_xy)
        h = MARKER_HALF
        pts = [cx, cy - h, cx + h, cy, cx, cy + h, cx - h, cy]
        self.canvas.create_polygon(
            *pts, fill=MARKER_COLOR, outline=MARKER_OUTLINE, width=2,
            tags=("endpoint", f"endpoint_{line}"))
        # Tiny line-number badge so the user can tell which marker is which
        # when several are clustered.
        self.canvas.create_text(
            cx, cy - h - 9, text=f"L{line}",
            fill=MARKER_COLOR, font=("Arial", 8, "bold"),
            tags=("endpoint", f"endpoint_{line}"))

    def _toggle_marker(self, line: int) -> None:
        # Cancel path: only act when something actually exists for this line.
        # If the segment was deleted, `line` is no longer in `marked_lines`,
        # but its frozen position may still be in `marker_positions` —
        # cancel must still remove it (otherwise the user has no way to
        # clean up an orphan marker).
        if line in self.marked_lines or line in self.marker_positions:
            self.marked_lines.discard(line)
            self.marker_positions.pop(line, None)
            verb = "取消标记"
        else:
            self.marked_lines.add(line)
            # Snapshot the END waypoint as an absolute world coord so the
            # marker stays put regardless of upstream edits.
            world_xy = self._current_end_of(line)
            if world_xy is not None:
                self.marker_positions[line] = world_xy
            verb = "已标记"
        self._render()
        self._set_status(f"{verb}第 {line} 行端点")

    def _current_end_of(self, line: int) -> tuple[float, float] | None:
        """Return the END waypoint of `line`'s segment right now, or None."""
        seg_idx = self.highlight.line_to_segment_idx.get(line)
        if seg_idx is None:
            return None
        return self.segments[seg_idx].waypoints[-1]

    def _delete_segment(self, line: int) -> None:
        """Splice a single command line out of Auto.cpp and reload.

        Distinct from `_hide_line` (which only does a visual skip). This
        permanently edits the file — caller must have confirmed via
        `messagebox.askyesno` first.
        """
        cmd = next((c for c in self.commands if c.line == line), None)
        if cmd is None or self.current_path is None:
            return
        path = Path(self.current_path)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            self._set_status(f"读取失败: {exc}")
            return
        # 1-based → 0-based line index.
        idx = line - 1
        lines = raw.split("\n")
        if idx < 0 or idx >= len(lines):
            self._set_status(f"行号 {line} 越界")
            return
        deleted_text = lines[idx]
        del lines[idx]
        # Also drop any comment-only lines that immediately follow the
        # deleted command (the user's usual convention is `// foo` right
        # after `chassis.drive_distance(...);`).
        while idx < len(lines) and lines[idx].lstrip().startswith("//"):
            del lines[idx]
        try:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self._set_status(f"写入失败: {exc}")
            return
        self._load(str(path))
        self._set_status(
            f"已删除第 {line} 行 ({deleted_text.strip()[:50]}),可通过 git 恢复")

    def _toggle_drawing(self) -> None:
        if self._drawing_mode:
            self._cancel_drawing()
        else:
            self._set_drawing_mode(True)

    def _set_drawing_mode(self, on: bool) -> None:
        self._drawing_mode = on
        if on:
            self.canvas.config(cursor="crosshair")
            self._draw_btn.configure(bootstyle="info")
            self._set_status(
                "绘制模式:右键一个端点开始画,ESC 完成,右键任意端点替换起点")
        else:
            self.canvas.config(cursor="")
            self._draw_btn.configure(bootstyle="info-outline")
            self._drawing_start_line = None
            self._drawing_start_world = None
            self._drawing_polyline = []

    def _cancel_drawing(self) -> None:
        self._set_drawing_mode(False)
        self._set_status("已退出绘制模式")

    def _on_escape(self) -> None:
        # ESC has dual semantics: when a polyline is in progress, finalize
        # it; otherwise just exit drawing mode.
        if self._drawing_mode:
            if self._drawing_polyline:
                self._finalize_drawing()
            else:
                self._cancel_drawing()

    def _on_delete_key(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        row = sel[0]
        if row >= len(self.commands):
            return
        self._prompt_and_delete(self.commands[row].line)

    def _prompt_and_delete(self, line: int) -> None:
        cmd = next((c for c in self.commands if c.line == line), None)
        if cmd is None:
            return
        body = cmd.raw_with_indent.rstrip()
        ok = messagebox.askyesno(
            "确认删除",
            f"将删除第 {line} 行:\n\n{body}\n\n"
            "此操作不可撤销(可通过 git 或备份恢复)。\n继续?",
            parent=self.root)
        if ok:
            self._delete_segment(line)

    def _on_canvas_right_click(self, event: Any) -> None:
        if self._drawing_mode:
            self._on_drawing_right_click(event)
            return
        # Hit-test: prefer marker hits over segment hits, so users can
        # remove a marker even if it's drawn on top of a trajectory line.
        items = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        for item in items:
            tags = self.canvas.gettags(item)
            end_tag = next((t for t in tags if t.startswith("endpoint_")), None)
            if end_tag:
                line = int(end_tag.split("_")[1])
                menu = tk.Menu(self.root, tearoff=0)
                menu.add_command(label="取消标记",
                                 command=lambda ln=line: (
                                     self.marked_lines.discard(ln),
                                     self._render(),
                                     self._set_status(f"取消标记第 {line} 行")))
                menu.tk_popup(event.x_root, event.y_root)
                return

    def _on_drawing_click(self, event: Any) -> None:
        """In drawing mode: left-click adds the next polyline point.

        If no start is set yet, the click is silently ignored — start is
        chosen via right-click only (matches the plan: "右键一个端点开始画").
        """
        if self._drawing_start_world is None:
            return
        m_w = self._preview_to_world(event.x, event.y)
        snapped = self._snap_to_marked(m_w)
        self._drawing_polyline.append(snapped)
        self._render()

    def _on_drawing_right_click(self, event: Any) -> None:
        """In drawing mode: right-click picks a segment's END as start."""
        items = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        # Prefer endpoint marker hits (most precise — the user is "clicking
        # at a key point"). Fall back to any segment tag if no marker is
        # at the cursor.
        target_line: int | None = None
        for item in items:
            tags = self.canvas.gettags(item)
            end_tag = next((t for t in tags if t.startswith("endpoint_")), None)
            if end_tag:
                target_line = int(end_tag.split("_")[1])
                break
        if target_line is None:
            for item in items:
                tags = self.canvas.gettags(item)
                seg_tag = next((t for t in tags if t.startswith("seg_")), None)
                if seg_tag:
                    target_line = int(seg_tag.split("_")[1])
                    break
        if target_line is None:
            return
        seg_idx = self.highlight.line_to_segment_idx.get(target_line)
        if seg_idx is None:
            return
        end_xy = self.segments[seg_idx].waypoints[-1]
        self._start_drawing_from(target_line, end_xy)

    def _start_drawing_from(self, line: int, world_xy: tuple[float, float]) -> None:
        """Set (or reset) the polyline's start point and clear in-progress points."""
        self._drawing_start_line = line
        self._drawing_start_world = world_xy
        self._drawing_polyline = []
        self._render()
        self._set_status(
            f"起点:第 {line} 行 ({world_xy[0]:+.1f}, {world_xy[1]:+.1f}) "
            "— 左键加点(近标记自动吸附),ESC 完成")

    def _finalize_drawing(self) -> None:
        """Compile the polyline into C++ and splice it after the start line."""
        if (self._drawing_start_line is None
                or self._drawing_start_world is None
                or not self._drawing_polyline):
            self._cancel_drawing()
            return
        # Recover the simulator's heading at the start moment so the
        # first turn delta is correct (relative to robot's actual heading
        # at that point in the trajectory).
        seg_idx = self.highlight.line_to_segment_idx.get(
            self._drawing_start_line)
        if seg_idx is None:
            heading = 0.0
        else:
            seg = self.segments[seg_idx]
            # TURN: arrow_heading = target (post-turn heading).
            # DRIVE: arrow_heading is None → use entry_heading (heading
            # while driving).
            heading = seg.arrow_heading if seg.arrow_heading is not None else seg.entry_heading
            # Last-resort fallback: derive from displacement.
            if heading == 0.0 and len(seg.waypoints) >= 2:
                (x0, y0), (x1, y1) = seg.waypoints[:2]
                heading = drawing.heading_from_vector(x1 - x0, y1 - y0)
        cmds = drawing.polyline_to_commands(
            self._drawing_start_world, self._drawing_polyline, heading)
        if not cmds:
            self._set_status("没有可生成的命令(距离都太短),已退出绘制模式")
            self._cancel_drawing()
            return
        new_lines = drawing.render_cpp_lines(cmds)
        path = Path(self.current_path)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            self._set_status(f"读取失败: {exc}")
            return
        lines = raw.split("\n")
        insert_at = self._drawing_start_line  # append after start line (1-based)
        lines = lines[:insert_at] + new_lines + lines[insert_at:]
        try:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self._set_status(f"写入失败: {exc}")
            return
        n = len(new_lines)
        self._set_drawing_mode(False)
        self._load(str(path))
        self._set_status(
            f"已插入 {n} 行命令到第 {self._drawing_start_line} 行之后")

    def _preview_to_world(self, cx: float, cy: float) -> tuple[float, float]:
        """Inverse of `_to_canvas` — used for click → world coords."""
        θ = math.radians(self.view_rotation)
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        rx = (cx - w / 2 - self.view_offset_x) / self._scale
        ry = -(cy - h / 2 - self.view_offset_y) / self._scale
        # rotate by -θ
        x = rx * math.cos(-θ) + ry * math.sin(-θ)
        y = -rx * math.sin(-θ) + ry * math.cos(-θ)
        return (x, y)

    def _snap_to_marked(self, world_xy: tuple[float, float]) -> tuple[float, float]:
        """If click is within SNAP_RADIUS_IN of a marked END, snap to it.

        Uses frozen `marker_positions` — so drawing-mode snaps land on the
        same world point that the user sees the marker at, even if the
        segment that originally produced it has since moved.
        """
        if not self.marked_lines:
            return world_xy
        x, y = world_xy
        best_line: int | None = None
        best_d2 = SNAP_RADIUS_IN * SNAP_RADIUS_IN
        best_xy: tuple[float, float] | None = None
        for line, (mx, my) in self.marker_positions.items():
            d2 = (mx - x) ** 2 + (my - y) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_line = line
                best_xy = (mx, my)
        if best_line is not None and best_xy is not None:
            self._set_status(f"↪ 已吸附到第 {best_line} 行端点")
            return best_xy
        return world_xy

    def _draw_polyline_overlay(self) -> None:
        """Dashed polyline + vertex dots for the in-progress draw."""
        pts_world: list[tuple[float, float]] = (
            [self._drawing_start_world] if self._drawing_start_world else []
        ) + list(self._drawing_polyline)
        # Segments.
        for (ax, ay), (bx, by) in zip(pts_world, pts_world[1:]):
            cxa, cya = self._to_canvas(ax, ay)
            cxb, cyb = self._to_canvas(bx, by)
            self.canvas.create_line(
                cxa, cya, cxb, cyb,
                fill=DRAW_COLOR, width=2, dash=(4, 4),
                tags=("drawing",))
        # Vertices (skip the start — already shown as the picked segment's
        # own END marker).
        for (vx, vy) in self._drawing_polyline:
            cvx, cvy = self._to_canvas(vx, vy)
            r = DRAW_POINT_RADIUS
            self.canvas.create_oval(
                cvx - r, cvy - r, cvx + r, cvy + r,
                fill=DRAW_COLOR, outline="white", width=1,
                tags=("drawing",))

# ---- playback ----

    def _toggle_play(self) -> None:
        self._is_playing = not self._is_playing
        self.play_btn.configure(text="⏸" if self._is_playing else "▶")
        if self._is_playing:
            self._tick()

    def _tick(self) -> None:
        if not self._is_playing:
            return
        dt = 0.033  # ~30 fps
        self._t_current = min(self.simulator.total_duration,
                              self._t_current + dt)
        self._sync_slider_and_highlight()
        if self._t_current >= self.simulator.total_duration:
            self._is_playing = False
            self.play_btn.configure(text="▶")
            return
        self._play_after_id = self.root.after(33, self._tick)

    def _reset_playback(self) -> None:
        self._is_playing = False
        self.play_btn.configure(text="▶")
        if self._play_after_id is not None:
            self.root.after_cancel(self._play_after_id)
            self._play_after_id = None
        self._t_current = 0.0
        self._sync_slider_and_highlight()

    def _on_slider_change(self, value: str) -> str:
        # Programmatic configure() doesn't fire this; user drags only.
        if self._is_playing:
            return ""
        try:
            self._t_current = float(value)
        except ValueError:
            return ""
        self._sync_slider_and_highlight()
        return ""

    def _sync_slider_and_highlight(self) -> None:
        total = self.simulator.total_duration
        # Slider value clamping (Tk Scale accepts the float directly).
        self.t_slider.configure(value=self._t_current, to=max(total, 1e-9))
        self.time_label.configure(
            text=f"{self._t_current:.2f} / {total:.2f}s")
        line = self.simulator.line_at(self._t_current)
        if line is not None:
            self._highlighted_lines = {line}
        else:
            self._highlighted_lines.clear()
        self._render()

    def _set_status(self, msg: str) -> None:
        self.status.configure(text=msg)

    def _update_pose_status(self) -> None:
        x, y, h = self.simulator.x, self.simulator.y, self.simulator.heading
        self._set_status(
            f"pose  x={x:+.1f}  y={y:+.1f}  h={h:+.1f}°   |   "
            f"{len(self.commands)} commands, {len(self.segments)} segments")


class SettingsDialog:
    def __init__(self, parent: tk.Misc, settings: Settings,
                 on_apply: Any) -> None:
        self._on_apply = on_apply
        self._settings = settings
        self._vars: dict[str, tk.StringVar] = {}

        self.win = ttkb.Toplevel(parent)
        self.win.title("设置")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()
        # Make the window visible immediately so winfo_reqheight reflects the
        # real layout (withdrawn windows report 1x1).
        self.win.deiconify()

        frame = ttkb.Frame(self.win, padding=15)
        frame.pack(fill=BOTH, expand=True)

        rows = [
            ("initial_x",          "起点 X (in)",          settings.initial_x),
            ("initial_y",          "起点 Y (in)",          settings.initial_y),
            ("initial_heading",    "起点航向 (°)",         settings.initial_heading),
            ("pixels_per_inch",    "缩放 (px/in, 0=自动)", settings.pixels_per_inch),
            ("track_width",        "底盘轮距 (in)",        settings.track_width),
            ("wheel_diameter",     "轮胎直径 (in)",        settings.wheel_diameter),
            ("linear_speed_in_s",  "直行速度 (in/s)",      settings.linear_speed_in_s),
            ("angular_speed_deg_s","转向速度 (°/s)",       settings.angular_speed_deg_s),
            ("stop_hold_seconds",  "STOP 停留 (s)",        settings.stop_hold_seconds),
            ("hide_voltage_threshold","隐藏电压阈值 (V)",   settings.hide_voltage_threshold),
        ]
        for i, (key, label, value) in enumerate(rows):
            ttkb.Label(frame, text=label).grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=str(value))
            self._vars[key] = var
            entry = ttkb.Entry(frame, textvariable=var, width=12)
            entry.grid(row=i, column=1, sticky="e", pady=2)
            entry.bind("<Return>", lambda _e: self._apply())

        # Button bar — placed BEFORE pack, so geometry() below can measure
        # the actual height. We compute the dialog height from content
        # rather than guessing.
        btn_bar = ttkb.Frame(self.win, padding=(15, 0, 15, 15))
        btn_bar.pack(fill=X)
        ttkb.Button(btn_bar, text="默认", command=self._restore_defaults,
                    bootstyle="secondary").pack(side=LEFT)
        ttkb.Button(btn_bar, text="取消", command=self.win.destroy,
                    bootstyle="secondary").pack(side=RIGHT, padx=(5, 0))
        ttkb.Button(btn_bar, text="应用", command=self._apply,
                    bootstyle="primary").pack(side=RIGHT)

        # Force geometry calculation then auto-fit to content height so the
        # button bar is always visible regardless of theme / font metrics.
        self.win.update_idletasks()
        req_w = self.win.winfo_reqwidth()
        req_h = self.win.winfo_reqheight()
        self.win.geometry(f"{max(req_w, 360)}x{req_h}")
        # Center on parent
        self.win.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")

    def _restore_defaults(self) -> None:
        s = Settings()
        for k, v in self._vars.items():
            v.set(str(getattr(s, k)))

    def _apply(self) -> None:
        new = Settings()
        for k, var in self._vars.items():
            try:
                setattr(new, k, float(var.get()))
            except ValueError:
                setattr(new, k, getattr(self._settings, k))
        self._on_apply(new)
        self.win.destroy()