terraform { required_version = ">= 1.5.0" }

locals {
  runtime_contract = {
    cloud                  = "aws"
    deployment_mode        = "private_cloud"
    tenant_id              = var.tenant_id
    environment            = var.environment
    region                 = var.region
    kubernetes_cluster_id  = var.eks_cluster_arn
    postgres_endpoint      = var.postgres_endpoint
    redis_endpoint         = var.redis_endpoint
    object_storage_id      = var.object_storage_bucket_arn
    secret_provider_id     = var.secrets_manager_arn
    image_registry         = var.registry_url
    tls_secret_name        = var.tls_secret_name
    customer_managed       = true
  }
}
