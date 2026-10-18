-- T6 | SUSPICIOUS LOGIN PATTERN: many failed logins in a short time (possible password guessing / brute force)
-- Why it matters: a burst of failures followed by a success can mean an attacker guessed the password.
-- Logic: for every failed login f1, count the DISTINCT failed logins f2 of the same account in the next
--        :burst_window_minutes minutes (f1 included). If that count reaches :burst_threshold it is a "burst".
--        Then check whether a SUCCESS followed within :burst_success_lookahead_minutes of the burst start.
WITH failed AS (
    SELECT event_id, username, system, event_time
    FROM login_events
    WHERE result = 'Failed'
),
bursts AS (
    SELECT f1.username,
           f1.system,
           f1.event_time AS window_start,
           COUNT(DISTINCT f2.event_id) AS failed_in_window
    FROM failed f1
    JOIN failed f2
      ON f2.username = f1.username
     AND f2.system   = f1.system
     AND datetime(f2.event_time) >= datetime(f1.event_time)
     AND datetime(f2.event_time) <  datetime(f1.event_time, '+' || :burst_window_minutes || ' minutes')
    GROUP BY f1.username, f1.system, f1.event_time
    HAVING COUNT(DISTINCT f2.event_id) >= :burst_threshold
)
SELECT b.username,
       b.system,
       MIN(b.window_start)         AS first_burst_time,
       MAX(b.failed_in_window)     AS max_failed_in_window,
       MAX(CASE WHEN EXISTS (
               SELECT 1 FROM login_events s
               WHERE s.username = b.username AND s.system = b.system AND s.result = 'Success'
                 AND datetime(s.event_time) >  datetime(b.window_start)
                 AND datetime(s.event_time) <= datetime(b.window_start, '+' || :burst_success_lookahead_minutes || ' minutes'))
           THEN 1 ELSE 0 END)      AS success_after_burst
FROM bursts b
GROUP BY b.username, b.system
ORDER BY success_after_burst DESC, max_failed_in_window DESC, b.username;
