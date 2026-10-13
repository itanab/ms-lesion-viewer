# Build context: repository root.  docker compose build
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
RUN pip install --no-cache-dir uv
WORKDIR /srv/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY --from=web /web/dist /srv/frontend/dist
ENV MSLV_DATA_ROOT=/data MSLV_FRONTEND_DIST=/srv/frontend/dist PATH="/srv/backend/.venv/bin:$PATH"
VOLUME /data
EXPOSE 8000
# 0.0.0.0 is container-internal only; compose publishes the port on the host loopback.
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
