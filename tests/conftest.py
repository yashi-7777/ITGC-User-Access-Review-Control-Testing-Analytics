"""Shared fixtures. Tiny hand-built datasets let each test control exactly one boundary condition."""

from __future__ import annotations

import pandas as pd
import pytest

from itgc_uar.config import load_config
from itgc_uar.datagen import generate, write_dataset
from itgc_uar.dataset import Dataset
from itgc_uar.engine import run_controls

HR_COLS = ["employee_id", "name", "department", "job_title", "hire_date", "status", "termination_date"]
UA_COLS = ["access_id", "employee_id", "username", "system", "role", "is_privileged", "account_type",
           "account_status", "granted_date", "approval_ticket", "last_login_date"]
EV_COLS = ["event_id", "username", "system", "event_time", "result"]
SOD = pd.DataFrame([("ERP", "Vendor Master Admin", "Payment Release", "fraud risk")],
                   columns=["system", "role_a", "role_b", "risk_description"])


def emp(eid="E1", status="Active", term=None, dept="Finance"):
    return dict(employee_id=eid, name=f"Person {eid}", department=dept, job_title="Analyst",
                hire_date="2020-01-01", status=status, termination_date=term)


def acc(aid="A1", eid="E1", system="ERP", role="Viewer", priv="0", typ="Individual", status="Active",
        ticket="CHG-1", last="2026-09-20", user=None):
    return dict(access_id=aid, employee_id=eid, username=user or f"user{eid}", system=system, role=role,
                is_privileged=priv, account_type=typ, account_status=status, granted_date="2025-01-01",
                approval_ticket=ticket, last_login_date=last)


def ev(i, user, time, result, system="ERP"):
    return dict(event_id=f"L{i:04d}", username=user, system=system, event_time=time, result=result)


def build(hr=(), access=(), events=(), sod=SOD):
    hr_df = pd.DataFrame(list(hr) or [emp("E0")], columns=HR_COLS)
    ua_df = pd.DataFrame(list(access) or [acc("A0", "E0")], columns=UA_COLS)
    ev_df = pd.DataFrame(list(events) or [ev(0, "userE0", "2026-09-01 10:00:00", "Success")], columns=EV_COLS)
    return Dataset.from_frames(hr_df, ua_df, sod.copy(), ev_df)


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def run(cfg):
    def _run(ds, config=None):
        return run_controls(ds, config or cfg)
    return _run


@pytest.fixture(scope="session")
def baseline_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("baseline")
    write_dataset(generate("baseline"), d)
    return d


@pytest.fixture(scope="session")
def enterprise_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("enterprise")
    write_dataset(generate("enterprise"), d)
    return d


@pytest.fixture(scope="session")
def baseline_result(baseline_dir):
    return run_controls(Dataset.from_dir(baseline_dir), load_config())


@pytest.fixture(scope="session")
def enterprise_result(enterprise_dir):
    return run_controls(Dataset.from_dir(enterprise_dir), load_config())
