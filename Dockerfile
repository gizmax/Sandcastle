# -- Dashboard builder stage --
FROM node:20-slim AS dashboard-builder

WORKDIR /dashboard

COPY dashboard/package*.json ./
RUN npm ci

COPY dashboard/ ./
RUN npm run build


# -- Python builder stage --
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN pip install --no-cache-dir hatchling

# Copy project metadata and source for pip install
COPY pyproject.toml README.md ./
COPY src/ src/
COPY --from=dashboard-builder /dashboard/dist dashboard/dist/

# Build a wheel and install into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir ".[docker]"


# -- Runtime stage --
FROM python:3.12-slim

LABEL maintainer="Tomas Pflanzer @gizmax"

# Create non-root user
RUN groupadd --gid 1000 sandcastle \
    && useradd --uid 1000 --gid sandcastle --create-home sandcastle

# Copy installed packages from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    WORKFLOWS_DIR=/app/workflows

WORKDIR /app

# Copy application source, migrations, and workflow definitions
COPY alembic.ini ./
COPY alembic/ alembic/
COPY src/ src/
COPY workflows/ workflows/

# Create data directory for local storage
RUN mkdir -p /app/data && chown -R sandcastle:sandcastle /app

USER sandcastle

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

# Keep the image single-worker by default.  Uvicorn runs the FastAPI lifespan in
# every worker, while scheduler ownership is configured separately in Compose.
# Set UVICORN_WORKERS deliberately only when SCHEDULER_ENABLED=false.
CMD ["sh", "-c", "python -m sandcastle db migrate && python -m sandcastle serve --host 0.0.0.0 --port 8080 --workers ${UVICORN_WORKERS:-1}"]
