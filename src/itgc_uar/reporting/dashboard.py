"""Self-contained interactive HTML dashboard (no server, no external files; works offline)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pandas as pd

from itgc_uar import __version__
from itgc_uar.engine import RunResult


def _clean(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.astype(object).where(df.notna(), None).to_json(orient="records"))


def build_payload(result: RunResult, meta: dict, validation: pd.DataFrame | None) -> dict:
    cfg = result.cfg
    reg = result.register
    return {
        "version": __version__, "review_date": cfg.review_date.isoformat(), "fingerprint": cfg.fingerprint(),
        "profile": meta.get("profile", "custom"), "reconciled": result.reconciled,
        "dq": {"errors": result.dq.errors, "warnings": result.dq.warnings},
        "sla_days": cfg.sla_days, "priority_bands": cfg.priority_bands,
        "summary": _clean(result.summary), "register": _clean(reg),
        "dept": _clean(result.dept_matrix.reset_index()),
        "recon": [{"control_id": r.control_id, "sql": r.sql_count, "pandas": r.pandas_count, "status": r.status}
                  for r in result.recon.values()],
        "validation": _clean(validation) if validation is not None else None,
    }


def write_dashboard(result: RunResult, out_dir: Path, meta: dict, validation: pd.DataFrame | None) -> Path:
    html = resources.files("itgc_uar").joinpath("reporting/dashboard_template.html").read_text(encoding="utf-8")
    data = json.dumps(build_payload(result, meta, validation), ensure_ascii=False).replace("</", "<\\/")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "ITGC_UAR_Dashboard.html"
    path.write_text(html.replace("/*__DATA__*/null", data), encoding="utf-8")
    return path
