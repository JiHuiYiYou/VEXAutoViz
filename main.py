"""VEXAutoViz entry point."""
from __future__ import annotations

import ttkbootstrap as ttkb

from app.ui import MainWindow


def main() -> None:
    root = ttkb.Window(themename="darkly")
    MainWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()