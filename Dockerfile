# syntax=docker/dockerfile:1
FROM node:22-bookworm AS console-build
WORKDIR /src/apps/console
COPY apps/console/package.json apps/console/package-lock.json ./
RUN npm ci
COPY apps/console/ ./
ARG VITE_CLERK_PUBLISHABLE_KEY=""
ENV VITE_CLERK_PUBLISHABLE_KEY=$VITE_CLERK_PUBLISHABLE_KEY
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY simulacra ./simulacra
COPY apps ./apps
COPY fixtures ./fixtures
COPY templates ./templates
COPY schemas ./schemas
COPY scripts ./scripts
COPY --from=console-build /src/apps/console/dist ./apps/console/dist
RUN pip install --no-cache-dir -e ".[demo]"
ENV SIMULACRA_AUTH_REQUIRED=1
ENV SIMULACRA_SANDBOX=worktree
ENV PYTHONPATH=/app
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
