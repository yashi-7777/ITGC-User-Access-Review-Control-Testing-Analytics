from __future__ import annotations

import hashlib

import pandas as pd

from itgc_uar.datagen import generate, write_dataset

# SHA-256 prefixes of the four CSVs produced by the ORIGINAL v1 generator (seed 42), recorded before the rewrite.
V1_HASHES = {"hr_master.csv": "46ee7fce158f", "user_access.csv": "43dd8e600ffc",
             "sod_rules.csv": "49f0de0172e5", "login_events.csv": "ccfc7f190415"}


def test_baseline_reproduces_the_original_v1_files_byte_for_byte(baseline_dir):
    for name, prefix in V1_HASHES.items():
        assert hashlib.sha256((baseline_dir / name).read_bytes()).hexdigest().startswith(prefix), name


def test_generation_is_deterministic(tmp_path):
    a, b = generate("baseline"), generate("baseline")
    for t in ("hr", "access", "sod", "events", "manifest"):
        pd.testing.assert_frame_equal(getattr(a, t), getattr(b, t))


def test_seed_changes_data(tmp_path):
    assert not generate("baseline", seed=1).access.equals(generate("baseline", seed=2).access)


def test_baseline_shape_and_manifest_counts():
    g = generate("baseline")
    assert (len(g.hr), len(g.access), len(g.events), len(g.sod)) == (400, 482, 2840, 4)
    assert (g.hr["status"] == "Terminated").sum() == 48
    counts = g.meta["manifest_counts"]
    assert counts == {"T1/planted": 14, "T2/planted": 34, "T3/planted": 10, "T4/planted": 9, "T5/planted": 11, "T6/planted": 4}
    assert g.meta["data_is_simulated"] is True


def test_enterprise_shape_has_decoys_and_unique_names():
    g = generate("enterprise")
    assert len(g.hr) == 2000 and g.hr["name"].is_unique
    assert any(k.endswith("/decoy") for k in g.meta["manifest_counts"])
    assert g.access["username"].notna().all()


def test_hidden_tag_column_never_leaks_into_csv(baseline_dir):
    assert "_tag" not in pd.read_csv(baseline_dir / "user_access.csv").columns


def test_write_dataset_files(tmp_path):
    write_dataset(generate("baseline"), tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {"hr_master.csv", "user_access.csv", "sod_rules.csv",
                                                   "login_events.csv", "ground_truth.csv", "meta.json"}
