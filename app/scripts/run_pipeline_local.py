"""Local end-to-end runner: audio -> STT -> analysis -> call_summary + graph + Obsidian vault.

This is the production entrypoint that connects every Phase-0..2 piece in one pass.

Example (local whisper, no external key needed):
    python -m app.scripts.run_pipeline_local --provider whisper --out out/ \\
        "file:///D:/crux_calls/by_duration/15-30s/26112204.mp3"

With the deterministic CDR join + lead enrichment:
    python -m app.scripts.run_pipeline_local --provider sarvam --out out/ \\
        --cdr cdr_export.csv --leads ".../leads_canonical.csv" file:///path/a.mp3 file:///path/b.mp3

Notes:
- ``--provider sarvam`` needs Redis (key-pool) + a VALID Sarvam key. ``--provider
  whisper`` runs fully local (downloads the model weights on first use).
- On a corporate-TLS host, export SSL_CERT_FILE/REQUESTS_CA_BUNDLE pointing at a
  bundle that includes the corp root CA (see infra/certs/). Windows local paths
  must be passed as ``file:///D:/...`` URIs.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import PurePath
from uuid import uuid4

from app.utils.crux_id import crux_call_id_from_name
from app.utils.phone import normalize_mobile


def join_phone(analysis: dict, cdr=None) -> str | None:
    """Resolve the call's join phone: CDR-primary, grounded-transcript fallback.

    Mirrors ``build_call_graph``'s join logic so the runner attaches the same
    leads the graph would.
    """
    cdr_phone = normalize_mobile(getattr(cdr, "caller_phone", None)) if cdr is not None else None
    if cdr_phone:
        return cdr_phone
    lead = analysis.get("lead") or {}
    if "phone" in (lead.get("grounded_fields") or []):
        return normalize_mobile(lead.get("phone"))
    return None


def lead_rows_for(phone: str | None, leads_df) -> list[dict]:
    """Matched ``leads_canonical`` rows for a normalized mobile (empty when no df/phone)."""
    if phone is None or leads_df is None:
        return []
    sub = leads_df[leads_df["_norm_mobile"] == phone]
    return sub.drop(columns=["_norm_mobile"], errors="ignore").to_dict("records")


async def _run(audio_uris, *, provider, out_dir, cdr_path, leads_path) -> dict:
    import pandas as pd

    from app.services.audio.io import cleanup_temp, download_to_temp
    from app.services.extraction.call_analysis import CallAnalyzer
    from app.services.llm.groq_client import GroqClient
    from app.services.pipeline import AnalyzedCall
    from app.services.pipeline import run as run_pipeline
    from app.services.stt import make_transcriber
    from app.services.stt.speaker_heuristic import map_speaker_roles
    from app.workers.tasks import _call_analysis_metadata

    cdr_index = None
    if cdr_path:
        from app.services.cdr import build_index, parse_cdr

        cdr_index = build_index(parse_cdr(cdr_path))
        print(f"loaded CDR: {len(cdr_index)} records")

    leads_df = None
    if leads_path:
        leads_df = pd.read_csv(leads_path, dtype=str, keep_default_na=False)
        leads_df["_norm_mobile"] = leads_df["MOBILE_NO"].map(normalize_mobile)
        print(f"loaded leads: {len(leads_df):,} rows")

    svc = make_transcriber(provider)
    client = GroqClient()
    analyzer = CallAnalyzer(client)

    calls: list[AnalyzedCall] = []
    for uri in audio_uris:
        crux_id = crux_call_id_from_name(PurePath(uri).name) or PurePath(uri).stem
        cid = uuid4()
        print(f"[{crux_id}] transcribing...")
        path = download_to_temp(uri)
        try:
            raw = await svc.transcribe_file(call_id=cid, audio_path=path)
        finally:
            cleanup_temp(path)
        utts = map_speaker_roles(raw)
        result = await analyzer.analyze(cid, utts)
        analysis = _call_analysis_metadata(result)
        cdr = cdr_index.resolve(crux_id) if cdr_index is not None else None
        phone = join_phone(analysis, cdr)
        calls.append(
            AnalyzedCall(
                call_id=crux_id,
                call_date="",  # CDR started_at / calls.created_at can supply this later
                analysis=analysis,
                cdr=cdr,
                lead_rows=lead_rows_for(phone, leads_df),
            )
        )
        print(f"[{crux_id}] disposition={analysis['disposition']} sentiment={analysis['sentiment']} phone={phone}")

    await client.aclose()
    stats = run_pipeline(calls, out_dir)
    print("--- artifacts ---")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app.scripts.run_pipeline_local")
    p.add_argument("audio", nargs="+", help="audio URIs (file:///D:/... on Windows)")
    p.add_argument("--provider", default="whisper", choices=["whisper", "sarvam", "indic_conformer"])
    p.add_argument("--out", default="pipeline_out")
    p.add_argument("--cdr", default=None, help="optional Crux CDR CSV (recording_id->phone)")
    p.add_argument("--leads", default=None, help="optional leads_canonical.csv for enrichment join")
    args = p.parse_args(argv)
    asyncio.run(
        _run(args.audio, provider=args.provider, out_dir=args.out, cdr_path=args.cdr, leads_path=args.leads)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
