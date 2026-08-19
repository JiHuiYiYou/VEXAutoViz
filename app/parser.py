"""C++ parser for VEX Drive-class chassis methods.

Strategy: regex-based, comment/string-aware. Not a real C++ grammar — only
recognizes the four chassis methods used in `2027-Template/chassis.cpp`.
"""
from __future__ import annotations

import re

from .commands import ChassisCommand, CommandKind


# Defaults matching chassis.h (the 5-arg overload of drive_distance at :102,
# with the remaining 3 args taking the values the chassis.cpp overloads use).
DRIVE_DEFAULTS: dict[str, Any] = {
    "drive_settle_error": 1.0,
    "drive_settle_time": 0.0,
    "drive_timeout": 2000.0,
}

DRIVE_KEYS = [
    "distance",
    "heading",
    "drive_max_voltage",
    "heading_max_voltage",
    "enable_slew",
    "drive_settle_error",
    "drive_settle_time",
    "drive_timeout",
]

TURN_KEYS = ["target", "max_voltage", "settle_error", "settle_time", "timeout"]
TURN_LR_KEYS = ["target", "left_gain", "right_gain", "max_voltage",
                "settle_error", "settle_time", "timeout"]


# Allow one level of nested parens (covers `chassis.get_absolute_heading()`).
_DRIVE_RE = re.compile(
    r'chassis\.drive_distance\s*\(((?:[^;()]|\((?:[^()]*)\))*)\)\s*;',
    re.DOTALL,
)
_TURN_RE = re.compile(
    r'chassis\.turn_to_angle\s*\(((?:[^;()]|\((?:[^()]*)\))*)\)\s*;',
    re.DOTALL,
)
_TURN_LR_RE = re.compile(
    r'chassis\.turn_LR_angle\s*\(((?:[^;()]|\((?:[^()]*)\))*)\)\s*;',
    re.DOTALL,
)
_STOP_RE = re.compile(
    r'chassis\.drive_stop\s*\(((?:[^;()]|\((?:[^()]*)\))*)\)\s*;',
    re.DOTALL,
)


def _strip_comments_and_strings(source: str) -> str:
    """Strip C++ comments and string literals while preserving line breaks."""
    def _block_repl(m: re.Match) -> str:
        return ''.join(c if c == '\n' else ' ' for c in m.group())
    source = re.sub(r'/\*.*?\*/', _block_repl, source, flags=re.DOTALL)
    source = re.sub(r'//[^\n]*', '', source)

    def _str_repl(m: re.Match) -> str:
        return ''.join(c if c == '\n' else ' ' for c in m.group())
    source = re.sub(r'"(?:[^"\\]|\\.)*"', _str_repl, source)
    return source


def _split_top_level_commas(s: str) -> list[str]:
    """Split on commas that are not inside nested () or []."""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in s:
        if ch in '([':
            depth += 1
            cur.append(ch)
        elif ch in ')]':
            depth -= 1
            cur.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        tail = ''.join(cur).strip()
        if tail:
            parts.append(tail)
    return parts


def _try_eval(s: str) -> Any:
    """Return float(s) when possible, else strip the string for expressions."""
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return s


def _line_of(source: str, offset: int) -> int:
    return source[:offset].count('\n') + 1


def _raw_line(source: str, line: int) -> str:
    return source.split('\n')[line - 1] if line >= 1 else ''


