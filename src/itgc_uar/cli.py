"""Command-line interface:  itgc-uar generate | run | all

Exit codes: 0 success, 1 usage/IO/config error, 2 SQL-vs-pandas reconciliation mismatch, 3 data-quality error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from itgc_uar import __version__
from itgc_uar.config import ConfigError, load_config
from itgc_uar.datagen import PROFILES, generate, write_dataset
from itgc_uar.dataset import Dataset, DatasetError
from itgc_uar.engine import DataQualityError, RunResult, run_controls
from itgc_uar.logging_utils import setup_logging
from itgc_uar.reporting.csvs import write_csvs
from itgc_uar.reporting.dashboard import write_dashboard
from itgc_uar.reporting.excel import write_excel
from itgc_uar.validate import load_manifest, validate_against_manifest

log = logging.getLogger("itgc_uar")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cmd_generate(profile: str, out: Path, seed: int | None, review_date: date) -> Path:
    gd = generate(profile, seed=seed, review_date=review_date)
    write_dataset(gd, out)
    log.info("Generated SIMULATED '%s' dataset in %s: %s", profile, out, gd.meta["rows"])
    return out


def cmd_run(data: Path, out: Path, config: Path | None, review_date: str | None, force: bool) -> tuple[RunResult, int]:
    overrides = {"review_date": review_date} if review_date else None
    cfg = load_config(config, overrides)
    ds = Dataset.from_dir(data)
    result = run_controls(ds, cfg, force=force)

    meta_path = data / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {"profile": "custom (no meta.json)"}
    manifest = load_manifest(data)
    validation = validate_against_manifest(result, manifest) if manifest is not None else None
    if validation is not None:
        log.info("Synthetic validation: %d/%d planted problems detected, %d decoys wrongly flagged",
                 int(validation["detected"].sum()), int(validation["planted"].sum()),
                 int(validation["decoys_wrongly_flagged"].sum()))

    out.mkdir(parents=True, exist_ok=True)
    files = write_csvs(result, out)
    files.append(write_excel(result, out, validation, meta))
    files.append(write_dashboard(result, out, meta, validation))
    if validation is not None:
        v = out / "csv" / "synthetic_validation.csv"
        validation.to_csv(v, index=False)
        files.append(v)

    manifest_out = {
        "tool": "itgc-uar", "version": __version__, "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(), "pandas": pd.__version__, "data_is_simulated": True,
        "dataset_meta": meta, "config_fingerprint": cfg.fingerprint(), "config": cfg.as_dict(),
        "inputs": result.dataset.file_info,
        "data_quality": {"errors": result.dq.errors, "warnings": result.dq.warnings},
        "results": result.summary[["control_id", "population", "exceptions", "high", "medium", "low",
                                   "conclusion", "reconciliation"]].to_dict("records"),
        "total_exceptions": result.total_exceptions, "reconciled": result.reconciled,
        "outputs": {str(f.relative_to(out)): _sha(f) for f in files if f.suffix != ".xlsx"},
        "timings_seconds": {k: round(v, 3) for k, v in result.timings.items()},
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest_out, indent=2, default=str), encoding="utf-8")
    log.info("Reports written to %s", out)
    return result, (0 if result.reconciled else 2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="itgc-uar", description="ITGC user-access-review control testing (SIMULATED data).")
    p.add_argument("--version", action="version", version=f"itgc-uar {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_gen(sp):
        sp.add_argument("--profile", choices=sorted(PROFILES), default="baseline",
                        help="baseline = 400 employees (matches v1); enterprise = 2,000 employees + decoys")
        sp.add_argument("--seed", type=int, default=None, help="override the profile's random seed")
        sp.add_argument("--gen-review-date", default="2026-09-30", help="review date used while generating data")

    def add_run(sp):
        sp.add_argument("--config", type=Path, help="YAML file overriding config/default.yaml")
        sp.add_argument("--review-date", help="override review_date (YYYY-MM-DD)")
        sp.add_argument("--force", action="store_true", help="continue despite data-quality errors")

    g = sub.add_parser("generate", help="create a simulated dataset")
    add_gen(g)
    g.add_argument("--out", type=Path, default=Path("data"))
    r = sub.add_parser("run", help="run control tests on a dataset folder and write reports")
    r.add_argument("--data", type=Path, default=Path("data"))
    r.add_argument("--out", type=Path, default=Path("outputs"))
    add_run(r)
    a = sub.add_parser("all", help="generate + run")
    add_gen(a)
    a.add_argument("--data", type=Path, default=Path("data"))
    a.add_argument("--out", type=Path, default=Path("outputs"))
    add_run(a)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = getattr(args, "out", Path("outputs"))
    setup_logging(args.verbose, (out_dir / "run.log") if args.cmd in ("run", "all") else None)
    try:
        if args.cmd in ("generate", "all"):
            target = args.out if args.cmd == "generate" else args.data
            cmd_generate(args.profile, target, args.seed, date.fromisoformat(args.gen_review_date))
        if args.cmd in ("run", "all"):
            _, code = cmd_run(args.data, args.out, args.config, args.review_date, args.force)
            if code:
                log.error("Reconciliation mismatch - results are NOT trustworthy. See the Reconciliation sheet.")
            return code
        return 0
    except DataQualityError as exc:
        log.error("%s", exc)
        return 3
    except (ConfigError, DatasetError, ValueError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
