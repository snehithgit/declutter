FROM node:22-bookworm-slim AS frontend
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PHOTOSIFT_FRONTEND_DIR=/app/frontend/dist
WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir . && useradd --uid 1000 --create-home photosift
COPY --from=frontend /ui/dist ./frontend/dist
COPY config.zima.toml /app/config.toml
RUN mkdir /data && chown photosift:photosift /data
USER photosift
EXPOSE 8765
HEALTHCHECK --interval=20s --timeout=5s --start-period=20s CMD python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/health')); exit(0 if r['status']=='ok' else 1)"
CMD ["python", "-m", "photosift", "run", "--config", "/app/config.toml"]
