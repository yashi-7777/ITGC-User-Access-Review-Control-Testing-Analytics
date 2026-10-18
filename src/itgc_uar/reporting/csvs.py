from __future__ import annotations

from pathlib import Path

from itgc_uar.config import CONTROL_IDS
from itgc_uar.engine import RunResult


def write_csvs(result: RunResult, out_dir: Path) -> list[Path]:
    out = out_dir / "csv"
    out.mkdir(parents=True, exist_ok=True)
    written = []

    def save(df, name):
        p = out / name
        df.to_csv(p, index=False)
        written.append(p)

    save(result.summary, "control_summary.csv")
    save(result.register, "exception_register.csv")
    save(result.dept_matrix.reset_index(), "department_matrix.csv")
    for cid in CONTROL_IDS:
        save(result.details[cid], f"{cid}_exceptions.csv")
    save(result.dq.to_frame(), "data_quality.csv")
    return written
