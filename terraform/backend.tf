terraform {
  backend "gcs" {
    bucket = "isw-pipeline-tfstate"
    prefix = "terraform/state"
  }
}
