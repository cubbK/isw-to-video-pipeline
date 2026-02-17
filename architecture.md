# ISW-to-Video Pipeline — POC Architecture

> **Status**: Approved  
> **Date**: 2026-02-17  
> **Scope**: Proof of Concept — single environment, incremental Terraform

---

## 1. Overview

An automated daily pipeline that ingests an ISW (Institute for the Study of War) report about the war in Ukraine, summarizes it into a ~5-minute news-style video with maps, images, and narration, and uploads it to YouTube.

```
                                       ┌─── tts_narration ──────┐
                                       │                         │
 ingest_report ──▶ generate_script ────┼─── render_maps ─────────┼──▶ assemble_video ──▶ upload_youtube
                                       │                         │
                                       ├─── search_images ──────┘
                                       │
                                       └─── generate_title_cards ┘
```

**Trigger**: Cloud Scheduler → Cloud Workflows (daily at 16:30 ET)

### POC Simplifications

| Production concern       | POC approach                                                          |
| ------------------------ | --------------------------------------------------------------------- |
| Multi-environment        | **Single environment** — no dev/staging/prod split                    |
| Per-service IAM          | **One shared service account** for all Cloud Run services             |
| Per-service buckets      | **One GCS bucket** for all pipeline data                              |
| Data retention lifecycle | **Skip** — manual cleanup during POC                                  |
| Monitoring & alerting    | **Cloud Logging only** — no custom dashboards or alert policies       |
| CI/CD pipeline           | **Manual deploys** — `gcloud run deploy` / `terraform apply` from CLI |
| Terraform at the end     | **Terraform ships with each work item** — infra grows incrementally   |

---

## 2. Terraform Strategy

### Principle: Infrastructure Ships With Code

Every work item includes both the application code **and** the Terraform to deploy it. There is no "Phase 9: do all the Terraform". When a work item is done, it's deployed and running.

### Layout

```
terraform/
├── main.tf              # Root module — composes all sub-modules
├── variables.tf         # project_id, region, bucket name
├── outputs.tf           # Service URLs, bucket name
├── versions.tf          # Provider versions (already exists)
├── backend.tf           # GCS remote state
└── modules/
    ├── apis/            # google_project_service for required APIs
    ├── storage/         # Single GCS bucket
    ├── artifact-registry/
    ├── iam/             # Single shared SA + role bindings
    ├── secrets/         # Secret Manager secrets
    ├── cloud-run-service/   # Reusable: one Cloud Run service
    ├── cloud-run-job/       # Reusable: one Cloud Run Job
    └── workflows/           # Workflow definition + Cloud Scheduler
```

No `environments/` directory. The root `main.tf` **is** the environment.

### Incremental Growth Per Work Item

| Work Item | New Terraform added                                                                                                        |
| --------- | -------------------------------------------------------------------------------------------------------------------------- |
| **WI-0**  | `versions.tf`, `backend.tf`, `variables.tf`, `modules/apis`, `modules/storage`, `modules/iam`, `modules/artifact-registry` |
| **WI-1**  | `module "ingestion"` using `modules/cloud-run-service`                                                                     |
| **WI-2**  | `module "script_gen"` using `modules/cloud-run-service`, add `aiplatform` to APIs                                          |
| **WI-3**  | `module "tts"` using `modules/cloud-run-service`, add `texttospeech` to APIs                                               |
| **WI-4**  | `module "map_renderer"` + `module "title_gen"` using `modules/cloud-run-service`                                           |
| **WI-5**  | `module "image_search"` using `modules/cloud-run-service`, add `customsearch` to APIs, `modules/secrets` for API key       |
| **WI-6**  | `module "video_assembly"` using `modules/cloud-run-job`                                                                    |
| **WI-7**  | `module "youtube_upload"` using `modules/cloud-run-service`, add YouTube OAuth to secrets                                  |
| **WI-8**  | `modules/workflows` — workflow definition + Cloud Scheduler trigger                                                        |

Each `terraform apply` is additive. Existing resources are never destroyed when adding a new module.

