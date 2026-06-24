from app.scripts.ingest_crux_slice import select_slice


def _row(fn, dur, bucket):
    return {"filename": fn, "duration_sec": dur, "bucket": bucket}


def test_filters_by_duration_and_bucket_and_limit():
    rows = [_row(f"{i}.mp3", float(i), "15-30s" if i >= 15 else "under_15s") for i in range(10, 40)]
    sel = select_slice(rows, min_duration=30.0, buckets={"15-30s", "30s+"}, limit=3, seed=1)
    assert len(sel) == 3
    assert all(float(r["duration_sec"]) >= 30.0 for r in sel)
    assert all(r["bucket"] in {"15-30s", "30s+"} for r in sel)


def test_deterministic_for_seed():
    rows = [_row(f"{i}.mp3", 60.0, "30s+") for i in range(100)]
    a = select_slice(rows, min_duration=0.0, buckets={"30s+"}, limit=5, seed=42)
    b = select_slice(rows, min_duration=0.0, buckets={"30s+"}, limit=5, seed=42)
    assert [r["filename"] for r in a] == [r["filename"] for r in b]
