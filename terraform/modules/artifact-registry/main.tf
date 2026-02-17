variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "repo_name" {
  type = string
}

resource "google_artifact_registry_repository" "repo" {
  project       = var.project_id
  location      = var.region
  repository_id = var.repo_name
  format        = "DOCKER"
  description   = "ISW-to-Video pipeline container images"
}

output "repo_url" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo_name}"
}
