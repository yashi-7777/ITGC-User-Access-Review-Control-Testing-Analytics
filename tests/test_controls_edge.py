"""Hand-built boundary cases. Each test builds a few rows, runs BOTH SQL and pandas (via the engine) and checks
the expected exception set - so a boundary bug in either implementation, or a disagreement, fails here."""

from __future__ import annotations

from conftest import acc, build, emp, ev


def keys(result, cid):
    return set(result.register.loc[result.register["control_id"] == cid, "entity_key"])


def assert_reconciled(result):
    assert result.reconciled, {k: r.examples() for k, r in result.recon.items() if not r.ok}


# ------------------------------------------------------------------ T1 leavers
def test_t1_active_account_of_leaver_is_flagged(run):
    r = run(build([emp("E1", "Terminated", "2026-06-01")], [acc("A1", "E1")]))
    assert keys(r, "T1") == {"A1"}
    assert_reconciled(r)


def test_t1_disabled_account_and_active_employee_are_not_flagged(run):
    r = run(build([emp("E1", "Terminated", "2026-06-01"), emp("E2")],
                  [acc("A1", "E1", status="Disabled"), acc("A2", "E2")]))
    assert keys(r, "T1") == set()


def test_t1_login_on_exit_day_is_not_after_but_next_day_is(run):
    r = run(build([emp("E1", "Terminated", "2026-06-01"), emp("E2", "Terminated", "2026-06-01")],
                  [acc("A1", "E1", last="2026-06-01"), acc("A2", "E2", last="2026-06-02")]))
    sev = r.register.set_index("entity_key")
    assert sev.loc["A1", "exception_type"] == "Leaver - access not removed"
    assert sev.loc["A2", "exception_type"] == "Leaver - login after exit date"
    assert sev.loc["A1", "severity"] == "Medium" and sev.loc["A2", "severity"] == "High"
    assert_reconciled(r)


def test_t1_future_dated_exit_is_not_a_leaver_yet(run):
    r = run(build([emp("E1", "Terminated", "2026-10-15")], [acc("A1", "E1")]))
    assert keys(r, "T1") == set()
    assert keys(r, "T2") == set()  # and not reported as dormant either, last login is recent
    assert_reconciled(r)


def test_t1_exit_on_review_date_counts(run):
    r = run(build([emp("E1", "Terminated", "2026-09-30")], [acc("A1", "E1")]))
    assert keys(r, "T1") == {"A1"}


# ------------------------------------------------------------------ T2 dormant
def test_t2_boundary_90_days_ok_91_flagged(run):
    # review date 2026-09-30: 2026-07-02 is exactly 90 days earlier, 2026-07-01 is 91
    r = run(build([emp("E1"), emp("E2"), emp("E3")],
                  [acc("A1", "E1", last="2026-07-02"), acc("A2", "E2", last="2026-07-01"), acc("A3", "E3", last="2026-07-03")]))
    assert keys(r, "T2") == {"A2"}
    assert_reconciled(r)


def test_t2_never_used_flagged_and_typed(run):
    r = run(build([emp("E1")], [acc("A1", "E1", last=None)]))
    row = r.register.iloc[0]
    assert row["exception_type"] == "Dormant - never used" and row["severity"] == "Low"
    assert_reconciled(r)


def test_t2_ignores_shared_service_disabled_and_leavers(run):
    r = run(build([emp("E1"), emp("E2", "Terminated", "2026-01-01")],
                  [acc("A1", None, typ="Shared", last="2020-01-01"), acc("A2", "E1", status="Disabled", last="2020-01-01"),
                   acc("A3", "E1", typ="Service", last="2020-01-01"), acc("A4", "E2", last="2020-01-01")]))
    assert keys(r, "T2") == set()  # shared, disabled and service accounts are out of scope for T2
    assert keys(r, "T1") == {"A4"}  # the leaver is reported by T1 instead


def test_t2_privileged_dormant_is_medium(run):
    r = run(build([emp("E1")], [acc("A1", "E1", role="DBA", system="Core DB", priv="1", last="2025-01-01")]))
    assert r.register.iloc[0]["severity"] == "Medium"


