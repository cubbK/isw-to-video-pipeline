provider "google" {
  project = var.project_id
  region  = var.region
}

# --- WI-0: Foundation ---

module "apis" {
  source     = "./modules/apis"
  project_id = var.project_id
}

module "storage" {
  source      = "./modules/storage"
  project_id  = var.project_id
  region      = var.region
  bucket_name = var.bucket_name
}

module "artifact_registry" {
  source     = "./modules/artifact-registry"
  project_id = var.project_id
  region     = var.region
  repo_name  = var.artifact_registry_repo
  depends_on = [module.apis]
}

module "iam" {
  source      = "./modules/iam"
  project_id  = var.project_id
  bucket_name = module.storage.bucket_name
  depends_on  = [module.apis]
}

# --- WI-1: Ingestion & Parsing ---

module "ingestion" {
  source = "./modules/cloud-run-service"

  project_id   = var.project_id
  region       = var.region
  service_name = "ingestion"
  image        = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}/ingestion:latest"

  service_account_email = module.iam.service_account_email

  env_vars = {
    BUCKET_NAME    = module.storage.bucket_name
    GCP_PROJECT_ID = var.project_id
  }

  depends_on = [module.apis, module.artifact_registry]
}
