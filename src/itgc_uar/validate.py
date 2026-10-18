"""Check the detections against the generator's ground-truth manifest (SIMULATED data only).

The manifest lists every problem the generator planted ("planted") and every boundary case it built to look
suspicious but be fine ("decoy"). Comparing detections with it gives recall (did we find what was planted?) and
a false-positive count on the decoys. The control tests never read the manifest - this is a separate check.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from itgc_uar.config import CONTROL_IDS
from itgc_uar.engine import RunResult


def load_manifest(data_dir: str | Path) -> pd.DataFrame | None:
    p = Path(data_dir) / "ground_truth.csv"
    return pd.read_csv(p, dtype=str, keep_default_na=False) if p.is_file() else None


def validate_against_manifest(result: RunResult, manifest: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cid in CONTROL_IDS:
        found = set(result.register.loc[result.register["control_id"] == cid, "entity_key"])
        m = manifest[manifest["control_id"] == cid]
        planted, decoys = set(m.loc[m["kind"] == "planted", "key"]), set(m.loc[m["kind"] == "decoy", "key"])
        hit = planted & found
        fp = decoys & found
        rows.append({
            "control_id": cid, "planted": len(planted), "detected": len(hit), "missed": len(planted - found),
            "recall": (len(hit) / len(planted)) if planted else None,
            "decoys": len(decoys), "decoys_wrongly_flagged": len(fp),
            "other_detections": len(found - planted - decoys),
            "missed_keys": ", ".join(sorted(planted - found)[:5]), "false_positive_keys": ", ".join(sorted(fp)[:5]),
        })
    return pd.DataFrame(rows)
