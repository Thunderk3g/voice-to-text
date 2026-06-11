"use client";

import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
} from "react";
import { Card } from "@/components/Card";
import { UploadBox } from "@/components/UploadBox";
import { CallStatusRow } from "@/components/CallStatusRow";

const STORAGE_KEY = "v2t.recentCalls";
const MAX_RECENT = 50;

function loadRecent(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed)
      ? parsed.filter((x): x is string => typeof x === "string")
      : [];
  } catch {
    return [];
  }
}

export default function CallsIndexPage(): JSX.Element {
  const router = useRouter();
  const [id, setId] = useState("");
  const [recent, setRecent] = useState<string[]>([]);
  const [hydrated, setHydrated] = useState(false);

  // Recent call ids persist across reloads (no list endpoint on the API yet).
  useEffect(() => {
    setRecent(loadRecent());
    setHydrated(true);
  }, []);

  const remember = useCallback((callId: string) => {
    setRecent((prev) => {
      const next = [callId, ...prev.filter((c) => c !== callId)].slice(
        0,
        MAX_RECENT,
      );
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        /* storage full / unavailable — queue still works in memory */
      }
      return next;
    });
  }, []);

  const clearRecent = useCallback(() => {
    setRecent([]);
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  function onSubmit(e: FormEvent): void {
    e.preventDefault();
    const v = id.trim();
    if (!v) return;
    router.push(`/calls/${v}`);
  }

  return (
    <div className="flex animate-fade-up flex-col gap-6">
      <header>
        <div className="kicker">Ingestion</div>
        <h1 className="page-title">Calls</h1>
        <p className="page-sub">
          Upload audio files or transcripts in bulk — each runs through STT,
          diarization, extraction, embedding and clustering.
        </p>
      </header>

      <UploadBox onUploaded={remember} />

      {hydrated && recent.length > 0 && (
        <Card
          title="Processing queue"
          subtitle="Live pipeline status — polling stops once a call is done or failed."
          right={
            <button
              type="button"
              onClick={clearRecent}
              className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-400 transition hover:text-ink-700"
            >
              Clear list
            </button>
          }
        >
          <div className="flex flex-col">
            {recent.map((callId) => (
              <CallStatusRow key={callId} callId={callId} />
            ))}
          </div>
        </Card>
      )}

      <Card title="Open by ID">
        <form onSubmit={onSubmit} className="flex items-end gap-3">
          <div className="flex-1">
            <label className="label">Call ID</label>
            <input
              type="text"
              value={id}
              onChange={(e) => setId(e.target.value)}
              placeholder="e.g. 4d6f7a90-..."
              className="input font-mono"
            />
          </div>
          <button type="submit" className="btn-primary">
            Open
          </button>
        </form>
      </Card>
    </div>
  );
}
