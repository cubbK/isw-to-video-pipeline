# ISW-to-Video Pipeline — Architecture Plan

> **Status**: Approved  
> **Date**: 2026-02-17  
> **Orchestrator**: Cloud Workflows (chosen over Cloud Composer for cost efficiency)

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

---

## 2. Components

### 2.1 Ingestion & Parsing — Cloud Run Service

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

### 2.2 NLP Summarization & Script Generation — Vertex AI Gemini

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

### 2.3 Text-to-Speech — Cloud Text-to-Speech API

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

### 2.4 Visual Asset Generation (3 parallel branches)

#### 2.4a Map Rendering — Cloud Run Service

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

#### 2.4b Web Image Search — Cloud Run Service

| Field       | Value                                                                              |
| ----------- | ---------------------------------------------------------------------------------- |
| GCP Service | Cloud Run                                                                          |
| Purpose     | Find relevant editorial/news photos from the web for non-map segments              |
| Inputs      | `image_search_query` from segments where `visual_type = "web_image"`               |
| Outputs     | `gs://<bucket>/visuals/<date>/images/*.png` (1920×1080) + `image_attribution.json` |

**Details:**

- Uses **Google Custom Search JSON API** (Image Search) to find relevant photos.
- Search flow per segment:
  1. Take the `image_search_query` from the script (e.g., "Geneva diplomatic summit Ukraine Russia 2026").
  2. Call Custom Search API with `searchType=image`, `imgSize=xlarge`, `imgType=photo`, `rights=cc_publicdomain|cc_attribute` (prefer openly licensed images).
  3. Receive ranked results with image URLs, source pages, and metadata.
  4. Apply filtering:
     - Prefer images from **news agencies** (Reuters, AP, AFP, EPA) and **official sources** (gov.ua, nato.int, un.org).
     - Exclude results from social media, meme sites, or low-resolution sources.
     - Minimum resolution: 1200px wide.
  5. Download the top-ranked image.
  6. Post-process with Pillow:
     - Resize/crop to 1920×1080 (center-crop or letterbox depending on aspect ratio).
     - Apply subtle vignette and color grade for visual consistency across the video.
     - Add source attribution overlay in bottom-right corner (small text: "Photo: Reuters" etc.).
  7. Save the image to GCS and record attribution metadata.

- **Attribution tracking** — `image_attribution.json` records for each image:

  ```json
  {
    "segment_id": 1,
    "source_url": "https://www.reuters.com/...",
    "source_name": "Reuters",
    "license": "Editorial use",
    "original_url": "https://...",
    "search_query": "Geneva diplomatic summit Ukraine Russia 2026"
  }
  ```

  This is included in the YouTube description for proper attribution.

- **Fallback chain** — if Custom Search returns no suitable results:
  1. Retry with a broader query (Gemini generates a fallback query at script time).
  2. If still no results, fall back to a **static stock image** from a curated library in `gs://<bucket>/assets/fallback_images/` organized by topic:
     - `diplomacy.png`, `energy.png`, `military.png`, `drone.png`, `sanctions.png`, etc.
  3. Never leave a segment without a visual.

- **API quota**: Google Custom Search API provides 100 free queries/day. At ~2-3 image searches per report, this stays well within the free tier. If volume increases, paid tier is $5 per 1000 queries.

- **Legal considerations**:
  - Prefer Creative Commons or public domain licensed images (`rights` parameter in API).
  - For editorial/news images used under fair use: the video is a non-commercial news summary with source attribution.
  - Attribution is displayed in the video overlay AND in the YouTube description.
  - Maintain a blocklist of domains to never use (e.g., Getty — strict licensing).

#### 2.4c Title Cards & Lower Thirds — Cloud Run Service

| Field       | Value                                            |
| ----------- | ------------------------------------------------ |
| GCP Service | Cloud Run (same service as map rendering)        |
| Purpose     | Generate intro/outro cards, lower-third overlays |
| Inputs      | Report date, segment titles from `script.json`   |
| Outputs     | `gs://<bucket>/visuals/<date>/titles/*.png`      |

**Details:**

- Intro card: "Ukraine War Daily Summary — February 16, 2026"
- Lower thirds: semi-transparent bar with segment title, animated in/out during assembly.
- Outro card: "Source: Institute for the Study of War | Subscribe for daily updates"
- Generated with Pillow from brand templates stored in `gs://<bucket>/assets/brand_templates/`.

---

### 2.5 Video Assembly — Cloud Run Jobs (FFmpeg)

