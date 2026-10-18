"""Synthetic (SIMULATED) enterprise dataset generator with a ground-truth manifest.

Everything produced here is fake: names, IDs, systems, logins. A fixed random seed makes every run reproducible.

Two profiles:
  baseline    400 employees, 3 systems. Reproduces the original v1 data exactly (same seed, same random-call order),
              so every v1 number stays verifiable.
  enterprise  2,000 employees, 5 systems, more planted problems, plus "decoys": records that sit right next to a
              control boundary but must NOT be flagged (e.g. a login exactly 90 days ago, a future-dated exit,
              4 failed logins when the threshold is 5). Decoys let us measure false positives, not just misses.

Alongside the four CSV tables the generator writes ground_truth.csv - the list of planted problems and decoys.
It is used ONLY to validate the detection logic (recall / false-positive checks); the control tests never read it.
"""

from __future__ import annotations

import json
import random
import string
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from itgc_uar import __version__

FIRST = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan",
         "Ananya", "Diya", "Saanvi", "Aadhya", "Kavya", "Meera", "Riya", "Navya", "Pooja", "Isha"]
LAST = ["Sharma", "Verma", "Gupta", "Singh", "Kumar", "Mehta", "Jain", "Agarwal", "Reddy", "Nair",
        "Iyer", "Das", "Bose", "Khan", "Joshi", "Malhotra", "Chopra", "Bhatia", "Saxena", "Kapoor"]

_BASE_TITLES = {
    "Finance": ["Accountant", "Senior Accountant", "Finance Manager", "AP Executive"],
    "Procurement": ["Buyer", "Procurement Lead", "Vendor Manager"],
    "IT": ["System Engineer", "Developer", "Database Admin", "IT Support"],
    "HR": ["HR Executive", "Payroll Executive", "HR Manager"],
    "Sales": ["Sales Executive", "Account Manager"],
    "Operations": ["Operations Analyst", "Ops Manager"],
}
_BASE_WEIGHTS = {"Finance": 70, "Procurement": 40, "IT": 60, "HR": 40, "Sales": 100, "Operations": 90}
_BASE_SYSTEMS = {"Finance": ["ERP"], "Procurement": ["ERP"], "IT": ["ERP", "Core DB"],
                 "HR": ["HRMS"], "Sales": ["ERP"], "Operations": ["ERP"]}
_BASE_NORMAL = {"ERP": ["Viewer", "Data Entry", "Approver"], "HRMS": ["Viewer", "HR Executive"],
                "Core DB": ["Read Only", "Developer"]}
_BASE_PRIV = {"ERP": ["System Admin", "Vendor Master Admin", "Payment Release"],
              "HRMS": ["System Admin", "Payroll Admin"], "Core DB": ["DBA"]}
_BASE_SOD = (
    ("ERP", "Vendor Master Admin", "Payment Release", "Can create a fake vendor and pay it (fraud risk)"),
    ("ERP", "Data Entry", "Approver", "Can enter and approve own transactions"),
    ("HRMS", "HR Executive", "Payroll Admin", "Can add a ghost employee and run their salary"),
    ("Core DB", "Developer", "DBA", "Can change code and push it straight to production data"),
)
_BASE_SHARED = (("finance_shared", "ERP", "Data Entry"), ("admin_common", "ERP", "System Admin"),
                ("hr_team", "HRMS", "HR Executive"), ("dba_backup", "Core DB", "DBA"),
                ("support_generic", "ERP", "Viewer"), ("payroll_shared", "HRMS", "Payroll Admin"))


@dataclass(frozen=True)
class Profile:
    name: str
    seed: int
    n_employees: int
    n_leavers: int
    leaver_window: tuple[date, date]
    hire_window: tuple[date, date]
    dept_titles: dict
    dept_weights: dict
    dept_systems: dict
    normal_roles: dict
    privileged_roles: dict
    sod_rules: tuple
    shared_accounts: tuple
    inj_leavers_active: int
    inj_leavers_login_after: int
    inj_dormant: int
    inj_never_used: int
    inj_sod: int
    inj_priv_no_ticket: int
    inj_priv_blank_ticket: int
    inj_orphans: int
    inj_bursts: int
    inj_bursts_with_success: int
    unique_name_initials: bool
    decoys: bool


