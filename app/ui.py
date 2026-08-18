"""Main window for VEXAutoViz."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import filedialog, font
from typing import Any

import ttkbootstrap as ttkb
from ttkbootstrap.constants import (
    BOTH, BOTTOM, END, HORIZONTAL, LEFT, RIGHT, TOP, X, Y,
)

from .commands import ChassisCommand, CommandKind, TrajectorySegment
from .highlight import HighlightMap
from .parser import CppParser
from .settings import Settings, load_settings, save_settings
from .simulator import Simulator


CANVAS_BG = "#1e1e1e"
GRID_MAJOR = "#2e2e2e"
START_COLOR = "#88ff88"
START_RADIUS = 6
HIGHLIGHT_COLOR = "#ffeb3b"
HIGHLIGHT_WIDTH_BOOST = 3

COMPASS_RADIUS = 26
COMPASS_HIT_RADIUS = 38
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

        self._build_toolbar()
        self._build_panes()
        self._build_status_bar()

        self.canvas.bind("<Configure>", lambda _e: self._render())
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<MouseWheel>", self._on_canvas_wheel)

    def _make_simulator(self) -> Simulator:
        return Simulator(
            initial_x=self.settings.initial_x,
            initial_y=self.settings.initial_y,
            initial_heading=self.settings.initial_heading,
        )

    def _build_toolbar(self) -> None:
        bar = ttkb.Frame(self.root, padding=5)
        bar.pack(side=TOP, fill=X)

        ttkb.Button(bar, text="打开 Auto.cpp", command=self.open_file,
                    bootstyle="primary").pack(side=LEFT, padx=(0, 5))
        ttkb.Button(bar, text="重新加载", command=self.reload,
                    bootstyle="secondary").pack(side=LEFT, padx=(0, 5))
        ttkb.Button(bar, text="设置", command=self.open_settings,
                    bootstyle="secondary").pack(side=LEFT, padx=(0, 5))
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

        # Right: trajectory canvas
        right = ttkb.Frame(self.panes)
        self.panes.add(right, weight=3)

        self.canvas = tk.Canvas(right, background=CANVAS_BG, highlightthickness=0)
        self.canvas.pack(fill=BOTH, expand=True)

    def _build_status_bar(self) -> None:
        self.status = ttkb.Label(self.root, text="就绪", anchor="w",
                                 bootstyle="secondary", padding=(5, 2))
        self.status.pack(side=BOTTOM, fill=X)

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
        self._render()

    def open_settings(self) -> None:
        SettingsDialog(self.root, self.settings, on_apply=self._apply_settings)

    def _apply_settings(self, new_settings: Settings) -> None:
        self.settings = new_settings
        save_settings(new_settings)
        self.view_rotation = new_settings.view_rotation
        self.simulator = self._make_simulator()
        if self.commands:
            self.segments = self.simulator.run(self.commands)
            self.highlight = HighlightMap(self.commands, self.segments)
        self._highlighted_lines.clear()
        self.listbox.selection_clear(0, END)
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
        self.segments = self.simulator.run(self.commands)
        self.highlight = HighlightMap(self.commands, self.segments)
        self._highlighted_lines.clear()
        self.view_offset_x = 0.0
        self.view_offset_y = 0.0
        self._auto_scale = True

        self._populate_listbox()
        self._render()
        self._update_pose_status()

    def _populate_listbox(self) -> None:
        self.listbox.delete(0, END)
        for i, cmd in enumerate(self.commands):
            self.listbox.insert(END, _format_row(cmd))
            self.listbox.itemconfigure(i, foreground=_row_color(cmd))

    def _render(self) -> None:
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 50 or h < 50:
            return

        self._autoscale(w, h)
        self._draw_grid(w, h)

        for seg in self.segments:
            self._draw_segment(seg)
        self._draw_start_marker()
        self._draw_compass(w, h)

    def _draw_grid(self, w: int, h: int) -> None:
        step = 12
        for x_in in range(-200, 200, step):
            cx, _ = self._to_canvas(x_in, 0)
            self.canvas.create_line(cx, 0, cx, h, fill=GRID_MAJOR, tags=("grid",))
        for y_in in range(-200, 200, step):
            _, cy = self._to_canvas(0, y_in)
            self.canvas.create_line(0, cy, w, cy, fill=GRID_MAJOR, tags=("grid",))
        # axes through the origin
        ax_x0, ax_x1 = self._to_canvas(-200, 0), self._to_canvas(200, 0)
        ax_y0, ax_y1 = self._to_canvas(0, -200), self._to_canvas(0, 200)
        self.canvas.create_line(ax_x0[0], ax_x0[1], ax_x1[0], ax_x1[1],
                                fill="#444", tags=("grid",))
        self.canvas.create_line(ax_y0[0], ax_y0[1], ax_y1[0], ax_y1[1],
                                fill="#444", tags=("grid",))

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
        rx = x * math.cos(θ) - y * math.sin(θ)
        ry = x * math.sin(θ) + y * math.cos(θ)
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
                                    fill=fill, width=width,
                                    tags=("drive", seg.tag))
        elif seg.kind in (CommandKind.TURN, CommandKind.TURN_LR) and seg.arrow_heading is not None:
            self._draw_turn_wedge(seg, fill, width)
        elif seg.kind == CommandKind.STOP:
            self._draw_stop_dot(seg, fill, width)

    def _draw_stop_dot(self, seg: TrajectorySegment, fill: str, width: int) -> None:
        x, y = seg.waypoints[0]
        cx, cy = self._to_canvas(x, y)
        r = 5 if seg.line not in self._highlighted_lines else 8
        outline = "white" if seg.line not in self._highlighted_lines else HIGHLIGHT_COLOR
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                fill=fill, outline=outline, width=width,
                                tags=("stop", seg.tag))

    def _draw_turn_wedge(self, seg: TrajectorySegment, fill: str, width: int) -> None:
        x, y = seg.waypoints[0]
        h = math.radians(seg.arrow_heading)
        length = 8.0           # inches
        half_w = 0.35 * length  # half-width of arrowhead base
        # Build tip / base points in world coords (relative to anchor),
        # then route through _to_canvas so the rotation is applied consistently.
        tip_wx, tip_wy = x + length * math.sin(h),  y + length * math.cos(h)
        base1_wx, base1_wy = x + half_w * math.cos(h), y - half_w * math.sin(h)
        base2_wx, base2_wy = x - half_w * math.cos(h), y + half_w * math.sin(h)
        tip_x, tip_y = self._to_canvas(tip_wx, tip_wy)
        b1_x, b1_y = self._to_canvas(base1_wx, base1_wy)
        b2_x, b2_y = self._to_canvas(base2_wx, base2_wy)
        self.canvas.create_polygon(
            tip_x, tip_y, b1_x, b1_y, b2_x, b2_y,
            fill=fill, outline=HIGHLIGHT_COLOR if seg.line in self._highlighted_lines else "",
            width=width,
            tags=("turn", seg.tag),
        )

    def _draw_start_marker(self) -> None:
        cx, cy = self._to_canvas(0, 0)
        self.canvas.create_oval(cx - START_RADIUS, cy - START_RADIUS,
                                cx + START_RADIUS, cy + START_RADIUS,
                                fill=START_COLOR, outline="", tags=("start",))
        self.canvas.create_text(cx, cy + START_RADIUS + 12, text="起点",
                                fill=START_COLOR, font=("Arial", 9),
                                tags=("start",))

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
        # angle CW from screen-up, in degrees
        self.view_rotation = math.degrees(math.atan2(dx, -dy))
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
            return
        lines = {self.commands[i].line for i in sel}
        self._highlighted_lines = lines
        self._render()

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
        # find listbox row for this line
        for i, cmd in enumerate(self.commands):
            if cmd.line == line:
                self.listbox.selection_clear(0, END)
                self.listbox.selection_set(i)
                self.listbox.activate(i)
                self.listbox.see(i)
                self._highlighted_lines = {line}
                self._render()
                return

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
        self.win.geometry("320x280")
        self.win.transient(parent)
        self.win.grab_set()

        frame = ttkb.Frame(self.win, padding=15)
        frame.pack(fill=BOTH, expand=True)

        rows = [
            ("initial_x",          "起点 X (in)",          settings.initial_x),
            ("initial_y",          "起点 Y (in)",          settings.initial_y),
            ("initial_heading",    "起点航向 (°)",         settings.initial_heading),
            ("pixels_per_inch",    "缩放 (px/in, 0=自动)", settings.pixels_per_inch),
            ("track_width",        "底盘轮距 (in)",        settings.track_width),
            ("wheel_diameter",     "轮胎直径 (in)",        settings.wheel_diameter),
        ]
        for i, (key, label, value) in enumerate(rows):
            ttkb.Label(frame, text=label).grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=str(value))
            self._vars[key] = var
            ttkb.Entry(frame, textvariable=var, width=12).grid(
                row=i, column=1, sticky="e", pady=2)

        btn_bar = ttkb.Frame(self.win, padding=(15, 0, 15, 15))
        btn_bar.pack(fill=X)
        ttkb.Button(btn_bar, text="默认", command=self._restore_defaults,
                    bootstyle="secondary").pack(side=LEFT)
        ttkb.Button(btn_bar, text="取消", command=self.win.destroy,
                    bootstyle="secondary").pack(side=RIGHT, padx=(5, 0))
        ttkb.Button(btn_bar, text="应用", command=self._apply,
                    bootstyle="primary").pack(side=RIGHT)

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