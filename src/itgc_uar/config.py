"""Configuration: load YAML, merge with defaults, validate, expose as an immutable object."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

CONTROL_IDS = ("T1", "T2", "T3", "T4", "T5", "T6")
PRIORITIES = ("P1", "P2", "P3", "P4")
_ALLOWED_TOP_KEYS = {
    "review_date", "thresholds", "tolerable_deviation_rate", "scoring",
    "remediation_sla_days", "fail_on_data_quality_error",
}


class ConfigError(ValueError):
    """Raised when the configuration is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class Config:
    review_date: date
    dormant_days: int
    burst_threshold: int
    burst_window_minutes: int
    burst_success_lookahead_minutes: int
    tolerable_rates: dict[str, float]
    base_scores: dict[str, int]
    critical_roles: tuple[str, ...]
    priority_bands: dict[str, int]
    sla_days: dict[str, int]
    fail_on_dq_error: bool = True

    @property
    def sql_params(self) -> dict[str, Any]:
        """Named parameters bound into the SQL files (:review_date, :dormant_days, ...)."""
        return {
            "review_date": self.review_date.isoformat(),
            "dormant_days": self.dormant_days,
            "burst_threshold": self.burst_threshold,
            "burst_window_minutes": self.burst_window_minutes,
            "burst_success_lookahead_minutes": self.burst_success_lookahead_minutes,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "review_date": self.review_date.isoformat(),
            "dormant_days": self.dormant_days,
            "burst_threshold": self.burst_threshold,
            "burst_window_minutes": self.burst_window_minutes,
            "burst_success_lookahead_minutes": self.burst_success_lookahead_minutes,
            "tolerable_rates": self.tolerable_rates,
            "base_scores": self.base_scores,
            "critical_roles": list(self.critical_roles),
            "priority_bands": self.priority_bands,
            "sla_days": self.sla_days,
            "fail_on_dq_error": self.fail_on_dq_error,
        }

    def fingerprint(self) -> str:
        """Short hash of the effective configuration - recorded in every run manifest."""
        blob = json.dumps(self.as_dict(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _positive_int(value: Any, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{name} must be an integer >= {minimum}, got {value!r}")
    return value


def _per_control(mapping: Any, name: str) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        raise ConfigError(f"{name} must be a mapping of control id -> value")
    missing = [c for c in CONTROL_IDS if c not in mapping]
    unknown = [c for c in mapping if c not in CONTROL_IDS]
    if missing or unknown:
        raise ConfigError(f"{name}: missing {missing or 'none'}, unknown {unknown or 'none'}")
    return mapping


def build_config(raw: dict[str, Any]) -> Config:
    """Validate a raw (already merged) mapping and return a Config."""
    unknown = set(raw) - _ALLOWED_TOP_KEYS
    if unknown:
        raise ConfigError(f"Unknown configuration key(s): {sorted(unknown)}")
    try:
        review = date.fromisoformat(str(raw["review_date"]))
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"review_date must be an ISO date (YYYY-MM-DD): {exc}") from exc

    th = raw.get("thresholds", {})
    rates = _per_control(raw.get("tolerable_deviation_rate"), "tolerable_deviation_rate")
    for cid, r in rates.items():
        if isinstance(r, bool) or not isinstance(r, (int, float)) or not 0.0 <= float(r) <= 1.0:
            raise ConfigError(f"tolerable_deviation_rate.{cid} must be between 0 and 1, got {r!r}")

    scoring = raw.get("scoring", {})
    base = _per_control(scoring.get("base"), "scoring.base")
    for cid, s in base.items():
        _positive_int(s, f"scoring.base.{cid}", minimum=0)
        if s > 100:
            raise ConfigError(f"scoring.base.{cid} must be <= 100")

    bands = scoring.get("priority_bands", {})
    if set(bands) != {"P1", "P2", "P3"}:
        raise ConfigError("scoring.priority_bands must define exactly P1, P2 and P3")
    if not (100 >= bands["P1"] > bands["P2"] > bands["P3"] > 0):
        raise ConfigError("scoring.priority_bands must satisfy 100 >= P1 > P2 > P3 > 0")

    sla = raw.get("remediation_sla_days", {})
    if set(sla) != set(PRIORITIES):
        raise ConfigError("remediation_sla_days must define P1, P2, P3 and P4")
    for p, d in sla.items():
        _positive_int(d, f"remediation_sla_days.{p}")

    roles = scoring.get("critical_roles", [])
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        raise ConfigError("scoring.critical_roles must be a list of role names")

    return Config(
        review_date=review,
        dormant_days=_positive_int(th.get("dormant_days"), "thresholds.dormant_days"),
        burst_threshold=_positive_int(th.get("burst_threshold"), "thresholds.burst_threshold", minimum=2),
        burst_window_minutes=_positive_int(th.get("burst_window_minutes"), "thresholds.burst_window_minutes"),
        burst_success_lookahead_minutes=_positive_int(
            th.get("burst_success_lookahead_minutes"), "thresholds.burst_success_lookahead_minutes"),
        tolerable_rates={c: float(rates[c]) for c in CONTROL_IDS},
        base_scores={c: int(base[c]) for c in CONTROL_IDS},
        critical_roles=tuple(roles),
        priority_bands={k: int(v) for k, v in bands.items()},
        sla_days={k: int(v) for k, v in sla.items()},
        fail_on_dq_error=bool(raw.get("fail_on_data_quality_error", True)),
    )


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Config:
    """Load the packaged default config, optionally overlay a user YAML file and/or a dict."""
    default_text = resources.files("itgc_uar").joinpath("config/default.yaml").read_text(encoding="utf-8")
    raw = yaml.safe_load(default_text)
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"Config file not found: {p}")
        try:
            user = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Config file is not valid YAML: {exc}") from exc
        if not isinstance(user, dict):
            raise ConfigError("Config file must contain a YAML mapping at the top level")
        raw = _deep_merge(raw, user)
    if overrides:
        raw = _deep_merge(raw, overrides)
    return build_config(raw)
