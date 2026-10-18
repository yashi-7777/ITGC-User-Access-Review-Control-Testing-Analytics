"""The v1 numbers must survive the rewrite, and detections must match the generator's ground truth."""

from __future__ import annotations

from itgc_uar.dataset import Dataset
from itgc_uar.validate import load_manifest, validate_against_manifest


def test_v1_headline_numbers(baseline_result):
    r = baseline_result
    assert r.total_exceptions == 82
    assert int((r.register["severity"] == "High").sum()) == 29
    assert r.reconciled and r.dq.ok
    ds = r.dataset
    assert (len(ds.hr), len(ds.access), len(ds.events)) == (400, 482, 2840)


def test_v1_per_control_breakdown(baseline_result):
    s = baseline_result.summary.set_index("control_id")
    expected = {"T1": (55, 14, 5, 9, 0), "T2": (435, 34, 0, 1, 33), "T3": (425, 10, 10, 0, 0),
                "T4": (30, 9, 9, 0, 0), "T5": (441, 11, 3, 8, 0), "T6": (386, 4, 2, 2, 0)}
    for cid, (pop, exc, hi, med, lo) in expected.items():
        row = s.loc[cid]
        assert (row["population"], row["exceptions"], row["high"], row["medium"], row["low"]) == (pop, exc, hi, med, lo), cid


def test_v1_department_totals(baseline_result):
    d = baseline_result.dept_matrix["Total"].to_dict()
    assert d == {"Sales": 22, "Operations": 17, "Finance": 13, "IT": 12,
                 "Unassigned (no owner / no HR record)": 11, "Procurement": 5, "HR": 2}
    assert sum(d.values()) == 82


def test_conclusions(baseline_result):
    s = baseline_result.summary.set_index("control_id")["conclusion"]
    assert (s == "Not effective").all()  # 34/435 = 7.8% > 5% tolerable for T2; every other control has exceptions


def test_every_planted_problem_found_and_no_decoy_flagged(baseline_result, enterprise_result, baseline_dir, enterprise_dir):
    for res, d in ((baseline_result, baseline_dir), (enterprise_result, enterprise_dir)):
        v = validate_against_manifest(res, load_manifest(d))
        assert (v["missed"] == 0).all(), v
        assert (v["decoys_wrongly_flagged"] == 0).all(), v
        assert (v["other_detections"] == 0).all(), v


def test_enterprise_reconciles_and_is_larger(enterprise_result):
    assert enterprise_result.reconciled
    assert enterprise_result.total_exceptions > 300


def test_validation_detects_a_missed_problem(baseline_result, baseline_dir):
    # Remove one detection from the register: the validator must report it as missed (it can fail).
    reg = baseline_result.register
    cut = reg[reg["control_id"] != "T4"].copy()
    import dataclasses
    broken = dataclasses.replace(baseline_result, register=cut)
    v = validate_against_manifest(broken, load_manifest(baseline_dir)).set_index("control_id")
    assert v.loc["T4", "missed"] == 9 and v.loc["T4", "recall"] == 0


def test_detections_are_stable_under_row_shuffle(baseline_dir):
    from itgc_uar.config import load_config
    from itgc_uar.engine import run_controls
    ds = Dataset.from_dir(baseline_dir)
    shuffled = Dataset.from_frames(ds.hr.sample(frac=1, random_state=1), ds.access.sample(frac=1, random_state=2),
                                   ds.sod, ds.events.sample(frac=1, random_state=3))
    a = run_controls(shuffled, load_config())
    assert a.total_exceptions == 82 and a.reconciled


def test_threshold_change_changes_results(baseline_dir):
    from itgc_uar.config import load_config
    from itgc_uar.engine import run_controls
    ds = Dataset.from_dir(baseline_dir)
    strict = run_controls(ds, load_config(overrides={"thresholds": {"dormant_days": 30}}))
    lax = run_controls(ds, load_config(overrides={"thresholds": {"dormant_days": 200}}))
    n = lambda r: int(r.summary.set_index("control_id").loc["T2", "exceptions"])  # noqa: E731
    assert n(strict) > 34 > n(lax)
    assert strict.reconciled and lax.reconciled
