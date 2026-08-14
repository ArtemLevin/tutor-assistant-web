terraform {
  required_version = ">= 1.8.0"

  required_providers {
    yandex = {
      source  = "yandex-cloud/yandex"
      version = "0.220.0"
    }
  }
}

provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
  zone      = var.zone
}
