"""The four input tables, loaded from CSV, fingerprinted, and exposed to SQL and pandas."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

TABLES = ("hr_master", "user_access", "sod_rules", "login_events")

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "hr_master": ["employee_id", "name", "department", "job_title", "hire_date", "status", "termination_date"],
    "user_access": ["access_id", "employee_id", "username", "system", "role", "is_privileged", "account_type",
                    "account_status", "granted_date", "approval_ticket", "last_login_date"],
    "sod_rules": ["system", "role_a", "role_b", "risk_description"],
    "login_events": ["event_id", "username", "system", "event_time", "result"],
}


class DatasetError(RuntimeError):
    """Input files are missing or unreadable."""


@dataclass
class Dataset:
    hr: pd.DataFrame
    access: pd.DataFrame
    sod: pd.DataFrame
    events: pd.DataFrame
    source: Path | None = None
    file_info: dict[str, dict] = field(default_factory=dict)  # table -> {file, rows, sha256}

    @classmethod
    def from_dir(cls, path: str | Path) -> Dataset:
        """Read the four CSVs as text (so nothing is silently re-typed) and record a SHA-256 of each."""
        folder = Path(path)
        if not folder.is_dir():
            raise DatasetError(f"Data folder not found: {folder}")
        frames: dict[str, pd.DataFrame] = {}
        info: dict[str, dict] = {}
        for name in TABLES:
            f = folder / f"{name}.csv"
            if not f.is_file():
                raise DatasetError(f"Missing input file: {f}  (generate it with: itgc-uar generate)")
            try:
                df = pd.read_csv(f, dtype=str, keep_default_na=False, na_values=[""])
            except Exception as exc:  # pandas raises several different parser errors
                raise DatasetError(f"Could not read {f}: {exc}") from exc
            frames[name] = df
            info[name] = {"file": f.name, "rows": int(len(df)), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
        return cls(frames["hr_master"], frames["user_access"], frames["sod_rules"], frames["login_events"],
                   source=folder, file_info=info)

    @classmethod
    def from_frames(cls, hr: pd.DataFrame, access: pd.DataFrame, sod: pd.DataFrame, events: pd.DataFrame) -> Dataset:
        """Build a dataset from in-memory frames (used by the tests)."""
        return cls(hr.copy(), access.copy(), sod.copy(), events.copy())

    def coerced(self) -> Dataset:
        """Return a copy where is_privileged is a real 0/1 integer. Call this only after data-quality checks pass."""
        access = self.access.copy()
        access["is_privileged"] = pd.to_numeric(access["is_privileged"], errors="coerce").fillna(0).astype(int)
        return Dataset(self.hr.copy(), access, self.sod.copy(), self.events.copy(), self.source, self.file_info)

    def to_sqlite(self) -> sqlite3.Connection:
        """In-memory SQLite database holding the four tables (+ indexes) for the SQL control tests."""
        conn = sqlite3.connect(":memory:")
        for name, df in (("hr_master", self.hr), ("user_access", self.access),
                         ("sod_rules", self.sod), ("login_events", self.events)):
            df.to_sql(name, conn, index=False)
        conn.executescript(
            "CREATE INDEX ix_hr_emp ON hr_master(employee_id);"
            "CREATE INDEX ix_ua_emp ON user_access(employee_id);"
            "CREATE INDEX ix_ua_user ON user_access(username, system);"
            "CREATE INDEX ix_ev_user ON login_events(username, system, result);"
        )
        return conn
