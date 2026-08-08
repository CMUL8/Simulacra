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
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
	&& curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
	&& apt-get install -y --no-install-recommends nodejs \
	&& rm -rf /var/lib/apt/lists/*
# Prime Agent needs a writable npm prefix (installer uses npm install -g)
ENV NPM_CONFIG_PREFIX=/opt/prime
ENV PATH="/opt/prime/bin:/usr/local/bin:${PATH}"
RUN mkdir -p /opt/prime \
	&& curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh | sh \
	&& command -v prime-agent \
	&& prime-agent --version || true
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
ENV SIMULACRA_USE_PRIME=1
ENV SIMULACRA_SANDBOX=worktree
ENV PYTHONPATH=/app
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
