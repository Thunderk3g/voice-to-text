# v2t STT Overhaul — Design Spec

**Date:** 2026-06-11
**Status:** Approved by user
**Scope:** Sarvam Batch STT + diarization, API key rotation pool, bulk upload queue, full UI redesign, pluggable local Indic STT models + local diarization.

## Problem

1. **Incomplete transcripts.** The Sarvam provider uses the sync REST API (30 s/request cap), so audio is sliced into ~25 s chunks (`app/services/stt/sarvam.py:275-329`). Failed chunks (timeout, 429) are *silently skipped* (`sarvam.py:144-152`), producing partial transcripts with no visible error.
2. **No real diarization.** Speaker labels come from a heuristic (`app/services/stt/speaker_heuristic.py`). Sarvam diarization exists but only on its Batch API; the local Whisper path has none.
3. **Single API key.** One `SARVAM_API_KEY`; rate limits (free tier: 60/min sync, 20/min batch incl. polls, per **account**) stall the pipeline. User has 7 keys from 7 different accounts and may scale to ~100.
4. **No bulk upload.** UI accepts one file at a time.
5. **UI quality.** User wants a full visual redesign.
6. **Local STT quality.** faster-whisper large-v3 int8 is the only local model; better Indic-specific open models exist.

## Design

### 1. Sarvam key pool — `app/services/stt/key_pool.py`

Redis-backed (workers are separate processes; state must be shared).

- **Config:** `SARVAM_API_KEYS` (comma-separated) in `.env`; legacy `SARVAM_API_KEY` accepted as a 1-key pool. Keys never committed; `.env` stays gitignored.
- **Selection:** round-robin over healthy keys (Redis `INCR` cursor over a sorted key list; skip keys with active cooldown/disabled flags).
- **Error policy** (Sarvam error body: `{"error": {"code": ...}}`):
  - HTTP 429 + `rate_limit_exceeded_error` → cooldown that key (exponential 10 s → 60 s cap), rotate, retry.
  - HTTP 429 + `insufficient_quota_error` → disable key 24 h (credits exhausted).
  - HTTP 403 + `invalid_api_key_error` → disable key permanently.
  - HTTP 500/503 → retry with backoff, same key (not a key problem).
- **All keys cooling** → sleep until soonest cooldown expiry, continue. Never crash the task.
- **Observability:** `GET /admin/keys` returns masked keys (`sk_n3nj…7hKJ`), status (healthy/cooldown/disabled), cooldown expiry, success/error counters. Surfaced on a small admin panel in the UI.

### 2. Sarvam Batch STT with diarization — rewrite `app/services/stt/sarvam.py`

Replace chunked sync flow with the Batch job flow (handles ≤2 h/file):

1. `POST /speech-to-text/job/v1` with `{model: "saaras:v3", mode: "transcribe", language_code: "unknown", with_timestamps: true, with_diarization: true, num_speakers: 2}`
2. `POST /speech-to-text/job/v1/upload-files` → PUT audio to presigned URL
3. `POST /speech-to-text/job/v1/{job_id}/start`
4. Poll `GET /speech-to-text/job/v1/{job_id}/status` (respect batch rate limit, which includes polls — poll interval ≥5 s with backoff)
5. `POST /speech-to-text/job/v1/download-files` → parse output JSON

- **Output mapping:** `diarized_transcript.entries[] = {transcript, start_time_seconds, end_time_seconds, speaker_id}` → utterance rows. `speaker_id` ("0"/"1") stored; AGENT/CUSTOMER role mapping uses the existing heuristic *only* to decide which speaker ID is the agent (not for segmentation).
- **Key pool integration:** every batch HTTP call acquires a key from the pool; 429/403 handled per pool policy. A job sticks to the key that created it (job state is per-account).
- **Fallback:** sync API path kept only for clips ≤30 s (single request, no chunking).
- **No silent failures:** job failure / failed file → call status FAILED, `error_message` persisted on the call row and shown in UI. Remove chunk-skip behavior entirely.

### 3. Bulk upload + sequential queue

- **Frontend:** UploadBox accepts multiple files (multi-select + drag-drop). Files POSTed to existing `/ingest/upload` one at a time from the client; each becomes one call.
- **Processing:** naturally serialized by Celery queue (transcription tasks process one call at a time per worker; batch polling is cheap and respects the 20/min batch limit per account).
- **Queue view:** calls page shows per-file status chips (queued → transcribing → extracting → embedding → clustering → done/failed) with live polling of `/calls/{id}`.

### 4. Full UI redesign (Next.js 14, existing pages)

New visual system (typography, palette, layout, dark-capable) applied to: calls list, call detail, clusters, cluster detail, memory graph, analytics, drift, playground.

- **Headline:** call detail transcript becomes a conversation view — speaker-colored chat bubbles, agent left / customer right, timestamps, language badges, links to extracted questions.
- **Pipeline visibility:** statuses and error messages prominent; failed calls show *why*.
- **Admin panel:** key pool health.
- Use the frontend-design skill during implementation.

### 5. Pluggable local Indic STT + local diarization

- **Whisper provider:** `WHISPER_MODEL` accepts any CT2-convertible HF checkpoint; auto-convert via `ct2-transformers-converter` on first run (cached in `hf_cache` volume). Recommended default for Hindi telephony: `Oriserve/Whisper-Hindi2Hinglish-Apex` (Apache 2.0, large-v3-turbo fine-tune, Hinglish output, ~2x faster than large-v3). `large-v3` remains the fallback.
- **New provider** `STT_PROVIDER=indic_conformer`: AI4Bharat `ai4bharat/indic-conformer-600m-multilingual` (MIT, 22 languages, CPU-friendly via transformers/ONNX). Native-script output; no word timestamps (CTC) — utterance timing from diarization segments when enabled.
- **Local diarization:** `LOCAL_DIARIZATION=true` runs pyannote `speaker-diarization-community-1` (CC-BY-4.0; one-time gated HF download via `HF_TOKEN`, then offline) on the local path; transcript segments aligned to diarization turns, same speaker-ID → role mapping as Sarvam path. If recordings are stereo with agent/customer on separate channels, channel-split is used instead (auto-detected).

## Out of scope

Extraction/clustering logic, embeddings, DB schema changes beyond: utterance `speaker_id` column (nullable) and call `error_message` column. Whisper task routing (`stt.heavy` queue) unchanged.

## Error handling summary

| Failure | Behavior |
|---|---|
| Sarvam key rate-limited | cooldown + rotate + retry; transparent to call |
| All keys cooling | wait, resume; call stays in TRANSCRIBING |
| Key invalid/exhausted | disabled; visible in admin panel |
| Batch job fails | call FAILED + error_message persisted + visible in UI |
| Local model download fails | call FAILED with actionable message |

## Testing

- Unit: key pool state machine (cooldown, disable, all-cooling wait) against fakeredis; Sarvam batch client against mocked httpx (success, 429 both codes, 403, job-failed); diarized-output → utterance mapping; stereo channel-split detection.
- Integration: end-to-end ingest of a short fixture through batch flow with a mock Sarvam server.
- UI: existing pages render with new components; bulk upload posts N files sequentially.

## Security note

The 7 Sarvam keys were shared in chat — treated as semi-exposed. They live only in `.env` (gitignored). User advised to rotate them in the Sarvam dashboard if the chat transcript is ever shared.
