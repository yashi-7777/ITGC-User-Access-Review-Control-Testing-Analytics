"""Independent SQL-vs-pandas reconciliation.

For each control we compare (1) the SET of exception keys, (2) every attribute of the exceptions found by both
methods, and (3) the population count. Anything other than a perfect match is reported as MISMATCH with examples.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from itgc_uar.controls import ControlSpec


def _norm(v) -> str:
    if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)):
        return "<NULL>"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


@dataclass
class ReconResult:
    control_id: str
    sql_count: int
    pandas_count: int
    only_sql: list[str] = field(default_factory=list)
    only_pandas: list[str] = field(default_factory=list)
    attribute_mismatches: list[str] = field(default_factory=list)
    sql_population: int = 0
    pandas_population: int = 0

    @property
    def matched_keys(self) -> int:
        return self.sql_count - len(self.only_sql)

    @property
    def population_match(self) -> bool:
        return self.sql_population == self.pandas_population

    @property
    def ok(self) -> bool:
        return not (self.only_sql or self.only_pandas or self.attribute_mismatches) and self.population_match

    @property
    def status(self) -> str:
        return "MATCH" if self.ok else "MISMATCH"

    def examples(self, n: int = 3) -> str:
        bits = []
        if self.only_sql:
            bits.append(f"only in SQL: {self.only_sql[:n]}")
        if self.only_pandas:
            bits.append(f"only in pandas: {self.only_pandas[:n]}")
        if self.attribute_mismatches:
            bits.append(f"attribute differences: {self.attribute_mismatches[:n]}")
        if not self.population_match:
            bits.append(f"population SQL {self.sql_population} vs pandas {self.pandas_population}")
        return " | ".join(bits)


def reconcile(spec: ControlSpec, sql_df: pd.DataFrame, pd_df: pd.DataFrame, sql_pop: int, pd_pop: int) -> ReconResult:
    res = ReconResult(spec.control_id, len(sql_df), len(pd_df), sql_population=sql_pop, pandas_population=pd_pop)
    a = sql_df.assign(_key=spec.make_key(sql_df).values) if len(sql_df) else sql_df.assign(_key=[])
    b = pd_df.assign(_key=spec.make_key(pd_df).values) if len(pd_df) else pd_df.assign(_key=[])
    dup = a["_key"].duplicated().sum() + b["_key"].duplicated().sum()
    if dup:
        res.attribute_mismatches.append(f"{dup} duplicate key(s) in result sets")
    ka, kb = set(a["_key"]), set(b["_key"])
    res.only_sql = sorted(ka - kb)
    res.only_pandas = sorted(kb - ka)
    common = sorted(ka & kb)
    if common:
        ai = a.drop_duplicates("_key").set_index("_key").loc[common]
        bi = b.drop_duplicates("_key").set_index("_key").loc[common]
        for col in spec.compare_columns:
            if col not in ai.columns or col not in bi.columns:
                res.attribute_mismatches.append(f"column {col} missing in one result")
                continue
            va, vb = ai[col].map(_norm), bi[col].map(_norm)
            bad = va.index[va.values != vb.values]
            res.attribute_mismatches += [f"{k}: {col} SQL={va[k]} pandas={vb[k]}" for k in bad]
    return res
