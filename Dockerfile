# syntax=docker/dockerfile:1.6
# =========================================================================
# v2t API image — multi-stage build
# Stage 1: builder installs build deps + Python wheels into a venv.
# Stage 2: slim runtime copies the venv + app source, drops to non-root.
# =========================================================================

# -------------------------------------------------------------------------
# Builder stage
# -------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

# Build deps required to compile a handful of wheels (hdbscan, fasttext,
# pyannote/torchaudio etc. when wheels are not available for the target).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        git \
        curl \
        ca-certificates \
        libsndfile1 \
        ffmpeg \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv "${VIRTUAL_ENV}"

WORKDIR /build

COPY requirements.txt ./

RUN pip install --upgrade pip setuptools wheel \
 && pip install -r requirements.txt


# -------------------------------------------------------------------------
# Runtime stage
# -------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    APP_HOME=/app

# Minimal runtime deps:
#  - tini → PID 1, reaps zombies, forwards signals
#  - ffmpeg / libsndfile1 → soundfile/librosa runtime
#  - curl → healthcheck convenience
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tini \
        ffmpeg \
        libsndfile1 \
        curl \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Copy the pre-built virtualenv from the builder
COPY --from=builder /opt/venv /opt/venv

# Non-root user
RUN groupadd --system --gid 1000 v2t \
 && useradd  --system --uid 1000 --gid 1000 --home "${APP_HOME}" v2t \
 && mkdir -p "${APP_HOME}" \
 && chown -R v2t:v2t "${APP_HOME}"

WORKDIR ${APP_HOME}

# Copy the app source
COPY --chown=v2t:v2t app ./app
COPY --chown=v2t:v2t alembic.ini ./alembic.ini
COPY --chown=v2t:v2t pyproject.toml ./pyproject.toml

USER v2t

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
