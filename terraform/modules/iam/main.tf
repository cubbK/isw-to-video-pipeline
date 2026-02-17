variable "project_id" {
  type = string
}

variable "bucket_name" {
  type = string
}

resource "google_service_account" "pipeline_sa" {
  project      = var.project_id
  account_id   = "isw-pipeline-sa"
  display_name = "ISW Pipeline Shared Service Account"
}

# Storage Object Admin on the pipeline bucket
resource "google_storage_bucket_iam_member" "sa_storage" {
  bucket = var.bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Cloud Run Invoker (for Workflows to call services)
resource "google_project_iam_member" "sa_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Logging writer
resource "google_project_iam_member" "sa_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

output "service_account_email" {
  value = google_service_account.pipeline_sa.email
}
