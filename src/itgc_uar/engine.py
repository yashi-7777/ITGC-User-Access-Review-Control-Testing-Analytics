"""Run all controls: data quality -> SQL + pandas tests -> reconciliation -> classification -> summary tables."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import pandas as pd

from itgc_uar.config import CONTROL_IDS, Config
from itgc_uar.controls import CONTROLS, UNASSIGNED, Context
from itgc_uar.dataquality import DQReport, run_data_quality
from itgc_uar.dataset import Dataset
from itgc_uar.reconcile import ReconResult, reconcile
from itgc_uar.scoring import due_date_for, explain, priority_for, total_score

log = logging.getLogger("itgc_uar")


class DataQualityError(RuntimeError):
    def __init__(self, report: DQReport):
        bad = [f"{c.check_id} {c.table}: {c.description} ({c.failed} failed) {c.detail}".strip()
               for c in report.checks if c.severity == "ERROR" and c.failed]
        super().__init__("Data-quality errors stop the run (use --force to override):\n  " + "\n  ".join(bad))
        self.report = report


@dataclass
class RunResult:
    cfg: Config
    dataset: Dataset
    dq: DQReport
    details: dict[str, pd.DataFrame]
    pandas_details: dict[str, pd.DataFrame]
    recon: dict[str, ReconResult]
    summary: pd.DataFrame
    register: pd.DataFrame
    dept_matrix: pd.DataFrame
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def reconciled(self) -> bool:
        return all(r.ok for r in self.recon.values())

    @property
    def total_exceptions(self) -> int:
        return int(len(self.register))


REGISTER_COLUMNS = ["exception_id", "control_id", "control", "entity_key", "employee_id", "name", "department",
                    "username", "system", "role", "access_ids", "exception_type", "severity", "risk_score",
                    "priority", "due_date", "owner", "evidence", "score_factors", "recommended_action"]


def run_controls(ds: Dataset, cfg: Config, force: bool = False) -> RunResult:
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    dq = run_data_quality(ds, cfg)
    for c in dq.checks:
        if c.failed and c.severity != "INFO":
            log.log(logging.ERROR if c.severity == "ERROR" else logging.WARNING,
                    "%s %s: %s -> %d of %d rows. %s", c.check_id, c.table, c.description, c.failed, c.total, c.detail)
    if not dq.ok:
        if cfg.fail_on_dq_error and not force:
            raise DataQualityError(dq)
        log.warning("Continuing despite %d data-quality error(s) (--force).", dq.errors)
    timings["data_quality"] = time.perf_counter() - t0

    ds = ds.coerced()
    conn = ds.to_sqlite()
    ctx = Context(
        cfg, access_by_id={r["access_id"]: r for r in ds.access.to_dict("records")},
        hr_by_id={r["employee_id"]: r for r in ds.hr.to_dict("records")},
        user_to_emp=ds.access.dropna(subset=["employee_id"]).drop_duplicates("username").set_index("username")["employee_id"].to_dict(),
    )

    acct_ids = ds.access.groupby(["username", "system"])["access_id"].apply(list).to_dict()
    details, pdetails, recon, summary_rows, register = {}, {}, {}, [], []
    for cid in CONTROL_IDS:
        spec = CONTROLS[cid]
        t = time.perf_counter()
        sql_df = pd.read_sql_query(spec.sql(), conn, params=cfg.sql_params)
        sql_pop = int(conn.execute(spec.population_sql, cfg.sql_params).fetchone()[0])
        t_sql = time.perf_counter() - t
        t = time.perf_counter()
        pd_df = spec.detect_pandas(ds, cfg)
        pd_pop = spec.population_pandas(ds, cfg)
        t_pd = time.perf_counter() - t
        rec = reconcile(spec, sql_df, pd_df, sql_pop, pd_pop)
        recon[cid] = rec
        (log.info if rec.ok else log.error)("%s %-26s SQL=%-4d pandas=%-4d population=%-5d %s %s", cid, spec.name,
                                           len(sql_df), len(pd_df), sql_pop, rec.status, rec.examples())
        timings[cid] = t_sql + t_pd

        sql_df = sql_df.assign(_k=spec.make_key(sql_df).values if len(sql_df) else []).sort_values("_k").reset_index(drop=True)
        keys = sql_df.pop("_k")
        extra = []
        for n, (key, row) in enumerate(zip(keys, sql_df.to_dict("records")), start=1):
            f = spec.classify(row, ctx)
            score = total_score(f["parts"])
            prio = priority_for(score, cfg)
            access_ids = [row[c] for c in ("access_id", "access_id_a", "access_id_b") if c in row and row[c] is not None]
            if cid == "T6":
                access_ids = acct_ids.get((row["username"], row["system"]), [])[:3]
            emp = row.get("employee_id")
            if emp is None or pd.isna(emp):
                emp = ctx.access_by_id.get(access_ids[0], {}).get("employee_id") if access_ids else None
                emp = None if emp is None or pd.isna(emp) else emp
            hr_rec = ctx.hr_by_id.get(emp)
            username = row.get("username") or (ctx.access_by_id[access_ids[0]]["username"] if access_ids else None)
            system = row.get("system")
            role = row.get("role") if cid != "T3" else f"{row['role_a']} + {row['role_b']}"
            extra.append({
                "exception_id": f"{cid}-{n:04d}", "exception_type": f["exception_type"], "severity": f["severity"],
                "risk_score": score, "priority": prio, "due_date": due_date_for(prio, cfg), "evidence": f["evidence"],
                "score_factors": explain(f["parts"]), "recommended_action": f["action"],
            })
            register.append({
                "exception_id": f"{cid}-{n:04d}", "control_id": cid, "control": spec.name, "entity_key": key,
                "employee_id": emp, "name": hr_rec["name"] if hr_rec else None,
                "department": ctx.dept_for(emp, username), "username": username, "system": system, "role": role,
                "access_ids": ";".join(access_ids), "exception_type": f["exception_type"], "severity": f["severity"],
                "risk_score": score, "priority": prio, "due_date": due_date_for(prio, cfg), "owner": spec.owner,
                "evidence": f["evidence"], "score_factors": explain(f["parts"]), "recommended_action": f["action"],
            })
        det = sql_df.copy()
        if extra:
            det = pd.concat([pd.DataFrame(extra), det], axis=1)
        details[cid] = det
        pdetails[cid] = pd_df

        sev = pd.Series([e["severity"] for e in extra], dtype=object)
        pri = pd.Series([e["priority"] for e in extra], dtype=object)
        scores = [e["risk_score"] for e in extra]
        n_exc = len(extra)
        rate = n_exc / sql_pop if sql_pop else 0.0
        summary_rows.append({
            "control_id": cid, "control": spec.name, "control_statement": spec.statement,
            "population_basis": spec.population_label, "population": sql_pop, "exceptions": n_exc,
            "deviation_rate": rate, "tolerable_rate": cfg.tolerable_rates[cid],
            "conclusion": "Effective" if rate <= cfg.tolerable_rates[cid] else "Not effective",
            "high": int((sev == "High").sum()), "medium": int((sev == "Medium").sum()), "low": int((sev == "Low").sum()),
            "p1": int((pri == "P1").sum()), "p2": int((pri == "P2").sum()),
            "p3": int((pri == "P3").sum()), "p4": int((pri == "P4").sum()),
            "avg_risk_score": round(sum(scores) / n_exc, 1) if n_exc else 0.0, "max_risk_score": max(scores, default=0),
            "owner": spec.owner, "reconciliation": rec.status,
        })

    reg = pd.DataFrame(register, columns=REGISTER_COLUMNS)
    reg = reg.sort_values(["risk_score", "exception_id"], ascending=[False, True]).reset_index(drop=True)
    dept = (reg.pivot_table(index="department", columns="control_id", values="exception_id", aggfunc="count",
                            fill_value=0).reindex(columns=list(CONTROL_IDS), fill_value=0))
    dept["Total"] = dept.sum(axis=1)
    dept = dept.sort_values("Total", ascending=False)
    timings["total"] = time.perf_counter() - t0
    log.info("Total exceptions: %d (%d High) - reconciliation %s", len(reg), int((reg["severity"] == "High").sum()),
             "ALL MATCH" if all(r.ok for r in recon.values()) else "MISMATCH")
    return RunResult(cfg, ds, dq, details, pdetails, recon, pd.DataFrame(summary_rows), reg, dept, timings)
