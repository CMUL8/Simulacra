output "runtime_contract" {
  description = "Validated references consumed by CMUL8 preflight; no resources are provisioned."
  value       = local.runtime_contract
  sensitive   = true
}
output "customer_managed_services" {
  value = ["aks", "postgres", "redis", "blob-storage", "key-vault", "registry", "dns-tls"]
}
