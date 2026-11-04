# Classroom Digital Twin — FastAPI + static frontend
#
# One image contains:
#   - the FastAPI backend
#   - the frontend/ static files (served by FastAPI)
#   - the pytest suite (so tests can also run inside the container)
#
# Build from the repository root:
#   docker compose build api

FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so Docker can cache this layer.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Application code + static frontend + test configuration.
COPY backend ./backend
COPY frontend ./frontend
COPY pytest.ini ./pytest.ini
COPY README.md ./README.md

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
