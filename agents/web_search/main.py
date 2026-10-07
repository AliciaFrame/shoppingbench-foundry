from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.web_search_runtime import run

if __name__ == "__main__":
    run()
