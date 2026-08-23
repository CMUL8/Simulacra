terraform { required_version = ">= 1.5.0" }

locals {
  runtime_contract = {
    cloud                  = "gcp"
    deployment_mode        = "private_cloud"
    tenant_id              = var.tenant_id
    environment            = var.environment
    region                 = var.region
    kubernetes_cluster_id  = var.gke_cluster_id
    postgres_endpoint      = var.postgres_endpoint
    redis_endpoint         = var.redis_endpoint
    object_storage_id      = var.storage_bucket_id
    secret_provider_id     = var.secret_manager_project
    image_registry         = var.registry_url
    tls_secret_name        = var.tls_secret_name
    customer_managed       = true
  }
}
