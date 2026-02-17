variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "service_name" {
  description = "Name of the Cloud Run service"
  type        = string
}

variable "image" {
  description = "Full Docker image path"
  type        = string
}

variable "service_account_email" {
  description = "Service account email to use"
  type        = string
}

variable "env_vars" {
  description = "Environment variables as a map"
  type        = map(string)
  default     = {}
}

variable "memory" {
  description = "Memory limit"
  type        = string
  default     = "512Mi"
}

variable "cpu" {
  description = "CPU limit"
  type        = string
  default     = "1"
}

variable "timeout" {
  description = "Request timeout in seconds"
  type        = number
  default     = 300
}

variable "max_instances" {
  description = "Max number of instances"
  type        = number
  default     = 2
}

resource "google_cloud_run_v2_service" "service" {
  project  = var.project_id
  location = var.region
  name     = var.service_name
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = var.service_account_email

    scaling {
      max_instance_count = var.max_instances
    }

    timeout = "${var.timeout}s"

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
      }

      dynamic "env" {
        for_each = var.env_vars
        content {
          name  = env.key
          value = env.value
        }
      }

      # Health check
      startup_probe {
        http_get {
          path = "/health"
        }
        initial_delay_seconds = 3
        period_seconds        = 3
        failure_threshold     = 3
      }
    }
  }

  # Do not allow unauthenticated access (IAM-only, ADR-006)
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image, # allow image tag updates outside TF
    ]
  }
}

# IAM: deny allUsers (no-allow-unauthenticated)
# The service is IAM-protected by default in v2.

output "service_url" {
  value = google_cloud_run_v2_service.service.uri
}

output "service_name" {
  value = google_cloud_run_v2_service.service.name
}
