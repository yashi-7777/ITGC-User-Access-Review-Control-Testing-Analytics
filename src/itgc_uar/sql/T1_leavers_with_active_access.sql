-- T1 | LEAVER CONTROL: terminated employees whose access is still ACTIVE
-- Why it matters: a former employee with a live account is a classic ITGC (access management) failure.
-- Logic: join HR master (source of truth) to the access list; keep Terminated + Active account.
-- Only exits dated ON OR BEFORE the review date count (a future-dated exit is not a leaver yet).
SELECT u.access_id,
       h.employee_id,
       h.name,
       h.department,
       h.termination_date,
       u.username,
       u.system,
       u.role,
       u.is_privileged,
       u.last_login_date,
       CAST(julianday(:review_date) - julianday(h.termination_date) AS INTEGER) AS days_since_termination,
       CASE WHEN u.last_login_date > h.termination_date THEN 1 ELSE 0 END      AS login_after_termination
FROM hr_master h
JOIN user_access u ON u.employee_id = h.employee_id
WHERE h.status = 'Terminated'
  AND h.termination_date <= :review_date
  AND u.account_status = 'Active'
ORDER BY login_after_termination DESC, days_since_termination DESC, u.access_id;
