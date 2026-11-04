-- ============================================================
--  Classroom Digital Twin - seed data (idempotent)
--  24 seats: C{column 1-4}-R{row 1-6}
--  4 recurring daily sessions
-- ============================================================

INSERT INTO seats (seat_code, row_number, column_number)
SELECT 'C' || c || '-R' || r, r, c
FROM generate_series(1, 4) AS c
CROSS JOIN generate_series(1, 6) AS r
ON CONFLICT (seat_code) DO NOTHING;

INSERT INTO sessions (session_number, start_time, end_time)
VALUES
    (1, '08:00', '10:30'),
    (2, '10:30', '13:00'),
    (3, '13:30', '16:00'),
    (4, '16:00', '18:30')
ON CONFLICT (session_number) DO UPDATE
    SET start_time = EXCLUDED.start_time,
        end_time   = EXCLUDED.end_time;
