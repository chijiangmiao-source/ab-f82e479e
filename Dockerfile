# ---- frontend build stage ----
FROM node:20-bookworm-slim AS frontend
WORKDIR /fe
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- backend stage ----
FROM python:3.11-slim-bookworm AS runtime
WORKDIR /srv

# g++ is needed to compile the exact search engine.
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /fe/dist ./app/static

# Build the native solver once at image build time.
RUN g++ -O2 -std=c++17 -o app/bin/solver app/solver.cpp \
    && rm -f app/solver.cpp

ENV SOLVER_BIN=/srv/app/bin/solver \
    SOLVER_TIMEOUT=20 \
    UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=4s --start-period=8s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${UVICORN_PORT}/health" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host ${UVICORN_HOST} --port ${UVICORN_PORT}"]
