"""Parallel batch processor — optimized for a MacBook to chew through the whole corpus.

Three resumable, crash-safe stages (each checkpoints by OUTPUT-FILE existence):

  1. transcribe : PROCESS pool, one cached STT model per worker (CPU-bound).
                  Writes ``<out>/transcripts/<crux_id>.json``.
  2. analyze    : async, high-concurrency Groq (IO-bound). One client, many in-flight.
                  Writes ``<out>/analysis/<crux_id>.json``.
  3. aggregate  : analysis -> call_summary.csv + typed graph + Obsidian vault
                  + knowledge_graph.json (via the pipeline orchestrator).

Mac usage (no Redis / no Sarvam key needed — local whisper):
    python -m app.scripts.batch_process --index "/Volumes/D/crux_calls/dataset/voice_to_text_index/duration_index.csv" \\
        --buckets 15-30s,30s+ --min-duration 20 --limit 2000 \\
        --engine whisper --model large-v3 --out out/ --stage all

Apple-Silicon Metal acceleration (much faster; needs `pip install mlx-whisper`):
    python -m app.scripts.batch_process --dir /path/to/mp3s --engine mlx --model mlx-community/whisper-large-v3-mlx ...

Worker counts auto-size to the host (cores + optional --ram-gb); override with
--stt-workers / --analyze-concurrency. On corporate-TLS hosts set
SSL_CERT_FILE/REQUESTS_CA_BUNDLE to a bundle including the corp root CA.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path, PurePath
from uuid import uuid4

from app.services.pipeline.batch import pending_items, plan_concurrency, run_async_pool
from app.utils.crux_id import crux_call_id_from_name
from app.utils.phone import normalize_mobile

# --------------------------------------------------------------------------- #
# Small IO helpers
# --------------------------------------------------------------------------- #
def _write_json(path: str | Path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _to_file_uri(path: str) -> str:
    """Local OS path -> file:// URI (download_to_temp rejects bare 'D:\\...')."""
    if "://" in path:
        return path
    return "file:///" + str(path).replace("\\", "/").lstrip("/")


def _crux_id_for(uri: str) -> str:
    name = PurePath(uri).name
    return crux_call_id_from_name(name) or PurePath(uri).stem


# --------------------------------------------------------------------------- #
# Stage 1: transcribe (process pool, one model per worker)
# --------------------------------------------------------------------------- #
_WORKER: dict = {"engine": "whisper", "svc": None}


def _init_worker(engine: str, model: str, device: str, compute_type: str, threads: int) -> None:
    """Per-process initializer: pin thread count, then load the STT model ONCE."""
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("CT2_NUM_THREADS", str(threads))
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    _WORKER["engine"] = engine
    if engine == "whisper":
        os.environ["STT_PROVIDER"] = "whisper"
        os.environ["WHISPER_MODEL"] = model
        os.environ["WHISPER_DEVICE"] = device
        os.environ["WHISPER_COMPUTE_TYPE"] = compute_type
        from app.services.stt import make_transcriber

        _WORKER["svc"] = make_transcriber("whisper")
    elif engine == "mlx":
        _WORKER["model"] = model  # mlx loads lazily in _transcribe_one


def _utt_to_dict(u) -> dict:
    d = u.model_dump(mode="json") if hasattr(u, "model_dump") else dict(u)
    return d


def _mlx_transcribe(local_path: str, model: str) -> list[dict]:
    """Apple-Metal transcription via mlx-whisper (Apple Silicon only)."""
    import mlx_whisper  # type: ignore

    out = mlx_whisper.transcribe(local_path, path_or_hf_repo=model, word_timestamps=False)
    segs = out.get("segments") or []
    utts = []
    for s in segs:
        utts.append({
            "call_id": str(uuid4()), "speaker": "UNKNOWN", "speaker_id": None,
            "start_ts": float(s.get("start", 0.0)), "end_ts": float(s.get("end", 0.0)),
            "text": (s.get("text") or "").strip(),
            "language": out.get("language") or "other", "confidence": 0.0, "words": None,
        })
    return utts


def _transcribe_one(task: tuple[str, str, str]) -> tuple[str, str, int, str | None]:
    """Worker: transcribe one audio URI to a transcript JSON. Never raises."""
    uri, crux_id, out_path = task
    from app.services.audio.io import cleanup_temp, download_to_temp

    try:
        local = download_to_temp(uri)
        try:
            if _WORKER["engine"] == "mlx":
                utts = _mlx_transcribe(local, _WORKER.get("model", ""))
            else:
                from app.services.stt.speaker_heuristic import map_speaker_roles

                raw = asyncio.run(_WORKER["svc"].transcribe_file(call_id=uuid4(), audio_path=local))
                utts = [_utt_to_dict(u) for u in map_speaker_roles(raw)]
        finally:
            cleanup_temp(local)
        _write_json(out_path, utts)
        return (crux_id, "ok", len(utts), None)
    except Exception as exc:  # noqa: BLE001 — isolate one bad file
        return (crux_id, "error", 0, str(exc))