def _find_arg_offsets_in_raw(raw_line: str) -> list[tuple[int, int]]:
    """Find positions of each top-level comma-separated arg in raw_line.

    Scans the *first* `( ... )` region at column depth 0 and returns a list
    of (start, end) character offsets within `raw_line` for each positional
    arg. Whitespace adjacent to commas is included in the slice — callers
    can re-trim if they want the bare value, but preserving the surrounding
    whitespace means in-place replacement (e.g. `" 16 "` → `" 17 "`) keeps
    formatting intact.

    Returns [] if no parens found.
    """
    open_pos = raw_line.find('(')
    if open_pos < 0:
        return []
    depth = 0
    close_pos = -1
    for i in range(open_pos, len(raw_line)):
        c = raw_line[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                close_pos = i
                break
    if close_pos < 0:
        return []
    args_region = raw_line[open_pos + 1:close_pos]
    offsets: list[tuple[int, int]] = []
    depth = 0
    a_start = 0
    for i, c in enumerate(args_region):
        if c in '([':
            depth += 1
        elif c in ')]':
            depth -= 1
        elif c == ',' and depth == 0:
            offsets.append((a_start, i))
            a_start = i + 1
    offsets.append((a_start, len(args_region)))
    return [(s + open_pos + 1, e + open_pos + 1) for s, e in offsets]


def _parse_args_with_positions(
    raw_args: str,
    keys: list[str],
    defaults: dict[str, Any],
    arg_offsets_in_raw: list[tuple[int, int]],
) -> tuple[dict[str, Any], dict[str, tuple[int, int]]]:
    """Like `_parse_args` but also returns offset of each *explicitly written* arg.

    Args filled from defaults (i.e. position beyond the number of positional
    parts in source) are NOT in the returned offsets dict — there's no token
    on disk to edit.
    """
    parts = _split_top_level_commas(raw_args)
    out: dict[str, Any] = {}
    offsets_out: dict[str, tuple[int, int]] = {}
    for i, val in enumerate(parts):
        if i < len(keys):
            k = keys[i]
            out[k] = _try_eval(val)
            if i < len(arg_offsets_in_raw):
                offsets_out[k] = arg_offsets_in_raw[i]
    for k, v in defaults.items():
        out.setdefault(k, v)
    return out, offsets_out


class CppParser:
    """Parse a C++ source file for chassis-method calls."""

    def parse(self, source: str) -> list[ChassisCommand]:
        cleaned = _strip_comments_and_strings(source)
        out: list[ChassisCommand] = []

        for m in _DRIVE_RE.finditer(cleaned):
            line = _line_of(cleaned, m.start())
            raw_with_indent = _raw_line(source, line)
            offsets = _find_arg_offsets_in_raw(raw_with_indent)
            args, arg_offsets = _parse_args_with_positions(
                m.group(1), DRIVE_KEYS, DRIVE_DEFAULTS, offsets)
            out.append(ChassisCommand(
                kind=CommandKind.DRIVE,
                line=line,
                raw=raw_with_indent.strip(),
                args=args,
                raw_with_indent=raw_with_indent,
                arg_offsets=arg_offsets,
            ))

        for m in _TURN_RE.finditer(cleaned):
            line = _line_of(cleaned, m.start())
            raw_with_indent = _raw_line(source, line)
            offsets = _find_arg_offsets_in_raw(raw_with_indent)
            args, arg_offsets = _parse_args_with_positions(
                m.group(1), TURN_KEYS, {}, offsets)
            out.append(ChassisCommand(
                kind=CommandKind.TURN,
                line=line,
                raw=raw_with_indent.strip(),
                args=args,
                raw_with_indent=raw_with_indent,
                arg_offsets=arg_offsets,
            ))

        for m in _TURN_LR_RE.finditer(cleaned):
            line = _line_of(cleaned, m.start())
            raw_with_indent = _raw_line(source, line)
            offsets = _find_arg_offsets_in_raw(raw_with_indent)
            args, arg_offsets = _parse_args_with_positions(
                m.group(1), TURN_LR_KEYS, {}, offsets)
            out.append(ChassisCommand(
                kind=CommandKind.TURN_LR,
                line=line,
                raw=raw_with_indent.strip(),
                args=args,
                raw_with_indent=raw_with_indent,
                arg_offsets=arg_offsets,
            ))

        for m in _STOP_RE.finditer(cleaned):
            arg = m.group(1).strip()
            line = _line_of(cleaned, m.start())
            raw_with_indent = _raw_line(source, line)
            offsets = _find_arg_offsets_in_raw(raw_with_indent)
            arg_offsets: dict[str, tuple[int, int]] = {}
            if offsets:
                arg_offsets["mode"] = offsets[0]
            out.append(ChassisCommand(
                kind=CommandKind.STOP,
                line=line,
                raw=raw_with_indent.strip(),
                args={"mode": arg},
                raw_with_indent=raw_with_indent,
                arg_offsets=arg_offsets,
            ))

        out.sort(key=lambda c: c.line)
        return out