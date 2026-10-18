-- T5 | ACCOUNTABILITY: shared/generic accounts and orphan accounts
-- Why it matters: if an account has no single named owner, nobody can be held responsible for what it does
-- (shared logins), and an account with no matching HR record (orphan) is usually a forgotten or unauthorised ID.
-- Logic: two checks stitched together with UNION ALL.
--   (a) account_type = 'Shared'  -> no individual owner
--   (b) Individual account whose employee_id does not exist in the HR master -> orphan
SELECT u.access_id, u.username, u.system, u.role, u.is_privileged, u.last_login_date,
       'Shared / generic account - no named owner' AS issue
FROM user_access u
WHERE u.account_status = 'Active'
  AND u.account_type = 'Shared'
UNION ALL
SELECT u.access_id, u.username, u.system, u.role, u.is_privileged, u.last_login_date,
       'Orphan account - no matching HR record' AS issue
FROM user_access u
LEFT JOIN hr_master h ON h.employee_id = u.employee_id
WHERE u.account_status = 'Active'
  AND u.account_type = 'Individual'
  AND h.employee_id IS NULL
ORDER BY issue, is_privileged DESC, access_id;
