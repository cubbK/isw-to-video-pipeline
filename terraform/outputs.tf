output "bucket_name" {
  description = "GCS bucket name"
  value       = module.storage.bucket_name
}

output "ingestion_service_url" {
  description = "URL of the ingestion Cloud Run service"
  value       = module.ingestion.service_url
}