BASELINE = Profile(
    name="baseline", seed=42, n_employees=400, n_leavers=48,
    leaver_window=(date(2025, 10, 1), date(2026, 8, 31)), hire_window=(date(2015, 1, 1), date(2025, 6, 30)),
    dept_titles=_BASE_TITLES, dept_weights=_BASE_WEIGHTS, dept_systems=_BASE_SYSTEMS,
    normal_roles=_BASE_NORMAL, privileged_roles=_BASE_PRIV, sod_rules=_BASE_SOD, shared_accounts=_BASE_SHARED,
    inj_leavers_active=14, inj_leavers_login_after=5, inj_dormant=30, inj_never_used=4, inj_sod=10,
    inj_priv_no_ticket=9, inj_priv_blank_ticket=0, inj_orphans=5, inj_bursts=4, inj_bursts_with_success=2,
    unique_name_initials=False, decoys=False,
)

ENTERPRISE = replace(
    BASELINE, name="enterprise", seed=2026, n_employees=2000, n_leavers=240,
    dept_systems={**_BASE_SYSTEMS, "Finance": ["ERP", "Treasury"], "IT": ["ERP", "Core DB", "Cloud IAM"]},
    normal_roles={**_BASE_NORMAL, "Treasury": ["Viewer", "Initiator"], "Cloud IAM": ["Read Only", "Developer"]},
    privileged_roles={**_BASE_PRIV, "Treasury": ["Payment Approver", "Treasury Admin"], "Cloud IAM": ["Cloud Admin"]},
    sod_rules=_BASE_SOD + (
        ("Treasury", "Initiator", "Payment Approver", "Can initiate and approve the same payment"),
        ("Cloud IAM", "Developer", "Cloud Admin", "Can write code and change production cloud permissions"),
    ),
    shared_accounts=_BASE_SHARED + (("treasury_ops", "Treasury", "Treasury Admin"), ("cloud_root", "Cloud IAM", "Cloud Admin")),
    inj_leavers_active=70, inj_leavers_login_after=25, inj_dormant=150, inj_never_used=20, inj_sod=50,
    inj_priv_no_ticket=45, inj_priv_blank_ticket=3, inj_orphans=25, inj_bursts=20, inj_bursts_with_success=10,
    unique_name_initials=True, decoys=True,
)

PROFILES = {"baseline": BASELINE, "enterprise": ENTERPRISE}


@dataclass
class GeneratedData:
    hr: pd.DataFrame
    access: pd.DataFrame
    sod: pd.DataFrame
    events: pd.DataFrame
    manifest: pd.DataFrame
    meta: dict


def _uname(name: str) -> str:
    return name.lower().replace(" ", ".")


