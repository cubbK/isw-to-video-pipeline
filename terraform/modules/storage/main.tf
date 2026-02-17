variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "bucket_name" {
  type = string
}

resource "google_storage_bucket" "pipeline" {
  name                        = var.bucket_name
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true # POC only
}

output "bucket_name" {
  value = google_storage_bucket.pipeline.name
}
