"""
Zero-dependency mock of the v2t backend API for frontend demos.

    python scripts/mock_api.py          # serves http://localhost:8080

Serves realistic insurance-call mock data for every endpoint the Next.js
dashboard hits. Freshly "uploaded" calls (POST /ingest/upload) progress
through the pipeline stages over ~25 seconds so the processing queue and
status pips animate like the real thing.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# ----------------------------------------------------------------------------
# Transcript — Bajaj Allianz renewal call, Hinglish, diarized
# ----------------------------------------------------------------------------
TRANSCRIPT = [
    ("0", "AGENT", 0.8, 7.2, "Good morning! Bajaj Allianz Life Insurance mein call karne ke liye dhanyavaad. Main Priya baat kar rahi hoon. Aapki kya sahayata kar sakti hoon?", "hi-en", 0.94),
    ("1", "CUSTOMER", 7.9, 13.4, "Haan ji namaste. Mera policy renewal ka message aaya tha, uske baare mein puchna tha.", "hi-en", 0.91),
    ("0", "AGENT", 14.0, 19.6, "Ji bilkul sir. Kya aap apna policy number bata sakte hain verification ke liye?", "hi-en", 0.95),
    ("1", "CUSTOMER", 20.2, 26.8, "Policy number hai zero zero four seven... 0047829153.", "hi-en", 0.88),
    ("0", "AGENT", 27.5, 38.1, "Thank you sir. Aapki Smart Protect Goal policy hai, annual premium ₹24,500, due date 28 June 2026. Aap online ya phone pe payment kar sakte hain.", "hi-en", 0.93),
    ("1", "CUSTOMER", 38.9, 46.0, "Achha ye batao, agar main is baar premium late pay karu toh kya policy lapse ho jayegi? Grace period kitna hota hai?", "hi-en", 0.90),
    ("0", "AGENT", 46.7, 58.3, "Sir aapko due date ke baad 30 din ka grace period milta hai annual mode pe. Us dauran policy active rehti hai. Grace period ke baad payment na hone par policy lapse ho sakti hai.", "hi-en", 0.94),
    ("1", "CUSTOMER", 59.0, 66.2, "Theek hai. Aur ek baat — nominee change karna ho toh kya process hai? Shaadi ke baad wife ko nominee banana hai.", "hi-en", 0.89),
    ("0", "AGENT", 66.9, 79.5, "Congratulations sir! Nominee update ke liye aapko nomination form bharna hoga — form 'Change of Nomination' — saath mein marriage certificate ki copy. Aap branch mein ya customer portal se online bhi kar sakte hain.", "hi-en", 0.92),
    ("1", "CUSTOMER", 80.1, 86.4, "Online ho jayega? Document upload karne padenge kya portal pe?", "hi-en", 0.90),
    ("0", "AGENT", 87.0, 96.8, "Ji haan, portal pe 'Service Requests' section mein nominee change select karke documents upload kar dijiye. Process 3 se 5 working days mein complete ho jata hai.", "hi-en", 0.95),
    ("1", "CUSTOMER", 97.5, 104.9, "Badhiya. Aur tax benefit ka kya scene hai is policy pe? 80C mein aata hai na?", "hi-en", 0.87),
    ("0", "AGENT", 105.6, 117.2, "Bilkul sir, premium Section 80C ke under deductible hai, ₹1.5 lakh tak. Maturity amount bhi Section 10(10D) ke under tax-free hai, conditions apply.", "hi-en", 0.93),
    ("1", "CUSTOMER", 117.9, 123.0, "Perfect. Toh main aaj hi online renewal kar deta hoon. Thank you Priya ji.", "hi-en", 0.92),
    ("0", "AGENT", 123.6, 131.4, "Thank you sir! Renewal ke baad confirmation SMS aa jayega. Bajaj Allianz choose karne ke liye dhanyavaad. Aapka din shubh ho!", "hi-en", 0.95),
]

QUESTIONS = [
    ("Agar premium late pay karu toh kya policy lapse ho jayegi?", "What happens if I pay the premium late — will the policy lapse?", "question", "renewal", "hi-en", 0.93),
    ("Grace period kitna hota hai?", "How long is the grace period?", "question", "premium_payment", "hi-en", 0.95),
    ("Nominee change karna ho toh kya process hai?", "What is the process to change the nominee?", "question", "nominee_update", "hi-en", 0.94),
    ("Document upload karne padenge kya portal pe?", "Do I need to upload documents on the portal?", "doubt", "document_request", "hi-en", 0.88),
    ("Tax benefit ka kya scene hai is policy pe? 80C mein aata hai na?", "Does this policy qualify for 80C tax benefit?", "question", "policy_details", "hi-en", 0.91),
]

CLUSTERS = [
    ("c1a96f10-1111-4000-8000-000000000001", "Premium late payment & grace period", "Grace period aur late payment ke rules kya hain?", "hi-en", ["premium_payment", "renewal"], 184),
    ("c1a96f10-1111-4000-8000-000000000002", "Nominee update process", "Nominee kaise change karein?", "hi-en", ["nominee_update", "document_request"], 142),
    ("c1a96f10-1111-4000-8000-000000000003", "Claim settlement timeline", "Claim settle hone mein kitna time lagta hai?", "hi", ["claim_process"], 131),
    ("c1a96f10-1111-4000-8000-000000000004", "Tax benefits (80C / 10(10D))", "Policy pe tax benefit kya milta hai?", "hi-en", ["policy_details", "maturity_benefit"], 117),
    ("c1a96f10-1111-4000-8000-000000000005", "Policy surrender & cancellation", "Policy cancel karne pe kitna paisa wapas milega?", "hi", ["cancellation"], 96),
    ("c1a96f10-1111-4000-8000-000000000006", "Health rider coverage", "Health rider mein kya kya cover hota hai?", "hi-en", ["health_coverage", "exclusions"], 88),
    ("c1a96f10-1111-4000-8000-000000000007", "Claim rejection reasons", "Claim reject kyun hua?", "hi", ["claim_rejection", "grievance"], 64),
    ("c1a96f10-1111-4000-8000-000000000008", "Maturity payout process", "Maturity amount kab aur kaise milega?", "te", ["maturity_benefit"], 51),
]

EDGES = [
    (0, 1, "related_to", 0.62), (0, 4, "leads_to", 0.58), (2, 6, "opposes", 0.71),
    (3, 7, "related_to", 0.66), (4, 7, "leads_to", 0.55), (1, 5, "co_occurs", 0.49),
    (6, 2, "caused_by", 0.68), (5, 0, "related_to", 0.52), (3, 0, "co_occurs", 0.57),
]

BASE = NOW() - timedelta(hours=3)

# Fixed showcase calls: (id, status, lang, duration, minutes_ago, error)
CALLS = [
    ("7f3a1c2e-0001-4aaa-9000-000000000001", "clustered", "hi-en", 131.4, 174, None),
    ("7f3a1c2e-0002-4aaa-9000-000000000002", "clustered", "hi", 484.0, 122, None),
    ("7f3a1c2e-0003-4aaa-9000-000000000003", "extraction_running", "hi-en", 367.5, 9, None),
    ("7f3a1c2e-0004-4aaa-9000-000000000004", "stt_running", None, None, 3, None),
    ("7f3a1c2e-0005-4aaa-9000-000000000005", "failed", None, 912.0, 41,
     "Sarvam batch job ended in state 'Failed': file 0 — Internal Server Error after 3 retries "
     "(request_id 9f1c2ab4). All 8 pool keys healthy; call can be re-ingested."),
    ("7f3a1c2e-0006-4aaa-9000-000000000006", "pending", None, None, 1, None),
]

# Uploads made through the mock progress through stages over time.
_uploaded: dict[str, float] = {}
_UPLOAD_TIMELINE = [
    (5, "stt_running"), (10, "diarization_done"), (16, "extraction_running"),
    (21, "embedding_done"), (25, "clustered"),
]


def call_read(cid: str) -> dict:
    fixed = next((c for c in CALLS if c[0] == cid), None)
    if fixed:
        _, status, lang, dur, mins, err = fixed
        created = NOW() - timedelta(minutes=mins)
    else:
        first = _uploaded.setdefault(cid, time.time())
        elapsed = time.time() - first
        status = "pending"
        for thresh, st in _UPLOAD_TIMELINE:
            if elapsed >= thresh:
                status = st
        lang = "hi-en" if status == "clustered" else None
        dur = 131.4 if status == "clustered" else None
        err = None
        created = datetime.fromtimestamp(first, tz=timezone.utc)
    return {
        "id": cid,
        "source_uri": f"minio://audio-raw/{cid}.mp3",
        "is_transcript": False,
        "status": status,
        "detected_language": lang,
        "duration_seconds": dur,
        "created_at": iso(created),
        "updated_at": iso(NOW()),
        "metadata": {"campaign": "renewals-q2", "channel": "inbound", "stt_provider": "sarvam"},
        "langsmith_trace_id": None,
        "error_message": err,
    }


def utterances(cid: str) -> list[dict]:
    return [
        {
            "id": str(uuid.uuid4()), "call_id": cid, "speaker": role, "speaker_id": sid,
            "start_ts": a, "end_ts": b, "text": text, "language": lang,
            "confidence": conf, "words": None,
        }
        for sid, role, a, b, text, lang, conf in TRANSCRIPT
    ]


def questions(cid: str) -> list[dict]:
    return [
        {
            "id": str(uuid.uuid4()), "call_id": cid, "utterance_id": None,
            "raw_text": raw, "normalized_text": raw, "english_gloss": gloss,
            "question_type": qtype, "intent": intent, "secondary_intents": [],
            "language": lang, "confidence": conf,
            "extracted_at": iso(NOW() - timedelta(minutes=50)),
        }
        for raw, gloss, qtype, intent, lang, conf in QUESTIONS
    ]


def cluster_record(i: int) -> dict:
    cid, label, canonical, lang, intents, freq = CLUSTERS[i]
    return {
        "id": cid, "label": label, "canonical_question": canonical,
        "centroid": [], "dominant_language": lang, "dominant_intents": intents,
        "frequency": freq, "last_updated": iso(NOW() - timedelta(hours=i + 1)),
        "representative_question_ids": [], "is_stable": i < 6,
    }


def cluster_detail(cid: str) -> dict:
    i = next((k for k, c in enumerate(CLUSTERS) if c[0] == cid), 0)
    rec = cluster_record(i)
    return {
        "cluster": rec,
        "canonical_faq": {
            "id": str(uuid.uuid4()), "cluster_id": rec["id"],
            "canonical_question": rec["canonical_question"],
            "canonical_question_en": rec["label"],
            "suggested_answer": "Annual-mode policies have a 30-day grace period after the due date; "
            "the policy stays active during it. After the grace period the policy may lapse and "
            "needs revival within 5 years with a health declaration.",
            "language": rec["dominant_language"], "confidence": 0.9, "version": 2,
            "created_at": iso(BASE), "updated_at": iso(NOW() - timedelta(hours=2)),
        },
        "examples": questions(str(uuid.uuid4()))[:3],
        "intent_distribution": {k: round(1.0 / max(len(rec["dominant_intents"]), 1), 2) for k in rec["dominant_intents"]},
        "language_distribution": {rec["dominant_language"]: 0.74, "hi": 0.26},
    }


def analytics() -> dict:
    growth = []
    for d in range(13, -1, -1):
        day = NOW() - timedelta(days=d)
        growth.append({
            "date": day.strftime("%Y-%m-%d"),
            "new_clusters": [1, 0, 2, 1, 3, 0, 1, 2, 4, 1, 0, 2, 5, 3][13 - d],
            "churned_clusters": [0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0][13 - d],
        })
    return {
        "total_calls": 412,
        "total_questions": 1287,
        "total_clusters": len(CLUSTERS),
        "language_distribution": {"hi-en": 0.46, "hi": 0.33, "en": 0.09, "te": 0.07, "ta": 0.04, "other": 0.01},
        "intent_distribution": {
            "premium_payment": 0.21, "renewal": 0.17, "claim_process": 0.14,
            "policy_details": 0.12, "nominee_update": 0.09, "maturity_benefit": 0.08,
            "cancellation": 0.06, "health_coverage": 0.05, "claim_rejection": 0.04,
            "document_request": 0.02, "grievance": 0.02,
        },
        "top_clusters": [cluster_record(i) for i in range(len(CLUSTERS))],
        "cluster_growth": growth,
        "emerging_topics": [
            {"cluster_id": CLUSTERS[6][0], "label": CLUSTERS[6][1],
             "first_seen": (NOW() - timedelta(days=4)).strftime("%Y-%m-%d"),
             "growth_rate": 2.4, "current_size": 64},
            {"cluster_id": CLUSTERS[7][0], "label": CLUSTERS[7][1],
             "first_seen": (NOW() - timedelta(days=9)).strftime("%Y-%m-%d"),
             "growth_rate": 1.3, "current_size": 51},
            {"cluster_id": CLUSTERS[5][0], "label": CLUSTERS[5][1],
             "first_seen": (NOW() - timedelta(days=2)).strftime("%Y-%m-%d"),
             "growth_rate": 3.1, "current_size": 88},
        ],
    }


def admin_keys() -> list[dict]:
    keys = []
    for i, (state, ok, err) in enumerate([
        ("healthy", 311, 2), ("healthy", 287, 0), ("healthy", 244, 5),
        ("cooldown", 198, 12), ("healthy", 176, 1), ("healthy", 154, 0),
        ("disabled", 89, 31), ("healthy", 61, 0),
    ]):
        keys.append({
            "masked": f"sk_{'n3njdc2g qf8htkv6 aoi7jzbu zb7o9ob0 5y05htu1 xv3zuljp t5tcrt8g tx5sko91'.split()[i]}…{'7hKJ aUBP 9Mpp k6gp X1LY XOhz qTwz TmcJ'.split()[i]}",
            "state": state,
            "available_at": time.time() + 47 if state == "cooldown" else None,
            "ok_count": ok, "err_count": err,
        })
    return keys


def memory_graph() -> dict:
    nodes = [cluster_record(i) for i in range(len(CLUSTERS))]
    edges = [
        {"id": str(uuid.uuid4()), "source_cluster_id": CLUSTERS[a][0],
         "target_cluster_id": CLUSTERS[b][0], "relation": rel, "weight": w,
         "reason": None, "created_at": iso(BASE)}
        for a, b, rel, w in EDGES
    ]
    return {"nodes": nodes, "edges": edges}


def search(query: str) -> dict:
    hits = [
        {"question": q, "cluster_id": CLUSTERS[i % len(CLUSTERS)][0], "score": round(0.92 - i * 0.07, 2)}
        for i, q in enumerate(questions(str(uuid.uuid4())))
    ]
    return {"query": query, "hits": hits, "cluster_aggregates": []}


# ----------------------------------------------------------------------------
# HTTP plumbing
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter logs
        print(f"  {self.command} {self.path}")

    def do_OPTIONS(self):
        self._send({})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/analytics":
            return self._send(analytics())
        if path == "/admin/keys":
            return self._send(admin_keys())
        if path == "/memory-graph":
            return self._send(memory_graph())
        m = re.fullmatch(r"/calls/([^/]+)", path)
        if m:
            return self._send(call_read(m.group(1)))
        m = re.fullmatch(r"/calls/([^/]+)/utterances", path)
        if m:
            return self._send(utterances(m.group(1)))
        m = re.fullmatch(r"/calls/([^/]+)/questions", path)
        if m:
            return self._send(questions(m.group(1)))
        m = re.fullmatch(r"/calls/([^/]+)/embeddings", path)
        if m:
            return self._send([])
        m = re.fullmatch(r"/cluster/([^/]+)", path)
        if m:
            return self._send(cluster_detail(m.group(1)))
        return self._send({"error": {"code": "not_found_error", "message": path}}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if path == "/ingest/upload":
            cid = str(uuid.uuid4())
            _uploaded[cid] = time.time()
            return self._send(
                {"call_id": cid, "source_uri": f"minio://audio-raw/{cid}.mp3", "is_transcript": False},
                202,
            )
        if path == "/search":
            try:
                q = json.loads(raw or b"{}").get("query", "")
            except ValueError:
                q = ""
            return self._send(search(q))
        if path == "/feedback":
            return self._send({"ok": True})
        return self._send({"error": {"code": "not_found_error", "message": path}}, 404)


if __name__ == "__main__":
    print(f"v2t mock API on http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
