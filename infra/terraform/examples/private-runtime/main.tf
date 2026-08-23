module "cmul8_contract" {
  source = "../../modules/aws"

  tenant_id                = "example-tenant"
  environment              = "production"
  region                   = "us-east-1"
  eks_cluster_arn          = "arn:aws:eks:us-east-1:111122223333:cluster/customer-managed"
  postgres_endpoint        = "postgresql://provided-at-apply"
  redis_endpoint           = "rediss://provided-at-apply"
  object_storage_bucket_arn = "arn:aws:s3:::customer-managed"
  secrets_manager_arn      = "arn:aws:secretsmanager:us-east-1:111122223333:secret:cmul8"
  registry_url             = "111122223333.dkr.ecr.us-east-1.amazonaws.com/cmul8"
  tls_secret_name          = "cmul8-tls"
}
