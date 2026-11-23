import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

CLASSPATH = Path(os.environ.get(
    "SIM_CLASSPATH",
    Path.home() / "Projects/Dia/AndroidAPS-w1/sim-bridge/build/sim-classpath.txt",
))
pytestmark = [
    pytest.mark.bridge,
    pytest.mark.skipif(not CLASSPATH.exists(), reason="sim-bridge classpath missing"),
]


def test_autoisf_returns_factors():
    from bridge.client import Bridge, insulin_from_settings, load_prefs
    root = Path(__file__).resolve().parents[1]
    prefs = load_prefs(root / "data/raw/settings.json")
    insulin = insulin_from_settings(root / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    with Bridge() as bridge:
        bridge.request({"cmd": "init", "prefs": prefs, "profiles": [], "insulin": insulin,
                        "treatments": [], "basal": [[1_790_000_000_000, 1.0]], "timezone": "Europe/Moscow"})
        response = bridge.request({
            "cmd": "autoisf", "now": 1_790_000_300_000,
            "readings": [[1_790_000_300_000 - i * 60_000, 120.0] for i in range(30)],
            "isf": 50.0, "carbRatio": 10.0, "target": 100.0, "currentBasal": 1.0,
        })
    aiv = response["aiv"]
    assert set(aiv) == {"acceIsf", "bgIsf", "ppIsf", "duraIsf", "finalIsf", "iobThEffective"}