---

## 3. Components

### 3.1 Ingestion & Parsing — Cloud Run Service

| Field       | Value                                                   |
| ----------- | ------------------------------------------------------- |
| GCP Service | Cloud Run                                               |
| Purpose     | Fetch daily ISW HTML report, parse into structured JSON |
| Inputs      | ISW report URL (or HTML file uploaded to GCS)           |
| Outputs     | `gs://<bucket>/parsed/<date>/parsed_report.json`        |

**Details:**

- Python service using BeautifulSoup/lxml.
- ISW reports have a consistent HTML structure:
  - `#toplines` div — bold-lead summary paragraphs (highest priority content).
  - `#key-takeaways` div — ordered list of key points.
  - Section divs with `data-id` attributes (`russian-ne`, `ru-me`, etc.) — battlefield details grouped by front direction.
  - `conflict-map-block` divs — contain `.webp` map image URLs and titles.
  - Footnote references — source links.
- Parser extracts all of the above into structured JSON:

```json
{
  "date": "2026-02-16",
  "title": "Russian Offensive Campaign Assessment, February 16, 2026",
  "toplines": [
    {
      "headline": "Russian officials are unlikely to deviate from...",
      "body": "Kremlin Spokesperson Dmitry Peskov stated..."
    }
  ],
  "key_takeaways": [
    "Russian officials are unlikely to deviate from their original war demands...",
    "Russia may try to exploit another temporary moratorium..."
  ],
  "sections": [
    {
      "id": "ukr-ops",
      "title": "Ukrainian Operations in the Russian Federation",
      "body": "...",
      "map_url": "https://understandingwar.org/wp-content/uploads/2026/02/...",
      "map_title": "Assessed Control of Terrain in the Sumy Direction..."
    }
  ],
  "overview_map_url": "https://understandingwar.org/wp-content/uploads/2026/02/Russo-Ukrainian-War-February-16-2026.webp",
  "source_refs": ["https://t.me/...", "..."]
}
```

---

### 3.2 NLP Summarization & Script Generation — Vertex AI Gemini

| Field       | Value                                                 |
| ----------- | ----------------------------------------------------- |
| GCP Service | Vertex AI Generative AI (Gemini 2.0 Flash)            |
| Purpose     | Summarize parsed report into a timed narration script |
| Inputs      | `parsed_report.json`                                  |
| Outputs     | `gs://<bucket>/scripts/<date>/script.json`            |

**Details:**

- Call Vertex AI Gemini 2.0 Flash with a structured prompt.
- Input priorities: Key Takeaways → Toplines → most significant section content.
- Output: ~750–900 words (≈5 min at TTS natural pace), split into 6–8 segments.
- Use Gemini JSON mode to enforce structured output:

```json
{
  "title": "Ukraine War Update — February 16, 2026",
  "description": "Auto-generated YouTube description text...",
  "tags": ["Ukraine", "Russia", "ISW", "war update"],
  "segments": [
    {
      "segment_id": 1,
      "segment_title": "Geneva Talks Preview",
      "narration_text": "Russian officials are heading to Geneva...",
      "visual_type": "web_image",
      "image_search_query": "Geneva diplomatic summit Ukraine Russia 2026",
      "map_region": null,
      "estimated_duration_seconds": 45
    },
    {
      "segment_id": 2,
      "segment_title": "Energy Infrastructure at Risk",
      "narration_text": "Ukraine plans to raise the issue of...",
      "visual_type": "map",
      "image_search_query": null,
      "map_region": "overview",
      "estimated_duration_seconds": 40
    }
  ]
}
```

- Persona: neutral news anchor, factual, no editorializing.
- Validation: post-generation check that script only references facts present in input.
- For `web_image` segments, Gemini generates an `image_search_query` — a concise, specific search string designed to find a relevant editorial/news photo on the open web.

---

### 3.3 Text-to-Speech — Cloud Text-to-Speech API

