"""Data classes for parsed commands and trajectory segments."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CommandKind(Enum):
    DRIVE = "drive"
    TURN = "turn"
    TURN_LR = "turn_lr"
    STOP = "stop"


@dataclass
class ChassisCommand:
    kind: CommandKind
    line: int
    raw: str
    args: dict[str, Any] = field(default_factory=dict)
    raw_with_indent: str = ""           # full line incl. leading whitespace + trailing // comments
    arg_offsets: dict[str, tuple[int, int]] = field(default_factory=dict)
                                        # arg_name → (start, end) within raw_with_indent


@dataclass
class TrajectorySegment:
    kind: CommandKind
    line: int
    cmd_index: int
    waypoints: list[tuple[float, float]] = field(default_factory=list)
    color: str = "#3b8edb"
    width: int = 2
    arrow_heading: float | None = None
    stop_mode: str | None = None
    tag: str = ""
    t_start: float = 0.0
    t_end: float = 0.0
    entry_heading: float = 0.0