output "instance_id" {
  description = "Compute Cloud VM ID."
  value       = yandex_compute_instance.production.id
}

output "public_ip" {
  description = "Reserved public IPv4 address."
  value       = yandex_vpc_address.production.external_ipv4_address[0].address
}

output "application_url" {
  description = "Expected HTTPS application URL after DNS and deployment."
  value       = "https://${var.domain}"
}

output "ansible_inventory_host" {
  description = "Host line for the example Ansible inventory."
  value       = "tutorboard ansible_host=${yandex_vpc_address.production.external_ipv4_address[0].address} ansible_user=tutor-deploy"
}
