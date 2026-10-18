-- T2 | DORMANT ACCOUNTS: active individual accounts unused for MORE than :dormant_days days (or never used)
-- Why it matters: unused accounts are easy targets (nobody notices misuse) and signal weak periodic review.
-- Leavers are excluded because T1 already reports them. Exactly :dormant_days days idle is NOT dormant.
SELECT u.access_id,
       u.employee_id,
       h.name,
       h.department,
       u.username,
       u.system,
       u.role,
       u.is_privileged,
       u.last_login_date,
       CAST(julianday(:review_date) - julianday(u.last_login_date) AS INTEGER) AS days_inactive
FROM user_access u
LEFT JOIN hr_master h ON h.employee_id = u.employee_id
WHERE u.account_status = 'Active'
  AND u.account_type = 'Individual'
  AND NOT (h.status = 'Terminated' AND h.termination_date <= :review_date)
  AND (u.last_login_date IS NULL
       OR julianday(:review_date) - julianday(u.last_login_date) > :dormant_days)
ORDER BY days_inactive DESC, u.access_id;
