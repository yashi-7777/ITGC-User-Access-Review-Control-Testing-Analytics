"""Data-quality checks run BEFORE any control test.

Audit context: the audit evidence is only as good as the data behind it. Auditors call the reports they receive from
the client "Information Produced by the Entity" (IPE) and must satisfy themselves that it is complete and accurate.
These checks are an automated first pass at that: schema, key uniqueness, allowed values, date validity and
internal consistency. ERRORs stop the run (unless --force); WARNs and INFOs are reported.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from itgc_uar.config import Config
from itgc_uar.dataset import REQUIRED_COLUMNS, Dataset

ISO_DATE = r"\d{4}-\d{2}-\d{2}"
ISO_DATETIME = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"


@dataclass
class Check:
    check_id: str
    table: str
    description: str
    severity: str  # ERROR | WARN | INFO
    failed: int
    total: int
    detail: str = ""

    @property
    def status(self) -> str:
        if self.severity == "INFO":
            return "INFO"
        return "PASS" if self.failed == 0 else "FAIL"


@dataclass
class DQReport:
    checks: list[Check]

    @property
    def errors(self) -> int:
        return sum(1 for c in self.checks if c.severity == "ERROR" and c.failed > 0)

    @property
    def warnings(self) -> int:
        return sum(1 for c in self.checks if c.severity == "WARN" and c.failed > 0)

    @property
    def ok(self) -> bool:
        return self.errors == 0

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"Check": c.check_id, "Table": c.table, "Description": c.description, "Severity": c.severity,
             "Status": c.status, "Failed rows": c.failed, "Rows checked": c.total, "Detail": c.detail}
            for c in self.checks
        ])


def _bad_format(series: pd.Series, pattern: str, fmt: str) -> int:
    """Rows that are non-empty but do not match the pattern or are not a real calendar date/time."""
    s = series.dropna().astype(str)
    if s.empty:
        return 0
    matches = s.str.fullmatch(pattern)
    parsed = pd.to_datetime(s.where(matches), format=fmt, errors="coerce")
    return int((~matches | parsed.isna()).sum())


def run_data_quality(ds: Dataset, cfg: Config) -> DQReport:
    checks: list[Check] = []

    def add(cid: str, table: str, desc: str, sev: str, failed: int, total: int, detail: str = "") -> None:
        checks.append(Check(cid, table, desc, sev, int(failed), int(total), detail))

    frames = {"hr_master": ds.hr, "user_access": ds.access, "sod_rules": ds.sod, "login_events": ds.events}

    # DQ01 - required columns. Without them nothing else can be checked.
    schema_failed = False
    for table, cols in REQUIRED_COLUMNS.items():
        missing = [c for c in cols if c not in frames[table].columns]
        add("DQ01", table, "Required columns are present", "ERROR", len(missing), len(cols),
            f"missing: {missing}" if missing else "")
        schema_failed |= bool(missing)
    if schema_failed:
        return DQReport(checks)

    hr, ua, sod, ev = ds.hr, ds.access, ds.sod, ds.events

    # DQ02 - tables are not empty
    for table, df, sev in (("hr_master", hr, "ERROR"), ("user_access", ua, "ERROR"), ("login_events", ev, "WARN")):
        add("DQ02", table, "Table contains rows", sev, int(len(df) == 0), 1)

    # DQ03 - keys are present and unique
    for table, df, key in (("hr_master", hr, "employee_id"), ("user_access", ua, "access_id"),
                           ("login_events", ev, "event_id")):
        nulls = int(df[key].isna().sum())
        dups = int(df[key].dropna().duplicated().sum())
        add("DQ03", table, f"{key} is present and unique", "ERROR", nulls + dups, len(df),
            f"{nulls} missing, {dups} duplicated" if nulls + dups else "")

    # DQ04 - allowed values
    allowed = [
        ("hr_master", hr, "status", {"Active", "Terminated"}),
        ("user_access", ua, "account_status", {"Active", "Disabled"}),
        ("user_access", ua, "account_type", {"Individual", "Shared", "Service"}),
        ("user_access", ua, "is_privileged", {"0", "1"}),
        ("login_events", ev, "result", {"Success", "Failed"}),
    ]
    for table, df, col, ok_values in allowed:
        bad = int((~df[col].astype(str).isin(ok_values) | df[col].isna()).sum())
        add("DQ04", table, f"{col} is one of {sorted(ok_values)}", "ERROR", bad, len(df))

    # DQ05 - dates are real ISO dates / datetimes (the SQL tests compare dates as text, so format matters)
    date_cols = [("hr_master", hr, "hire_date", False), ("hr_master", hr, "termination_date", True),
                 ("user_access", ua, "granted_date", False), ("user_access", ua, "last_login_date", True)]
    for table, df, col, nullable in date_cols:
        missing = 0 if nullable else int(df[col].isna().sum())
        add("DQ05", table, f"{col} is a valid YYYY-MM-DD date", "ERROR",
            _bad_format(df[col], ISO_DATE, "%Y-%m-%d") + missing, len(df))
    add("DQ05", "login_events", "event_time is a valid YYYY-MM-DD HH:MM:SS timestamp", "ERROR",
        _bad_format(ev["event_time"], ISO_DATETIME, "%Y-%m-%d %H:%M:%S") + int(ev["event_time"].isna().sum()), len(ev))

    # Parsed helpers for the logic checks (invalid values were already counted above and become NaT here)
    d = lambda s: pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")  # noqa: E731
    hire, term = d(hr["hire_date"]), d(hr["termination_date"])
    review = pd.Timestamp(cfg.review_date)

    # DQ06 - internal consistency
    add("DQ06", "hr_master", "Terminated employees have a termination_date", "ERROR",
        int(((hr["status"] == "Terminated") & hr["termination_date"].isna()).sum()), int((hr["status"] == "Terminated").sum()))
    add("DQ06", "hr_master", "Active employees have no termination_date", "WARN",
        int(((hr["status"] == "Active") & hr["termination_date"].notna()).sum()), int((hr["status"] == "Active").sum()))
    add("DQ06", "hr_master", "termination_date is not before hire_date", "WARN", int((term < hire).sum()), int(term.notna().sum()))
    add("DQ06", "hr_master", "termination_date is not after the review date (future-dated exits are ignored by T1)",
        "WARN", int((term > review).sum()), int(term.notna().sum()))
    add("DQ06", "user_access", "last_login_date is not after the review date", "WARN",
        int((d(ua["last_login_date"]) > review).sum()), int(ua["last_login_date"].notna().sum()))
    add("DQ06", "user_access", "granted_date is not after the review date", "WARN",
        int((d(ua["granted_date"]) > review).sum()), len(ua))
    ev_time = pd.to_datetime(ev["event_time"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    add("DQ06", "login_events", "event_time is not after the review date", "WARN",
        int((ev_time >= review + pd.Timedelta(days=1)).sum()), len(ev))

    # DQ07 - referential integrity
    known_emp = set(hr["employee_id"].dropna())
    linked = ua["employee_id"].notna() & (ua["account_type"] == "Individual")
    orphans = int((linked & ~ua["employee_id"].isin(known_emp)).sum())
    add("DQ07", "user_access", "Individual accounts whose employee_id is not in the HR master (reported by T5)",
        "INFO", orphans, int(linked.sum()))
    known_users = set(ua["username"].dropna())
    add("DQ07", "login_events", "Login events whose username has no account in user_access", "WARN",
        int((~ev["username"].isin(known_users)).sum()), len(ev))

    # DQ08 - duplicate event rows
    add("DQ08", "login_events", "No exact-duplicate login events (same user, system, time, result)", "WARN",
        int(ev.duplicated(["username", "system", "event_time", "result"]).sum()), len(ev))

    # DQ09 - SoD rule sanity
    add("DQ09", "sod_rules", "Each SoD rule pairs two different roles", "ERROR",
        int((sod["role_a"] == sod["role_b"]).sum() + sod[["system", "role_a", "role_b"]].isna().any(axis=1).sum()), len(sod))

    # DQ10 - completeness information (not pass/fail)
    add("DQ10", "user_access", "Accounts with no approval_ticket (information)", "INFO",
        int(ua["approval_ticket"].isna().sum()), len(ua))
    add("DQ10", "user_access", "Accounts with no last_login_date (information)", "INFO",
        int(ua["last_login_date"].isna().sum()), len(ua))

    return DQReport(checks)
