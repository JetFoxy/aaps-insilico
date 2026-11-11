#!/usr/bin/env python3
"""W1 replay: real Kotlin pipeline -> determine_basal -> compare with the recorded RT."""
from __future__ import annotations

import argparse
import json
import re
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.client import Bridge, load_carbs, load_prefs, load_treatments, insulin_from_settings

ROOT = Path(__file__).resolve().parents[2]
SENS = re.compile(r"profile\.sens: ([\d.]+), sens: ([\d.]+), baseCR: ([\d.]+), effectiveCR: ([\d.]+)")
TARGET = re.compile(r"current target ([\d.]+)")
IOBTH = re.compile(r"User setting iobTH=(\d+)%")
MAXSMB = re.compile(r"profile\.maxSMBBasalMinutes: (\d+)")
MAXUAM = re.compile(r"profile\.maxUAMSMBBasalMinutes: (\d+)")
POWER = re.compile(r"Loop allows (\w+) power|Loop power level temporarily capped")
SMBRATIO = re.compile(r"SMB delivery ratio set to (?:fixed value|interpolated value|max of fixed and interpolated values) ([\d.]+)")
COB = re.compile(r"COB: ([\d.]+) / ([\d.]+)")
PARABOLA = re.compile(r"Parabola fit results were acceleration:(-?[\d.]+), correlation:([\d.]+), duration:([\d.]+)m")


TEMP_LEFT = re.compile(r"(\d+)m left and ([\d.,]+) ~ req")
TEMP_RATE = re.compile(r"temp ([\d.,]+) [~<>=]+ ?(?:req|~)")
TEMP_HIGH = re.compile(r"Replacing high temp basal of ([\d.,]+)")
TEMP_SHORTEN = re.compile(r"Shortening (\d+)m long zero temp")
TEMP_AT = re.compile(r"(\d+)m@([\d.,]+) is a lot less")
TEMP_BELOW = re.compile(r"Temp ([\d.,]+) <= current basal")


def num(text: str) -> float:
    return float(text.replace(",", "."))


def parse_cycle(cycle: dict) -> dict | None:
    text = " ".join(cycle.get("consoleError") or [])
    sens = SENS.search(text)
    if not sens:
        return None
    target = TARGET.search(text)
    iobth = IOBTH.search(text)
    maxsmb = MAXSMB.search(text)
    maxuam = MAXUAM.search(text)
    power = POWER.search(text)
    smbratio = SMBRATIO.search(text)
    cob = COB.search(text)
    parabola = PARABOLA.search(text)
    loop = "AAPS"
    if power:
        word = power.group(1)
        loop = {"minimal": "blocked", "medium": "enforced", "maximum": "fullLoop", "AAPS": "AAPS"}.get(word, "AAPS")
        if power.group(0) == "Loop power level temporarily capped":
            loop = "iobTH"
    reason = cycle.get("reason") or ""
    temp_left = TEMP_LEFT.search(reason)
    temp_rate = TEMP_RATE.search(reason)
    temp_high = TEMP_HIGH.search(reason)
    temp_shorten = TEMP_SHORTEN.search(reason)
    temp_at = TEMP_AT.search(reason)
    temp_below = TEMP_BELOW.search(reason)
    current_temp = None
    if temp_left:
        current_temp = {"duration": int(temp_left.group(1)), "rate": num(temp_left.group(2))}
    elif temp_shorten:
        current_temp = {"duration": int(temp_shorten.group(1)), "rate": 0.0}
    elif temp_at:
        current_temp = {"duration": int(temp_at.group(1)), "rate": num(temp_at.group(2))}
    elif temp_high:
        current_temp = {"duration": 10, "rate": num(temp_high.group(1))}
    elif temp_below:
        current_temp = {"duration": 10, "rate": num(temp_below.group(1))}
    elif temp_rate:
        current_temp = {"duration": 10, "rate": num(temp_rate.group(1))}
    return {
        "currentTemp": current_temp,
        "profileSens": float(sens.group(1)),
        "variableSens": float(sens.group(2)),
        "carbRatio": float(sens.group(3)),
        "target": float(target.group(1)) if target else None,
        "iobTh": int(iobth.group(1)) if iobth else None,
        "maxSmbMinutes": int(maxsmb.group(1)) if maxsmb else None,
        "maxUamMinutes": int(maxuam.group(1)) if maxuam else None,
        "loop": loop,
        "smbRatio": float(smbratio.group(1)) if smbratio else None,
        "mealCOB": float(cob.group(1)) if cob else 0.0,
        "carbs": float(cob.group(2)) if cob else 0.0,
        "microBolusAllowed": "SMB disabled (!microBolusAllowed)" not in text,
        "flatBGs": "CGM data is unchanged" in text,
        "parabola": (float(parabola.group(1)), float(parabola.group(2)), float(parabola.group(3))) if parabola else None,
    }


