"""Root conftest — deliberately thin.

PRTA's root `conftest.py` is 1568 lines holding fixtures, marker gates, collection hooks,
report hooks, evidence capture, screenshot/video embedding, ffmpeg frame detection,
preflight orchestration and lock lifecycle — with interleaved ordering that only comments
explain. That is the mistake this file exists to avoid.

Everything real lives in `archy_harness.pytest_plugin`. This file only:
  * makes `lib/` importable without an install step,
  * registers the plugin explicitly (no magic autoload),
  * declares the markers so `pytest --markers` is useful.
"""

from __future__ import annotations

import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS / "lib"))

pytest_plugins = ["archy_harness.pytest_plugin"]
