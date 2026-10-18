from __future__ import annotations

import pytest

from itgc_uar.config import ConfigError, load_config


def test_defaults_load_and_fingerprint_is_stable():
    a, b = load_config(), load_config()
    assert a.dormant_days == 90 and a.burst_threshold == 5
    assert a.fingerprint() == b.fingerprint() and len(a.fingerprint()) == 12


def test_override_changes_fingerprint():
    assert load_config().fingerprint() != load_config(overrides={"thresholds": {"dormant_days": 60}}).fingerprint()


@pytest.mark.parametrize("overrides", [
    {"thresholds": {"dormant_days": 0}},
    {"thresholds": {"dormant_days": "ninety"}},
    {"thresholds": {"burst_threshold": 1}},
    {"tolerable_deviation_rate": {"T1": 1.5}},
    {"scoring": {"priority_bands": {"P1": 40, "P2": 50, "P3": 30}}},
    {"scoring": {"base": {"T1": 500}}},
    {"remediation_sla_days": {"P1": 0}},
    {"review_date": "30/09/2026"},
    {"surprise_key": 1},
])
def test_invalid_config_is_rejected(overrides):
    with pytest.raises(ConfigError):
        load_config(overrides=overrides)


def test_yaml_file_overlay_and_errors(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("thresholds:\n  dormant_days: 45\n")
    assert load_config(p).dormant_days == 45
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")
    p.write_text("- just\n- a list\n")
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text("a: [unclosed")
    with pytest.raises(ConfigError):
        load_config(p)