def quantiles(values: list[float]) -> str:
    if not values:
        return "n=0"
    ordered = sorted(values)
    def q(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * p))]
    return (f"n={len(ordered)} median={statistics.median(ordered):.3f} "
            f"p90={q(0.9):.3f} p99={q(0.99):.3f} max={ordered[-1]:.3f}")


def temp_target_at(treatments: list[dict], now: int) -> dict | None:
    active = None
    for row in treatments:
        if row["type"] != "Temporary Target":
            continue
        if row["ts"] <= now and row["ts"] + int(row.get("duration") or 0) * 60_000 > now:
            active = row
    return active


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--from-time", default=None)
    ap.add_argument("--version", default=None)
    ap.add_argument("--uploader", default="libre2")
    ap.add_argument("--cgm", choices=("ns", "db"), default="ns", help="CGM source: Nightscout entries or xDrip BgReadings")
    ap.add_argument("--raw-fit", action="store_true", default=True)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--cob-zero", action="store_true", help="only cycles with COB = 0")
    ap.add_argument("--temp-source", choices=("treatments", "suggested", "enacted", "reason", "devicestatus"), default="reason")
    args = ap.parse_args()

    golden = ROOT / "data/golden"
    cycles = [json.loads(line) for line in (golden / "cycles.jsonl").read_text().splitlines() if line.strip()]
    cycle_treatments = [json.loads(line) for line in (golden / "treatments.jsonl").read_text().splitlines() if line.strip()]
    cgm = []
    for line in (golden / "cgm.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("source", "xdrip") != "xdrip":
            continue
        if args.uploader not in (None, "merged60") and row.get("uploader") != args.uploader:
            continue
        cgm.append((row["ts"], row["sgv"]))
    cgm.sort()
    if args.cgm == "db":
        cgm = [ (json.loads(line)["ts"], json.loads(line)["sgv"]) for line in (golden / "cgm_db.jsonl").read_text().splitlines() if line.strip() ]
    if args.uploader == "merged60" and args.cgm == "ns":
        merged = []
        for ts, sgv in cgm:
            if merged and ts - merged[-1][0] < 60_000:
                continue
            merged.append((ts, sgv))
        cgm = merged
    boluses, temps = load_treatments(golden / "treatments.jsonl")
    carbs = load_carbs(golden / "treatments.jsonl")
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    if args.raw_fit:
        prefs["always_promote_advanced_filtering"] = True
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")

    from datetime import datetime, timezone
    from_time = None
    if args.from_time:
        from_time = int(datetime.fromisoformat(args.from_time).replace(tzinfo=timezone.utc).timestamp() * 1000)

    candidates = []
    for cycle in cycles:
        if cycle["algo"] != "AUTO_ISF":
            continue
        parsed = parse_cycle(cycle)
        if parsed is None or cycle["bg"] is None:
            continue
        if args.version and cycle.get("appVersion") != args.version:
            continue
        if from_time and cycle["ts"] < from_time:
            continue
        if args.cob_zero and (cycle.get("cob") or 0) > 0:
            continue
        cycle["_p"] = parsed
        candidates.append(cycle)
        if len(candidates) >= args.limit:
            break

    suggested_temps = sorted(
        (c["ts"], float(c["rateSuggested"]), int(c["durationSuggested"]))
        for c in cycles
        if c.get("rateSuggested") is not None and c.get("durationSuggested") is not None and c["rateSuggested"] >= 0
    )
    suggested_ts = [row[0] for row in suggested_temps]

    from zoneinfo import ZoneInfo
    msk = ZoneInfo("Europe/Moscow")
    for cycle in cycles:
        raw_start = cycle.get("tempStart")
        if raw_start:
            from datetime import datetime as _dt
            parsed_start = None
            for fmt in ("%d.%m.%Y %H:%M", "%m/%d/%y %H:%M", "%m/%d/%Y %H:%M", "%d.%m.%y %H:%M"):
                try:
                    parsed_start = int(_dt.strptime(raw_start, fmt).replace(tzinfo=msk).timestamp() * 1000)
                    break
                except ValueError:
                    continue
            cycle["tempStartMs"] = parsed_start
        else:
            cycle["tempStartMs"] = None

    enacted_segments = sorted(
        (c["ts"], float(c["rate"]), int(c["duration"]))
        for c in cycles
        if c.get("rate") not in (None, -1) and (c.get("duration") or 0) > 0
    )
    enacted_ts = [row[0] for row in enacted_segments]

    def enacted_temp(now: int) -> dict:
        import bisect as _bisect
        index = _bisect.bisect_right(enacted_ts, now) - 1
        while index >= 0:
            ts, rate, duration = enacted_segments[index]
            elapsed = (now - ts) / 60_000.0
            if elapsed < duration:
                return {"duration": int(round(duration - elapsed)), "rate": rate, "minutesrunning": int(round(elapsed))}
            index -= 1
        return {"duration": 0, "rate": 0.0, "minutesrunning": 0}

    def app_temp(cycle: dict, now: int) -> dict:
        start = cycle.get("tempStartMs")
        rate = cycle.get("tempRate")
        if not start or rate is None:
            return {"duration": 0, "rate": 0.0, "minutesrunning": 0}
        duration = 30
        for ts, cand_rate, cand_duration in suggested_temps:
            if abs(ts - start) <= 180_000:
                duration = cand_duration
                break
        elapsed = (now - start) / 60_000.0
        if elapsed >= duration or elapsed < 0:
            return {"duration": 0, "rate": 0.0, "minutesrunning": 0}
        return {"duration": int(round(duration - elapsed)), "rate": float(rate), "minutesrunning": int(round(elapsed))}

    def running_temp(now: int) -> dict:
        import bisect as _bisect
        index = _bisect.bisect_right(suggested_ts, now) - 1
        while index >= 0:
            ts, rate, duration = suggested_temps[index]
            elapsed = (now - ts) / 60_000.0
            if elapsed < duration:
                return {"duration": int(round(duration - elapsed)), "rate": rate, "minutesrunning": int(round(elapsed))}
            index -= 1
        return {"duration": 0, "rate": 0.0, "minutesrunning": 0}

    last_bolus = max((b["ts"] for b in boluses), default=0)
    carb_rows = [t for t in cycle_treatments if t["type"] in ("Meal Bolus", "Carb Correction")]
    last_carb = max((t["ts"] for t in carb_rows), default=0)

    rate_matches = duration_matches = smb_matches = 0
    insulin_errors, eventual_errors, pred_errors = [], [], []
    evaluated = 0
    fallbacks = 0
    outliers = [0, 0]
    rate_null_extra = rate_missing = rate_different = 0
    rate_diffs: list[float] = []
    rate_close = 0
    duration_close = 0
    both_null = 0
    insulin_close = 0
    pred_within_1 = pred_within_5 = pred_total = 0
    cob_errors: list[float] = []
    uam_errors: list[float] = []

    with Bridge() as bridge:
        bridge.request({
            "cmd": "init",
            "prefs": prefs,
            "profiles": [],
            "insulin": insulin,
            "treatments": boluses + temps + carbs,
            "basal": [[c["ts"], c["baseBasal"]] for c in cycles if c.get("baseBasal") is not None],
            "timezone": "Europe/Moscow",
        })
        for cycle in candidates:
            now = cycle["ts"]
            parsed = cycle["_p"]
            window = [[ts, sgv] for ts, sgv in cgm if now - 34 * 3600_000 <= ts <= now + 120_000]
            glucose = bridge.request({"cmd": "glucose", "now": now, "readings": window, "sourceSensor": "Libre2"})["glucose"]
            if glucose is None:
                continue
            iob = bridge.request({
                "cmd": "iob", "now": now, "autosens": cycle.get("sensRatio") or 1.0,
                "exerciseMode": False, "halfBasalExerciseTarget": 160, "isTempTarget": False,
            })["iob"]

            tt = temp_target_at(cycle_treatments, now)
            target = parsed["target"]
            if target is None:
                reason = cycle.get("reason") or ""
                m = re.search(r"Target: ([\d.,]+)", reason)
                target = float(m.group(1).rstrip(".,").replace(",", ".")) if m else 5.6
            target_low = target_high = target * 18.0
            temptarget_set = False
            if tt is not None:
                temptarget_set = True
                target_low = float(tt.get("targetBottom") or target_high)
                target_high = float(tt.get("targetTop") or target_low)

            auto = bridge.request({
                "cmd": "autoisf", "now": now, "readings": window, "sourceSensor": "Libre2",
                "isf": parsed["profileSens"], "carbRatio": parsed["carbRatio"],
                "target": target * 18.0, "targetLow": target_low, "targetHigh": target_high,
                "currentBasal": cycle.get("baseBasal") or 0.0,
            })

            if args.temp_source == "enacted":
                current_temp = enacted_temp(now)
            elif args.temp_source == "devicestatus":
                current_temp = app_temp(cycle, now)
            elif args.temp_source == "reason":
                current_temp = dict(parsed["currentTemp"], minutesrunning=0) if parsed["currentTemp"] is not None else {"duration": 0, "rate": 0.0, "minutesrunning": 0}
            elif args.temp_source == "suggested":
                current_temp = running_temp(now)
            else:
                current_temp = {"duration": 0, "rate": 0.0, "minutesrunning": 0}
            if False:
                active_temp = None
                for row in temps:
                    if row["ts"] <= now < row["ts"] + row["duration"]:
                        active_temp = row
                if active_temp is not None:
                    elapsed = (now - active_temp["ts"]) / 60_000.0
                    remaining = max(0.0, active_temp["duration"] / 60_000.0 - elapsed)
                    current_temp = {"duration": int(round(remaining)), "rate": float(active_temp["rate"]),
                                    "minutesrunning": int(round(elapsed))}

            variable_sens = auto.get("sens")
            if variable_sens is None or not math.isfinite(variable_sens):
                variable_sens = parsed["variableSens"]
                fallbacks += 1
            meal = bridge.request({
                "cmd": "meal", "now": now, "readings": window, "sourceSensor": "Libre2",
                "isf": parsed["profileSens"], "carbRatio": parsed["carbRatio"],
                "target": target * 18.0, "targetLow": target_low, "targetHigh": target_high,
                "currentBasal": cycle.get("baseBasal") or 0.0,
            })
            profile = {
                "sens": parsed["profileSens"],
                "variable_sens": variable_sens,
                "carb_ratio": parsed["carbRatio"],
                "target_bg": (target_low + target_high) / 2.0,
                "min_bg": target_low,
                "max_bg": target_high,
                "current_basal": cycle.get("baseBasal") or 0.0,
                "temptargetSet": temptarget_set,
                "enable_autoISF": True,
                "out_units": "mmol/L",
                "maxSMBBasalMinutes": parsed["maxSmbMinutes"] or 30,
                "maxUAMSMBBasalMinutes": parsed["maxUamMinutes"] or 30,
                "iob_threshold_percent": parsed["iobTh"] or 100,
            }
            response = bridge.request({
                "cmd": "step",
                "input": {
                    "now": now,
                    "glucoseStatus": glucose,
                    "currentTemp": current_temp,
                    "iob": iob,
                    "autosens": {"ratio": cycle.get("sensRatio") or 1.0},
                    "meal": {
                        "carbs": meal["carbs"],
                        "mealCOB": meal["mealCOB"],
                        "slopeFromMaxDeviation": meal["slopeFromMaxDeviation"],
                        "slopeFromMinDeviation": meal["slopeFromMinDeviation"],
                        "usedMinCarbsImpact": meal["usedMinCarbsImpact"],
                        "lastBolusTime": meal["lastBolusTime"],
                        "lastCarbTime": meal["lastCarbTime"],
                    },
                    "profile": profile,
                    "microBolusAllowed": parsed["microBolusAllowed"],
                    "flatBGs": parsed["flatBGs"],
                    "autoIsfMode": True,
                    "loopWantedSmb": parsed["loop"],
                    "smbRatio": parsed["smbRatio"] or 0.5,
                    "smbMaxRangeExtension": 1.0,
                    "iobThresholdPercent": parsed["iobTh"] or 100,
                    "profilePercentage": 100,
                    "activityConsoleLog": "",
                },
            })["rt"]

            evaluated += 1
            recorded_rate = cycle.get("rateSuggested")
            replayed_rate = response.get("rate")
            if replayed_rate == recorded_rate:
                rate_matches += 1
                if recorded_rate is None:
                    both_null += 1
            elif recorded_rate is None and replayed_rate is not None:
                rate_null_extra += 1
                if args.verbose and rate_null_extra <= 12:
                    print(json.dumps({"nullcase_ts": now, "currentTemp": current_temp, "ours_rate": replayed_rate,
                                      "app_reason": (cycle.get("reason") or "")[-150:],
                                      "ours_reason": (response.get("reason") or "")[-150:],
                                      "zt_min": min((response.get("predBGs") or {}).get("ZT") or [0]),
                                      "iob_min": min(i["iob"] for i in iob),
                                      "iob_max": max(i["iob"] for i in iob),
                                      "zero_min": min((i.get("iobWithZeroTemp") or {}).get("iob", 0) for i in iob),
                                      "sens": profile["variable_sens"], "carbs": parsed["carbs"]}))
            elif recorded_rate is not None and replayed_rate is None:
                rate_missing += 1
            else:
                rate_different += 1
                rate_diffs.append(abs(replayed_rate - recorded_rate))
                if abs(replayed_rate - recorded_rate) <= 0.05:
                    rate_close += 1
                if args.verbose and abs(replayed_rate - recorded_rate) > 0.15 and outliers[1] < 6:
                    outliers[1] += 1
                    print(json.dumps({"bigrate_ts": now, "recorded": recorded_rate, "replayed": replayed_rate,
                                      "req_recorded": cycle.get("insulinReq"), "req_replayed": response.get("insulinReq"),
                                      "target": target, "app": (cycle.get("reason") or "")[-90:],
                                      "ours": (response.get("reason") or "")[-90:]}))
            if response.get("duration") == cycle.get("durationSuggested"):
                duration_matches += 1
            if response.get("duration") is not None and cycle.get("durationSuggested") is not None:
                if abs(response["duration"] - cycle["durationSuggested"]) <= 5:
                    duration_close += 1
            if (response.get("units") or 0) == (cycle.get("unitsSuggested") or 0):
                smb_matches += 1
            _uam_app = re.search(r"UAM Impact: (-?[\d.]+) mg/dL per 5m; UAM Duration: ([\d.]+) hours", " ".join(cycle.get("consoleError") or []))
            _uam_ours = re.search(r"UAM Impact: (-?[\d.]+) mg/dL per 5m; UAM Duration: ([\d.]+) hours", response.get("consoleError") and " ".join(response["consoleError"]) or (response.get("reason") or ""))
            if _uam_app and _uam_ours:
                uam_errors.append(abs(float(_uam_app.group(1)) - float(_uam_ours.group(1))))
            if cycle.get("cob") is not None:
                cob_errors.append(abs(meal["mealCOB"] - cycle["cob"]))
            if response.get("insulinReq") is not None and cycle.get("insulinReq") is not None:
                delta_insulin = abs(response["insulinReq"] - cycle["insulinReq"])
                insulin_errors.append(delta_insulin)
                if delta_insulin <= 0.01:
                    insulin_close += 1
            if response.get("eventualBG") is not None and cycle.get("eventualBG") is not None:
                eventual_errors.append(abs(response["eventualBG"] - cycle["eventualBG"]))
            pred = response.get("predBGs") or {}
            recorded = cycle.get("predBGs") or {}
            for key in ("IOB", "ZT", "UAM", "COB"):
                a, b = pred.get(key), recorded.get(key)
                if a and b:
                    pred_errors.append(max(abs(x - y) for x, y in zip(a, b)))
                    for x, y in zip(a, b):
                        pred_total += 1
                        if abs(x - y) <= 1:
                            pred_within_1 += 1
                        if abs(x - y) <= 5:
                            pred_within_5 += 1

            _ev = response.get("eventualBG")
            if _ev is not None and cycle.get("eventualBG") is not None and abs(_ev - cycle["eventualBG"]) > 50 and outliers[0] < 3:
                outliers[1] += 1
                print(json.dumps({"outlier_ts": now, "bg": cycle["bg"], "recordedEB": cycle["eventualBG"],
                                  "replayedEB": _ev, "target": target, "reason": (response.get("reason") or "")[-140:]}))
            if args.verbose and evaluated <= 20:
                print(json.dumps({"ts": now, "cob_recorded": cycle.get("cob"), "cob_meal": round(meal["mealCOB"], 2),
                                  "slope_max": round(meal["slopeFromMaxDeviation"], 3), "slope_min": round(meal["slopeFromMinDeviation"], 3)}))
            if args.verbose and evaluated <= 10:
                print(json.dumps({
                    "ts": now,
                    "recorded": {"rate": cycle.get("rateSuggested"), "duration": cycle.get("durationSuggested"), "units": cycle.get("unitsSuggested"), "insulinReq": cycle.get("insulinReq")},
                    "replayed": {"rate": response.get("rate"), "duration": response.get("duration"), "units": response.get("units"), "insulinReq": response.get("insulinReq"), "reason": (response.get("reason") or "")[-160:]},
                }))



    print(f"cycles={evaluated} autoisf_fallbacks={fallbacks}")
    print(f"tier A rate={rate_matches}/{evaluated} duration={duration_matches}/{evaluated} smb={smb_matches}/{evaluated}")
    print(f"rate breakdown: null-vs-set={rate_null_extra} set-vs-null={rate_missing} both-set-differ={rate_different} both-null={both_null}")
    print(f"rate within 0.05: {rate_close + rate_matches}/{evaluated}  duration within 5m: {duration_close}/{evaluated}")
    print(f"insulinReq <=0.01: {insulin_close}/{len(insulin_errors)}  predBGs <=1: {pred_within_1}/{pred_total}  <=5: {pred_within_5}/{pred_total}")
    if rate_diffs:
        print(f"rate diff       {quantiles(rate_diffs)}")
    print(f"mealCOB     {quantiles(cob_errors)}")
    print(f"UAM impact  {quantiles(uam_errors)}")
    print(f"insulinReq  {quantiles(insulin_errors)}")
    print(f"eventualBG  {quantiles(eventual_errors)}")
    print(f"predBGs max {quantiles(pred_errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
