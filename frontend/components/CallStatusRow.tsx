"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { ChevronDown, ChevronUp } from "lucide-react";
import { isTerminalStatus, type CallRead } from "@/lib/types";
import { StatusChip, StagePips } from "./StatusChip";

const ERROR_PREVIEW_CHARS = 140;

export function CallStatusRow({ callId }: { callId: string }): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const { data, error } = useSWR<CallRead>(`/calls/${callId}`, {
    // Poll every ~4s until the call reaches a terminal status, then stop.
    refreshInterval: (latest) =>
      isTerminalStatus(latest?.status) ? 0 : 4000,
  });

  const status = data?.status;
  const failed = status === "failed";
  const errMsg = data?.error_message ?? null;
  const longError = !!errMsg && errMsg.length > ERROR_PREVIEW_CHARS;

  return (
    <div className="flex flex-col gap-1.5 border-b border-ink-200/60 px-1 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <span className="font-mono text-xs text-ink-600" title={callId}>
            {callId.slice(0, 8)}
          </span>
          {error ? (
            <span className="inline-flex items-center rounded-full border border-danger-500/40 bg-danger-500/10 px-2.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-danger-400">
              fetch error
            </span>
          ) : (
            <>
              <StatusChip status={status} />
              <StagePips status={status} className="hidden sm:inline-flex" />
            </>
          )}
          {data?.detected_language && (
            <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-400">
              {data.detected_language}
            </span>
          )}
        </div>
        <Link
          href={`/calls/${callId}`}
          className="text-sm font-medium text-brand-600 underline-offset-4 transition hover:text-brand-700 hover:underline"
        >
          View →
        </Link>
      </div>

      {failed && errMsg && (
        <div className="rounded-lg border border-danger-500/30 bg-danger-500/10 px-3 py-2 text-xs text-danger-300">
          <span className="break-words">
            {expanded || !longError
              ? errMsg
              : `${errMsg.slice(0, ERROR_PREVIEW_CHARS)}…`}
          </span>
          {longError && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="ml-2 inline-flex items-center gap-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-danger-400 hover:text-danger-300"
            >
              {expanded ? (
                <>
                  less <ChevronUp className="h-3 w-3" />
                </>
              ) : (
                <>
                  more <ChevronDown className="h-3 w-3" />
                </>
              )}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default CallStatusRow;