def test_t2_threshold_is_configurable(run, cfg):
    from itgc_uar.config import load_config
    ds = build([emp("E1")], [acc("A1", "E1", last="2026-08-30")])  # 31 days idle
    assert keys(run(ds), "T2") == set()
    assert keys(run(ds, load_config(overrides={"thresholds": {"dormant_days": 30}})), "T2") == {"A1"}


# ------------------------------------------------------------------ T3 SoD
def test_t3_both_roles_active_flagged_once(run):
    r = run(build([emp("E1")], [acc("A1", "E1", role="Vendor Master Admin", priv="1"),
                                acc("A2", "E1", role="Payment Release", priv="1")]))
    assert keys(r, "T3") == {"E1|ERP|Vendor Master Admin|Payment Release"}
    assert r.register.iloc[0]["severity"] == "High"
    assert_reconciled(r)


def test_t3_one_disabled_role_is_not_a_conflict(run):
    r = run(build([emp("E1")], [acc("A1", "E1", role="Vendor Master Admin", priv="1"),
                                acc("A2", "E1", role="Payment Release", priv="1", status="Disabled")]))
    assert keys(r, "T3") == set()


def test_t3_roles_on_different_systems_or_people_are_fine(run):
    r = run(build([emp("E1"), emp("E2")],
                  [acc("A1", "E1", role="Vendor Master Admin", priv="1"),
                   acc("A2", "E2", role="Payment Release", priv="1"),
                   acc("A3", "E1", system="HRMS", role="Payment Release", priv="1")]))
    assert keys(r, "T3") == set()


def test_t3_duplicate_grant_of_same_role_is_one_exception(run):
    r = run(build([emp("E1")], [acc("A1", "E1", role="Vendor Master Admin", priv="1"),
                                acc("A2", "E1", role="Vendor Master Admin", priv="1"),
                                acc("A3", "E1", role="Payment Release", priv="1")]))
    assert len(keys(r, "T3")) == 1
    assert_reconciled(r)


# ------------------------------------------------------------------ T4 privileged approval
def test_t4_null_empty_and_whitespace_tickets_flagged(run):
    rows = [acc("A1", "E1", priv="1", ticket=None), acc("A2", "E2", priv="1", ticket=""),
            acc("A3", "E3", priv="1", ticket="   "), acc("A4", "E4", priv="1", ticket="\t \n"),
            acc("A5", "E5", priv="1", ticket="CHG-1"), acc("A6", "E6", priv="0", ticket=None)]
    r = run(build([emp(f"E{i}") for i in range(1, 7)], rows))
    # NULL, empty, spaces-only and tab/newline-only tickets are all "no approval"; a real ticket and a
    # non-privileged role are not exceptions.
    assert keys(r, "T4") == {"A1", "A2", "A3", "A4"}
    assert_reconciled(r)


def test_t4_blank_vs_missing_have_different_types(run):
    r = run(build([emp("E1"), emp("E2")], [acc("A1", "E1", priv="1", ticket=None), acc("A2", "E2", priv="1", ticket="  ")]))
    t = r.register.set_index("entity_key")["exception_type"]
    assert t["A1"] == "Privileged access - no approval ticket" and t["A2"] == "Privileged access - ticket is blank"


def test_t4_critical_role_scores_higher(run):
    r = run(build([emp("E1"), emp("E2")],
                  [acc("A1", "E1", role="DBA", system="Core DB", priv="1", ticket=None),
                   acc("A2", "E2", role="Payment Release", priv="1", ticket=None)]))
    s = r.register.set_index("entity_key")["risk_score"]
    assert s["A1"] == s["A2"] + 10