| Field       | Value                                                                         |
| ----------- | ----------------------------------------------------------------------------- |
| GCP Service | Cloud Text-to-Speech (Neural2 or Studio voices)                               |
| Purpose     | Convert narration text into speech audio                                      |
| Inputs      | `script.json` segment `narration_text` fields                                 |
| Outputs     | `gs://<bucket>/audio/<date>/segment_01.wav` … `segment_N.wav` + `timing.json` |

**Details:**

- Voice: `en-US-Studio-Q` or `en-US-Neural2-J` (professional male news voice).
- Speaking rate: 0.95 (slightly slower for clarity).
- SSML markup for Ukrainian/Russian place name pronunciation.
- Maintain a static pronunciation dictionary at `gs://<bucket>/assets/pronunciation_dict.json`:

```json
{
  "Pokrovsk": "<phoneme alphabet='ipa' ph='pɔˈkrɔfsk'>Pokrovsk</phoneme>",
  "Vovchansk": "<phoneme alphabet='ipa' ph='vɔvˈtʃɑnsk'>Vovchansk</phoneme>",
  "Zaporizhia": "<phoneme alphabet='ipa' ph='zɑpɔˈrʲiʒʒɑ'>Zaporizhia</phoneme>"
}
```

- Record actual audio duration per segment into `timing.json` for video assembly.

---

### 3.4 Visual Asset Generation (3 parallel branches)

#### 3.4a Map Rendering — Cloud Run Service

| Field       | Value                                                           |
| ----------- | --------------------------------------------------------------- |
| GCP Service | Cloud Run                                                       |
| Purpose     | Download ISW maps, crop/annotate for video                      |
| Inputs      | Map URLs from `parsed_report.json` + `script.json` `map_region` |
| Outputs     | `gs://<bucket>/visuals/<date>/maps/*.png` (1920×1080)           |

**Details:**

- Download ISW's published `.webp` maps (authoritative source — see ADR-001).
- Post-process with Pillow/OpenCV:
  - Crop to relevant region based on `map_region`.
  - Add zoom/pan keyframe metadata for Ken Burns effect.
  - Add branded overlay (date, segment title, source attribution: "Source: ISW").
  - Resize/pad to 1920×1080.
- Map region → ISW map URL mapping derived from parsed `conflict-map-block` sections.

#### 3.4b Web Image Search — Cloud Run Service

| Field       | Value                                                                              |
| ----------- | ---------------------------------------------------------------------------------- |
| GCP Service | Cloud Run                                                                          |
| Purpose     | Find relevant editorial/news photos from the web for non-map segments              |
| Inputs      | `image_search_query` from segments where `visual_type = "web_image"`               |
| Outputs     | `gs://<bucket>/visuals/<date>/images/*.png` (1920×1080) + `image_attribution.json` |

**Details:**

- Uses **Google Custom Search JSON API** (Image Search) to find relevant photos.
- Search flow per segment:
  1. Take the `image_search_query` from the script.
  2. Call Custom Search API with `searchType=image`, `imgSize=xlarge`, `imgType=photo`, `rights=cc_publicdomain|cc_attribute`.
  3. Apply filtering: prefer news agencies (Reuters, AP, AFP, EPA), exclude social media, minimum 1200px wide.
  4. Download top-ranked image, resize/crop to 1920×1080, add source attribution overlay.
  5. Save to GCS and record attribution metadata.

- **Attribution tracking** — `image_attribution.json` per image (source URL, source name, license, search query). Included in YouTube description.

- **Fallback chain**: (1) retry with broader query, (2) curated fallback stock images from `gs://<bucket>/assets/fallback_images/`, (3) use overview map.

- **API quota**: ~3 queries/day, well within 100 free/day limit.

#### 3.4c Title Cards & Lower Thirds — Cloud Run Service

| Field       | Value                                            |
| ----------- | ------------------------------------------------ |
| GCP Service | Cloud Run (same service as map rendering)        |
| Purpose     | Generate intro/outro cards, lower-third overlays |
| Inputs      | Report date, segment titles from `script.json`   |
| Outputs     | `gs://<bucket>/visuals/<date>/titles/*.png`      |

**Details:**