| Field       | Value                                                     |
| ----------- | --------------------------------------------------------- |
| GCP Service | Cloud Run Jobs (4 vCPU, 8 GB RAM)                         |
| Purpose     | Assemble all assets into final YouTube-ready MP4          |
| Inputs      | Audio segments, visual assets, timing metadata            |
| Outputs     | `gs://<bucket>/output/<date>/final.mp4` + `subtitles.srt` |

**Details:**

- Python script builds an FFmpeg filter graph:
  1. **Intro** (3s) — title card with fade-in + background music.
  2. **Segments 1–N** — each segment's image/map with Ken Burns effect (slow zoom/pan), timed to actual audio duration.
  3. **Transitions** — 0.5s crossfade between segments.
  4. **Lower thirds** — text overlays animated in/out.
  5. **Background music** — royalty-free news-style track mixed at -20dB under narration.
  6. **Outro** (5s) — source attribution + subscribe CTA.
- Encoding: H.264 High Profile, AAC 192kbps, ~8 Mbps video bitrate.
- Subtitle generation: produce `.srt` from segment narration text + audio timing.
- Timeout: 15 minutes (more than enough for a 5-min video).

---

### 2.6 YouTube Upload — Cloud Run Service

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| GCP Service | Cloud Run                                                             |
| Purpose     | Upload video to YouTube with metadata                                 |
| Inputs      | `final.mp4`, `subtitles.srt`, `script.json`, `image_attribution.json` |
| Outputs     | YouTube video ID                                                      |

**Details:**

- YouTube Data API v3.
- Upload as **unlisted** (human reviews via Slack notification, then manually sets to public).
- Auto-generated metadata from `script.json`:
  - **Title**: `script.title`
  - **Description**: key takeaways + link to original ISW report + image attribution credits
  - **Tags**: `script.tags`
  - **Thumbnail**: overview map image
  - **Captions**: `.srt` upload
- OAuth2 credentials in Secret Manager.
- Image attributions from `image_attribution.json` appended to description under a "Image Credits" section.

---

### 2.7 Orchestration — Cloud Workflows

| Field       | Value                              |
| ----------- | ---------------------------------- |
| GCP Service | Cloud Workflows + Cloud Scheduler  |
| Purpose     | Orchestrate the full pipeline      |
| Trigger     | Cloud Scheduler, daily at 16:30 ET |

**Why Workflows over Composer:**

- Single daily DAG with straightforward dependencies.
- Cost: ~$0.01/month vs ~$300+/month for Composer.
- No need for Airflow's backfill, complex branching, or rich UI.
- Parallel step execution is natively supported.

**Workflow definition (pseudo-YAML):**

```yaml
main:
  steps:
    - init:
        assign:
          - date: ${sys.now()}
          - bucket: "isw-video-pipeline-prod"

    - ingest_report:
        call: http.post
        args:
          url: ${INGEST_SERVICE_URL}
          body:
            date: ${date}
        result: ingest_result

    - generate_script:
        call: http.post
        args:
          url: ${SCRIPT_SERVICE_URL}
          body:
            parsed_report_path: ${ingest_result.body.parsed_report_path}
        result: script_result

    - generate_assets:
        parallel:
          branches:
            - tts_narration:
                call: http.post
                args:
                  url: ${TTS_SERVICE_URL}
                  body:
                    script_path: ${script_result.body.script_path}
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
                  body:
                    script_path: ${script_result.body.script_path}
                result: images_result

            - generate_titles:
                call: http.post
                args:
                  url: ${TITLES_SERVICE_URL}
                  body:
                    script_path: ${script_result.body.script_path}
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

**Error handling:**

- Each `http.post` step wrapped with retry policy (3 retries, exponential backoff).
- On final failure: call a notification Cloud Run endpoint that posts to Slack.
- Workflow execution logs visible in Cloud Logging.

---

## 3. GCS Bucket Structure

```
gs://isw-video-pipeline-<env>/
├── raw/<date>/report.html
├── parsed/<date>/parsed_report.json
├── scripts/<date>/script.json
├── audio/<date>/
│   ├── segment_01.wav
│   ├── segment_02.wav
│   ├── ...
│   └── timing.json
├── visuals/<date>/
│   ├── maps/
│   │   ├── overview.png
│   │   ├── sumy.png
│   │   └── pokrovsk.png
│   ├── images/
│   │   ├── diplomacy.png
│   │   └── energy.png
│   └── titles/
│       ├── intro.png
│       ├── outro.png
│       └── lower_01.png ... lower_N.png
├── output/<date>/
│   ├── final.mp4
│   ├── subtitles.srt
│   └── image_attribution.json
└── assets/                         # Static, version-controlled
    ├── background_music.mp3
    ├── pronunciation_dict.json
    ├── brand_templates/
    │   ├── intro_template.png
    │   ├── outro_template.png
    │   └── lower_third_template.png
    ├── fallback_images/            # Curated stock images by topic
    │   ├── diplomacy.png
    │   ├── energy.png
    │   ├── military.png
    │   ├── drone.png
    │   ├── sanctions.png
    │   └── humanitarian.png
    └── image_search_blocklist.json  # Domains to never source images from
