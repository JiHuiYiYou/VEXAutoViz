"""User settings for VEXAutoViz.

Persisted to ~/.vexautoviz.json. Only the fields users can edit via the
settings dialog are exposed here.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


CONFIG_PATH = Path.home() / ".vexautoviz.json"


@dataclass
class Settings:
    initial_x: float = 0.0           # field-frame start X (inches)
    initial_y: float = 0.0           # field-frame start Y (inches)
    initial_heading: float = 0.0     # field-frame start heading (degrees)
    pixels_per_inch: float = 0.0     # 0 = auto-fit to trajectory extent
    track_width: float = 12.0        # inches — hook for future arc turns
    wheel_diameter: float = 3.25     # inches — for future arc/dist computations
    view_rotation: float = 0.0       # degrees — rotate the world view CCW

    def reset_to_defaults(self) -> None:
        for f in fields(self):
            object.__setattr__(self, f.name, f.default)


def load_settings() -> Settings:
    if not CONFIG_PATH.exists():
        return Settings()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    s = Settings()
    for f in fields(s):
        if f.name in data:
            setattr(s, f.name, data[f.name])
    return s


def save_settings(s: Settings) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(asdict(s), indent=2),
                               encoding="utf-8")
    except OSError:
        pass