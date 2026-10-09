# WALLTEST api image: builds the React UI, then serves UI + API from one Python process.
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app/backend
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/ backend/
COPY db/ db/
COPY data/ data/
COPY docs/defaults_derivation.json docs/defaults_derivation.json
COPY docs/benchmarks/ docs/benchmarks/
COPY --from=web /web/dist frontend/dist
EXPOSE 8000
# bootstrap = wait for Postgres, apply migrations (as the admin login), seed once; the API itself runs as the non-owner walltest_api role
CMD ["sh", "-c", "python -m walltest.bootstrap && exec uvicorn walltest.api:app --host 0.0.0.0 --port 8000"]
