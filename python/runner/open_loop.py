"""Open loop: the pump drives the core without any AAPS decision.

This is the M6 control case: with quantization and delay disabled the trace must equal the M1
simulation, and with them enabled it must differ only in the insulin input.
"""
from __future__ import annotations

from dataclasses import dataclass

from pump.pump import Pump, PumpConfig
from simcore.model import Bolus, Meal, Schedule, Trace, simulate
from simcore.params import Subject


@dataclass
class OpenLoopResult:
    trace: Trace
    pump: Pump
    schedule: Schedule


def run_open_loop(
    subject: Subject,
    basal_u_per_h: float,
    meals: list[Meal],
    boluses: list[tuple[float, float]],
    t_end: float = 24 * 60,
    dt: float = 1.0,
    config: PumpConfig | None = None,
    smooth: bool = True,
) -> OpenLoopResult:
    config = config or PumpConfig()
    pump = Pump(config)
    basal_u_per_min = pump.basal_u_per_min(basal_u_per_h) if smooth else basal_u_per_h / 60.0
    bolus_events = []
    for time_min, units in boluses:
        if smooth:
            delivered = pump.bolus(units)
        else:
            delivered = units
            pump.delivered_units += units
        bolus_events.append(Bolus(time=time_min, units=delivered, duration=config.bolus_duration_min))
    schedule = Schedule(basal_u_per_min=basal_u_per_min, meals=meals, boluses=bolus_events)
    return OpenLoopResult(trace=simulate(subject, schedule, t_end, dt), pump=pump, schedule=schedule)
