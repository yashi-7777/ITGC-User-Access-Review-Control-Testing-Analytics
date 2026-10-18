-- T3 | SEGREGATION OF DUTIES (SoD): one person holding two roles that must be kept apart
-- Why it matters: SoD stops one person from completing a fraud end-to-end (e.g. create vendor AND pay it).
-- Logic: join user_access to itself - same employee, same system, role A in one row and role B in the other,
-- both rows ACTIVE. GROUP BY collapses duplicate grants of the same role into one exception.
SELECT a.employee_id,
       h.name,
       h.department,
       a.system,
       r.role_a,
       r.role_b,
       r.risk_description,
       h.status AS employee_status,
       MIN(a.access_id) AS access_id_a,
       MIN(b.access_id) AS access_id_b,
       MAX(a.is_privileged) AS priv_a,
       MAX(b.is_privileged) AS priv_b
FROM sod_rules r
JOIN user_access a ON a.system = r.system AND a.role = r.role_a AND a.account_status = 'Active'
JOIN user_access b ON b.system = r.system AND b.role = r.role_b AND b.account_status = 'Active'
                  AND b.employee_id = a.employee_id
JOIN hr_master h   ON h.employee_id = a.employee_id
GROUP BY a.employee_id, a.system, r.role_a, r.role_b
ORDER BY a.system, h.name, r.role_a;
