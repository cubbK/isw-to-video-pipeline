variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "dan-learning-0929"
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "us-central1"
}

variable "bucket_name" {
  description = "GCS bucket for pipeline data"
  type        = string
  default     = "isw-video-pipeline"
}

variable "artifact_registry_repo" {
  description = "Artifact Registry repository name"
  type        = string
  default     = "isw-pipeline"
}