def transcribe_stage(tasks: list[tuple[str, str, str]], plan, model, device, compute_type) -> dict:
    """Run the transcribe tasks across a process pool. Returns counts."""
    ok = err = 0
    failures: list[tuple[str, str]] = []
    if not tasks:
        return {"ok": 0, "error": 0, "failures": []}
    with ProcessPoolExecutor(
        max_workers=plan.stt_workers,
        initializer=_init_worker,
        initargs=(plan.engine, model, device, compute_type, plan.stt_threads),
    ) as ex:
        futs = [ex.submit(_transcribe_one, t) for t in tasks]
        for fut in as_completed(futs):
            crux_id, status, n, error = fut.result()
            if status == "ok":
                ok += 1
            else:
                err += 1
                failures.append((crux_id, error or "unknown"))
                print(f"  STT FAIL {crux_id}: {error}")
            if (ok + err) % 25 == 0:
                print(f"  transcribed {ok} ok / {err} err ...")
    return {"ok": ok, "error": err, "failures": failures}


# --------------------------------------------------------------------------- #
# Stage 2: analyze (async Groq, bounded concurrency)
# --------------------------------------------------------------------------- #
async def analyze_stage(crux_ids: list[str], tdir: Path, adir: Path, concurrency: int) -> dict:
    from app.models.schemas import UtteranceSchema
    from app.services.extraction.call_analysis import CallAnalyzer
    from app.services.llm.groq_client import GroqClient
    from app.workers.tasks import _call_analysis_metadata

    adir.mkdir(parents=True, exist_ok=True)
    client = GroqClient()
    analyzer = CallAnalyzer(client)

    async def worker(crux_id: str):
        rows = _read_json(tdir / f"{crux_id}.json")
        utts = [UtteranceSchema.model_validate(r) for r in rows]
        res = await analyzer.analyze(uuid4(), utts)
        meta = _call_analysis_metadata(res)
        meta["crux_call_id"] = crux_id
        _write_json(adir / f"{crux_id}.json", meta)
        return meta["disposition"]

    counts = {"ok": 0, "error": 0}

    def _on(crux_id, val, err):
        counts["ok" if err is None else "error"] += 1
        if err is not None:
            print(f"  ANALYZE FAIL {crux_id}: {err}")

    await run_async_pool(crux_ids, worker, concurrency=concurrency, on_result=_on)
    await client.aclose()
    return counts


# --------------------------------------------------------------------------- #
# Stage 3: aggregate (orchestrator -> call_summary + graph + vault)
# --------------------------------------------------------------------------- #
def aggregate_stage(adir: Path, out_dir: Path, cdr_path: str | None, leads_path: str | None) -> dict:
    import pandas as pd

    from app.services.pipeline import AnalyzedCall
    from app.services.pipeline import run as run_pipeline
    from app.services.pipeline.orchestrate import build_artifacts, export_artifacts

    cdr_index = None
    if cdr_path:
        from app.services.cdr import build_index, parse_cdr

        cdr_index = build_index(parse_cdr(cdr_path))
    leads_df = None
    if leads_path:
        leads_df = pd.read_csv(leads_path, dtype=str, keep_default_na=False)
        leads_df["_norm_mobile"] = leads_df["MOBILE_NO"].map(normalize_mobile)

    calls: list[AnalyzedCall] = []
    for f in sorted(adir.glob("*.json")):
        meta = _read_json(f)
        crux_id = meta.get("crux_call_id", f.stem)
        cdr = cdr_index.resolve(crux_id) if cdr_index is not None else None
        phone = normalize_mobile(getattr(cdr, "caller_phone", None)) if cdr else None
        if phone is None:
            lead = meta.get("lead") or {}
            if "phone" in (lead.get("grounded_fields") or []):
                phone = normalize_mobile(lead.get("phone"))
        lead_rows = []
        if phone and leads_df is not None:
            sub = leads_df[leads_df["_norm_mobile"] == phone]
            lead_rows = sub.drop(columns=["_norm_mobile"], errors="ignore").to_dict("records")
        calls.append(AnalyzedCall(call_id=crux_id, call_date="", analysis=meta, cdr=cdr, lead_rows=lead_rows))

    arts = build_artifacts(calls)
    stats = export_artifacts(arts, out_dir)
    # also dump the graph as JSON for the API/frontend
    _write_json(
        Path(out_dir) / "knowledge_graph.json",
        {
            "nodes": [{"id": n.id, "type": str(n.type), "label": n.label, "attrs": n.attrs} for n in arts.graph.nodes],
            "edges": [{"source": e.src_id, "target": e.dst_id, "relation": str(e.relation), "weight": e.weight, "reason": e.reason} for e in arts.graph.edges],
        },
    )
    return stats


