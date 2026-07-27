"""Put the harness root on sys.path so ``import harness...`` works under pytest.

Mirrors how the harness is run directly (cwd = benchmarks/memory_eval), so tests
and the CLI resolve the ``harness`` package identically.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
