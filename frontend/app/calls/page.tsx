"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";
import { Card } from "@/components/Card";
import { UploadBox } from "@/components/UploadBox";
import { CallStatusRow } from "@/components/CallStatusRow";

export default function CallsIndexPage(): JSX.Element {
  const router = useRouter();
  const [id, setId] = useState("");
  const [uploaded, setUploaded] = useState<string[]>([]);

  const onUploaded = useCallback((callId: string) => {
    // Newest first; de-dupe in case of a re-upload of the same id.
    setUploaded((prev) => [callId, ...prev.filter((c) => c !== callId)]);
  }, []);

  function onSubmit(e: FormEvent): void {
    e.preventDefault();
    const v = id.trim();
    if (!v) return;
    router.push(`/calls/${v}`);
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">Calls</h1>
        <p className="text-sm text-ink-500">
          Upload an audio file or transcript to run the pipeline, or open an
          existing call by ID.
        </p>
      </header>

      <UploadBox onUploaded={onUploaded} />

      {uploaded.length > 0 && (
        <Card
          title="Pipeline progress"
          subtitle="Live status; polling stops once a call is clustered or failed."
        >
          <div className="flex flex-col">
            {uploaded.map((callId) => (
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
              className="input"
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
