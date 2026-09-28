ARG NODE_IMAGE=node:20-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${NODE_IMAGE} AS frontend-builder

WORKDIR /app/static

COPY static/package.json static/package-lock.json ./
RUN npm ci

COPY static/ ./
RUN npm run build:admin-css


FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY backend/ ./backend/
COPY docs/ ./docs/
COPY static/admin/ ./static/admin/
COPY static/public/ ./static/public/
COPY static/assets/ ./static/assets/
COPY static/vendor/ ./static/vendor/
COPY static/logo.svg ./static/logo.svg
COPY static/logo.png ./static/logo.png
COPY static/logo-mono.svg ./static/logo-mono.svg
COPY static/logo-mono.png ./static/logo-mono.png
COPY --from=frontend-builder /app/static/admin.css ./static/admin.css
COPY --from=frontend-builder /app/static/public.css ./static/public.css
COPY --from=frontend-builder /app/static/assets/js/alpine.min.js ./static/assets/js/alpine.min.js
COPY --from=frontend-builder /app/static/assets/js/vendor/mathjax/tex-svg.js ./static/assets/js/vendor/mathjax/tex-svg.js

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/healthz\", timeout=3)"]

CMD ["sh", "-c", "python -m uvicorn backend.md_quiz.main:app --host ${APP_HOST:-0.0.0.0} --port ${PORT:-8000}"]
