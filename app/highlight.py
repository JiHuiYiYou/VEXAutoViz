"""Bidirectional line ↔ segment lookup for click highlighting.

MVP: 1:1 mapping (each command produces exactly one segment).
"""
from __future__ import annotations

from .commands import ChassisCommand, TrajectorySegment


class HighlightMap:
    def __init__(self, commands: list[ChassisCommand],
                 segments: list[TrajectorySegment]) -> None:
        self.line_to_segment_idx: dict[int, int] = {}
        self.segment_idx_to_line: dict[int, int] = {}
        for i, seg in enumerate(segments):
            self.line_to_segment_idx[seg.line] = i
            self.segment_idx_to_line[i] = seg.line

    def segments_for_line(self, line: int) -> list[int]:
        if line in self.line_to_segment_idx:
            return [self.line_to_segment_idx[line]]
        return []

    def line_for_segment(self, seg_idx: int) -> int | None:
        return self.segment_idx_to_line.get(seg_idx)