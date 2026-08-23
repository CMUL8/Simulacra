# Environment and secret contract

Required keys are `CMUL8_DEPLOYMENT_MODE`, `CMUL8_TENANT_ID`,
`CMUL8_ENVIRONMENT`, `CMUL8_POSTGRES_URL`, `CMUL8_REDIS_URL`,
`CMUL8_OBJECT_STORAGE_URL`, `CMUL8_SECRET_PROVIDER`, and
`CMUL8_IMAGE_REGISTRY`. `CMUL8_TLS_REQUIRED` defaults to `true`.

PostgreSQL must use `postgres://` or `postgresql://`; Redis must use `redis://`
or `rediss://`; object storage may use `s3://`, `gs://`, `az://`, or HTTPS.
Object-storage URLs must not embed credentials. Production deployments should
use TLS-bearing database/queue URLs and workload identity or short-lived
secret-provider credentials.

The Helm chart references keys in an existing Kubernetes Secret. It never
creates that Secret or serializes values into a ConfigMap. Service accounts do
not automount Kubernetes tokens; platform teams may add narrowly scoped
workload-identity annotations.

Preflight validates shape and can optionally resolve hosts. It deliberately
does not open network connections, mutate databases, test permissions, or infer
production readiness. The injected smoke checks own those target-aware tests.
