"""The quality gate: limits per profile, read from `eval/gate.toml`.

A metric under `[<profile>.min]` must be at least its limit, one under
`[<profile>.max]` at most. A limit naming a metric that does not exist is an
error, not a silent pass: a renamed metric must not switch its gate off.
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Limits:
    minimum: Mapping[str, float]
    maximum: Mapping[str, float]
    # The configured `min_score` must lie in the calibrated interval: catches a
    # threshold that was right for yesterday's corpus or embedding model.
    require_calibrated_threshold: bool = False


def load_limits(path: Path, profile: str) -> Limits:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    if profile not in config:
        raise ValueError(f"{path.name} has no limits for profile {profile!r}")
    section = config[profile]
    return Limits(
        minimum=section.get("min", {}),
        maximum=section.get("max", {}),
        require_calibrated_threshold=section.get("require_calibrated_threshold", False),
    )


def check(metrics: Mapping[str, float | int], limits: Limits) -> list[str]:
    """Every violated limit, as a readable line; empty when the gate passes."""
    unknown = sorted((set(limits.minimum) | set(limits.maximum)) - set(metrics))
    if unknown:
        raise ValueError(f"the gate names unknown metrics: {unknown}")
    failures = [
        f"{name} = {metrics[name]:.3f}, below the minimum {limit}"
        for name, limit in sorted(limits.minimum.items())
        if metrics[name] < limit
    ]
    failures += [
        f"{name} = {metrics[name]:.3f}, above the maximum {limit}"
        for name, limit in sorted(limits.maximum.items())
        if metrics[name] > limit
    ]
    return failures
