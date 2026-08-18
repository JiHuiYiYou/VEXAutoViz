"""Parser tests — no GUI required. Run with `pytest tests/` from project root."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.commands import CommandKind  # noqa: E402
from app.parser import CppParser        # noqa: E402
from app.simulator import Simulator     # noqa: E402


SAMPLE_MINIMAL = """
void Auto1() {
  chassis.drive_distance(16, 0, 6, 6, true, 1, 100, 2000);
  chassis.turn_to_angle(-90, 6, 16, 0, 800);
  chassis.turn_LR_angle(185, 0.4, 1.8, 10, 1, 0, 1200);
  chassis.drive_distance(8, chassis.get_absolute_heading(), 6, 6, true, 1, 100, 2000);
  chassis.drive_stop(coast);
  chassis.drive_stop(brake);
  chassis.drive_stop(hold);
}
"""


def test_parses_all_four_kinds():
    cmds = CppParser().parse(SAMPLE_MINIMAL)
    kinds = [c.kind for c in cmds]
    assert kinds.count(CommandKind.DRIVE) == 2
    assert kinds.count(CommandKind.TURN) == 1
    assert kinds.count(CommandKind.TURN_LR) == 1
    assert kinds.count(CommandKind.STOP) == 3


def test_drive_distance_full_args():
    cmds = CppParser().parse(SAMPLE_MINIMAL)
    drive = cmds[0]
    assert drive.kind == CommandKind.DRIVE
    assert drive.args["distance"] == 16.0
    assert drive.args["heading"] == 0.0
    assert drive.args["drive_max_voltage"] == 6.0
    assert drive.args["heading_max_voltage"] == 6.0
    assert drive.args["enable_slew"] is True or drive.args["enable_slew"] == "true"
    assert drive.args["drive_settle_error"] == 1.0
    assert drive.args["drive_settle_time"] == 100.0
    assert drive.args["drive_timeout"] == 2000.0


def test_drive_distance_default_args_padded():
    src = "chassis.drive_distance(16, 0, 6, 6);"
    cmds = CppParser().parse(src)
    assert len(cmds) == 1
    args = cmds[0].args
    assert args["distance"] == 16.0
    assert args["heading"] == 0.0
    assert args["drive_settle_error"] == 1.0     # default
    assert args["drive_settle_time"] == 0.0       # default
    assert args["drive_timeout"] == 2000.0      # default


def test_turn_to_angle_nested_heading():
    cmds = CppParser().parse(SAMPLE_MINIMAL)
    turn = cmds[3]   # the nested-get_absolute_heading one
    assert turn.kind == CommandKind.DRIVE
    assert turn.args["distance"] == 8.0
    assert turn.args["heading"] == "chassis.get_absolute_heading()"


def test_turn_lr_angle_three_gains():
    cmds = CppParser().parse(SAMPLE_MINIMAL)
    lr = next(c for c in cmds if c.kind == CommandKind.TURN_LR)
    assert lr.args["target"] == 185.0
    assert lr.args["left_gain"] == 0.4
    assert lr.args["right_gain"] == 1.8
    assert lr.args["max_voltage"] == 10.0


def test_drive_stop_three_modes():
    cmds = CppParser().parse(SAMPLE_MINIMAL)
    stops = [c for c in cmds if c.kind == CommandKind.STOP]
    modes = sorted(c.args["mode"] for c in stops)
    assert modes == ["brake", "coast", "hold"]


def test_line_numbers_correct():
    src = (
        "// line 1 comment\n"           # 1
        "void f() {\n"                  # 2
        "  chassis.drive_distance(1, 0, 6, 6);  // L4\n"   # 3 (call on line 3)
        "}\n"                           # 4
        "void g() {\n"                  # 5
        "  chassis.turn_to_angle(90);\n"  # 6
        "}\n"                           # 7
    )
    cmds = CppParser().parse(src)
    assert [c.line for c in cmds] == [3, 6]


def test_strips_block_comments():
    src = """
/*
      chassis.drive_distance(99, 0, 6, 6);  <- this should NOT be parsed
    */
    void f() { chassis.drive_distance(5, 0, 6, 6); }
"""
    cmds = CppParser().parse(src)
    assert len(cmds) == 1
    assert cmds[0].args["distance"] == 5.0


def test_strips_line_comments():
    src = """
    void f() {
      // chassis.drive_distance(99, 0, 6, 6);
      chassis.drive_distance(7, 0, 6, 6);
    }
"""
    cmds = CppParser().parse(src)
    assert len(cmds) == 1
    assert cmds[0].args["distance"] == 7.0


def test_simulator_resolves_nested_heading_expression():
    cmds = CppParser().parse("""
        chassis.turn_to_angle(90);
        chassis.drive_distance(10, chassis.get_absolute_heading(), 6, 6);
        chassis.turn_to_angle(chassis.get_absolute_heading() - 180, 6, 1, 100, 1000);
    """)
    sim = Simulator()
    segs = sim.run(cmds)
    # After turn to 90 and drive 10 at 90: x=10, y=0, h=90
    # Then turn to (90 - 180) = -90: h=-90
    assert sim.heading == -90.0
    assert abs(sim.x - 10.0) < 1e-6
    assert abs(sim.y) < 1e-6
    assert len(segs) == 3


def test_simulator_real_blue_far_trajectory():
    """Smoke test against the user's actual Auto.cpp — 22 commands expected."""
    src_path = Path(r'C:\Users\liuzhen\Documents\vexcode-projects\override\蓝远\src\Auto.cpp')
    if not src_path.exists():
        return  # skip if the user's project is not available
    cmds = CppParser().parse(src_path.read_text(encoding="utf-8"))
    assert len(cmds) == 22
    sim = Simulator()
    segs = sim.run(cmds)
    assert len(segs) == 22
    # The trajectory should stay within a 50-inch radius from start
    assert abs(sim.x) < 50
    assert abs(sim.y) < 50