- Intro card, lower thirds, and outro card generated with Pillow from brand templates in `gs://<bucket>/assets/brand_templates/`.

---

### 3.5 Video Assembly — Cloud Run Jobs (FFmpeg)

| Field       | Value                                                     |
| ----------- | --------------------------------------------------------- |
| GCP Service | Cloud Run Jobs (4 vCPU, 8 GB RAM)                         |
| Purpose     | Assemble all assets into final YouTube-ready MP4          |
| Inputs      | Audio segments, visual assets, timing metadata            |
| Outputs     | `gs://<bucket>/output/<date>/final.mp4` + `subtitles.srt` |

**Details:**

- Python script builds an FFmpeg filter graph:
  1. **Intro** (3s) — title card with fade-in + background music.
  2. **Segments 1–N** — each segment's image/map with Ken Burns effect, timed to actual audio duration.
  3. **Transitions** — 0.5s crossfade between segments.
  4. **Lower thirds** — text overlays animated in/out.
  5. **Background music** — royalty-free news-style track mixed at -20dB under narration.
  6. **Outro** (5s) — source attribution + subscribe CTA.
- Encoding: H.264 High Profile, AAC 192kbps, ~8 Mbps video bitrate.
- Subtitle generation: produce `.srt` from segment narration text + audio timing.
- Timeout: 15 minutes.

---

### 3.6 YouTube Upload — Cloud Run Service

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| GCP Service | Cloud Run                                                             |
| Purpose     | Upload video to YouTube with metadata                                 |
| Inputs      | `final.mp4`, `subtitles.srt`, `script.json`, `image_attribution.json` |
| Outputs     | YouTube video ID                                                      |

**Details:**

- YouTube Data API v3.
- Upload as **unlisted** (manually set to public after review).
- Metadata from `script.json`: title, description (key takeaways + ISW link + image credits), tags, thumbnail (overview map), captions (`.srt`).
- OAuth2 credentials in Secret Manager.

---

### 3.7 Orchestration — Cloud Workflows

| Field       | Value                              |
| ----------- | ---------------------------------- |
| GCP Service | Cloud Workflows + Cloud Scheduler  |
| Purpose     | Orchestrate the full pipeline      |
| Trigger     | Cloud Scheduler, daily at 16:30 ET |

**Why Workflows over Composer:** ~$0.15/month vs ~$300+/month. Simple DAG, no need for Airflow.

**Workflow definition (pseudo-YAML):**

```yaml
main:
  steps:
    - init:
        assign:
          - date: ${sys.now()}
          - bucket: "isw-video-pipeline"

    - ingest_report:
        call: http.post
        args:
          url: ${INGEST_SERVICE_URL}
          body: { date: ${date} }
        result: ingest_result

    - generate_script:
        call: http.post
        args:
          url: ${SCRIPT_SERVICE_URL}
          body: { parsed_report_path: ${ingest_result.body.parsed_report_path} }
        result: script_result

    - generate_assets:
        parallel:
          branches:
            - tts_narration:
                call: http.post
                args:
                  url: ${TTS_SERVICE_URL}
                  body: { script_path: ${script_result.body.script_path} }
                result: tts_result
            - render_maps:
                call: http.post
                args:
                  url: ${MAP_SERVICE_URL}
                  body:
                    parsed_report_path: ${ingest_result.body.parsed_report_path}
                    script_path: ${script_result.body.script_path}
                result: maps_result
            - search_images:
                call: http.post
                args:
                  url: ${IMAGE_SEARCH_SERVICE_URL}
                  body: { script_path: ${script_result.body.script_path} }
                result: images_result
            - generate_titles:
                call: http.post
                args:
                  url: ${TITLES_SERVICE_URL}
                  body: { script_path: ${script_result.body.script_path} }
                result: titles_result

    - assemble_video:
        call: http.post
        args:
          url: ${ASSEMBLY_SERVICE_URL}
          body:
            audio_path: ${tts_result.body.audio_path}
            maps_path: ${maps_result.body.maps_path}
            images_path: ${images_result.body.images_path}
            titles_path: ${titles_result.body.titles_path}
            script_path: ${script_result.body.script_path}
        result: assembly_result

    - upload_youtube:
        call: http.post
        args:
          url: ${UPLOAD_SERVICE_URL}
          body:
            video_path: ${assembly_result.body.video_path}
            subtitles_path: ${assembly_result.body.subtitles_path}
            script_path: ${script_result.body.script_path}
        result: upload_result

    - return_result:
        return:
          video_id: ${upload_result.body.video_id}
          video_url: ${upload_result.body.video_url}
```