def generate(profile: str | Profile = "baseline", seed: int | None = None,
             review_date: date = date(2026, 9, 30)) -> GeneratedData:
    prof = PROFILES[profile] if isinstance(profile, str) else profile
    if seed is not None:
        prof = replace(prof, seed=seed)
    rng = random.Random(prof.seed)
    review = review_date

    def rand_date(start: date, end: date, r: random.Random = rng) -> date:
        return start + timedelta(days=r.randint(0, (end - start).days))

    priv_set = {(s, r) for s, roles in prof.privileged_roles.items() for r in roles}

    # ------------------------------------------------------------------ 1. HR master
    if prof.unique_name_initials:
        pairs = [(f"{f} {c}", last) for f in FIRST for c in string.ascii_uppercase for last in LAST]
    else:
        pairs = [(f, last) for f in FIRST for last in LAST]
    rng.shuffle(pairs)
    pairs = pairs[:prof.n_employees]

    depts = list(prof.dept_weights)
    weights = [prof.dept_weights[d] for d in depts]
    hr_rows = []
    for i, (f, last) in enumerate(pairs, start=1):
        dept = rng.choices(depts, weights)[0]
        hr_rows.append({
            "employee_id": f"E{i:04d}", "name": f"{f} {last}", "department": dept,
            "job_title": rng.choice(prof.dept_titles[dept]),
            "hire_date": rand_date(*prof.hire_window).isoformat(),
            "status": "Active", "termination_date": None,
        })
    hr = pd.DataFrame(hr_rows)

    for idx in rng.sample(range(prof.n_employees), prof.n_leavers):
        hr.loc[idx, "status"] = "Terminated"
        hr.loc[idx, "termination_date"] = rand_date(*prof.leaver_window).isoformat()

    # ------------------------------------------------------------------ 2. user access
    access_rows: list[dict] = []
    aid = [0]

    def add_access(r: random.Random, employee_id, username, system, role, account_type="Individual",
                   status="Active", granted=None, ticket="auto", last_login="auto") -> None:
        aid[0] += 1
        is_priv = 1 if (system, role) in priv_set else 0
        if ticket == "auto":
            ticket = f"CHG-{r.randint(10000, 99999)}"
        if granted is None:
            granted = rand_date(date(2023, 1, 1), date(2026, 3, 31), r).isoformat()
        if last_login == "auto":
            last_login = (review - timedelta(days=r.randint(0, 60))).isoformat()
        access_rows.append({
            "access_id": f"A{aid[0]:05d}", "employee_id": employee_id, "username": username,
            "system": system, "role": role, "is_privileged": is_priv, "account_type": account_type,
            "account_status": status, "granted_date": granted, "approval_ticket": ticket,
            "last_login_date": last_login,
        })

    for _, e in hr.iterrows():
        for system in prof.dept_systems[e["department"]]:
            if rng.random() < 0.04:  # 4%: a normal user legitimately holds a privileged role (with approval)
                role = rng.choice(prof.privileged_roles[system])
            else:
                role = rng.choice(prof.normal_roles[system])
            if e["status"] == "Terminated":  # normal leaver handling: disabled, last login before exit
                term = date.fromisoformat(e["termination_date"])
                add_access(rng, e["employee_id"], _uname(e["name"]), system, role, status="Disabled",
                           last_login=(term - timedelta(days=rng.randint(0, 10))).isoformat())
            else:
                add_access(rng, e["employee_id"], _uname(e["name"]), system, role)

    access = pd.DataFrame(access_rows)
    access["_tag"] = ""  # internal bookkeeping for the manifest; never written to the CSV

    def tag(idx, code: str) -> None:
        access.loc[idx, "_tag"] = access.loc[idx, "_tag"] + code + ";"

    # --- PLANTED 1 (T1): leavers whose accounts are still Active; the first N also logged in AFTER leaving
    term_ids = hr.index[hr["status"] == "Terminated"].tolist()
    for k, hidx in enumerate(rng.sample(term_ids, prof.inj_leavers_active)):
        eid = hr.loc[hidx, "employee_id"]
        term = date.fromisoformat(hr.loc[hidx, "termination_date"])
        ridx = access.index[access["employee_id"] == eid][0]
        access.loc[ridx, "account_status"] = "Active"
        if k < prof.inj_leavers_login_after:
            after = min(review, term + timedelta(days=rng.randint(5, 40)))
            access.loc[ridx, "last_login_date"] = after.isoformat()
        tag(ridx, "T1")

    # --- PLANTED 2 (T2): dormant accounts (100-400 days idle) and never-used accounts
    active_rows = access.index[(access["account_status"] == "Active") &
                               (access["employee_id"].isin(hr.loc[hr["status"] == "Active", "employee_id"]))].tolist()
    dormant = rng.sample(active_rows, prof.inj_dormant + prof.inj_never_used)
    for j, ridx in enumerate(dormant):
        if j < prof.inj_dormant:
            access.loc[ridx, "last_login_date"] = (review - timedelta(days=rng.randint(100, 400))).isoformat()
        else:
            access.loc[ridx, "last_login_date"] = None
        tag(ridx, "T2")

    # --- PLANTED 3 (T3): Segregation-of-Duties conflicts (one person holds BOTH roles of a rule)
    pool = hr[hr["status"] == "Active"]["employee_id"].tolist()
    sod_planted: list[tuple[str, str, str, str]] = []
    for k, eid in enumerate(rng.sample(pool, prof.inj_sod)):
        system, ra, rb, _ = prof.sod_rules[k % len(prof.sod_rules)]
        name = hr.loc[hr["employee_id"] == eid, "name"].iloc[0]
        access = access[~((access["employee_id"] == eid) & (access["system"] == system))]
        for role in (ra, rb):
            add_access(rng, eid, _uname(name), system, role)
        access = pd.concat([access, pd.DataFrame(access_rows[-2:]).assign(_tag="T3;")], ignore_index=True)
        sod_planted.append((eid, system, ra, rb))

    # --- PLANTED 4 (T4): privileged access with no approval ticket (and, in enterprise, whitespace-only tickets)
    priv_idx = access.index[(access["is_privileged"] == 1) & (access["account_status"] == "Active")].tolist()
    chosen = rng.sample(priv_idx, min(prof.inj_priv_no_ticket + prof.inj_priv_blank_ticket, len(priv_idx)))
    for j, ridx in enumerate(chosen):
        access.loc[ridx, "approval_ticket"] = None if j < prof.inj_priv_no_ticket else "   "
        tag(ridx, "T4")

    # --- PLANTED 5 (T5): shared/generic accounts with no named owner, and orphan accounts with no HR record
    new: list[dict] = []
    for u, s, r in prof.shared_accounts:
        aid[0] += 1
        new.append({"access_id": f"A{aid[0]:05d}", "employee_id": None, "username": u, "system": s, "role": r,
                    "is_privileged": 1 if (s, r) in priv_set else 0, "account_type": "Shared",
                    "account_status": "Active",
                    "granted_date": rand_date(date(2020, 1, 1), date(2023, 1, 1)).isoformat(),
                    "approval_ticket": f"CHG-{rng.randint(10000, 99999)}",
                    "last_login_date": (review - timedelta(days=rng.randint(0, 30))).isoformat(), "_tag": "T5;"})
    systems = list(prof.normal_roles)
    for k in range(prof.inj_orphans):
        aid[0] += 1
        new.append({"access_id": f"A{aid[0]:05d}", "employee_id": f"E9{k:03d}", "username": f"temp.user{k + 1}",
                    "system": rng.choice(systems), "role": "Viewer" if k % 2 else "Data Entry",
                    "is_privileged": 0, "account_type": "Individual", "account_status": "Active",
                    "granted_date": rand_date(date(2022, 1, 1), date(2024, 12, 31)).isoformat(),
                    "approval_ticket": None,
                    "last_login_date": (review - timedelta(days=rng.randint(0, 50))).isoformat(), "_tag": "T5;"})
    role_ok = {s: set(prof.normal_roles[s]) | set(prof.privileged_roles[s]) for s in prof.normal_roles}
    for row in new[len(prof.shared_accounts):]:
        if row["role"] not in role_ok[row["system"]]:
            row["role"] = prof.normal_roles[row["system"]][0]
    access = pd.concat([access, pd.DataFrame(new)], ignore_index=True)
    access["_tag"] = access["_tag"].fillna("")

    # --- Enterprise only: decoys, built with a SEPARATE random stream so the baseline sequence is untouched
    decoy_sod: list[tuple[str, str, str, str]] = []
    decoy_bursts: list[tuple[str, str]] = []
    extra_burst_planted: list[tuple[str, str]] = []
    decoy_plan: dict = {}
    if prof.decoys:
        r2 = random.Random(prof.seed + 1000)
        emp_active = set(hr.loc[hr["status"] == "Active", "employee_id"])
        clean = access[(access["_tag"] == "") & (access["account_status"] == "Active") &
                       (access["account_type"] == "Individual") & access["employee_id"].isin(emp_active)]
        pool_idx = list(clean.index)
        r2.shuffle(pool_idx)

        # D2: logins exactly 90 days ago (boundary: NOT dormant, rule is "more than 90") and 89 days ago
        for n, ridx in enumerate(pool_idx[:24]):
            access.loc[ridx, "last_login_date"] = (review - timedelta(days=90 if n < 12 else 89)).isoformat()
            tag(ridx, "D2")
        pool_idx = pool_idx[24:]

        # D1b: exit dated AFTER the review date -> not yet a leaver; their active accounts must not be flagged
        emp_clean = [e for e in access.loc[pool_idx, "employee_id"].unique()
                     if (access.loc[access["employee_id"] == e, "_tag"] == "").all()]
        future_emps = emp_clean[:6]
        for e in future_emps:
            h = hr.index[hr["employee_id"] == e][0]
            hr.loc[h, "status"] = "Terminated"
            hr.loc[h, "termination_date"] = (review + timedelta(days=10)).isoformat()
            tag(access.index[access["employee_id"] == e], "D1")
        pool_idx = [i for i in pool_idx if access.loc[i, "employee_id"] not in set(future_emps)]

        # D1a: disabled leaver account whose last login is the termination day itself (not "after")
        disabled_leavers = access[(access["account_status"] == "Disabled") & (access["_tag"] == "")]
        for ridx in list(disabled_leavers.index)[:10]:
            term = hr.loc[hr["employee_id"] == access.loc[ridx, "employee_id"], "termination_date"].iloc[0]
            access.loc[ridx, "last_login_date"] = term
            tag(ridx, "D1")

        # D3: holds both SoD roles, but one of the two accounts is Disabled -> not a live conflict
        cand_emps = [e for e in access.loc[pool_idx, "employee_id"].unique()]
        for n, e in enumerate(cand_emps[:10]):
            system, ra, rb, _ = prof.sod_rules[n % len(prof.sod_rules)]
            same = access[(access["employee_id"] == e) & (access["system"] == system)]
            if (same["_tag"] != "").any():
                continue
            name = hr.loc[hr["employee_id"] == e, "name"].iloc[0]
            access = access[~((access["employee_id"] == e) & (access["system"] == system))]
            add_access(r2, e, _uname(name), system, ra)
            add_access(r2, e, _uname(name), system, rb, status="Disabled")
            access = pd.concat([access, pd.DataFrame(access_rows[-2:]).assign(_tag="D3;")], ignore_index=True)
            decoy_sod.append((e, system, ra, rb))
        access["_tag"] = access["_tag"].fillna("")
        decoy_plan = {"D1": "future-dated exits and same-day last login", "D2": "idle exactly 90 / 89 days",
                      "D3": "SoD roles with one account disabled", "D6": "sub-threshold or slow failed-login patterns"}
        # remaining clean accounts are reused below for the login-event decoys
        clean_after = access[(access["_tag"] == "") & (access["account_status"] == "Active") &
                             (access["account_type"] == "Individual") & access["employee_id"].isin(emp_active)]
        burst_pool = list(clean_after.index)
        r2.shuffle(burst_pool)
    else:
        r2 = None
        burst_pool = []

    access["access_id"] = [f"A{i:05d}" for i in range(1, len(access) + 1)]  # clean sequential IDs

    # ------------------------------------------------------------------ 3. login events
    events = []
    base_start = datetime.combine(review - timedelta(days=90), datetime.min.time())
    for _, a in access.iterrows():
        if a["account_status"] != "Active" or a["account_type"] == "Service":
            continue
        if a["last_login_date"] is None or pd.isna(a["last_login_date"]):
            continue
        last = datetime.combine(date.fromisoformat(a["last_login_date"]), datetime.min.time())
        if last < base_start:
            continue  # dormant: nothing in the 90-day window
        for _ in range(rng.randint(2, 10)):
            t = base_start + (last - base_start) * rng.random() + timedelta(hours=rng.randint(8, 19),
                                                                            minutes=rng.randint(0, 59))
            res = "Failed" if rng.random() < 0.04 else "Success"
            events.append((a["username"], a["system"], t.strftime("%Y-%m-%d %H:%M:%S"), res))
        events.append((a["username"], a["system"], (last + timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S"), "Success"))

    # --- PLANTED 6 (T6): brute-force bursts; the first N end with a successful login (possible compromise)
    cands = access[(access["account_status"] == "Active") & (access["account_type"] == "Individual") &
                   (access["employee_id"].isin(hr["employee_id"]))].sample(prof.inj_bursts, random_state=prof.seed)
    burst_planted: list[tuple[str, str]] = []
    for k, (_, a) in enumerate(cands.iterrows()):
        t0 = datetime.combine(review - timedelta(days=rng.randint(3, 40)), datetime.min.time()) + \
            timedelta(hours=rng.randint(0, 5), minutes=rng.randint(0, 59))
        for j in range(rng.randint(8, 20)):
            events.append((a["username"], a["system"], (t0 + timedelta(seconds=25 * j)).strftime("%Y-%m-%d %H:%M:%S"), "Failed"))
        if k < prof.inj_bursts_with_success:
            events.append((a["username"], a["system"], (t0 + timedelta(minutes=9)).strftime("%Y-%m-%d %H:%M:%S"), "Success"))
        burst_planted.append((a["username"], a["system"]))

    if prof.decoys and r2 is not None:
        used = set(burst_planted)
        picks = [(access.loc[i, "username"], access.loc[i, "system"]) for i in burst_pool]
        picks = [p for p in dict.fromkeys(picks) if p not in used]

        def stamp(base: datetime, seconds: int) -> str:
            return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")

        def when() -> datetime:
            return datetime.combine(review - timedelta(days=r2.randint(3, 40)), datetime.min.time()) + \
                timedelta(hours=r2.randint(0, 5), minutes=r2.randint(0, 59))

        # D6a: 4 failures inside a minute (threshold is 5) -> must NOT be flagged
        for u, s in picks[:6]:
            t0 = when()
            events += [(u, s, stamp(t0, 20 * j), "Failed") for j in range(4)]
            decoy_bursts.append((u, s))
        # D6b: 10 failures but 3 minutes apart -> never more than 4 inside any 10-minute window -> NOT flagged
        for u, s in picks[6:10]:
            t0 = when()
            events += [(u, s, stamp(t0, 180 * j), "Failed") for j in range(10)]
            decoy_bursts.append((u, s))
        # PLANTED (T6): burst of 6 followed by a success 45 min later (outside the 30-min look-ahead):
        # flagged as a burst, but NOT as "success after burst"
        for u, s in picks[10:13]:
            t0 = when()
            events += [(u, s, stamp(t0, 20 * j), "Failed") for j in range(6)]
            events.append((u, s, stamp(t0, 45 * 60), "Success"))
            extra_burst_planted.append((u, s))

    ev = pd.DataFrame(events, columns=["username", "system", "event_time", "result"])
    ev = ev.sort_values("event_time").reset_index(drop=True)
    ev.insert(0, "event_id", [f"L{i:06d}" for i in range(1, len(ev) + 1)])

    # ------------------------------------------------------------------ 4. manifest + outputs
    rows = []
    kinds = {"T1": ("T1", "planted"), "T2": ("T2", "planted"), "T4": ("T4", "planted"), "T5": ("T5", "planted"),
             "D1": ("T1", "decoy"), "D2": ("T2", "decoy")}
    for _, a in access.iterrows():
        for code in [c for c in a["_tag"].split(";") if c in kinds]:
            cid, kind = kinds[code]
            rows.append({"control_id": cid, "key": a["access_id"], "kind": kind})
    rows += [{"control_id": "T3", "key": "|".join(t), "kind": "planted"} for t in sod_planted]
    rows += [{"control_id": "T3", "key": "|".join(t), "kind": "decoy"} for t in decoy_sod]
    rows += [{"control_id": "T6", "key": "|".join(t), "kind": "planted"} for t in burst_planted + extra_burst_planted]
    rows += [{"control_id": "T6", "key": "|".join(t), "kind": "decoy"} for t in decoy_bursts]
    manifest = pd.DataFrame(rows, columns=["control_id", "key", "kind"]).sort_values(["control_id", "kind", "key"]).reset_index(drop=True)

    access = access.drop(columns=["_tag"])
    sod = pd.DataFrame(prof.sod_rules, columns=["system", "role_a", "role_b", "risk_description"])
    meta = {
        "profile": prof.name, "seed": prof.seed, "review_date": review.isoformat(),
        "generator_version": __version__, "data_is_simulated": True,
        "rows": {"hr_master": len(hr), "user_access": len(access), "sod_rules": len(sod), "login_events": len(ev)},
        "planted_requested": {
            "T1": prof.inj_leavers_active, "T2": prof.inj_dormant + prof.inj_never_used, "T3": prof.inj_sod,
            "T4": prof.inj_priv_no_ticket + prof.inj_priv_blank_ticket,
            "T5": len(prof.shared_accounts) + prof.inj_orphans,
            "T6": prof.inj_bursts + (3 if prof.decoys else 0),
        },
        "manifest_counts": {f"{c}/{k}": int(n) for (c, k), n in manifest.groupby(["control_id", "kind"]).size().items()},
        "decoy_types": decoy_plan,
    }
    return GeneratedData(hr, access, sod, ev, manifest, meta)


def write_dataset(gd: GeneratedData, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gd.hr.to_csv(out / "hr_master.csv", index=False)
    gd.access.to_csv(out / "user_access.csv", index=False)
    gd.sod.to_csv(out / "sod_rules.csv", index=False)
    gd.events.to_csv(out / "login_events.csv", index=False)
    gd.manifest.to_csv(out / "ground_truth.csv", index=False)
    (out / "meta.json").write_text(json.dumps(gd.meta, indent=2), encoding="utf-8")
    return out
