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
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg gosu \
	&& curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
	&& apt-get install -y --no-install-recommends nodejs \
	&& rm -rf /var/lib/apt/lists/*
# The operational builder uses the official Codex app-server protocol.
ENV NPM_CONFIG_PREFIX=/opt/codex
ENV PATH="/opt/codex/bin:/usr/local/bin:${PATH}"
RUN mkdir -p /opt/codex \
	&& npm install -g @openai/codex@0.148.0 \
	&& command -v codex \
	&& codex --version
COPY pyproject.toml README.md ./
COPY simulacra ./simulacra
COPY apps ./apps
COPY deploy ./deploy
COPY fixtures ./fixtures
COPY templates ./templates
COPY schemas ./schemas
COPY scripts ./scripts
COPY deploy/bin/cmul8-entrypoint /opt/cmul8/bin/cmul8-entrypoint
COPY deploy/bin/cmul8-mission-sandbox /opt/cmul8/bin/cmul8-mission-sandbox
COPY deploy/executor-registry.json /opt/cmul8/executors/registry.json
COPY --from=console-build /src/apps/console/dist ./apps/console/dist
RUN pip install --no-cache-dir -e ".[demo]" \
	&& chmod 0555 /opt/cmul8/bin/cmul8-entrypoint \
		/opt/cmul8/bin/cmul8-mission-sandbox \
	&& chmod 0444 /opt/cmul8/executors/registry.json \
	&& for process in api web web-worker worker worker-health preflight doctor migrate smoke; do \
		ln -s /opt/cmul8/bin/cmul8-entrypoint "/opt/cmul8/bin/cmul8-${process}"; \
	done \
	&& groupadd --gid 65532 cmul8 \
	&& useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin cmul8 \
	&& mkdir -p /app/data/codex /app/runs \
	&& chown -R 65532:65532 /app/data /app/runs
ENV SIMULACRA_AUTH_REQUIRED=1
ENV CMUL8_MODEL_PROVIDER=openai
ENV CMUL8_CODEX_BIN=/opt/codex/bin/codex
ENV CMUL8_MISSION_ISOLATION_LAUNCHER=/opt/cmul8/bin/cmul8-mission-sandbox
ENV CODEX_HOME=/app/data/codex
ENV CMUL8_MISSION_RUNTIME_ROOT=/app/data/mission-runtime
ENV SIMULACRA_SANDBOX=worktree
ENV PYTHONPATH=/app
ENV PORT=8000
EXPOSE 8000
USER 65532:65532
ENTRYPOINT ["/opt/cmul8/bin/cmul8-entrypoint"]
CMD ["api"]