**Error handling:** Each step has retry policy (3 retries, exponential backoff). Final failures logged to Cloud Logging.

---

## 4. GCS Bucket — Single Bucket

```
gs://isw-video-pipeline/
├── raw/<date>/report.html
├── parsed/<date>/parsed_report.json
├── scripts/<date>/script.json
├── audio/<date>/
│   ├── segment_01.wav … segment_N.wav
│   └── timing.json
├── visuals/<date>/
│   ├── maps/*.png
│   ├── images/*.png
│   └── titles/*.png
├── output/<date>/
│   ├── final.mp4
│   ├── subtitles.srt
│   └── image_attribution.json
└── assets/                         # Static, version-controlled
    ├── background_music.mp3
    ├── pronunciation_dict.json
    ├── brand_templates/
    ├── fallback_images/
    └── image_search_blocklist.json
```

No lifecycle rules for POC. One bucket, no environment suffix.

---

## 5. Security (POC-scoped)

| Concern          | POC Solution                                                     |
| ---------------- | ---------------------------------------------------------------- |
| Cloud Run auth   | `--no-allow-unauthenticated` + IAM-only (see ADR-006)            |
| Service identity | **One shared SA** (`isw-pipeline-sa`) with required roles        |
| Secrets          | Secret Manager for YouTube OAuth + Custom Search API key         |
| GCS access       | Shared SA has `roles/storage.objectAdmin` on the single bucket   |
| Vertex AI        | Shared SA has `roles/aiplatform.user`                            |
| Workflow auth    | Shared SA has `roles/run.invoker` to call all Cloud Run services |

**POC → Production upgrade path**: Split the shared SA into per-service SAs with least-privilege roles. Add data retention lifecycle rules.

---

## 6. Cost Estimate (Monthly — 30 videos)

| Component                         | Est. Cost   |
| --------------------------------- | ----------- |
| Cloud Workflows + Cloud Scheduler | ~$0.15      |
| Cloud Run (all services)          | ~$1–2       |
| Cloud Run Jobs (FFmpeg)           | ~$3–5       |
| Vertex AI Gemini 2.0 Flash        | ~$0.50      |
| Google Custom Search API          | $0 (free)   |
| Cloud Text-to-Speech (Studio)     | ~$24        |
| Cloud Storage                     | ~$0.20      |
| Artifact Registry                 | ~$0.20      |
| Secret Manager                    | ~$0.04      |
| **Total**                         | **~$30/mo** |

---

## 7. Required GCP APIs

Managed via `modules/apis` Terraform module. New APIs added incrementally per work item.

```
cloudrun.googleapis.com          # WI-0 (base)
storage.googleapis.com           # WI-0
artifactregistry.googleapis.com  # WI-0
secretmanager.googleapis.com     # WI-0
iam.googleapis.com               # WI-0
aiplatform.googleapis.com        # WI-2 (script gen)
texttospeech.googleapis.com      # WI-3 (TTS)
customsearch.googleapis.com      # WI-5 (image search)
workflows.googleapis.com         # WI-8 (orchestration)
cloudscheduler.googleapis.com    # WI-8
logging.googleapis.com           # WI-0
```

---

## 8. Architecture Decision Records

### ADR-001: Use ISW's published maps instead of custom map generation

