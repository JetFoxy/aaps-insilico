"""Insulin pump: quantization, limits, reservoir, delivery delay.

The pump is the only actuator in the bench. It does not change the glucose model; it changes the
insulin input (quantized boluses, capped basal, delayed delivery), which is what an APS sees.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PumpConfig:
    bolus_step: float = 0.05
    basal_step: float = 0.01
    max_bolus: float = 10.0
    max_basal: float = 4.0
    reservoir_units: float = 300.0
    bolus_duration_min: float = 1.0
    bolus_delay_min: float = 0.0
    basal_delay_min: float = 0.0


def quantize(value: float, step: float) -> float:
    if step <= 0:
        return float(value)
    return round(round(value / step) * step, 6)


@dataclass
class Pump:
    config: PumpConfig = field(default_factory=PumpConfig)
    delivered_units: float = 0.0

    @property
    def remaining_units(self) -> float:
        return max(0.0, self.config.reservoir_units - self.delivered_units)

    def bolus(self, units: float) -> float:
        requested = min(max(0.0, units), self.config.max_bolus, self.remaining_units)
        delivered = min(quantize(requested, self.config.bolus_step), self.remaining_units)
        self.delivered_units += delivered
        return delivered

    def temp_basal(self, rate_u_per_h: float) -> float:
        requested = min(max(0.0, rate_u_per_h), self.config.max_basal)
        return quantize(requested, self.config.basal_step)

    def basal_u_per_min(self, rate_u_per_h: float) -> float:
        return self.temp_basal(rate_u_per_h) / 60.0
