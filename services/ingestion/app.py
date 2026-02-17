"""Ingestion & Parsing — FastAPI service.

Fetches an ISW daily report HTML (from a URL or from GCS), parses it
into structured JSON, and writes the result to GCS.
"""

from __future__ import annotations

import json
import logging
import os

import google.cloud.logging as gcloud_logging
import httpx
from fastapi import FastAPI, HTTPException
from google.cloud import storage
from pydantic import BaseModel

from parser import ParsedReport, parse_report

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BUCKET_NAME = os.environ.get("BUCKET_NAME", "isw-video-pipeline")
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
ISW_BASE_URL = os.environ.get(
    "ISW_BASE_URL",
    "https://understandingwar.org/research/russia-ukraine/"
    "russian-offensive-campaign-assessment-{slug}/",
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
if os.environ.get("K_SERVICE"):
    # Running on Cloud Run — use structured logging
    client = gcloud_logging.Client()
    client.setup_logging()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="ISW Ingestion Service")


class IngestRequest(BaseModel):
    """Inbound request body."""

    date: str  # ISO date, e.g. "2026-02-16"
    html_gcs_path: str | None = None  # Optional: gs:// path to pre-uploaded HTML


class IngestResponse(BaseModel):
    """Response body."""

    parsed_report_path: str  # GCS path to the parsed JSON


def _date_to_slug(date_str: str) -> str:
    """Convert '2026-02-16' → 'february-16-2026'."""
    import datetime

    dt = datetime.date.fromisoformat(date_str)
    return dt.strftime("%B-%-d-%Y").lower()


def _fetch_html_from_url(date_str: str) -> str:
    """Fetch ISW report HTML from the public website."""
    slug = _date_to_slug(date_str)
    url = ISW_BASE_URL.format(slug=slug)
    logger.info("Fetching ISW report from %s", url)

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                )
            },
        )
        resp.raise_for_status()
    return resp.text


def _fetch_html_from_gcs(gcs_path: str) -> str:
    """Download HTML from a GCS path like gs://bucket/raw/2026-02-16/report.html."""
    # Strip gs://bucket/ prefix
    path = gcs_path.replace(f"gs://{BUCKET_NAME}/", "")
    gcs = storage.Client()
    bucket = gcs.bucket(BUCKET_NAME)
    blob = bucket.blob(path)
    return blob.download_as_text()


def _upload_raw_html(date_str: str, html: str) -> str:
    """Store the raw HTML in GCS for reproducibility."""
    gcs = storage.Client()
    bucket = gcs.bucket(BUCKET_NAME)
    blob_path = f"raw/{date_str}/report.html"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(html, content_type="text/html")
    return f"gs://{BUCKET_NAME}/{blob_path}"


def _upload_parsed_report(date_str: str, report: ParsedReport) -> str:
    """Upload parsed JSON to GCS and return the path."""
    gcs = storage.Client()
    bucket = gcs.bucket(BUCKET_NAME)
    blob_path = f"parsed/{date_str}/parsed_report.json"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        content_type="application/json",
    )
    return f"gs://{BUCKET_NAME}/{blob_path}"


@app.post("/", response_model=IngestResponse)
async def ingest(request: IngestRequest) -> IngestResponse:
    """Main endpoint: fetch, parse, and store an ISW report."""
    logger.info("Ingestion request for date=%s", request.date)

    try:
        # 1. Obtain HTML
        if request.html_gcs_path:
            html = _fetch_html_from_gcs(request.html_gcs_path)
        else:
            html = _fetch_html_from_url(request.date)

        # 2. Store raw HTML
        _upload_raw_html(request.date, html)
        logger.info("Raw HTML stored for date=%s", request.date)

        # 3. Parse
        report = parse_report(html)
        logger.info(
            "Parsed report: %d toplines, %d key_takeaways, %d sections",
            len(report.toplines),
            len(report.key_takeaways),
            len(report.sections),
        )

        # 4. Upload parsed JSON
        parsed_path = _upload_parsed_report(request.date, report)
        logger.info("Parsed report stored at %s", parsed_path)

        return IngestResponse(parsed_report_path=parsed_path)

    except httpx.HTTPStatusError as exc:
        logger.exception("Failed to fetch ISW report")
        raise HTTPException(
            status_code=502,
            detail=f"Upstream fetch failed: {exc.response.status_code}",
        ) from exc
    except ValueError as exc:
        logger.exception("Parse error")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error during ingestion")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
async def health() -> dict:
    """Readiness probe."""
    return {"status": "ok"}