# --------------------------------------------------------------------------- #
# Enumeration + CLI
# --------------------------------------------------------------------------- #
def enumerate_audio(args) -> list[str]:
    if args.audio:
        return [_to_file_uri(a) for a in args.audio]
    if args.dir:
        return [_to_file_uri(str(p)) for p in sorted(Path(args.dir).glob("*.mp3"))]
    if args.index:
        from app.scripts.ingest_crux_slice import select_slice

        rows = []
        import csv

        with open(args.index, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        buckets = {b.strip() for b in args.buckets.split(",")} if args.buckets else set()
        chosen = select_slice(rows, min_duration=args.min_duration, buckets=buckets, limit=args.limit, seed=args.seed)
        uris = []
        for r in chosen:
            path = r.get("new_path") or r.get("original_path") or ""
            if path:
                uris.append(_to_file_uri(path))
        return uris
    raise SystemExit("provide audio URIs, --dir, or --index")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app.scripts.batch_process")
    src = p.add_argument_group("source (one of)")
    src.add_argument("audio", nargs="*", help="audio URIs/paths")
    src.add_argument("--dir", default=None, help="directory of *.mp3")
    src.add_argument("--index", default=None, help="duration_index.csv to slice from")
    src.add_argument("--buckets", default="15-30s,30s+")
    src.add_argument("--min-duration", type=float, default=0.0)
    src.add_argument("--limit", type=int, default=1000)
    src.add_argument("--seed", type=int, default=42)

    eng = p.add_argument_group("engine / concurrency")
    eng.add_argument("--engine", default="whisper", choices=["whisper", "mlx"])
    eng.add_argument("--model", default="large-v3")
    eng.add_argument("--device", default="cpu")
    eng.add_argument("--compute-type", default="int8")
    eng.add_argument("--stt-workers", type=int, default=None)
    eng.add_argument("--analyze-concurrency", type=int, default=None)
    eng.add_argument("--ram-gb", type=float, default=None, help="hint to bound STT workers by memory")

    out = p.add_argument_group("output / stages / join")
    out.add_argument("--out", default="batch_out")
    out.add_argument("--stage", default="all", choices=["all", "transcribe", "analyze", "aggregate"])
    out.add_argument("--cdr", default=None)
    out.add_argument("--leads", default=None)
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    tdir = out_dir / "transcripts"
    adir = out_dir / "analysis"

    plan = plan_concurrency(
        engine=args.engine, model=args.model, ram_gb=args.ram_gb,
        analyze_concurrency=args.analyze_concurrency,
    )
    if args.stt_workers:
        plan = plan.__class__(engine=plan.engine, stt_workers=args.stt_workers,
                              stt_threads=plan.stt_threads, analyze_concurrency=plan.analyze_concurrency)
    print(f"plan: engine={plan.engine} stt_workers={plan.stt_workers} stt_threads={plan.stt_threads} "
          f"analyze_concurrency={plan.analyze_concurrency}")

    if args.stage in ("all", "transcribe"):
        uris = enumerate_audio(args)
        tasks = [(u, _crux_id_for(u), str(tdir / f"{_crux_id_for(u)}.json")) for u in uris]
        tasks = [t for t in tasks if not Path(t[2]).exists()]  # resume
        print(f"transcribe: {len(tasks)} pending")
        r = transcribe_stage(tasks, plan, args.model, args.device, args.compute_type)
        print(f"transcribe done: {r['ok']} ok / {r['error']} err")

    if args.stage in ("all", "analyze"):
        ids = [p.stem for p in sorted(tdir.glob("*.json"))]
        ids = pending_items(ids, adir)
        print(f"analyze: {len(ids)} pending")
        r = asyncio.run(analyze_stage(ids, tdir, adir, plan.analyze_concurrency))
        print(f"analyze done: {r['ok']} ok / {r['error']} err")

    if args.stage in ("all", "aggregate"):
        stats = aggregate_stage(adir, out_dir, args.cdr, args.leads)
        print("aggregate:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
