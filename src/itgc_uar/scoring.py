"""Risk score -> remediation priority -> due date.

The score is a transparent heuristic: base score of the control plus the modifiers listed next to each finding
(`score_factors`). It is NOT an industry standard - it exists so findings can be ordered for remediation, and every
point of it can be traced and changed in the config. Severity (High/Medium/Low) is the separate, simpler rating
kept from the first version of this project.
"""

from __future__ import annotations

from datetime import timedelta

from itgc_uar.config import Config


def total_score(parts: list[tuple[str, int]]) -> int:
    return int(max(0, min(100, sum(p for _, p in parts))))


def priority_for(score: int, cfg: Config) -> str:
    b = cfg.priority_bands
    if score >= b["P1"]:
        return "P1"
    if score >= b["P2"]:
        return "P2"
    if score >= b["P3"]:
        return "P3"
    return "P4"


def due_date_for(priority: str, cfg: Config) -> str:
    return (cfg.review_date + timedelta(days=cfg.sla_days[priority])).isoformat()


def explain(parts: list[tuple[str, int]]) -> str:
    return "; ".join(f"{label}: +{pts}" for label, pts in parts if pts)