```

---

## 4. Security

| Concern          | Solution                                                                                               |
| ---------------- | ------------------------------------------------------------------------------------------------------ |
| YouTube OAuth    | Tokens in Secret Manager, accessed only by upload service account                                      |
| Vertex AI access | Dedicated SA with `roles/aiplatform.user`                                                              |
| GCS access       | Per-service least-privilege IAM (ingestion writes `raw/`, etc.)                                        |
| Network          | Cloud Run services are internal-only (ingress = `internal`), invoked by Workflows                      |
| Secrets          | Secret Manager for all credentials (including Custom Search API key)                                   |
| API keys         | Custom Search API key in Secret Manager, all other auth via service accounts + Workload Identity       |
| Data retention   | GCS lifecycle: delete `raw/`, `parsed/`, `audio/`, `visuals/` after 30 days; keep `output/` for 1 year |
| Workflow auth    | Workflows uses a dedicated SA with `roles/run.invoker` on each Cloud Run service                       |
| Image licensing  | Attribution tracked in `image_attribution.json`, included in YouTube description; blocklist enforced   |

---

## 5. Cost Estimate (Monthly — 30 videos)

| Component                                           | Usage                            | Est. Cost         |
| --------------------------------------------------- | -------------------------------- | ----------------- |
| Cloud Workflows + Cloud Scheduler                   | 30 executions                    | ~$0.15            |
| Cloud Run (ingestion, maps, images, titles, upload) | ~150 invocations, <1 min each    | ~$1–2             |
| Cloud Run Jobs (FFmpeg assembly)                    | 30 jobs × 4 vCPU × 5 min         | ~$3–5             |
| Vertex AI Gemini 2.0 Flash                          | 30 calls × ~5K tokens            | ~$0.50            |
| Google Custom Search API                            | ~90 queries (free tier: 100/day) | $0                |
| Cloud Text-to-Speech (Neural2)                      | ~150K chars                      | ~$24              |
| Cloud Storage                                       | ~10 GB                           | ~$0.20            |
| YouTube Data API                                    | Free tier                        | $0                |
| Secret Manager                                      | ~6 secrets                       | ~$0.04            |
| **Total**                                           |                                  | **~$30–33/month** |

---

## 6. Architecture Decision Records

### ADR-001: Use ISW's published maps instead of custom map generation

- **Context**: ISW publishes high-quality control-of-terrain maps with every report, embedded as `.webp` images in the HTML.
- **Decision**: Download and reprocess ISW maps rather than generating custom maps.
- **Rationale**: ISW maps are the authoritative source, require zero ML infrastructure, and are already trusted by the audience.
- **Tradeoff**: Dependency on ISW's HTML structure. If they change their format, the parser needs updating. Mitigated by structural tests in CI.

### ADR-002: Use Gemini Flash for summarization instead of fine-tuned model

- **Context**: We need to convert ~3000-word structured reports into ~800-word narration scripts.
- **Decision**: Use Vertex AI Gemini 2.0 Flash with structured prompting and JSON mode.
- **Rationale**: ISW reports are already well-structured with bold toplines and key takeaways. This is a reformatting/selection task. Gemini Flash costs ~$0.01/report with zero training infrastructure.
- **Tradeoff**: Small hallucination risk. Mitigated with post-generation validation that checks all facts in the script exist in the source.

### ADR-003: FFmpeg on Cloud Run Jobs instead of ML-based video generation

- **Context**: We need to assemble still images + audio into a video with transitions and overlays.
- **Decision**: Use FFmpeg in a Cloud Run Jobs container.
- **Rationale**: News-style videos with maps, lower thirds, and narration are a compositing problem, not a generation problem. FFmpeg is free, fast, and deterministic. ML video generation (Runway, Pika) would cost $50+/day with unpredictable results.

### ADR-004: Cloud Workflows instead of Cloud Composer

- **Context**: Pipeline runs once daily with a linear DAG (sequential stages + one parallel fan-out).
- **Decision**: Use Cloud Workflows + Cloud Scheduler.
- **Rationale**: ~$0.15/month vs ~$300+/month for Composer. The pipeline has no need for Airflow's backfill, XComs, complex branching, or plugin ecosystem. Workflows natively supports parallel branches, retries, and HTTP service invocation.
- **Tradeoff**: No built-in UI for run history inspection. Mitigated with Cloud Logging + a simple Cloud Run dashboard if needed later.

### ADR-005: Web image search instead of AI image generation

- **Context**: Non-map segments (diplomacy, energy, drone tech) need accompanying visuals.
- **Decision**: Use Google Custom Search API (Image Search) to find real editorial/news photos from the web instead of generating images with Vertex AI Imagen 3.
- **Rationale**:
  - **Authenticity** — real news photos are more credible for a news-style video than AI-generated imagery. Audiences increasingly distrust AI-generated visuals in news contexts.
  - **Cost** — Custom Search API is free for up to 100 queries/day (we use ~3/day). Imagen 3 at ~90 images/month cost $3–5.
  - **Speed** — an API call + image download is faster than image generation (~1s vs ~10s).
  - **No hallucinated visuals** — AI image generation can produce inaccurate or misleading editorial imagery. Real photos are grounded in reality.
- **Tradeoff**: Dependency on image availability — some niche topics may not return good results. Mitigated with a 3-tier fallback chain: (1) retry with broader query, (2) curated fallback stock images, (3) use the overview map as a last resort. Image licensing must be tracked and attributed.

---

## 7. Service Map & Terraform Modules

```
terraform/
├── modules/
│   ├── networking/          # VPC, serverless VPC connector (if needed)
│   ├── storage/             # GCS buckets, lifecycle rules
│   ├── iam/                 # Service accounts, role bindings
│   ├── secrets/             # Secret Manager secrets
│   ├── cloud-run/           # All Cloud Run services + jobs
│   │   ├── ingestion/
│   │   ├── script-gen/
│   │   ├── tts/
│   │   ├── map-renderer/
│   │   ├── image-search/
│   │   ├── title-gen/
│   │   ├── video-assembly/  # Cloud Run Job
│   │   └── youtube-upload/
│   ├── workflows/           # Workflow definition + Cloud Scheduler
│   └── monitoring/          # Alerting policies, log-based metrics
├── environments/
│   ├── dev/
│   │   └── main.tf
│   ├── staging/
│   │   └── main.tf
│   └── prod/
│       └── main.tf
├── backend.tf               # GCS remote state
├── variables.tf
└── versions.tf
```

---

## 8. Implementation Order

| Phase  | What                                          | Deliverable                        |
| ------ | --------------------------------------------- | ---------------------------------- |
| **1**  | Ingestion & parsing service                   | Cloud Run service + unit tests     |
| **2**  | Script generation (Gemini prompt engineering) | Cloud Run service + prompt tests   |
| **3**  | TTS narration service                         | Cloud Run service                  |
| **4**  | Map rendering + title card generation         | Cloud Run service                  |
| **5**  | Web image search service                      | Cloud Run service + fallback tests |
| **6**  | Video assembly (FFmpeg)                       | Cloud Run Job + assembly tests     |
| **7**  | YouTube upload service                        | Cloud Run service                  |
| **8**  | Workflow definition + Scheduler               | Cloud Workflows YAML               |
| **9**  | Terraform infrastructure                      | All modules + CI/CD                |
| **10** | End-to-end testing + monitoring               | Integration tests + alerting       |

> **Recommended start**: Phase 1–3 can be prototyped locally as plain Python scripts before deploying to Cloud Run. This validates the core logic (parse → summarize → narrate) without any infrastructure.

---

## 9. Open Questions

| #   | Question                                                                                         | Owner            |
| --- | ------------------------------------------------------------------------------------------------ | ---------------- |
| 1   | Multi-language support (Ukrainian narration)? Affects TTS voice and script generation prompts.   | Product          |
| 2   | Custom animated maps (Mapbox/Leaflet) vs ISW maps? Custom allows front-line animation over time. | ML Expert        |
| 3   | Cloud Run Jobs vs Cloud Batch for FFmpeg assembly? Batch supports GPUs for faster encoding.      | GCP Expert       |
| 4   | Terraform module split — by pipeline stage or by GCP service type? Current plan: by stage.       | Terraform Expert |
| 5   | Should we curate a larger fallback image library, or is ~6 topic images sufficient?              | Product          |
