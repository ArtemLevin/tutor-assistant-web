variable "cloud_id" {
  description = "Yandex Cloud ID."
  type        = string
}

variable "folder_id" {
  description = "Yandex Cloud folder ID for the production resources."
  type        = string
}

variable "zone" {
  description = "Compute Cloud availability zone."
  type        = string
  default     = "ru-central1-a"
}

variable "instance_name" {
  description = "Production VM and related resource name prefix."
  type        = string
  default     = "tutorboard-production"
}

variable "domain" {
  description = "Public application domain without a scheme."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$", var.domain))
    error_message = "domain must be a valid DNS name without a scheme or trailing dot."
  }
}

variable "dns_zone_id" {
  description = "Optional existing Yandex Cloud DNS public zone ID."
  type        = string
  default     = null
  nullable    = true
}

variable "subnet_cidr" {
  description = "Private IPv4 CIDR for the application subnet."
  type        = string
  default     = "10.40.0.0/24"
}

variable "ssh_allowed_cidrs" {
  description = "Narrow IPv4 CIDRs allowed to reach SSH. Never use 0.0.0.0/0 in production."
  type        = list(string)

  validation {
    condition = (
      length(var.ssh_allowed_cidrs) > 0 &&
      !contains(var.ssh_allowed_cidrs, "0.0.0.0/0")
    )
    error_message = "Provide at least one restricted SSH CIDR; 0.0.0.0/0 is forbidden."
  }
}

variable "ssh_public_key" {
  description = "OpenSSH public key installed for tutor-deploy by cloud-init."
  type        = string
  sensitive   = true
}

variable "lockbox_secret_id" {
  description = "Existing Lockbox secret containing production and GHCR credentials. Payload is never read by Terraform."
  type        = string
}

variable "platform_id" {
  description = "Compute Cloud VM platform."
  type        = string
  default     = "standard-v3"
}

variable "cores" {
  description = "Production VM vCPU count."
  type        = number
  default     = 8
}

variable "memory_gb" {
  description = "Production VM RAM in GiB."
  type        = number
  default     = 32
}

variable "boot_disk_gb" {
  description = "Encrypted-at-rest boot disk size in GiB."
  type        = number
  default     = 200
}

variable "deletion_protection" {
  description = "Protect the reserved production address from accidental deletion."
  type        = bool
  default     = true
}

variable "ubuntu_image_family" {
  description = "Yandex Cloud Marketplace Ubuntu image family."
  type        = string
  default     = "ubuntu-2404-lts"
}

variable "labels" {
  description = "Additional labels applied to supported resources."
  type        = map(string)
  default     = {}
}
