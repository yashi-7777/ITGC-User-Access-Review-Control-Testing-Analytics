"""The six ITGC control tests.

Every control is implemented TWICE, independently:
  * SQL    - a .sql file run on an in-memory SQLite copy of the data (how an analyst would query a data warehouse)
  * pandas - vectorised DataFrame code that does not share any logic with the SQL
`reconcile.py` then proves both give the same exceptions. Each control also has a `classify` step that turns a
raw exception row into an audit-style finding: type, severity, risk score, evidence trail and recommended action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from typing import Callable

import numpy as np
import pandas as pd

from itgc_uar.config import Config
from itgc_uar.dataset import Dataset

UNASSIGNED = "Unassigned (no owner / no HR record)"
WS = " \t\n\r"  # characters that make an approval ticket "blank"


# ---------------------------------------------------------------------------------------------- helpers
def _d(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="%Y-%m-%d", errors="coerce")


def _i(series: pd.Series) -> pd.Series:
    return series.astype("Int64")


def _blank(series: pd.Series) -> pd.Series:
    return series.isna() | (series.fillna("").str.strip(WS) == "")


def load_sql(filename: str) -> str:
    return resources.files("itgc_uar").joinpath("sql", filename).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------- pandas twins
def t1_pandas(ds: Dataset, cfg: Config) -> pd.DataFrame:
    review = pd.Timestamp(cfg.review_date)
    h = ds.hr.assign(_term=_d(ds.hr["termination_date"]))
    leavers = h[(h["status"] == "Terminated") & (h["_term"] <= review)]
    m = leavers.merge(ds.access[ds.access["account_status"] == "Active"], on="employee_id", how="inner")
    last = _d(m["last_login_date"])
    out = pd.DataFrame({
        "access_id": m["access_id"], "employee_id": m["employee_id"], "name": m["name"], "department": m["department"],
        "termination_date": m["termination_date"], "username": m["username"], "system": m["system"], "role": m["role"],
        "is_privileged": m["is_privileged"], "last_login_date": m["last_login_date"],
        "days_since_termination": _i((review - m["_term"]).dt.days),
        "login_after_termination": (last > m["_term"]).astype(int),
    })
    return out.sort_values("access_id").reset_index(drop=True)


def t2_pandas(ds: Dataset, cfg: Config) -> pd.DataFrame:
    review = pd.Timestamp(cfg.review_date)
    m = ds.access.merge(ds.hr, on="employee_id", how="left", suffixes=("", "_hr"))
    term = _d(m["termination_date"])
    is_leaver = (m["status"] == "Terminated") & (term <= review)
    last = _d(m["last_login_date"])
    idle = (review - last).dt.days
    keep = ((m["account_status"] == "Active") & (m["account_type"] == "Individual") & ~is_leaver &
            (last.isna() | (idle > cfg.dormant_days)))
    m = m[keep]
    out = pd.DataFrame({
        "access_id": m["access_id"], "employee_id": m["employee_id"], "name": m["name"], "department": m["department"],
        "username": m["username"], "system": m["system"], "role": m["role"], "is_privileged": m["is_privileged"],
        "last_login_date": m["last_login_date"], "days_inactive": _i(idle[keep]),
    })
    return out.sort_values("access_id").reset_index(drop=True)


def t3_pandas(ds: Dataset, cfg: Config) -> pd.DataFrame:
    act = ds.access[ds.access["account_status"] == "Active"]
    parts = []
    for rule in ds.sod.itertuples(index=False):
        a = act[(act["system"] == rule.system) & (act["role"] == rule.role_a)]
        b = act[(act["system"] == rule.system) & (act["role"] == rule.role_b)]
        m = a.merge(b, on="employee_id", suffixes=("_a", "_b"))
        if m.empty:
            continue
        g = m.groupby("employee_id", as_index=False).agg(
            access_id_a=("access_id_a", "min"), access_id_b=("access_id_b", "min"),
            priv_a=("is_privileged_a", "max"), priv_b=("is_privileged_b", "max"))
        g["system"], g["role_a"], g["role_b"] = rule.system, rule.role_a, rule.role_b
        g["risk_description"] = rule.risk_description
        parts.append(g)
    cols = ["employee_id", "name", "department", "system", "role_a", "role_b", "risk_description",
            "employee_status", "access_id_a", "access_id_b", "priv_a", "priv_b"]
    if not parts:
        return pd.DataFrame(columns=cols)
    g = pd.concat(parts).merge(ds.hr, on="employee_id", how="inner").rename(columns={"status": "employee_status"})
    return g[cols].sort_values(["employee_id", "system", "role_a"]).reset_index(drop=True)


def t4_pandas(ds: Dataset, cfg: Config) -> pd.DataFrame:
    u = ds.access[(ds.access["account_status"] == "Active") & (ds.access["is_privileged"] == 1) &
                  _blank(ds.access["approval_ticket"])]
    m = u.merge(ds.hr, on="employee_id", how="left").rename(columns={"status": "employee_status"})
    cols = ["access_id", "employee_id", "name", "department", "username", "system", "role", "granted_date",
            "account_type", "employee_status"]
    return m[cols].sort_values("access_id").reset_index(drop=True)


def t5_pandas(ds: Dataset, cfg: Config) -> pd.DataFrame:
    act = ds.access[ds.access["account_status"] == "Active"]
    shared = act[act["account_type"] == "Shared"].assign(issue="Shared / generic account - no named owner")
    ind = act[act["account_type"] == "Individual"]
    orphan = ind[~ind["employee_id"].isin(set(ds.hr["employee_id"].dropna()))].assign(
        issue="Orphan account - no matching HR record")
    cols = ["access_id", "username", "system", "role", "is_privileged", "last_login_date", "issue"]
    return pd.concat([shared, orphan])[cols].sort_values("access_id").reset_index(drop=True)


def t6_pandas(ds: Dataset, cfg: Config) -> pd.DataFrame:
    ev = ds.events.assign(_t=pd.to_datetime(ds.events["event_time"], format="%Y-%m-%d %H:%M:%S", errors="coerce"))
    win = np.timedelta64(cfg.burst_window_minutes, "m")
    look = np.timedelta64(cfg.burst_success_lookahead_minutes, "m")
    rows = []
    for (user, system), g in ev.groupby(["username", "system"], sort=True):
        fail = np.sort(g.loc[g["result"] == "Failed", "_t"].to_numpy())
        if len(fail) < cfg.burst_threshold:
            continue
        # for each failure: how many failures fall in [t, t + window)?
        counts = np.searchsorted(fail, fail + win, side="left") - np.arange(len(fail))
        starts = fail[counts >= cfg.burst_threshold]
        if len(starts) == 0:
            continue
        succ = np.sort(g.loc[g["result"] == "Success", "_t"].to_numpy())
        after = np.searchsorted(succ, starts + look, side="right") - np.searchsorted(succ, starts, side="right")
        rows.append({
            "username": user, "system": system,
            "first_burst_time": pd.Timestamp(starts.min()).strftime("%Y-%m-%d %H:%M:%S"),
            "max_failed_in_window": int(counts.max()), "success_after_burst": int((after > 0).any()),
        })
    cols = ["username", "system", "first_burst_time", "max_failed_in_window", "success_after_burst"]
    return pd.DataFrame(rows, columns=cols).sort_values(["username", "system"]).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------- populations
def _pop_t1(ds, cfg):
    review = pd.Timestamp(cfg.review_date)
    h = ds.hr[(ds.hr["status"] == "Terminated") & (_d(ds.hr["termination_date"]) <= review)]
    return int(ds.access["employee_id"].isin(set(h["employee_id"])).sum())


def _pop_t2(ds, cfg):
    return int(((ds.access["account_status"] == "Active") & (ds.access["account_type"] == "Individual")).sum())


def _pop_t3(ds, cfg):
    a = ds.access[(ds.access["account_status"] == "Active") & ds.access["employee_id"].notna()]
    return int(len(a[["employee_id", "system"]].drop_duplicates()))


def _pop_t4(ds, cfg):
    return int(((ds.access["account_status"] == "Active") & (ds.access["is_privileged"] == 1)).sum())


def _pop_t5(ds, cfg):
    return int((ds.access["account_status"] == "Active").sum())


def _pop_t6(ds, cfg):
    return int(len(ds.events[["username", "system"]].drop_duplicates()))


# ---------------------------------------------------------------------------------------------- classification
@dataclass
class Context:
    cfg: Config
    access_by_id: dict
    hr_by_id: dict
    user_to_emp: dict

    def dept_for(self, employee_id=None, username=None) -> str:
        emp = employee_id if employee_id is not None and not pd.isna(employee_id) else self.user_to_emp.get(username)
        rec = self.hr_by_id.get(emp)
        return rec["department"] if rec else UNASSIGNED


def _cap(x: float) -> int:
    return int(max(0, min(100, round(x))))


def _isna(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA


def classify_t1(r, ctx: Context) -> dict:
    login = bool(r["login_after_termination"] == 1)
    priv = bool(r["is_privileged"] == 1)
    days = int(r["days_since_termination"])
    if login:
        etype = "Leaver - login after exit date"
    elif priv:
        etype = "Leaver - privileged access retained"
    else:
        etype = "Leaver - access not removed"
    parts = [("Base score for leaver control", ctx.cfg.base_scores["T1"])]
    if login:
        parts.append(("Account used after the exit date", 20))
    if priv:
        parts.append(("Privileged role", 15))
    parts.append((f"{days} days since exit (1 pt per 30 days, max 10)", min(10, days // 30)))
    ev = (f"hr_master {r['employee_id']} is Terminated on {r['termination_date']} ({days} days before the review date); "
          f"user_access {r['access_id']} ({r['system']} / {r['role']}) is still Active; last login "
          f"{r['last_login_date'] if not _isna(r['last_login_date']) else 'none recorded'}"
          f"{' - AFTER the exit date' if login else ''}.")
    return dict(exception_type=etype, severity="High" if (login or priv) else "Medium", parts=parts, evidence=ev,
                action="Disable the account now; if a login followed the exit, review that session's activity and "
                       "raise a security incident if it cannot be explained.")


def classify_t2(r, ctx: Context) -> dict:
    never = _isna(r["last_login_date"])
    priv = bool(r["is_privileged"] == 1)
    days = None if never else int(r["days_inactive"])
    etype = "Dormant - never used" if never else ("Dormant - privileged account" if priv else "Dormant - inactive account")
    parts = [("Base score for dormant-account control", ctx.cfg.base_scores["T2"])]
    if priv:
        parts.append(("Privileged role", 10))
    if never:
        parts.append(("Account has never been used", 5))
    else:
        parts.append((f"Idle beyond limit (1 pt per 36 days over {ctx.cfg.dormant_days}, max 10)",
                      max(0, min(10, (days - ctx.cfg.dormant_days) // 36))))
    ev = (f"user_access {r['access_id']} ({r['system']} / {r['role']}) is Active and "
          + ("has no login recorded." if never else f"was last used {r['last_login_date']} ({days} days before the review date; limit {ctx.cfg.dormant_days})."))
    return dict(exception_type=etype, severity="Medium" if priv else "Low", parts=parts, evidence=ev,
                action="Confirm with the manager whether the access is still needed; disable if not. Add the account to "
                       "the monthly dormant-account sweep.")


def classify_t3(r, ctx: Context) -> dict:
    privs = int(r["priv_a"]) + int(r["priv_b"])
    term = r["employee_status"] == "Terminated"
    parts = [("Base score for SoD control", ctx.cfg.base_scores["T3"])]
    if privs:
        parts.append((f"{privs} of the 2 conflicting roles are privileged (10 each)", 10 * privs))
    if term:
        parts.append(("Employee is a leaver", 10))
    ev = (f"user_access {r['access_id_a']} ({r['system']} / {r['role_a']}) and {r['access_id_b']} "
          f"({r['system']} / {r['role_b']}) are both Active for {r['employee_id']}. Rule: {r['risk_description']}.")
    return dict(exception_type="Segregation-of-duties conflict", severity="High", parts=parts, evidence=ev,
                action="Remove one of the two roles; if the business needs both, document a compensating control "
                       "(for example independent review of every transaction) and get risk-owner sign-off.")


def classify_t4(r, ctx: Context) -> dict:
    acc = ctx.access_by_id[r["access_id"]]
    critical = r["role"] in ctx.cfg.critical_roles
    shared = r["account_type"] == "Shared"
    term = r["employee_status"] == "Terminated"
    blank = not _isna(acc["approval_ticket"])
    parts = [("Base score for privileged-approval control", ctx.cfg.base_scores["T4"])]
    if critical:
        parts.append(("Critical role (configured list)", 10))
    if shared:
        parts.append(("Shared account", 10))
    if term:
        parts.append(("Owner is a leaver", 10))
    ev = (f"user_access {r['access_id']} ({r['system']} / {r['role']}, privileged) is Active, granted {r['granted_date']}, "
          f"and approval_ticket is {'blank (whitespace only)' if blank else 'empty (NULL)'}.")
    return dict(exception_type="Privileged access - ticket is blank" if blank else "Privileged access - no approval ticket",
                severity="High", parts=parts, evidence=ev,
                action="Obtain retrospective approval from the system owner or remove the access; make the ticket field "
                       "mandatory in the provisioning workflow.")


def classify_t5(r, ctx: Context) -> dict:
    shared = str(r["issue"]).startswith("Shared")
    priv = bool(r["is_privileged"] == 1)
    parts = [("Base score for account-ownership control", ctx.cfg.base_scores["T5"])]
    if priv:
        parts.append(("Privileged role", 25))
    if not shared:
        parts.append(("Orphan: no HR record at all", 10))
    ev = (f"user_access {r['access_id']} ({r['username']}, {r['system']} / {r['role']}) is Active; "
          + ("account_type is Shared, so no single person is accountable." if shared else
             "its employee_id does not exist in hr_master."))
    return dict(exception_type="Shared / generic account" if shared else "Orphan account",
                severity="High" if priv else "Medium", parts=parts, evidence=ev,
                action=("Replace with named accounts, or vault the credential and log every checkout."
                        if shared else "Confirm with the system owner who uses it; disable if nobody claims it."))


def classify_t6(r, ctx: Context) -> dict:
    succ = bool(r["success_after_burst"] == 1)
    mx = int(r["max_failed_in_window"])
    extra = max(0, min(10, mx - ctx.cfg.burst_threshold))
    parts = [("Base score for failed-login control", ctx.cfg.base_scores["T6"])]
    if succ:
        parts.append(("Successful login soon after the burst (possible compromise)", 30))
    parts.append((f"Failures above threshold ({mx} vs {ctx.cfg.burst_threshold}), max 10", extra))
    ev = (f"login_events: {r['username']} on {r['system']} had up to {mx} failed logins within "
          f"{ctx.cfg.burst_window_minutes} minutes starting {r['first_burst_time']}"
          f"{'; a successful login followed within ' + str(ctx.cfg.burst_success_lookahead_minutes) + ' minutes' if succ else ''}.")
    return dict(exception_type="Failed-login burst followed by success" if succ else "Failed-login burst",
                severity="High" if succ else "Medium", parts=parts, evidence=ev,
                action=("Force a password reset, review the session and source IP; treat as possible account takeover."
                        if succ else "Check the source of the failures; confirm lockout and alerting rules fired."))


# ---------------------------------------------------------------------------------------------- registry
@dataclass(frozen=True)
class ControlSpec:
    control_id: str
    name: str
    statement: str
    sql_file: str
    population_label: str
    population_sql: str
    population_pandas: Callable
    detect_pandas: Callable
    classify: Callable
    key_columns: tuple[str, ...]
    compare_columns: tuple[str, ...]
    owner: str
    keys_note: str = field(default="")

    def sql(self) -> str:
        return load_sql(self.sql_file)

    def make_key(self, df: pd.DataFrame) -> pd.Series:
        if df.empty:
            return pd.Series([], dtype=object)
        return df[list(self.key_columns)].astype(str).agg("|".join, axis=1) if len(self.key_columns) > 1 \
            else df[self.key_columns[0]].astype(str)


_LEAVER = "h.status = 'Terminated' AND h.termination_date <= :review_date"

CONTROLS: dict[str, ControlSpec] = {c.control_id: c for c in [
    ControlSpec(
        "T1", "Leaver access removal", "Access of terminated employees is disabled on or before the termination date.",
        "T1_leavers_with_active_access.sql", "Access entitlements of terminated employees",
        f"SELECT COUNT(*) FROM user_access u JOIN hr_master h ON h.employee_id = u.employee_id WHERE {_LEAVER}",
        _pop_t1, t1_pandas, classify_t1, ("access_id",),
        ("employee_id", "termination_date", "username", "system", "role", "is_privileged", "last_login_date",
         "days_since_termination", "login_after_termination"), "HR Operations + IAM team"),
    ControlSpec(
        "T2", "Dormant account review", "Accounts unused for more than the dormancy limit are reviewed and disabled.",
        "T2_dormant_accounts.sql", "Active individual accounts",
        "SELECT COUNT(*) FROM user_access WHERE account_status = 'Active' AND account_type = 'Individual'",
        _pop_t2, t2_pandas, classify_t2, ("access_id",),
        ("employee_id", "username", "system", "role", "is_privileged", "last_login_date", "days_inactive"),
        "System owner / line manager"),
    ControlSpec(
        "T3", "Segregation of duties", "No user holds two conflicting roles on the same system.",
        "T3_segregation_of_duties.sql", "Employee-system access pairs (active)",
        "SELECT COUNT(*) FROM (SELECT DISTINCT employee_id, system FROM user_access "
        "WHERE account_status = 'Active' AND employee_id IS NOT NULL)",
        _pop_t3, t3_pandas, classify_t3, ("employee_id", "system", "role_a", "role_b"),
        ("access_id_a", "access_id_b", "priv_a", "priv_b", "employee_status"), "Application owner + Risk & Compliance"),
    ControlSpec(
        "T4", "Privileged access approval", "Every privileged access grant is supported by an approved request.",
        "T4_privileged_without_approval.sql", "Active privileged accounts",
        "SELECT COUNT(*) FROM user_access WHERE account_status = 'Active' AND is_privileged = 1",
        _pop_t4, t4_pandas, classify_t4, ("access_id",),
        ("employee_id", "username", "system", "role", "granted_date", "account_type", "employee_status"),
        "Application owner / IAM team"),
    ControlSpec(
        "T5", "Account ownership", "Every active account is linked to one named, current employee.",
        "T5_shared_and_orphan_accounts.sql", "All active accounts",
        "SELECT COUNT(*) FROM user_access WHERE account_status = 'Active'",
        _pop_t5, t5_pandas, classify_t5, ("access_id",),
        ("username", "system", "role", "is_privileged", "last_login_date", "issue"), "IAM team + system owner"),
    ControlSpec(
        "T6", "Failed-login monitoring", "Bursts of failed logins are detected and investigated.",
        "T6_bruteforce_logins.sql", "Accounts with login activity",
        "SELECT COUNT(*) FROM (SELECT DISTINCT username, system FROM login_events)",
        _pop_t6, t6_pandas, classify_t6, ("username", "system"),
        ("first_burst_time", "max_failed_in_window", "success_after_burst"), "Security operations (SOC)"),
]}
