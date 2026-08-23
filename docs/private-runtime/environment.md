# Environment and secret contract

The V0 Compose runtime requires `CMUL8_DEPLOYMENT_MODE`, `CMUL8_TENANT_ID`,
`CMUL8_ENVIRONMENT`, `CMUL8_POSTGRES_URL`, and `CMUL8_REDIS_URL` inside the
application containers. Compose derives service URLs from
`CMUL8_POSTGRES_PASSWORD`; operators must also set
`SIMULACRA_BOOTSTRAP_PASSWORD`.

PostgreSQL must use `postgres://` or `postgresql://`; Redis must use `redis://`
or `rediss://`. Object storage is optional in V0 and may use `s3://`, `gs://`,
`az://`, or HTTPS without embedded credentials.

Keep `.env` mode `0600`, outside version control, and readable only by the host
operator. For external production environments, prefer Docker secrets or a host
secret manager. Terminate TLS in a reviewed reverse proxy or load balancer and
retain the default loopback-only application binding.
