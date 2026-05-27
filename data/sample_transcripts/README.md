# Sample transcripts

Realistic, hand-authored Indian insurance customer-support call transcripts
used to seed the v2t platform during local development, demos, and tests.

Each file is a JSON array of utterance dicts that conform to
`app.models.schemas.UtteranceSchema` minus the runtime-assigned ids:

```json
{
  "speaker": "AGENT | CUSTOMER",
  "start_ts": 0.0,           // seconds from call start
  "end_ts":   4.8,
  "text":     "...",
  "language": "hi | en | hi-en | hi-roman | ta | te"
}
```

## Coverage

| File | Language | Primary intent | Secondary intent(s) |
|---|---|---|---|
| `call_001_hindi.json`       | `hi` (Devanagari)   | `claim_rejection`  | `grievance`, `document_request` |
| `call_002_hinglish.json`    | `hi-en`             | `premium_payment`  | `renewal`, `maturity_benefit` |
| `call_003_english.json`     | `en`                | `policy_details`   | `nominee_update`, `maturity_benefit` |
| `call_004_tamil.json`       | `ta`                | `health_coverage`  | `claim_process`, `exclusions` |
| `call_005_telugu.json`      | `te`                | `claim_process`    | `document_request` |
| `call_006_roman_hindi.json` | `hi-roman` (Latin)  | `agent_complaint`  | `grievance` |

## Vocabulary

The transcripts use real Indian life- and health-insurance domain terms,
including but not limited to:

- Products: term plan, ULIP, Jeevan Anand, Family Health Optima
- Money: sum assured, premium, surrender value, maturity benefit, NEFT/RTGS
- Process: nominee update, free-look period, grace period, cashless, reimbursement, IRDAI Bima Bharosa, Ombudsman
- Documents: policy bond, KYC, PAN/Aadhaar, FIR, post-mortem, discharge summary, indemnity bond
- Coverage: pre-existing diseases, waiting period, sub-limit, room rent limit, co-payment, network hospital
- Compliance: non-disclosure of material facts, mis-selling, Section 10(10D), grievance redressal

## How to ingest

```bash
python -m app.scripts.seed_data --api-url http://localhost:8080
```

The seed script sends each transcript to `POST /ingest` with
`is_transcript=true`. The Celery pipeline picks them up just like real
ingested calls and runs extraction → embedding → clustering → memory edges.

## Adding more samples

1. Drop a new `call_NNN_<lang>.json` file in this directory.
2. Stick to the schema above.
3. Make sure speaker labels alternate naturally (10–20 turns).
4. Keep `start_ts` monotonically non-decreasing.
5. Use **real** insurance vocabulary so the LLM extractor produces useful
   intents — generic chit-chat will be filtered out by the extractor.
