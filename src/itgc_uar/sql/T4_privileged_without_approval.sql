-- T4 | PRIVILEGED ACCESS WITHOUT APPROVAL: active privileged accounts with no approval ticket on record
-- Why it matters: privileged roles (System Admin, DBA, Payment Release...) can change data and settings.
-- Every grant must trace back to an approved request - no ticket = no evidence the access was authorised.
-- Logic: filter active + privileged, keep rows where the approval_ticket is NULL or only whitespace (space, tab, CR, LF).
SELECT u.access_id,
       u.employee_id,
       h.name,
       h.department,
       u.username,
       u.system,
       u.role,
       u.granted_date,
       u.account_type,
       h.status AS employee_status
FROM user_access u
LEFT JOIN hr_master h ON h.employee_id = u.employee_id
WHERE u.account_status = 'Active'
  AND u.is_privileged = 1
  AND (u.approval_ticket IS NULL OR TRIM(u.approval_ticket, ' ' || char(9) || char(10) || char(13)) = '')
ORDER BY u.system, u.role, u.access_id;
