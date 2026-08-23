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
COPY deploy/bin/cmul8-entrypoint /opt/cmul8/bin/cmul8-entrypoint
COPY --from=console-build /src/apps/console/dist ./apps/console/dist
RUN pip install --no-cache-dir -e ".[demo]" \
	&& chmod 0555 /opt/cmul8/bin/cmul8-entrypoint \
	&& for process in api web worker worker-health preflight migrate smoke; do \
		ln -s /opt/cmul8/bin/cmul8-entrypoint "/opt/cmul8/bin/cmul8-${process}"; \
	done
ENV SIMULACRA_AUTH_REQUIRED=1
ENV SIMULACRA_USE_PRIME=1
# OpenRouter model id (auth via OPENROUTER_API_KEY)
ENV SIMULACRA_PRIME_PROVIDER=openrouter
ENV SIMULACRA_PRIME_MODEL=deepseek/deepseek-v4-pro
ENV SIMULACRA_SANDBOX=worktree
ENV PYTHONPATH=/app
ENV PORT=8000
EXPOSE 8000
ENTRYPOINT ["/opt/cmul8/bin/cmul8-entrypoint"]
CMD ["api"]
