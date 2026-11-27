"""One profile policy for every runner: derive CR/TDI/ISF, never invent them silently.

Three of the derived subjects have no feasible CR under either the S2008 or the S2013 definition.
The old runners substituted CR 10.5, TDI 40 and CF 50; that adult fallback over-insulinised them
and drove the safety tail (A38). A caller that genuinely wants the old behaviour must ask for it.
"""
from __future__ import annotations

from simcore.population import (
    cached_subject_profile, compute_cf, compute_cr, compute_isf_model, compute_tdi,
)

FALLBACK = (10.5, 40.0, 50.0)


def _checked(subject, cr, tdi, isf, allow_fallback: bool):
    missing = [
        name for name, value in (("CR", cr), ("TDI", tdi), ("CF", isf))
        if value is None or (isinstance(value, (int, float)) and value <= 0)
    ]
    if missing:
        if not allow_fallback:
            raise ValueError(
                f"{subject.name}: no feasible {'/'.join(missing)} under S2008/S2013"
            )
        cr = cr if cr and cr > 0 else FALLBACK[0]
        tdi = tdi if tdi and tdi > 0 else FALLBACK[1]
        isf = isf if isf and isf > 0 else FALLBACK[2]
    return float(cr), float(tdi), float(isf)


def profile_values(subject, allow_fallback: bool = False):
    """``(CR, TDI, CF)`` from the published definitions, or raise."""
    cr = compute_cr(subject)
    tdi = compute_tdi(subject, cr) if cr else None
    isf = compute_cf(tdi)
    return _checked(subject, cr, tdi, isf, allow_fallback)


def isf_for_source(subject, source: str = "rule", allow_fallback: bool = False) -> float:
    _, _, isf = profile_values(subject, allow_fallback)
    return compute_isf_model(subject) if source == "model" else isf


def cached_profile_values(subject, isf_source: str = "rule", allow_fallback: bool = False):
    """The same policy for runners that use the cached per-subject profile."""
    cached = cached_subject_profile(subject, isf_source)
    return _checked(subject, cached["cr"], cached["tdi"], cached["isf"], allow_fallback)


def eligible_names(subjects: dict, allow_fallback: bool = False,
                   isf_source: str | None = None) -> list[str]:
    """Sorted subject names with a feasible profile, unless the fallback is explicitly allowed."""
    if allow_fallback:
        return sorted(subjects)
    names = []
    for name in sorted(subjects):
        try:
            if isf_source is None:
                profile_values(subjects[name])
            else:
                cached_profile_values(subjects[name], isf_source)
        except ValueError:
            continue
        names.append(name)
    return names
