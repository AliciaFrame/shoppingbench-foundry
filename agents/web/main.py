from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SHOPPINGBENCH_TASK", "web")

from shared.runtime import run

if __name__ == "__main__":
    run()
