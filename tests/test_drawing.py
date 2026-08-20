"""Unit tests for app/drawing.py — polyline → C++ command generation."""
from __future__ import annotations

import math
import sys
from pathlib import Path

# Allow `from app.drawing import ...` without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.drawing import (  # noqa: E402
    drive_cpp_line,
    heading_from_vector,
    polyline_to_commands,
    render_cpp_lines,
    turn_cpp_line,
)


def _approx(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) < eps


def test_heading_from_vector_basic_axes():
    assert _approx(heading_from_vector(0, 1), 0.0)       # +y → north
    assert _approx(heading_from_vector(1, 0), 90.0)      # +x → east
    assert _approx(heading_from_vector(0, -1), 180.0)    # -y → south
    assert _approx(heading_from_vector(-1, 0), 270.0)    # -x → west


def test_heading_from_vector_modular():
    # Both `dx=0,dy=0` and slightly off-axis values produce a 0..360 result.
    h = heading_from_vector(0, 0)
    assert 0.0 <= h < 360.0
    h2 = heading_from_vector(0.001, 1.0)
    assert 0.0 <= h2 < 360.0


def test_drive_cpp_line_format():
    s = drive_cpp_line(distance=24.5, heading=90.0)
    assert s.startswith("  chassis.drive_distance(")
    assert s.endswith(");")
    assert "24.5" in s
    assert "90.0000" in s
    assert "6, 6, true" in s  # default voltages


def test_drive_cpp_line_custom_voltages():
    s = drive_cpp_line(distance=10, heading=0, max_v=8, head_v=4)
    assert "8, 4" in s
    assert "10, 0.0000" in s


def test_turn_cpp_line_format():
    s = turn_cpp_line(target=-90)
    assert s.startswith("  chassis.turn_to_angle(")
    assert s.endswith(");")
    assert "-90.0000" in s
    assert "6, 16, 0, 800" in s  # defaults


def test_polyline_straight_east():
    """Already pointing east, no TURN needed, single DRIVE."""
    cmds = polyline_to_commands(
        start_xy=(0.0, 0.0),
        points=[(10.0, 0.0)],
        initial_heading=90.0)
    assert len(cmds) == 1
    kind, args = cmds[0]
    assert kind == "drive"
    assert _approx(args["distance"], 10.0)
    assert _approx(args["heading"], 90.0)


def test_polyline_right_angle_north_then_west():
    """Start pointing east, go east→north→west → drive + 2 turns + 2 drives."""
    cmds = polyline_to_commands(
        start_xy=(0.0, 0.0),
        points=[(10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        initial_heading=90.0)
    # First leg (east) needs no turn because initial_heading=90 already.
    # Subsequent legs each need a turn + drive.
    assert len(cmds) == 5
    kinds = [c[0] for c in cmds]
    assert kinds == ["drive", "turn", "drive", "turn", "drive"]
    # Leg 1: east, no turn.
    _, d0 = cmds[0]
    assert _approx(d0["distance"], 10.0)
    assert _approx(d0["heading"], 90.0)
    # Leg 2: east→north. TURN to 0, DRIVE north.
    _, t1 = cmds[1]
    assert _approx(t1["target"], 0.0)
    _, d1 = cmds[2]
    assert _approx(d1["distance"], 10.0)
    assert _approx(d1["heading"], 0.0)
    # Leg 3: north→west. TURN to 270, DRIVE west.
    _, t2 = cmds[3]
    assert _approx(t2["target"], 270.0)
    _, d2 = cmds[4]
    assert _approx(d2["distance"], 10.0)
    assert _approx(d2["heading"], 270.0)


def test_polyline_short_way_turn_picks_smaller_delta():
    """Heading delta wraps to (-180, 180] — so a +10° delta wins over
    the alternative +350°, and one TURN is emitted (target wraps to 0°)."""
    cmds = polyline_to_commands(
        start_xy=(0.0, 0.0),
        points=[(0.0, 1.0)],  # +y → heading 0
        initial_heading=350.0)
    # delta = (0 - 350 + 540) % 360 - 180 = 190 % 360 - 180 = 190 - 180 = 10
    # → one TURN to 0, then a DRIVE north.
    assert len(cmds) == 2
    assert cmds[0][0] == "turn"
    assert _approx(cmds[0][1]["target"], 0.0)
    assert cmds[1][0] == "drive"
    assert _approx(cmds[1][1]["heading"], 0.0)


def test_polyline_drops_sub_half_inch_clicks():
    cmds = polyline_to_commands(
        start_xy=(0.0, 0.0),
        points=[(0.1, 0.0)],  # 0.1 in, below the 0.5 threshold
        initial_heading=0.0)
    assert cmds == []


def test_polyline_keeps_long_segment_even_when_no_heading_change():
    cmds = polyline_to_commands(
        start_xy=(0.0, 0.0),
        points=[(0.0, 24.0)],  # straight north, 24 in
        initial_heading=0.0)
    assert len(cmds) == 1
    assert cmds[0][0] == "drive"
    assert _approx(cmds[0][1]["distance"], 24.0)
    assert _approx(cmds[0][1]["heading"], 0.0)


def test_polyline_skips_turn_when_delta_below_threshold():
    """0.4° delta is below the 0.5° cutoff → no TURN."""
    # dx=cos(0.4°), dy=sin(0.4°) — heading ≈ 0.4°
    rad = math.radians(0.4)
    pt = (math.sin(rad), math.cos(rad))  # ≈ (0.007, 0.9998)
    cmds = polyline_to_commands(
        start_xy=(0.0, 0.0),
        points=[pt],
        initial_heading=0.0)
    assert len(cmds) == 1
    assert cmds[0][0] == "drive"


def test_render_cpp_lines_round_trip():
    cmds = polyline_to_commands(
        start_xy=(0.0, 0.0),
        points=[(10.0, 0.0), (10.0, 10.0)],
        initial_heading=0.0)
    lines = render_cpp_lines(cmds)
    assert len(lines) == len(cmds)
    # First a TURN, then a DRIVE, then a TURN, then a DRIVE.
    assert lines[0].startswith("  chassis.turn_to_angle(")
    assert lines[1].startswith("  chassis.drive_distance(")
    assert lines[2].startswith("  chassis.turn_to_angle(")
    assert lines[3].startswith("  chassis.drive_distance(")
    # All lines have the expected 2-space indent.
    for ln in lines:
        assert ln.startswith("  ")
        assert ln.endswith(";")