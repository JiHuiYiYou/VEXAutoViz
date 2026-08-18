"""Simulator: applies ChassisCommands to a 2D pose and produces segments.

Point-turn model: heading changes instantly. `chassis.get_absolute_heading()`
is resolved as the simulator's current heading (no gyro drift).

Each segment carries `t_start`/`t_end` (seconds) plus `entry_heading`, so
`pose_at(t)` can reconstruct any frame of playback.
"""
from __future__ import annotations

import math
from typing import Any

from .commands import ChassisCommand, CommandKind, TrajectorySegment
from .settings import Settings


def _drive_color(v: float) -> str:
    if v <= 6.0:
        return "#3b8edb"  # blue — normal
    if v <= 10.0:
        return "#e8923b"  # orange — overdrive
    return "#d44a3b"      # red — max


def _drive_width(v: float) -> int:
    return max(2, min(5, int(round(v / 2.0))))


def _resolve_heading(arg: Any, current: float) -> float:
    """Resolve a heading arg.

    - literal float → use it
    - `chassis.get_absolute_heading()` → current pose
    - `chassis.get_absolute_heading() ± N` → current pose ± N
    - other expression strings → current pose (best-effort)
    """
    if not isinstance(arg, str):
        return float(arg)
    s = arg.strip()
    bare = "chassis.get_absolute_heading()"
    if s == bare:
        return current
    for op in ("-", "+"):
        prefix = f"{bare} {op} "
        if s.startswith(prefix):
            try:
                return current + float(s[len(prefix):]) * (-1 if op == "-" else 1)
            except ValueError:
                return current
    return current


