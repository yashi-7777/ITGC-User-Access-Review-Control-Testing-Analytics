# Control catalogue

> All data is **simulated**. Parameters (thresholds, tolerable rates, scores) are illustrative defaults in
> `src/itgc_uar/config/default.yaml`; in a real engagement they come from the audit methodology and client risk appetite.

Every control is implemented twice (SQL file in `src/itgc_uar/sql/` and a pandas function in `controls.py`) and the
two are reconciled on every run.

| ID | Control objective | Population (denominator) | Exception = | Severity rule | Typical audit evidence / ITGC area |
|----|-------------------|--------------------------|-------------|---------------|-------------------------------------|
| T1 | Leaver access is removed on/before exit | Access entitlements of employees terminated on/before the review date | Terminated employee, account still **Active** | High if the account was used *after* exit or is privileged, else Medium | HR leavers list vs. system user list (Logical access - termination) |
| T2 | Dormant accounts are reviewed/disabled | Active individual accounts | Active account unused for **more than** `dormant_days` (or never used), owner not a leaver | Medium if privileged, else Low | Last-login report (Logical access - periodic review) |
| T3 | No conflicting roles held together | Active employee-system pairs | Both roles of an SoD rule **Active** for one employee on one system | High | SoD matrix vs. user-role listing (Segregation of duties) |
| T4 | Privileged access is approved | Active privileged accounts | Privileged account with NULL / empty / whitespace-only approval ticket | High | Access request tickets (Logical access - provisioning) |
| T5 | Every account has one named, current owner | All active accounts | Active **Shared** account, or Individual account with no HR record (orphan) | High if privileged, else Medium | HR master vs. account list (Accountability) |
| T6 | Failed-login bursts are detected | Accounts with login activity | At least `burst_threshold` failures by one account on one system within `burst_window_minutes` | High if a success follows within `burst_success_lookahead_minutes`, else Medium | Authentication logs (Security monitoring) |

## Boundary rules (all covered by tests)

* T1: an exit dated **after** the review date is not a leaver yet. A last login **on** the exit day is not "after exit".
* T2: exactly `dormant_days` days idle is **not** dormant; one more day is.
* T3: a role on a *different system*, or one whose account is *Disabled*, is not a conflict. Duplicate grants of the same role count once.
* T4: a ticket made only of spaces, tabs or line breaks counts as missing.
* T6: failures on different systems never combine. The window is half-open `[t, t + window)`, so 5 failures spread over exactly 10 minutes do **not** qualify.

## Risk score (0-100) and remediation priority

Severity (High/Medium/Low) is the simple rating from v1 of the project. The risk score is an extra, transparent
heuristic used only to *order* remediation; **it is not an industry standard**.

`score = base(control) + modifiers`, capped at 100. Each finding lists its own build-up in `score_factors`.

| Control | Base | Modifiers |
|---------|------|-----------|
| T1 | 40 | +20 login after exit, +15 privileged, +1 per 30 days since exit (max 10) |
| T2 | 15 | +10 privileged, +5 never used, else +1 per 36 days beyond the limit (max 10) |
| T3 | 55 | +10 per privileged role in the pair, +10 if the employee is a leaver |
| T4 | 50 | +10 critical role (config list), +10 shared account, +10 owner is a leaver |
| T5 | 35 | +25 privileged, +10 orphan |
| T6 | 30 | +30 success after burst, +1 per failure above threshold (max 10) |

Priority bands (config): P1 >= 70, P2 >= 50, P3 >= 30, else P4. Due date = review date + SLA days (P1 7, P2 30, P3 60, P4 90).

## Conclusion per control

`deviation rate = exceptions / population`. Conclusion is **Effective** if the rate is <= the tolerable rate, otherwise
**Not effective**. With a tolerable rate of 0 % (default for most controls) a single exception makes the control "Not
effective" - this mirrors how auditors treat controls where any failure matters, but it is a simplification: real
sampling conclusions also consider sample size, compensating controls and the nature of each deviation.
