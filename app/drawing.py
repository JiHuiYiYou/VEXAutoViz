"""Polyline → C++ command generation.

Pure-Python helpers that turn a sequence of world-space points into the
sequence of `chassis.drive_distance` / `chassis.turn_to_angle` lines that
the simulator can replay. Used by `app/ui.py`'s canvas-drawing tool.

Heading convention matches the simulator: 0° = north (+y), positive
clockwise. `heading_from_vector((dx, dy))` returns that heading.
"""
from __future__ import annotations

import math
from typing import Literal, TypedDict


class DriveArgs(TypedDict):
    distance: float
    heading: float


class TurnArgs(TypedDict):
    target: float


DriveCmd = tuple[Literal["drive"], DriveArgs]
TurnCmd = tuple[Literal["turn"], TurnArgs]
PolyCmd = DriveCmd | TurnCmd


def heading_from_vector(dx: float, dy: float) -> float:
    """Heading (deg, CW from north) for a 2D displacement (dx, dy).

    atan2(dx, dy) gives the CW angle from north — exactly the
    simulator's convention where end = (x + d*sin(h), y + d*cos(h)).
    """
    return math.degrees(math.atan2(dx, dy)) % 360.0


def drive_cpp_line(*, distance: float, heading: float,
                   max_v: float = 6, head_v: float = 6,
                   settle_err: float = 1, settle_time: int = 100,
                   settle_timeout: int = 2000) -> str:
    """One `chassis.drive_distance(...)` line at 2-space indent."""
    return (
        f"  chassis.drive_distance({distance:g}, {heading:.4f}, "
        f"{max_v}, {head_v}, true, {settle_err}, {settle_time}, "
        f"{settle_timeout});"
    )


def turn_cpp_line(*, target: float, max_v: float = 6,
                  settle_err: float = 16, settle_time: int = 0,
                  timeout: int = 800) -> str:
    """One `chassis.turn_to_angle(...)` line at 2-space indent."""
    return (
        f"  chassis.turn_to_angle({target:.4f}, {max_v}, "
        f"{settle_err}, {settle_time}, {timeout});"
    )


def polyline_to_commands(start_xy: tuple[float, float],
                         points: list[tuple[float, float]],
                         initial_heading: float) -> list[PolyCmd]:
    """Walk a polyline and emit (kind, args) commands.

    A new TURN is prepended whenever the segment heading differs from
    the current heading by more than `0.5°`. Distances shorter than
    0.5 in are dropped (treated as accidental double-clicks).

    Returns ordered list suitable for splicing after the user's
    selected start line.
    """
    cmds: list[PolyCmd] = []
    cx, cy = start_xy
    cur_h = initial_heading
    for nx, ny in points:
        dx = nx - cx
        dy = ny - cy
        dist = math.hypot(dx, dy)
        if dist < 0.5:
            continue
        new_h = heading_from_vector(dx, dy)
        # Wrap delta into (-180, 180] so we always turn the short way.
        delta = (new_h - cur_h + 540.0) % 360.0 - 180.0
        if abs(delta) > 0.5:
            cur_h = cur_h + delta
            cmds.append(("turn", {"target": cur_h % 360.0}))
        cmds.append(("drive", {"distance": dist, "heading": cur_h % 360.0}))
        cx, cy = nx, ny
    return cmds


def render_cpp_lines(cmds: list[PolyCmd]) -> list[str]:
    """Convert PolyCmd list back to C++ source lines (used by UI on save)."""
    out: list[str] = []
    for kind, args in cmds:
        if kind == "drive":
            out.append(drive_cpp_line(**args))
        elif kind == "turn":
            out.append(turn_cpp_line(**args))
    return out