- **Context**: ISW publishes high-quality control-of-terrain maps with every report, embedded as `.webp` images in the HTML.
- **Decision**: Download and reprocess ISW maps rather than generating custom maps.
- **Rationale**: ISW maps are the authoritative source, require zero ML infrastructure, and are already trusted by the audience.
- **Tradeoff**: Dependency on ISW's HTML structure. Mitigated by structural tests.

### ADR-002: Use Gemini Flash for summarization instead of fine-tuned model

- **Context**: We need to convert ~3000-word structured reports into ~800-word narration scripts.
- **Decision**: Use Vertex AI Gemini 2.0 Flash with structured prompting and JSON mode.
- **Rationale**: This is a reformatting/selection task, not a generation task. ~$0.01/report, zero training infrastructure.
- **Tradeoff**: Small hallucination risk. Mitigated with post-generation validation.

### ADR-003: FFmpeg on Cloud Run Jobs instead of ML-based video generation

- **Context**: We need to assemble still images + audio into a video with transitions and overlays.
- **Decision**: Use FFmpeg in a Cloud Run Jobs container.
- **Rationale**: Compositing problem, not a generation problem. FFmpeg is free, fast, and deterministic.

### ADR-004: Cloud Workflows instead of Cloud Composer

- **Context**: Pipeline runs once daily with a simple DAG.
- **Decision**: Use Cloud Workflows + Cloud Scheduler.
- **Rationale**: ~$0.15/month vs ~$300+/month for Composer.

### ADR-005: Web image search instead of AI image generation

- **Context**: Non-map segments need accompanying visuals.
- **Decision**: Use Google Custom Search API (Image Search) for real editorial/news photos.
- **Rationale**: More authentic, free tier sufficient, faster, no hallucinated visuals.
- **Tradeoff**: Dependency on image availability. Mitigated with fallback chain.

### ADR-006: IAM-based auth instead of network-level ingress restriction

- **Context**: Cloud Run services need to be invocable by Cloud Workflows but not publicly accessible.
- **Decision**: Use `ingress = all` with `--no-allow-unauthenticated` (IAM-only).
- **Rationale**: Avoids $7/month VPC connector. Google-recommended pattern for Workflows → Cloud Run.

### ADR-007: Single shared service account for POC

- **Context**: Production would use per-service SAs with least-privilege roles.
- **Decision**: Use one shared SA (`isw-pipeline-sa`) with a combined role set for the POC.
- **Rationale**: Reduces IAM complexity from ~8 SAs to 1. Acceptable for a POC with a single developer. The upgrade path is clear: split into per-service SAs before going to production.
- **Tradeoff**: Blast radius — any compromised service has access to all pipeline resources. Acceptable for POC scope.

### ADR-008: Incremental Terraform over big-bang infrastructure

- **Context**: The original plan had Terraform as Phase 9 (after all services were built).
- **Decision**: Each work item includes its Terraform. Infrastructure grows with the code.
- **Rationale**: (1) Terraform is tested at every step, not a risky big-bang. (2) Services are deployed and testable on real GCP immediately. (3) No manual `gcloud` provisioning that needs to be reverse-engineered into Terraform later. (4) Forces clean module boundaries early.
- **Tradeoff**: Slightly more overhead per work item. Worth it.

---

## 9. Implementation — Work Items

Each work item produces **working code + Terraform + tests**. Nothing is "done" until it's deployed.

### WI-0: Foundation

**Goal**: Project bootstrap — GCS bucket, Artifact Registry, shared SA, base APIs.

**Terraform**: `main.tf`, `backend.tf`, `variables.tf`, `outputs.tf`, `modules/apis`, `modules/storage`, `modules/iam`, `modules/artifact-registry`

**Deliverables**:

- GCS bucket `isw-video-pipeline` exists
- Artifact Registry repo `isw-pipeline` exists
- Shared SA `isw-pipeline-sa` exists with base roles
- Terraform state stored in GCS (`isw-pipeline-tfstate` bucket)
- `terraform plan` and `terraform apply` work cleanly

**Acceptance**: `gsutil ls gs://isw-video-pipeline/` succeeds.

---

### WI-1: Ingestion & Parsing

**Goal**: Cloud Run service that fetches an ISW report and produces `parsed_report.json`.

