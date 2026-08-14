locals {
  labels = merge(
    {
      environment = "production"
      managed_by  = "terraform"
      service     = "tutorboard"
    },
    var.labels,
  )
}

data "yandex_compute_image" "ubuntu" {
  family = var.ubuntu_image_family
}

resource "yandex_vpc_network" "production" {
  name   = "${var.instance_name}-network"
  labels = local.labels
}

resource "yandex_vpc_subnet" "production" {
  name           = "${var.instance_name}-subnet"
  zone           = var.zone
  network_id     = yandex_vpc_network.production.id
  v4_cidr_blocks = [var.subnet_cidr]
  labels         = local.labels
}

resource "yandex_vpc_security_group" "production" {
  name       = "${var.instance_name}-security-group"
  network_id = yandex_vpc_network.production.id
  labels     = local.labels

  ingress {
    description    = "Restricted SSH administration"
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = var.ssh_allowed_cidrs
  }

  ingress {
    description    = "HTTP for ACME redirect and challenge"
    protocol       = "TCP"
    port           = 80
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description    = "HTTPS and WebSocket"
    protocol       = "TCP"
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description    = "HTTP/3"
    protocol       = "UDP"
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description    = "Application and package egress"
    protocol       = "ANY"
    from_port      = 0
    to_port        = 65535
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "yandex_vpc_address" "production" {
  name                = "${var.instance_name}-public-ip"
  deletion_protection = var.deletion_protection
  labels              = local.labels

  external_ipv4_address {
    zone_id = var.zone
  }
}

resource "yandex_iam_service_account" "production_vm" {
  name        = "${var.instance_name}-vm"
  description = "Reads the TutorBoard production payload from Lockbox."
}

resource "yandex_lockbox_secret_iam_binding" "production_vm_payload" {
  secret_id = var.lockbox_secret_id
  role      = "lockbox.payloadViewer"
  members   = ["serviceAccount:${yandex_iam_service_account.production_vm.id}"]
}

resource "yandex_compute_instance" "production" {
  name                      = var.instance_name
  hostname                  = var.instance_name
  platform_id               = var.platform_id
  zone                      = var.zone
  service_account_id        = yandex_iam_service_account.production_vm.id
  allow_stopping_for_update = true
  deletion_protection       = var.deletion_protection
  labels                    = local.labels

  resources {
    cores         = var.cores
    memory        = var.memory_gb
    core_fraction = 100
  }

  boot_disk {
    auto_delete = true
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = var.boot_disk_gb
      type     = "network-ssd"
    }
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.production.id
    nat                = true
    nat_ip_address     = yandex_vpc_address.production.external_ipv4_address[0].address
    security_group_ids = [yandex_vpc_security_group.production.id]
  }

  metadata = {
    user-data          = templatefile("${path.module}/cloud-init.tftpl", {
      lockbox_secret_id = var.lockbox_secret_id
      ssh_public_key    = var.ssh_public_key
    })
    serial-port-enable = "0"
  }

  metadata_options {
    gce_http_endpoint = 1
    gce_http_token    = 1
  }

  scheduling_policy {
    preemptible = false
  }

  depends_on = [yandex_lockbox_secret_iam_binding.production_vm_payload]
}

resource "yandex_dns_recordset" "production" {
  count   = var.dns_zone_id == null ? 0 : 1
  zone_id = var.dns_zone_id
  name    = "${var.domain}."
  type    = "A"
  ttl     = 300
  data    = [yandex_vpc_address.production.external_ipv4_address[0].address]
}
