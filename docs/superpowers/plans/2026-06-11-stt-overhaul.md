# STT Overhaul Implementation Plan

> Spec: `docs/superpowers/specs/2026-06-11-stt-overhaul-design.md`

**Goal:** Sarvam Batch STT with real diarization + rotating key pool, bulk upload with queue view, full UI redesign, pluggable local Indic STT.

**Architecture:** Redis-backed key pool feeds a rewritten Sarvam provider that uses the Batch job API (full-file, diarized) instead of 25s sync chunks. Failures persist `calls.error_message` instead of silently dropping audio. Frontend gets a redesign plus multi-file upload with live queue statuses. Local path gains CT2 auto-conversion (Oriserve Apex), pyannote diarization, and an IndicConformer provider.

## Phase 1 — Backend: key pool + batch STT (this session, inline)

1. **Config** (`app/core/config.py`): `sarvam_api_keys` (comma-separated, `sarvam_key_list()` helper, falls back to `sarvam_api_key`), batch poll/timeout settings, `sarvam_num_speakers=2`, model default `saaras:v3`, cooldown/disable durations, `local_diarization`, `hf_token`, `indic_conformer_model`; `stt_provider` gains `indic_conformer`. Remove dead chunking settings.
2. **Key pool** (`app/services/stt/key_pool.py`, new): `classify_sarvam_error(status, code)` → rotate-cooldown (429 rate limit) / disable-24h (429 quota) / disable-permanent (403) / retry-backoff (5xx) / fail. `SarvamKeyPool` over `redis.asyncio`: round-robin `acquire()` that sleeps until soonest cooldown when all keys cool, `report_*` methods, `statuses()` for admin. Tests with fakeredis.
3. **Schema/DB**: `UtteranceSchema.speaker_id`, `CallRead.error_message`; ORM columns `utterances.speaker_id` (String, nullable), `calls.error_message` (Text, nullable); Alembic migration; `insert_utterances` + `_utt_dict` carry `speaker_id`.
4. **Sarvam provider rewrite** (`app/services/stt/sarvam.py`): delete chunking; ≤30s → sync transcribe; else Batch job (create with `with_diarization=true` → upload → start → poll ≥10s interval → download → parse `diarized_transcript.entries[]` into utterances with `speaker_id`). Every Sarvam call wrapped in key-rotation retry. Tests with stubbed SDK.
5. **Role mapping** (`speaker_heuristic.py`): `map_speaker_roles()` — aggregate greeting/word-count/interrogative signals per `speaker_id`, label the winner AGENT. Tests.
6. **Worker** (`app/workers/tasks.py`): `_mark_failed(call_id, error)` persists `error_message`; transcribe uses `map_speaker_roles` when speaker IDs present, else old heuristic.
7. **Admin API** (`app/api/routes/admin.py`, new): `GET /admin/keys` → masked key statuses.

## Phase 2+3 — Frontend (delegated subagent, `frontend/` only)

8. Multi-file upload with sequential client-side queue + per-file status.
9. Calls page: live pipeline status chips, error messages surfaced.
10. Call detail: transcript as conversation view (speaker bubbles, timestamps, language badges).
11. Full visual redesign (frontend-design skill): tokens, layout, nav, all pages.
12. Admin page for key pool health.

## Phase 4 — Local Indic STT

13. **Whisper model resolution** (`whisper.py`): HF repo IDs auto-converted to CTranslate2 (`ctranslate2.converters.TransformersConverter`) and cached; recommended `Oriserve/Whisper-Hindi2Hinglish-Apex`.
14. **Local diarization** (`app/services/stt/local_diarization.py`, new): stereo channel-split when channels differ, else pyannote `speaker-diarization-community-1` (HF_TOKEN); align STT segments to turns by overlap → `speaker_id`.
15. **IndicConformer provider** (`app/services/stt/indic_conformer.py`, new): `ai4bharat/indic-conformer-600m-multilingual` via transformers; per-turn transcription when diarization on.
16. Requirements: `fakeredis` (test), `pyannote.audio`, `torchaudio`.

## Verification

- `pytest app/tests -k "key_pool or sarvam or speaker"` green.
- `npm run build` green in `frontend/`.
- `.env` gets `SARVAM_API_KEYS` (7 keys, never committed).
