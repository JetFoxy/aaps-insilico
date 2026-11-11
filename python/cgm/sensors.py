"""Published CGM sensor-error models (Facchinetti 2014, Vettoretti 2019).

The equations and parameters are taken from the papers, with the reference implementation in
LoopInsighT1 (MIT, https://github.com/hpeuscher/loopinsight1, src/core/sensors/Facchinetti2014.ts
and Vettoretti2019.ts) used to cross-check the transcription.

Facchinetti A, Del Favero S, Sparacino G, Castle JR, Ward WK, Cobelli C. "Modeling the Glucose
Sensor Error." IEEE Trans Biomed Eng. 2014;61(3). DOI 10.1109/TBME.2013.2284023:
  CGM(t) = a(t)·IG(t) + b(t) + v(t) + cc(t)
  a(t) = a0 + a1·d + a2·d², b(t) = b0 + b1·d + b2·d², d = days since calibration
  v, cc are AR(2) processes updated once per minute.

Vettoretti M, Del Favero S, Sparacino G, Facchinetti A. "Modeling the error of factory-calibrated
continuous glucose monitoring sensors: application to Dexcom G6 sensor data." EMBC 2019.
DOI 10.1109/EMBC.2019.8856790: same structure, parameters from Table 1/2.
"""
from __future__ import annotations

import numpy as np

FACCHINETTI_2014 = {
    "alpha_w1": 1.013, "alpha_w2": -0.2135, "sigma_2_w": 14.45,
    "alpha_cc1": 1.23, "alpha_cc2": -0.3995, "sigma_2_cc": 11.3,
    "a0": 1.1, "a1": 2e-4, "a2": 0.0,
    "b0": -14.8, "b1": 0.04, "b2": 0.0,
}

VETTORETTI_2019 = {
    "alpha_w1": 1.220, "alpha_w2": -0.331, "sigma_2_w": 3.641,
    "alpha_cc1": 1.34, "alpha_cc2": -0.492, "sigma_2_cc": 5.538,
    "a0": 1.048, "a1": -0.033, "a2": 0.002,
    "b0": -6.398, "b1": 6.179, "b2": -0.448,
}


class SensorErrorModel:
    """CGM = a(t)·IG + b(t) + v(t) + cc(t), with two AR(2) processes updated per minute."""

    def __init__(self, params: dict, seed: int = 0):
        self.p = params
        self.rng = np.random.default_rng(seed)
        self.v = [0.0, 0.0]
        self.cc = [0.0, 0.0]
        self.t_min = 0.0

    def _step(self) -> None:
        p = self.p
        w_v = float(np.sqrt(p["sigma_2_w"]) * self.rng.normal())
        v = p["alpha_w1"] * self.v[1] + p["alpha_w2"] * self.v[0] + w_v
        self.v = [self.v[1], v]
        w_cc = float(np.sqrt(p["sigma_2_cc"]) * self.rng.normal())
        cc = p["alpha_cc1"] * self.cc[1] + p["alpha_cc2"] * self.cc[0] + w_cc
        self.cc = [self.cc[1], cc]

    @property
    def noise(self) -> float:
        return self.v[1] + self.cc[1]

    def measure(self, ig: float, t_min: float) -> float:
        steps = max(1, int(round(t_min - self.t_min)))
        for _ in range(steps):
            self._step()
        self.t_min = t_min
        p = self.p
        days = t_min / 1440.0
        a = p["a0"] + p["a1"] * days + p["a2"] * days * days
        b = p["b0"] + p["b1"] * days + p["b2"] * days * days
        return a * ig + b + self.v[1] + self.cc[1]


def make_sensor(kind: str, seed: int = 0) -> SensorErrorModel | None:
    if kind == "facchinetti":
        return SensorErrorModel(FACCHINETTI_2014, seed)
    if kind == "dexcom":
        return SensorErrorModel(VETTORETTI_2019, seed)
    return None
