from __future__ import annotations

import pandas as pd
import pytest
from conftest import acc, build, emp, ev

from itgc_uar.dataquality import run_data_quality
from itgc_uar.engine import DataQualityError, run_controls


def failed(report, cid):
    return sum(c.failed for c in report.checks if c.check_id == cid and c.severity != "INFO")


def test_clean_data_passes(cfg):
    r = run_data_quality(build([emp("E1")], [acc("A1", "E1")], [ev(1, "userE1", "2026-09-01 10:00:00", "Success")]), cfg)
    assert r.ok and r.warnings == 0


def test_duplicate_and_missing_keys(cfg):
    r = run_data_quality(build([emp("E1")], [acc("A1", "E1"), acc("A1", "E1")]), cfg)
    assert not r.ok and failed(r, "DQ03") == 1


def test_bad_enums_and_dates(cfg):
    ds = build([emp("E1")], [acc("A1", "E1", status="Locked", last="2026-13-45")])
    r = run_data_quality(ds, cfg)
    assert failed(r, "DQ04") >= 1 and failed(r, "DQ05") >= 1


def test_terminated_without_date_and_future_dates(cfg):
    ds = build([emp("E1", "Terminated", None), emp("E2", "Terminated", "2026-12-01"), emp("E3", "Active", "2026-01-01")],
               [acc("A1", "E1"), acc("A2", "E2", last="2027-01-01")])
    r = run_data_quality(ds, cfg)
    assert not r.ok and failed(r, "DQ06") >= 3


def test_missing_column_stops_early(cfg):
    ds = build([emp("E1")], [acc("A1", "E1")])
    ds.access = ds.access.drop(columns=["approval_ticket"])
    r = run_data_quality(ds, cfg)
    assert not r.ok and failed(r, "DQ01") == 1


def test_event_without_account_warns(cfg):
    ds = build([emp("E1")], [acc("A1", "E1")], [ev(1, "stranger", "2026-09-01 10:00:00", "Failed")])
    r = run_data_quality(ds, cfg)
    assert r.ok and r.warnings >= 1


def test_errors_stop_the_engine_unless_forced(cfg):
    ds = build([emp("E1")], [acc("A1", "E1", status="Locked")])
    with pytest.raises(DataQualityError):
        run_controls(ds, cfg)
    assert run_controls(ds, cfg, force=True) is not None


def test_sod_rule_pairing_same_role_is_error(cfg):
    ds = build([emp("E1")], [acc("A1", "E1")], sod=pd.DataFrame([("ERP", "X", "X", "bad")],
               columns=["system", "role_a", "role_b", "risk_description"]))
    assert not run_data_quality(ds, cfg).ok
