"use client";

import { UploadCloud } from "lucide-react";
import {
  useRef,
  useState,
  type DragEvent,
  type FormEvent,
} from "react";
import { apiUpload, ApiError } from "@/lib/api";
import type { STTProvider, UploadResponse } from "@/lib/types";
import { STT_PROVIDERS, STT_PROVIDER_LABEL } from "@/lib/types";
import { Card } from "./Card";
import { Spinner } from "./Spinner";

const ACCEPT = ".wav,.mp3,.m4a,.ogg,.flac,.webm,.mp4,.json";

export function UploadBox({
  onUploaded,
}: {
  onUploaded: (callId: string) => void;
}): JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [campaign, setCampaign] = useState("");
  const [channel, setChannel] = useState("");
  const [sttProvider, setSttProvider] = useState<STTProvider>("whisper");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset(): void {
    setFile(null);
    setCampaign("");
    setChannel("");
    setSttProvider("whisper");
    if (inputRef.current) inputRef.current.value = "";
  }

  function onDrop(e: DragEvent<HTMLDivElement>): void {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) {
      setFile(dropped);
      setError(null);
    }
  }

  async function onSubmit(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (!file || uploading) return;
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const c = campaign.trim();
      const ch = channel.trim();
      if (c) form.append("campaign", c);
      if (ch) form.append("channel", ch);
      form.append("stt_provider", sttProvider);
      const res = await apiUpload<UploadResponse>("/ingest/upload", form);
      reset();
      onUploaded(res.call_id);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.body?.trim() || err.message);
      } else {
        setError(err instanceof Error ? err.message : "Upload failed.");
      }
    } finally {
      setUploading(false);
    }
  }

  return (
    <Card
      title="Upload a call"
      subtitle="Drop an audio file or transcript (.json) to run it through the pipeline."
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
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
          className={[
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors",
            dragging
              ? "border-brand-500 bg-brand-50"
              : "border-ink-200 bg-ink-50 hover:border-brand-400",
          ].join(" ")}
        >
          <UploadCloud className="h-6 w-6 text-ink-400" />
          {file ? (
            <span className="text-sm font-medium text-ink-900 break-all">
              {file.name}
            </span>
          ) : (
            <span className="text-sm text-ink-500">
              Drag &amp; drop a file here, or click to browse
            </span>
          )}
          <span className="text-xs text-ink-400">
            .wav .mp3 .m4a .ogg .flac .webm .mp4 · .json
          </span>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setError(null);
            }}
          />
        </div>

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

        {error && (
          <p className="text-sm text-red-600 break-words">{error}</p>
        )}

        <div className="flex items-center gap-3">
          <button
            type="submit"
            className="btn-primary"
            disabled={!file || uploading}
          >
            {uploading ? "Uploading..." : "Upload & run"}
          </button>
          {uploading && <Spinner />}
        </div>
      </form>
    </Card>
  );
}

export default UploadBox;