# ------------------------------------------------------------------ T5 ownership
def test_t5_shared_and_orphan(run):
    r = run(build([emp("E1")], [acc("A1", None, typ="Shared", user="shared1"), acc("A2", "E999", user="ghost"),
                                acc("A3", "E1"), acc("A4", None, typ="Shared", status="Disabled", user="old")]))
    assert keys(r, "T5") == {"A1", "A2"}
    types = r.register.set_index("entity_key")["exception_type"]
    assert types["A1"] == "Shared / generic account" and types["A2"] == "Orphan account"
    assert_reconciled(r)


def test_t5_privileged_shared_is_high(run):
    r = run(build([emp("E1")], [acc("A1", None, typ="Shared", role="System Admin", priv="1", user="adm")]))
    assert r.register.iloc[0]["severity"] == "High"


# ------------------------------------------------------------------ T6 failed-login bursts
def burst(user, start_min, n, gap_s, result="Failed", i0=0):
    base = 10 * 60 + start_min
    return [ev(i0 + k, user, f"2026-09-10 {(base + (k * gap_s) // 60) // 60:02d}:{(base + (k * gap_s) // 60) % 60:02d}:{(k * gap_s) % 60:02d}", result)
            for k in range(n)]


def test_t6_threshold_4_not_flagged_5_flagged(run):
    ds = build([emp("E1"), emp("E2")], [acc("A1", "E1"), acc("A2", "E2")],
               burst("userE1", 0, 4, 30) + burst("userE2", 0, 5, 30, i0=100))
    r = run(ds)
    assert keys(r, "T6") == {"userE2|ERP"}
    assert_reconciled(r)


def test_t6_window_is_exclusive_of_exactly_ten_minutes(run):
    # 5 failures spaced 150 s: first to fifth is 600 s = exactly 10 minutes -> NOT inside [t, t+10min)
    r = run(build([emp("E1")], [acc("A1", "E1")], burst("userE1", 0, 5, 150)))
    assert keys(r, "T6") == set()
    # spaced 149 s -> fits
    r2 = run(build([emp("E1")], [acc("A1", "E1")], burst("userE1", 0, 5, 149)))
    assert keys(r2, "T6") == {"userE1|ERP"}
    assert_reconciled(r2)


def test_t6_success_within_30_min_is_high_after_31_is_not(run):
    fails = burst("userE1", 0, 6, 20) + burst("userE2", 0, 6, 20, i0=100)
    succ = [ev(200, "userE1", "2026-09-10 10:29:00", "Success"), ev(201, "userE2", "2026-09-10 10:31:00", "Success")]
    r = run(build([emp("E1"), emp("E2")], [acc("A1", "E1"), acc("A2", "E2")], fails + succ))
    sev = r.register.set_index("entity_key")["severity"]
    assert sev["userE1|ERP"] == "High" and sev["userE2|ERP"] == "Medium"
    assert_reconciled(r)


def test_t6_failures_on_different_systems_do_not_combine(run):
    events = burst("userE1", 0, 3, 20) + [dict(ev(50 + k, "userE1", f"2026-09-10 10:00:{k * 20:02d}", "Failed", "HRMS")) for k in range(3)]
    r = run(build([emp("E1")], [acc("A1", "E1")], events))
    assert keys(r, "T6") == set()


# ------------------------------------------------------------------ scoring / priority
def test_score_is_capped_at_100_and_priority_bands(run, cfg):
    from itgc_uar.scoring import priority_for, total_score
    assert total_score([("a", 80), ("b", 80)]) == 100
    assert [priority_for(s, cfg) for s in (70, 69, 50, 49, 30, 29)] == ["P1", "P2", "P2", "P3", "P3", "P4"]


def test_due_date_follows_sla(run, cfg):
    r = run(build([emp("E1", "Terminated", "2026-01-01")], [acc("A1", "E1", role="System Admin", priv="1", last="2026-03-01")]))
    row = r.register.iloc[0]
    assert row["priority"] == "P1" and row["due_date"] == "2026-10-07"  # 2026-09-30 + 7 days


def test_clean_dataset_has_no_exceptions(run):
    r = run(build([emp("E1")], [acc("A1", "E1")]))
    assert r.total_exceptions == 0 and r.reconciled
    assert (r.summary["conclusion"] == "Effective").all()
