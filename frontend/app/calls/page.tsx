"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Card } from "@/components/Card";

export default function CallsIndexPage(): JSX.Element {
  const router = useRouter();
  const [id, setId] = useState("");

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
          Enter a call ID to open its inspector. A listing endpoint is TODO.
        </p>
      </header>
      <Card>
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