class Simulator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.total_duration: float = 0.0
        self._segments_cache: list[TrajectorySegment] = []
        self.reset()

    def reset(self) -> None:
        self.x = self.settings.initial_x
        self.y = self.settings.initial_y
        self.heading = self.settings.initial_heading
        self.total_duration = 0.0
        self._segments_cache = []

    def run(self, commands: list[ChassisCommand]) -> list[TrajectorySegment]:
        self.reset()
        # Default the heading to the first DRIVE's target heading so the robot
        # starts already pointing along the first move (overrides settings
        # only for this run — settings.initial_heading stays untouched).
        if commands and commands[0].kind == CommandKind.DRIVE:
            h_arg = commands[0].args.get("heading")
            if h_arg is not None:
                self.heading = _resolve_heading(h_arg, self.settings.initial_heading)
        segments: list[TrajectorySegment] = []
        t = 0.0
        for idx, cmd in enumerate(commands):
            entry_h = self.heading
            dur = self._duration_of(cmd, entry_h)
            seg = self._step(idx, cmd)
            if seg is not None:
                seg.entry_heading = entry_h
                seg.t_start = t
                seg.t_end = t + dur
                seg.tag = f"seg_{idx}"
                t += dur
                segments.append(seg)
        self.total_duration = t
        self._segments_cache = segments
        return segments

    def _duration_of(self, cmd: ChassisCommand, prev_heading: float) -> float:
        if cmd.kind == CommandKind.DRIVE:
            d = abs(float(cmd.args.get("distance", 0.0)))
            v = max(self.settings.linear_speed_in_s, 1e-6)
            return d / v
        if cmd.kind in (CommandKind.TURN, CommandKind.TURN_LR):
            target = _resolve_heading(cmd.args.get("target", prev_heading),
                                      prev_heading)
            w = max(self.settings.angular_speed_deg_s, 1e-6)
            return abs(target - prev_heading) / w
        if cmd.kind == CommandKind.STOP:
            return max(self.settings.stop_hold_seconds, 0.0)
        return 0.0

    def _step(self, idx: int, cmd: ChassisCommand) -> TrajectorySegment | None:
        match cmd.kind:
            case CommandKind.DRIVE:
                return self._drive(idx, cmd)
            case CommandKind.TURN:
                return self._turn(idx, cmd, color="#d65bce", lr=False)
            case CommandKind.TURN_LR:
                return self._turn(idx, cmd, color="#5bd0ce", lr=True)
            case CommandKind.STOP:
                return self._stop(idx, cmd)
        return None

    def _drive(self, idx: int, cmd: ChassisCommand) -> TrajectorySegment:
        d = float(cmd.args.get("distance", 0.0))
        h = _resolve_heading(cmd.args.get("heading", self.heading), self.heading)
        dv = float(cmd.args.get("drive_max_voltage", 6.0))
        rad = math.radians(h)
        start = (self.x, self.y)
        self.x += d * math.sin(rad)
        self.y += d * math.cos(rad)
        return TrajectorySegment(
            kind=CommandKind.DRIVE,
            line=cmd.line,
            cmd_index=idx,
            waypoints=[start, (self.x, self.y)],
            color=_drive_color(dv),
            width=_drive_width(dv),
        )

    def _turn(self, idx: int, cmd: ChassisCommand, color: str, lr: bool) -> TrajectorySegment:
        target = _resolve_heading(cmd.args.get("target", self.heading), self.heading)
        self.heading = target
        return TrajectorySegment(
            kind=CommandKind.TURN_LR if lr else CommandKind.TURN,
            line=cmd.line,
            cmd_index=idx,
            waypoints=[(self.x, self.y)],
            color=color,
            width=2,
            arrow_heading=target,
        )

    def _stop(self, idx: int, cmd: ChassisCommand) -> TrajectorySegment:
        mode = str(cmd.args.get("mode", "brake")).strip()
        return TrajectorySegment(
            kind=CommandKind.STOP,
            line=cmd.line,
            cmd_index=idx,
            waypoints=[(self.x, self.y)],
            color="#ff5252" if mode == "brake" else
                  "#ffd54f" if mode == "hold" else "#9e9e9e",
            width=4,
            stop_mode=mode,
        )

    def pose_at(self, t: float) -> tuple[float, float, float]:
        """Interpolated pose at time t (seconds). Falls back to start/end if out of range."""
        segs = self._segments_cache
        if not segs:
            return (self.settings.initial_x, self.settings.initial_y,
                    self.settings.initial_heading)
        if t <= 0:
            s = segs[0]
            return (s.waypoints[0][0], s.waypoints[0][1], s.entry_heading)
        if t >= self.total_duration:
            s = segs[-1]
            last_heading = (s.arrow_heading if s.arrow_heading is not None
                            else s.entry_heading)
            return (s.waypoints[-1][0], s.waypoints[-1][1], last_heading)
        for s in segs:
            if s.t_start <= t < s.t_end:
                return self._interp(s, t)
        # t exactly on the final boundary
        s = segs[-1]
        last_heading = (s.arrow_heading if s.arrow_heading is not None
                        else s.entry_heading)
        return (s.waypoints[-1][0], s.waypoints[-1][1], last_heading)

    def _interp(self, s: TrajectorySegment, t: float) -> tuple[float, float, float]:
        span = max(s.t_end - s.t_start, 1e-9)
        frac = max(0.0, min(1.0, (t - s.t_start) / span))
        if s.kind == CommandKind.DRIVE:
            (x0, y0), (x1, y1) = s.waypoints[0], s.waypoints[1]
            return (x0 + (x1 - x0) * frac,
                    y0 + (y1 - y0) * frac,
                    s.entry_heading)
        if s.kind in (CommandKind.TURN, CommandKind.TURN_LR):
            (x, y) = s.waypoints[0]
            entry = s.entry_heading
            tgt = s.arrow_heading if s.arrow_heading is not None else entry
            return (x, y, entry + (tgt - entry) * frac)
        # STOP — pose frozen at waypoint[0]
        (x, y) = s.waypoints[0]
        return (x, y, s.entry_heading)

    def line_at(self, t: float) -> int | None:
        segs = self._segments_cache
        if not segs:
            return None
        if t <= 0:
            return segs[0].line
        if t >= self.total_duration:
            return segs[-1].line
        for s in segs:
            if s.t_start <= t < s.t_end:
                return s.line
        return segs[-1].line