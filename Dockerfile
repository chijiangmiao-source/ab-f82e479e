# syntax=docker/dockerfile:1

# ---- frontend build ----
FROM node:20-bookworm-slim AS frontend
WORKDIR /fe
COPY frontend/package.json ./
# package-lock is optional; install resolves and caches dependencies
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- backend runtime ----
FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000
WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /backend/static ./static

EXPOSE 8000

# Container-level health check (curl is not in slim; use urllib).
HEALTHCHECK --interval=10s --timeout=4s --start-period=8s --retries=6 \
  CMD python -c "import json,os,sys,urllib.request as u; \
port=os.environ.get('PORT','8000'); \
r=u.urlopen('http://127.0.0.1:%s/health'%port, timeout=3); \
sys.exit(0 if r.status==200 else 1)"

# Exec form so PORT expansion happens at runtime.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
