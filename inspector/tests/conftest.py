"""Make the inspector backend importable as ``backend`` during tests."""

from __future__ import annotations

import sys
from pathlib import Path

_INSPECTOR = Path(__file__).resolve().parent.parent
if str(_INSPECTOR) not in sys.path:
    sys.path.insert(0, str(_INSPECTOR))
