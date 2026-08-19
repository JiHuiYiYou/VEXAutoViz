"""Detail panel: edit current command's args + view raw line + nearby context.

Layout (top to bottom):
  - Header: "L{n} · kind  ●" (● = dirty marker)
  - Args grid: each parsed arg as Label + Entry (Entry is readonly if the
    arg has no token in source, e.g. a default fill-in)
  - Source line: monospace display of cmd.raw_with_indent (live-reflects edits)
  - Context block: ±3 lines around the current line, each editable
  - Button bar: "保存到文件" / "撤销改动"

Editing an arg uses token-level in-place replacement: the parser records
each arg's (start, end) offset inside cmd.raw_with_indent, so changing a
value only swaps that token — leading whitespace, surrounding commas, and
trailing // comments are preserved.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Any, Callable

import ttkbootstrap as ttkb
from ttkbootstrap.constants import LEFT, RIGHT, X

from .commands import ChassisCommand


ARG_UNITS = {
    "distance": "in",
    "heading": "°",
    "drive_max_voltage": "V",
    "heading_max_voltage": "V",
    "drive_settle_error": "in",
    "drive_settle_time": "ms",
    "drive_timeout": "ms",
    "target": "°",
    "max_voltage": "V",
    "settle_error": "°",
    "settle_time": "ms",
    "timeout": "ms",
}


def _format_arg_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e16:
            return str(int(v))
        return f"{v:g}"
    if isinstance(v, int):
        return str(v)
    return str(v)


def _parse_arg_input(s: str, current: Any) -> Any:
    """Convert user input string back to a typed value matching `current`'s type.

    - bool   ← "true"/"false"/"1"/"0"
    - number ← float(s)
    - string ← s as-is (used for TURN target like `chassis.get_absolute_heading()`,
               STOP mode, etc.)
    """
    s = s.strip()
    if isinstance(current, bool):
        low = s.lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
        raise ValueError(f"expected true/false, got {s!r}")
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        return float(s)
    return s


class DetailPanel(ttkb.Frame):
    def __init__(self, parent: tk.Misc, *,
                 on_dirty: Callable[[], None],
                 on_save: Callable[[], None],
                 on_discard: Callable[[], None]) -> None:
        super().__init__(parent)
        self._on_dirty = on_dirty
        self._on_save = on_save
        self._on_discard = on_discard

        self.file_lines: list[str] = []
        self.commands_by_line: dict[int, ChassisCommand] = {}
        self.current_cmd: ChassisCommand | None = None
        self.dirty_lines: dict[int, str] = {}  # 1-based line_no -> new text
        self.path: str | None = None

        # Transient widget references (cleared on _rebuild)
        self._arg_vars: dict[str, tk.StringVar] = {}
        self._context_vars: dict[int, tk.StringVar] = {}
        self._raw_label: ttkb.Label | None = None
        self._header_label: ttkb.Label | None = None
        self._status_label: ttkb.Label | None = None
        self._args_frame: ttkb.Frame
        self._ctx_frame: ttkb.Frame

        self._build_layout()

    # ---- layout ----

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        # Row 0: header, Row 1: args label, Row 2: args grid,
        # Row 3: raw + context, Row 4: buttons.
        self.rowconfigure(3, weight=1)

        self._header_label = ttkb.Label(self, text="(未选中)",
                                        font=("Arial", 10, "bold"),
                                        bootstyle="secondary")
        self._header_label.grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))

        ttkb.Label(self, text="参数", bootstyle="secondary").grid(
            row=1, column=0, sticky="w", padx=8)

        self._args_frame = ttkb.Frame(self)
        self._args_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        self._args_frame.columnconfigure(1, weight=1)

        # Lower pane: source line + context (scrollable region)
        lower = ttkb.Frame(self)
        lower.grid(row=3, column=0, sticky="nsew", padx=8, pady=4)
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(1, weight=1)

        src_frame = ttkb.Frame(lower)
        src_frame.grid(row=0, column=0, sticky="ew")
        src_frame.columnconfigure(1, weight=1)
        ttkb.Label(src_frame, text="源码行", bootstyle="secondary").grid(
            row=0, column=0, sticky="w")
        self._raw_label = ttkb.Label(src_frame, text="", font=("Courier", 10),
                                     bootstyle="info", anchor="w")
        self._raw_label.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ctx_outer = ttkb.Frame(lower)
        ctx_outer.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        ctx_outer.columnconfigure(0, weight=1)
        ctx_outer.rowconfigure(1, weight=1)
        ttkb.Label(ctx_outer, text="上下文 (±3 行)", bootstyle="secondary").grid(
            row=0, column=0, sticky="w")
        self._ctx_frame = ttkb.Frame(ctx_outer)
        self._ctx_frame.grid(row=1, column=0, sticky="nsew")
        self._ctx_frame.columnconfigure(1, weight=1)

        btn_bar = ttkb.Frame(self)
        btn_bar.grid(row=4, column=0, sticky="ew", padx=8, pady=(4, 8))
        ttkb.Button(btn_bar, text="保存到文件", command=self._save,
                    bootstyle="primary").pack(side=RIGHT, padx=(5, 0))
        ttkb.Button(btn_bar, text="撤销改动", command=self._discard,
                    bootstyle="secondary").pack(side=RIGHT)
        self._status_label = ttkb.Label(btn_bar, text="", bootstyle="secondary")
        self._status_label.pack(side=LEFT)

    # ---- public API ----

    def set_file(self, path: str, file_lines: list[str],
                 commands_by_line: dict[int, ChassisCommand]) -> None:
        self.path = path
        self.file_lines = list(file_lines)
        self.commands_by_line = commands_by_line
        self.dirty_lines.clear()
        self._refresh_status()

    def show(self, cmd: ChassisCommand | None) -> None:
        self.current_cmd = cmd
        self._rebuild()

    # ---- rebuild on selection ----

    def _rebuild(self) -> None:
        for w in self._args_frame.winfo_children():
            w.destroy()
        for w in self._ctx_frame.winfo_children():
            w.destroy()
        self._arg_vars.clear()
        self._context_vars.clear()

        cmd = self.current_cmd
        if cmd is None:
            self._header_label.configure(text="(未选中)")
            if self._raw_label:
                self._raw_label.configure(text="")
            return

        marker = "  ●" if cmd.line in self.dirty_lines else ""
        self._header_label.configure(text=f"L{cmd.line} · {cmd.kind.value}{marker}")
        self._raw_label.configure(text=cmd.raw_with_indent)

        # Args grid
        for i, (k, v) in enumerate(cmd.args.items()):
            has_offset = k in cmd.arg_offsets
            unit = ARG_UNITS.get(k, "")
            label_text = f"{k}" + (f" ({unit})" if unit else "")
            ttkb.Label(self._args_frame, text=label_text).grid(
                row=i, column=0, sticky="w", padx=(0, 6), pady=2)
            var = tk.StringVar(value=_format_arg_value(v))
            self._arg_vars[k] = var
            entry = ttkb.Entry(self._args_frame, textvariable=var, width=14,
                               state=("normal" if has_offset else "readonly"))
            entry.grid(row=i, column=1, sticky="ew", pady=2)
            if has_offset:
                entry.bind("<FocusOut>",
                           lambda _e, kk=k: self._commit_arg(kk))
                entry.bind("<Return>",
                           lambda _e, kk=k: (self._commit_arg(kk), "break")[1])

        # Context lines ±3
        for i, line_no in enumerate(range(cmd.line - 3, cmd.line + 4)):
            actual_i = i
            row_text = f"L{line_no}:"
            ttkb.Label(self._ctx_frame, text=row_text, width=6, anchor="e",
                       bootstyle=("info" if line_no == cmd.line else "secondary")
                       ).grid(row=actual_i, column=0, sticky="e",
                              padx=(0, 4), pady=1)
            current_text = self._text_for_line(line_no)
            var = tk.StringVar(value=current_text)
            self._context_vars[line_no] = var
            entry = ttkb.Entry(self._ctx_frame, textvariable=var,
                               font=("Courier", 10),
                               bootstyle=("info" if line_no == cmd.line else ""))
            entry.grid(row=actual_i, column=1, sticky="ew", pady=1)
            entry.bind("<FocusOut>",
                       lambda _e, ln=line_no: self._commit_context(ln))
            entry.bind("<Return>",
                       lambda _e, ln=line_no: (self._commit_context(ln), "break")[1])

        self._refresh_status()

    def _text_for_line(self, line_no: int) -> str:
        if line_no in self.dirty_lines:
            return self.dirty_lines[line_no]
        if 1 <= line_no <= len(self.file_lines):
            return self.file_lines[line_no - 1]
        return ""

    # ---- arg edits ----

    def _commit_arg(self, key: str) -> None:
        cmd = self.current_cmd
        if cmd is None or key not in cmd.arg_offsets:
            return
        var = self._arg_vars.get(key)
        if var is None:
            return
        try:
            new_val = _parse_arg_input(var.get(), cmd.args[key])
        except ValueError:
            # Revert display on parse failure
            var.set(_format_arg_value(cmd.args[key]))
            return
        if new_val == cmd.args[key]:
            return
        s, e = cmd.arg_offsets[key]
        old_str = cmd.raw_with_indent[s:e]
        new_str = _format_arg_value(new_val)
        delta = len(new_str) - len(old_str)
        cmd.raw_with_indent = (cmd.raw_with_indent[:s] + new_str
                               + cmd.raw_with_indent[e:])
        # Shift subsequent offsets by delta.
        for k, (ss, ee) in cmd.arg_offsets.items():
            if ss >= e:
                cmd.arg_offsets[k] = (ss + delta, ee + delta)
        cmd.args[key] = new_val
        cmd.raw = cmd.raw_with_indent.strip()
        # Mark current line dirty
        self.dirty_lines[cmd.line] = cmd.raw_with_indent
        # Update raw display + dirty marker
        self._raw_label.configure(text=cmd.raw_with_indent)
        self._header_label.configure(
            text=f"L{cmd.line} · {cmd.kind.value}  ●")
        self._refresh_status()
        self._on_dirty()

    # ---- context edits ----

    def _commit_context(self, line_no: int) -> None:
        var = self._context_vars.get(line_no)
        if var is None:
            return
        new_text = var.get()
        clean = (self.file_lines[line_no - 1]
                 if 1 <= line_no <= len(self.file_lines) else "")
        if new_text == clean:
            self.dirty_lines.pop(line_no, None)
        else:
            self.dirty_lines[line_no] = new_text
        # Re-show header marker if current line became dirty / clean
        cmd = self.current_cmd
        if cmd is not None:
            marker = "  ●" if cmd.line in self.dirty_lines else ""
            self._header_label.configure(
                text=f"L{cmd.line} · {cmd.kind.value}{marker}")
        self._refresh_status()
        self._on_dirty()

    # ---- save / discard ----

    def _save(self) -> None:
        if not self.dirty_lines or not self.path:
            return
        new_lines = list(self.file_lines)
        for ln, text in self.dirty_lines.items():
            if 1 <= ln <= len(new_lines):
                new_lines[ln - 1] = text
        try:
            Path(self.path).write_text("\n".join(new_lines) + "\n",
                                       encoding="utf-8")
        except OSError as exc:
            self._status_label.configure(text=f"保存失败: {exc}")
            return
        self.file_lines = new_lines
        self.dirty_lines.clear()
        self._status_label.configure(text="已保存")
        self._on_save()

    def _discard(self) -> None:
        if not self.dirty_lines:
            return
        self.dirty_lines.clear()
        # Rebuild the form so Entry widgets reflect on-disk values
        self._rebuild()
        self._status_label.configure(text="已撤销")
        self._on_discard()

    def _refresh_status(self) -> None:
        n = len(self.dirty_lines)
        if n == 0:
            self._status_label.configure(text="")
        else:
            self._status_label.configure(text=f"{n} 行未保存")