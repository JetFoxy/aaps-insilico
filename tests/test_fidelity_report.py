"""Report wiring: compression is opt-in and sourced."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.fidelity_report import compression_for


def test_compression_for_is_off_at_zero_and_carries_the_rate():
    assert compression_for(0) is None
    config = compression_for(3.5)
    assert config is not None
    assert config.rate_per_30d == 3.5
