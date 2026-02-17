# Ingestion & Parsing Service

Cloud Run service that fetches an ISW daily assessment report, parses it into structured JSON, and stores the result in GCS.

## Architecture Reference

See [architecture.md](../../architecture.md) § 3.1 — Ingestion & Parsing.

## API Contract

### `POST /`

Ingest and parse an ISW report.

**Request body:**

```json
{
  "date": "2026-02-16",
  "html_gcs_path": "gs://isw-video-pipeline/raw/2026-02-16/report.html" // optional
}
```

- `date` (required) — ISO date string (YYYY-MM-DD).
- `html_gcs_path` (optional) — GCS path to a pre-uploaded HTML file. If omitted, the service fetches the report from the ISW website.

**Response body:**

```json
{
  "parsed_report_path": "gs://isw-video-pipeline/parsed/2026-02-16/parsed_report.json"
}
```

### `GET /health`

Readiness probe.

```json
{ "status": "ok" }
```

## Parsed Report Schema

```json
{
  "date": "2026-02-16",
  "title": "Russian Offensive Campaign Assessment, February 16, 2026",
  "toplines": [
    {
      "headline": "Bold lead summary...",
      "body": "Detailed text..."
    }
  ],
  "key_takeaways": ["Point 1...", "Point 2..."],
  "sections": [
    {
      "id": "ukr-ops",
      "title": "Ukrainian Operations in the Russian Federation",
      "body": "Section text...",
      "map_url": "https://understandingwar.org/wp-content/uploads/2026/02/...",
      "map_title": "Assessed Control of Terrain..."
    }
  ],
  "overview_map_url": "https://understandingwar.org/.../Russo-Ukrainian-War-February-16-2026.webp",
  "source_refs": ["https://tass.ru/...", "..."]
}
```

## Environment Variables

| Variable         | Description                  | Default              |
| ---------------- | ---------------------------- | -------------------- |
| `BUCKET_NAME`    | GCS bucket for pipeline data | `isw-video-pipeline` |
| `GCP_PROJECT_ID` | GCP project ID               | (none)               |
| `ISW_BASE_URL`   | ISW report URL template      | (ISW default)        |

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Run locally
uvicorn app:app --reload --port 8080
```

## Docker

```bash
# Build
docker build -t ingestion .

# Run locally
docker run -p 8080:8080 \
  -e BUCKET_NAME=isw-video-pipeline \
  -e GCP_PROJECT_ID=dan-learning-0929 \
  ingestion
```

## Deployment

```bash
# Build & push
docker build --platform linux/amd64 -t us-central1-docker.pkg.dev/dan-learning-0929/isw-pipeline/ingestion:latest .
docker push us-central1-docker.pkg.dev/dan-learning-0929/isw-pipeline/ingestion:latest

# Deploy via Terraform
cd ../../terraform && terraform apply
```

## Testing

```bash
# Run unit tests
pytest tests/test_parser.py -v

# Test deployed service
curl -X POST \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-02-16"}' \
  https://ingestion-xulryhbbyq-uc.a.run.app/
```