**Code**: `services/ingestion/` — Python + Dockerfile  
**Terraform**: Add `module "ingestion"` using `modules/cloud-run-service`  
**Tests**: Unit tests for HTML parser against the example report in `examples/`

**Acceptance**: `curl <service-url> -d '{"date":"2026-02-16"}'` returns parsed JSON path. File exists in GCS.

---

### WI-2: Script Generation

**Goal**: Cloud Run service that takes parsed report and produces a narration script via Gemini.

**Code**: `services/script-gen/` — Python + Dockerfile  
**Terraform**: Add `module "script_gen"`, add `aiplatform.googleapis.com` to APIs  
**Tests**: Prompt regression tests — golden input/output pairs

**Acceptance**: Given a parsed report in GCS, service produces a valid `script.json` with correct schema.

---

### WI-3: Text-to-Speech

**Goal**: Cloud Run service that converts script segments into audio files.

**Code**: `services/tts/` — Python + Dockerfile  
**Terraform**: Add `module "tts"`, add `texttospeech.googleapis.com` to APIs  
**Tests**: Integration test — verify audio files are valid WAV, `timing.json` has correct durations

**Acceptance**: Given a `script.json`, service produces numbered `.wav` files + `timing.json` in GCS.

---

### WI-4: Map Rendering + Title Cards

**Goal**: Cloud Run service that downloads ISW maps, processes them, and generates title cards.

**Code**: `services/visuals/` — Python + Dockerfile (single service, two endpoints: `/maps` and `/titles`)  
**Terraform**: Add `module "visuals"`  
**Tests**: Unit tests for image processing (resize, crop, overlay)

**Acceptance**: Given a parsed report + script, produces 1920×1080 map PNGs and title card PNGs in GCS.

---

### WI-5: Web Image Search

**Goal**: Cloud Run service that finds editorial photos for non-map segments.

**Code**: `services/image-search/` — Python + Dockerfile  
**Terraform**: Add `module "image_search"`, add `customsearch.googleapis.com` to APIs, add Custom Search API key to `modules/secrets`  
**Tests**: Unit tests for fallback chain, integration test with live API

**Acceptance**: Given a script with `web_image` segments, produces images + `image_attribution.json` in GCS.

---

### WI-6: Video Assembly

**Goal**: Cloud Run Job that assembles all assets into a final MP4.

**Code**: `services/video-assembly/` — Python + FFmpeg + Dockerfile  
**Terraform**: Add `module "video_assembly"` using `modules/cloud-run-job`  
**Tests**: Assembly test with fixture assets (known images + audio → expected video properties)

**Acceptance**: Given audio + visuals + script in GCS, produces `final.mp4` + `subtitles.srt`.

---

### WI-7: YouTube Upload

**Goal**: Cloud Run service that uploads a video to YouTube as unlisted.

**Code**: `services/youtube-upload/` — Python + Dockerfile  
**Terraform**: Add `module "youtube_upload"`, add YouTube OAuth token to secrets  
**Tests**: Integration test against YouTube API (upload a test video, verify, delete)

**Acceptance**: Given a video + metadata in GCS, uploads to YouTube and returns video ID.

---

### WI-8: Orchestration (Workflow + Scheduler)

**Goal**: Cloud Workflows definition that chains all services, plus Cloud Scheduler trigger.

**Terraform**: Add `modules/workflows` — workflow YAML + scheduler  
**Tests**: End-to-end test — trigger workflow manually, verify full pipeline produces a YouTube video

**Acceptance**: `gcloud workflows run isw-pipeline` completes successfully. Video appears on YouTube (unlisted).

---

## 10. Open Questions

| #   | Question                                                           | Owner   |
| --- | ------------------------------------------------------------------ | ------- |
| 1   | Multi-language support (Ukrainian narration)? Deferred — post-POC. | Product |
| 2   | Custom animated maps vs ISW maps? Deferred — post-POC.             | Product |
| 3   | Should fallback image library be larger than ~6 topic images?      | Product |
