# Classroom Digital Twin

A 3D classroom (Three.js) connected to a FastAPI + PostgreSQL + WebSocket stack. Occupancy events come from manual 3D simulation controls or API ingestion.

---

## 1. Quick Start & Setup

### Step 1: Configure Environment Variables

Copy `.env.example` to `.env`:

```powershell
cp .env.example .env
```

Open `.env` and configure your custom database username, password, and database name:

```env
POSTGRES_USER=your_custom_user
POSTGRES_PASSWORD=your_secure_password
POSTGRES_DB=classroom_twin

DATABASE_URL=postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
TEST_DATABASE_URL=postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}_test

TWIN_TIMEZONE=Africa/Casablanca
OCCUPIED_CONFIRM_SECONDS=2
EMPTY_CONFIRM_SECONDS=3
```

### Step 2: Build and Run with Docker

Start the entire application (database + API + frontend) with one command:

```powershell
docker compose up -d --build
```

- Open <http://127.0.0.1:8000/> in your browser.
- API documentation: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/api/health>

To stop the application while preserving database volume:
```powershell
docker compose down
```

To stop and delete the database volume (clean start):
```powershell
docker compose down -v
```

---

## 2. Project Structure

```text
DT-SITEe/
├── frontend/
│   ├── index.html             3D scene + UI markup
│   ├── digital-twin.js        Three.js scene, avatars, manual controls
│   └── backend-client.js      API/WS client, TEST producer, stats, history
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app, lifespan, WS, static frontend
│   │   ├── config.py          environment / .env configuration
│   │   ├── db.py              SQLAlchemy engine + session factory
│   │   ├── db_init.py         executes the SQL schema + seed
│   │   ├── models.py          Seat, Session, OccupancyRecord
│   │   ├── schemas.py         Pydantic request/response models
│   │   ├── timeutil.py        Africa/Casablanca helpers
│   │   ├── ws.py              WebSocket connection hub
│   │   ├── api/               seats, sessions, occupancy, statistics, ingest
│   │   └── services/
│   │       ├── session_manager.py
│   │       └── occupancy_manager.py
│   ├── sql/
│   │   ├── 000_create_test_db.sh test database initialization script
│   │   ├── 001_schema.sql     tables, constraints, indexes
│   │   └── 002_seed.sql       24 seats + 4 sessions (idempotent)
│   ├── scripts/
│   │   ├── init_db.py
│   │   └── simulate_occupancy.py   external producer test tool
│   ├── tests/                 pytest suite (real PostgreSQL)
│   └── requirements.txt
├── .env.example
├── .gitignore
├── .dockerignore              files excluded from the Docker build context
├── Dockerfile                 API + static frontend image (Python 3.10 slim)
├── docker-compose.yml         db (PostgreSQL 17) + api (FastAPI) + volume
├── pytest.ini
└── README.md
```

---

## 3. Database Architecture

The database consists of exactly three tables:

- `seats` — 24 rows, codes `C1-R1` … `C4-R6`.
- `sessions` — 4 fixed daily slots:
  - #1: 08:00 – 10:30
  - #2: 10:30 – 13:00
  - #3: 13:30 – 16:00
  - #4: 16:00 – 18:30
- `occupancy_records` — one row per continuous occupancy period.

### `occupancy_records` Columns

| Column | Type | Notes |
|---|---|---|
| id | BIGSERIAL PK | |
| seat_id | INTEGER FK → seats.id | ON DELETE CASCADE |
| session_id | INTEGER FK → sessions.id | ON DELETE RESTRICT |
| session_date | DATE | Casablanca date |
| occupied_from | TIMESTAMPTZ | start of the continuous period |
| occupied_until | TIMESTAMPTZ NULL | NULL = record still open |
| created_at | TIMESTAMPTZ | insertion time |

A partial unique index guarantees at most **one open record** per `(seat_id, session_id, session_date)` where `occupied_until IS NULL`.

---

## 4. API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | database + WebSocket status |
| GET | `/api/seats` | 24 seats, `present` and `occupied` |
| GET | `/api/sessions` | the four sessions |
| GET | `/api/sessions/current` | current session / break / after-hours |
| GET | `/api/occupancy/current` | live presence + open records + stats |
| GET | `/api/occupancy/history` | history; filters `date`, `session_id`, `session_number`, `seat`, `open_only`, `limit`, `offset` |
| GET | `/api/statistics` | totals, empty/occupied, percentage |
| POST | `/api/ingest/snapshot` | full snapshot: `{"seats":["C1-R1","C2-R3"]}` |
| POST | `/api/ingest/event` | one observation: `{"seat":"C1-R1","action":"enter"}` |
| WS | `/ws` | real-time occupancy snapshots |

---

## 5. Tests

Run tests inside the container against the real PostgreSQL test database:

```powershell
docker compose exec api python -m pytest -q
```

### Test Coverage

| Area | What is checked |
|---|---|
| Rule 1 | occupy then leave → one closed record |
| Rule 2 | returning creates a second record |
| Rule 3 | session boundary closes/opens at exact times |
| Rule 4 | repeated detections create only one record |
| Rule 5 | absence shorter than 3 s keeps the record open |
| Rule 6 | 13:00–13:30 break creates no records |
| Rule 7 | restart recovery without duplicates |
| Schema | 24 seats, 4 sessions, partial unique index |
| API | REST endpoints, filters, WebSocket broadcast |
| Simulation producer | 2 Hz snapshot heartbeat, 2 s/3 s delays, fill/clear, returning student |
