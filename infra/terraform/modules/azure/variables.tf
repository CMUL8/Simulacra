variable "tenant_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9_-]{1,62}$", var.tenant_id))
    error_message = "tenant_id must be a lowercase deployment identifier."
  }
}
variable "environment" { type = string }
variable "location" { type = string }
variable "aks_cluster_id" { type = string }
variable "postgres_endpoint" {
  type      = string
  sensitive = true
}
variable "redis_endpoint" {
  type      = string
  sensitive = true
}
variable "storage_account_id" { type = string }
variable "key_vault_id" { type = string }
variable "registry_url" { type = string }
variable "tls_secret_name" { type = string }
