# Imagem única do Aegis (usada pelo Railway): build do React + API Flask
# servindo o build. Front e API na mesma origem, como antes na Render.

# ── 1) build do frontend ────────────────────────────────────────────────
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── 2) backend Python servindo o build ──────────────────────────────────
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY --from=frontend /app/frontend/dist frontend/dist

WORKDIR /app/backend
# Um processo só (o cache e o resumo em memória são por processo) com 4
# threads; timeout longo para importação de arquivos grandes. O Railway
# injeta a porta em $PORT.
CMD gunicorn -b 0.0.0.0:${PORT:-8000} --timeout 300 --workers 1 --worker-class gthread --threads 4 app:app
