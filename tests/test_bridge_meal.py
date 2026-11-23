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

NOW = 1_790_000_300_000
CARB_TS = NOW - 5 * 60_000


def test_cycle_uses_treatment_carbs_for_cob():
    from bridge.client import Bridge, insulin_from_settings, load_prefs
    root = Path(__file__).resolve().parents[1]
    prefs = load_prefs(root / "data/raw/settings.json")
    insulin = insulin_from_settings(root / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    with Bridge() as bridge:
        bridge.request({"cmd": "init", "prefs": prefs, "profiles": [], "insulin": insulin,
                        "treatments": [], "basal": [[1_790_000_000_000, 1.0]], "timezone": "Europe/Moscow"})
        response = bridge.request({
            "cmd": "cycle", "now": NOW,
            "readings": [[NOW - i * 60_000, 120.0] for i in range(30)],
            "sourceSensor": "Libre2", "autosens": 1.0,
            "exerciseMode": False, "halfBasalExerciseTarget": 160,
            "isTempTarget": False,
            "treatments": [{"kind": "carbs", "ts": CARB_TS, "amount": 30.0, "duration": 0}],
            "mealMode": "announced",
            "isf": 50.0, "carbRatio": 10.5, "target": 110.0,
            "targetLow": 110.0, "targetHigh": 110.0,
            "currentBasal": 1.0, "tdd": 40.0, "algorithm": "autoisf",
            "input": {
                "now": NOW, "TDD": 40.0,
                "currentTemp": {"duration": 0, "rate": 1.0, "minutesrunning": 0},
                "autosens": {"ratio": 1.0},
                "profile": {
                    "sens": 50.0, "variable_sens": 50.0, "carb_ratio": 10.5,
                    "target_bg": 110.0, "min_bg": 110.0, "max_bg": 110.0,
                    "current_basal": 1.0, "max_iob": 12.0, "max_basal": 4.0,
                    "SMBInterval": 3, "bolus_increment": 0.05,
                    "enable_autoISF": True, "out_units": "mg/dl",
                    "temptargetSet": False, "TDD": 40.0,
                    "insulinDivisor": 75, "use_TDD_for_predictions": True,
                },
                "microBolusAllowed": True, "flatBGs": False, "autoIsfMode": True,
                "loopWantedSmb": "AAPS", "smbRatio": 0.5,
                "smbMaxRangeExtension": 1.0, "iobThresholdPercent": 100,
                "profilePercentage": 100, "activityConsoleLog": "",
            },
        })
    meal = response["meal"]
    assert meal is not None
    assert meal["lastCarbTime"] == CARB_TS
    assert meal["mealCOB"] > 0
