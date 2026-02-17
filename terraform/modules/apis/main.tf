variable "project_id" {
  type = string
}

# APIs needed incrementally — add here as work items progress
locals {
  apis = [
    "run.googleapis.com",
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
