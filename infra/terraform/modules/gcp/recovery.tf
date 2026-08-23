variable "recovery_contract" {
  description = "Customer-attested recovery controls; this module does not configure or test them."
  type = object({
    postgres_pitr_enabled       = bool
    object_versioning_enabled   = bool
    registry_digest_retained    = bool
    secret_recovery_documented  = bool
    tested_restore_reference    = string
    rpo_minutes                 = number
    rto_minutes                 = number
  })
  validation {
    condition     = var.recovery_contract.rpo_minutes > 0 && var.recovery_contract.rto_minutes > 0
    error_message = "Recovery RPO and RTO must be positive minutes."
  }
}

variable "network_contract" {
  description = "Customer-attested private connectivity; no network resources are provisioned."
  type = object({
    private_cluster_endpoint = bool
    private_postgres         = bool
    private_redis            = bool
    private_object_storage   = bool
    ingress_restricted       = bool
  })
}

locals {
  recovery_gaps = compact([
    var.recovery_contract.postgres_pitr_enabled ? "" : "postgres-pitr",
    var.recovery_contract.object_versioning_enabled ? "" : "object-versioning",
    var.recovery_contract.registry_digest_retained ? "" : "registry-retention",
    var.recovery_contract.secret_recovery_documented ? "" : "secret-recovery",
    trimspace(var.recovery_contract.tested_restore_reference) != "" ? "" : "tested-restore-evidence",
  ])
  network_gaps = compact([
    var.network_contract.private_cluster_endpoint ? "" : "private-cluster-endpoint",
    var.network_contract.private_postgres ? "" : "private-postgres",
    var.network_contract.private_redis ? "" : "private-redis",
    var.network_contract.private_object_storage ? "" : "private-object-storage",
    var.network_contract.ingress_restricted ? "" : "restricted-ingress",
  ])
}

output "network_assessment" {
  value = {
    ready_for_operator_review = length(local.network_gaps) == 0
    gaps                      = local.network_gaps
    attestation_only          = true
  }
}

output "recovery_assessment" {
  value = {
    ready_for_operator_review = length(local.recovery_gaps) == 0
    gaps                      = local.recovery_gaps
    rpo_minutes               = var.recovery_contract.rpo_minutes
    rto_minutes               = var.recovery_contract.rto_minutes
    attestation_only          = true
  }
}
