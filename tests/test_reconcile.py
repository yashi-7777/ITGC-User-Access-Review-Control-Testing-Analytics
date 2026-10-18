"""Prove the reconciliation can actually FAIL: break one implementation on purpose and expect a mismatch."""

from __future__ import annotations

import dataclasses

import pandas as pd

from conftest import acc, build, emp
from itgc_uar import controls
from itgc_uar.engine import run_controls
from itgc_uar.reconcile import reconcile


def test_identical_results_match(cfg):
    ds = build([emp("E1")], [acc("A1", "E1", last="2025-01-01")]).coerced()
    spec = controls.CONTROLS["T2"]
    a = spec.detect_pandas(ds, cfg)
    res = reconcile(spec, a, a.copy(), 1, 1)
    assert res.ok and res.matched_keys == 1


def test_missing_row_extra_row_attribute_and_population_differences_are_all_detected(cfg):
    ds = build([emp("E1"), emp("E2")], [acc("A1", "E1", last="2025-01-01"), acc("A2", "E2", last="2025-02-01")]).coerced()
    spec = controls.CONTROLS["T2"]
    a = spec.detect_pandas(ds, cfg)
    assert len(a) == 2
    r = reconcile(spec, a, a.iloc[:1], 2, 2)
    assert r.status == "MISMATCH" and r.only_sql == ["A2"]
    r = reconcile(spec, a.iloc[:1], a, 2, 2)
    assert r.only_pandas == ["A2"]
    b = a.copy()
    b.loc[0, "days_inactive"] = 5
    r = reconcile(spec, a, b, 2, 2)
    assert r.attribute_mismatches and "days_inactive" in r.attribute_mismatches[0]
    assert reconcile(spec, a, a, 2, 3).status == "MISMATCH"


def test_a_bug_in_the_pandas_twin_is_caught_by_the_engine(cfg, monkeypatch):
    ds = build([emp("E1")], [acc("A1", "E1", last="2025-01-01")])
    good = controls.CONTROLS["T2"]

    def buggy(ds_, cfg_):  # off-by-N: pretends nothing is dormant
        return good.detect_pandas(ds_, cfg_).iloc[0:0]

    monkeypatch.setitem(controls.CONTROLS, "T2", dataclasses.replace(good, detect_pandas=buggy))
    res = run_controls(ds, cfg)
    assert not res.reconciled and res.recon["T2"].only_sql == ["A1"]
    assert res.summary.set_index("control_id").loc["T2", "reconciliation"] == "MISMATCH"


def test_a_bug_in_the_sql_is_caught_by_the_engine(cfg, monkeypatch):
    ds = build([emp("E1")], [acc("A1", "E1", last="2026-07-02")])  # exactly 90 days idle: NOT dormant
    good = controls.CONTROLS["T2"]
    broken_sql = good.sql().replace("> :dormant_days", ">= :dormant_days")  # classic off-by-one
    assert broken_sql != good.sql()
    spec = dataclasses.replace(good)
    object.__setattr__(spec, "sql", lambda: broken_sql)  # frozen dataclass: override the method on this copy
    monkeypatch.setitem(controls.CONTROLS, "T2", spec)
    res = run_controls(ds, cfg)
    assert not res.reconciled and res.recon["T2"].only_sql == ["A1"]


def test_pandas_frames_unaffected_types(cfg):
    ds = build([emp("E1")], [acc("A1", "E1", last=None)]).coerced()
    out = controls.CONTROLS["T2"].detect_pandas(ds, cfg)
    assert pd.isna(out.loc[0, "days_inactive"])
