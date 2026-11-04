-- ============================================================
--  Classroom Digital Twin - schema
--  PostgreSQL 13+
-- ============================================================

CREATE TABLE IF NOT EXISTS seats (
    id            SERIAL PRIMARY KEY,
    seat_code     VARCHAR(16) NOT NULL UNIQUE,
    row_number    INTEGER     NOT NULL,
    column_number INTEGER     NOT NULL,
    CONSTRAINT uq_seats_row_col UNIQUE (row_number, column_number),
    CONSTRAINT ck_seats_row CHECK (row_number BETWEEN 1 AND 6),
    CONSTRAINT ck_seats_col CHECK (column_number BETWEEN 1 AND 4)
);

CREATE TABLE IF NOT EXISTS sessions (
    id             SERIAL PRIMARY KEY,
    session_number INTEGER NOT NULL UNIQUE,
    start_time     TIME    NOT NULL,
    end_time       TIME    NOT NULL,
    CONSTRAINT ck_sessions_range CHECK (start_time < end_time),
    CONSTRAINT ck_sessions_number CHECK (session_number BETWEEN 1 AND 4)
);

CREATE TABLE IF NOT EXISTS occupancy_records (
    id             BIGSERIAL PRIMARY KEY,
    seat_id        INTEGER   NOT NULL REFERENCES seats(id) ON DELETE CASCADE,
    session_id     INTEGER   NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    session_date   DATE      NOT NULL,
    occupied_from  TIMESTAMPTZ NOT NULL,
    occupied_until TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_occ_until CHECK (occupied_until IS NULL OR occupied_until >= occupied_from)
);

-- At most one OPEN record per seat/session/date.
CREATE UNIQUE INDEX IF NOT EXISTS uq_occ_open
    ON occupancy_records (seat_id, session_id, session_date)
    WHERE occupied_until IS NULL;

CREATE INDEX IF NOT EXISTS ix_occ_date_session
    ON occupancy_records (session_date, session_id);

CREATE INDEX IF NOT EXISTS ix_occ_seat_date
    ON occupancy_records (seat_id, session_date);

CREATE INDEX IF NOT EXISTS ix_occ_open_seat
    ON occupancy_records (seat_id)
    WHERE occupied_until IS NULL;
