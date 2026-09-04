# Waterflood Optimizer — API image (architecture §18: on-prem / private cloud, images pinned).
#
#   docker build -t wfo-api .                       # API only (nginx serves the UI in the prod profile)
#   docker build -t wfo-offline --target offline .  # single container: API + built UI, SQLite, in-process worker
#
# WeasyPrint needs Pango/Cairo (installed below) so PDF reports render without a browser.

# ---- UI build (shared by the offline target) -------------------------------------------------
FROM node:22-bookworm-slim AS ui-build
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npx tsc -b && npx vite build

# ---- API ---------------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    WFO_STORE=/data/store WFO_HOST=0.0.0.0 WFO_PORT=8000
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libcairo2 libgdk-pixbuf-2.0-0 libffi8 \
        fonts-dejavu-core curl git \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 wfo
WORKDIR /app
COPY pyproject.toml README.md ./
COPY waterflood_app ./waterflood_app
COPY config ./config
COPY scripts ./scripts
RUN pip install . weasyprint python-docx jinja2 xlsxwriter \
    && python -c "import weasyprint, docx, waterflood_app" \
    && mkdir -p /data/store && chown -R wfo:wfo /data /app
USER wfo
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/ready || exit 1
CMD ["python", "-m", "waterflood_app.api.app"]

# ---- offline: API + UI in one container ---------------------------------------------------------
FROM api AS offline
ENV WFO_UI_DIST=/app/ui/dist WFO_JOBS_SYNC=0
COPY --from=ui-build --chown=wfo:wfo /ui/dist /app/ui/dist
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health/ready || exit 1
CMD ["python", "-m", "waterflood_app.api.app"]
