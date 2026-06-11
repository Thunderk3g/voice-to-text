"use client";

import Link from "next/link";
import {
  AlertTriangle,
  CheckCircle2,
  FileAudio2,
  RotateCcw,
  UploadCloud,
  X,
} from "lucide-react";
import {
  useCallback,
  useRef,
  useState,
  type DragEvent,
  type FormEvent,
} from "react";
import clsx from "clsx";
import { apiUpload, ApiError } from "@/lib/api";
import type { STTProvider, UploadResponse } from "@/lib/types";
import { STT_PROVIDERS, STT_PROVIDER_LABEL } from "@/lib/types";
import { Card } from "./Card";
import { Spinner } from "./Spinner";

const ACCEPT = ".wav,.mp3,.m4a,.ogg,.flac,.webm,.mp4,.json";

type ItemStatus = "waiting" | "uploading" | "uploaded" | "failed";

interface QueueItem {
  key: string;
  file: File;
  status: ItemStatus;
  callId?: string;
  error?: string;
}

let nextKey = 0;
function makeKey(): string {
  nextKey += 1;
  return `f-${Date.now()}-${nextKey}`;
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.body?.trim() || err.message;
  return err instanceof Error ? err.message : "Upload failed.";
}

export function UploadBox({
  onUploaded,
}: {
  onUploaded: (callId: string) => void;
}): JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<QueueItem[]>([]);
  const [campaign, setCampaign] = useState("");
  const [channel, setChannel] = useState("");
  const [sttProvider, setSttProvider] = useState<STTProvider>("whisper");
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);

  // Live mirror so the sequential loop always reads fresh state.
  const itemsRef = useRef<QueueItem[]>(items);
  itemsRef.current = items;

  const patchItem = useCallback(
    (key: string, patch: Partial<QueueItem>): void => {
      setItems((prev) =>
        prev.map((it) => (it.key === key ? { ...it, ...patch } : it)),
      );
    },
    [],
  );

  function addFiles(list: FileList | File[] | null): void {
    if (!list) return;
    const incoming = Array.from(list);
    if (incoming.length === 0) return;
    setItems((prev) => [
      ...prev,
      ...incoming.map<QueueItem>((file) => ({
        key: makeKey(),
        file,
        status: "waiting",
      })),
    ]);
  }

  function onDrop(e: DragEvent<HTMLDivElement>): void {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  }

  const uploadOne = useCallback(
    async (key: string): Promise<void> => {
      const item = itemsRef.current.find((it) => it.key === key);
      if (!item || item.status === "uploading" || item.status === "uploaded") {
        return;
      }
      patchItem(key, { status: "uploading", error: undefined });
      try {
        const form = new FormData();
        form.append("file", item.file);
        const c = campaign.trim();
        const ch = channel.trim();
        if (c) form.append("campaign", c);
        if (ch) form.append("channel", ch);
        form.append("stt_provider", sttProvider);
        const res = await apiUpload<UploadResponse>("/ingest/upload", form);
        patchItem(key, { status: "uploaded", callId: res.call_id });
        onUploaded(res.call_id);
      } catch (err) {
        patchItem(key, { status: "failed", error: errorText(err) });
      }
    },
    [campaign, channel, sttProvider, onUploaded, patchItem],
  );

  async function onSubmit(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (busy) return;
    const pending = itemsRef.current.filter((it) => it.status === "waiting");
    if (pending.length === 0) return;
    setBusy(true);
    try {
      // Strictly sequential: await each upload before starting the next.
      for (const item of pending) {
        // eslint-disable-next-line no-await-in-loop
        await uploadOne(item.key);
      }
    } finally {
      setBusy(false);
    }
  }

  async function retryOne(key: string): Promise<void> {
    if (busy) return;
    setBusy(true);
    try {
      await uploadOne(key);
    } finally {
      setBusy(false);
    }
  }

  function removeItem(key: string): void {
    setItems((prev) => prev.filter((it) => it.key !== key));
  }

  function clearFinished(): void {
    setItems((prev) =>
      prev.filter((it) => it.status === "waiting" || it.status === "uploading"),
    );
  }

  const waitingCount = items.filter((it) => it.status === "waiting").length;
  const hasFinished = items.some(
    (it) => it.status === "uploaded" || it.status === "failed",
  );

  return (
    <Card
      title="Upload calls"
      subtitle="Drop audio files or transcripts (.json) — they upload one at a time, then run through the pipeline."
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-5">
        <div
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={clsx(
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-10 text-center transition-colors",
            dragging
              ? "border-brand-500 bg-brand-500/10 shadow-glow"
              : "border-ink-300/60 bg-ink-100/50 hover:border-brand-500/60 hover:bg-ink-100",
          )}
        >
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-brand-500/15 text-brand-500 ring-1 ring-brand-500/30">
            <UploadCloud className="h-5 w-5" />
          </span>
          <span className="text-sm font-medium text-ink-800">
            Drag &amp; drop files here, or click to browse
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400">
            .wav .mp3 .m4a .ogg .flac .webm .mp4 · .json — multiple files
            supported
          </span>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              addFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {items.length > 0 && (
          <div className="overflow-hidden rounded-xl border border-ink-200">
            <div className="flex items-center justify-between border-b border-ink-200 bg-ink-100/70 px-3.5 py-2">
              <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-ink-400">
                Upload queue · {items.length} file{items.length === 1 ? "" : "s"}
              </span>
              {hasFinished && (
                <button
                  type="button"
                  onClick={clearFinished}
                  className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-400 transition hover:text-ink-700"
                >
                  Clear finished
                </button>
              )}
            </div>
            <ul className="divide-y divide-ink-200/70">
              {items.map((it) => (
                <li
                  key={it.key}
                  className="flex items-start gap-3 px-3.5 py-2.5"
                >
                  <FileAudio2 className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-ink-800">
                      {it.file.name}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-2">
                      {it.status === "waiting" && (
                        <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-400">
                          waiting
                        </span>
                      )}
                      {it.status === "uploading" && (
                        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-warn-400">
                          <Spinner
                            size={10}
                            className="border-warn-500/30 border-t-warn-400"
                          />
                          uploading
                        </span>
                      )}
                      {it.status === "uploaded" && it.callId && (
                        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-ok-400">
                          <CheckCircle2 className="h-3 w-3" />
                          uploaded ·
                          <Link
                            href={`/calls/${it.callId}`}
                            className="text-brand-600 underline-offset-2 hover:underline"
                          >
                            {it.callId.slice(0, 8)}
                          </Link>
                        </span>
                      )}
                      {it.status === "failed" && (
                        <span className="inline-flex max-w-full items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-danger-400">
                          <AlertTriangle className="h-3 w-3 shrink-0" />
                          <span className="truncate normal-case tracking-normal">
                            {it.error ?? "Upload failed."}
                          </span>
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {it.status === "failed" && (
                      <button
                        type="button"
                        onClick={() => void retryOne(it.key)}
                        disabled={busy}
                        className="btn-ghost px-2 py-1 text-xs"
                        title="Retry upload"
                      >
                        <RotateCcw className="h-3 w-3" />
                        Retry
                      </button>
                    )}
                    {(it.status === "waiting" || it.status === "failed") && (
                      <button
                        type="button"
                        onClick={() => removeItem(it.key)}
                        disabled={busy && it.status === "waiting"}
                        className="rounded-md p-1 text-ink-400 transition hover:bg-ink-200/60 hover:text-ink-700"
                        title="Remove from queue"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <label className="label">Campaign (optional)</label>
            <input
              type="text"
              value={campaign}
              onChange={(e) => setCampaign(e.target.value)}
              placeholder="e.g. renewals-q2"
              className="input"
            />
          </div>
          <div>
            <label className="label">Channel (optional)</label>
            <input
              type="text"
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              placeholder="e.g. inbound"
              className="input"
            />
          </div>
          <div>
            <label className="label">STT Provider</label>
            <select
              value={sttProvider}
              onChange={(e) => setSttProvider(e.target.value as STTProvider)}
              className="input"
            >
              {STT_PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {STT_PROVIDER_LABEL[p]}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            className="btn-primary"
            disabled={waitingCount === 0 || busy}
          >
            {busy
              ? "Uploading…"
              : waitingCount > 1
                ? `Upload ${waitingCount} files & run`
                : "Upload & run"}
          </button>
          {busy && <Spinner />}
        </div>
      </form>
    </Card>
  );
}

export default UploadBox;
