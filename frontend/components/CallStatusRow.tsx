"use client";

import Link from "next/link";
import useSWR from "swr";
import { CallStatus, type CallRead } from "@/lib/types";
import { Badge } from "./Badge";
import { Spinner } from "./Spinner";

function isTerminal(status?: CallStatus): boolean {
  return status === CallStatus.CLUSTERED || status === CallStatus.FAILED;
}

export function CallStatusRow({ callId }: { callId: string }): JSX.Element {
  const { data, error } = useSWR<CallRead>(`/calls/${callId}`, {
    // Poll every 2s until the call reaches a terminal status, then stop.
    refreshInterval: (latest) => (isTerminal(latest?.status) ? 0 : 2000),
  });

  const status = data?.status;
  const terminal = isTerminal(status);

  let pill: JSX.Element;
  if (error) {
    pill = <Badge color="#dc2626">error</Badge>;
  } else if (status === CallStatus.FAILED) {
    pill = <Badge color="#dc2626">{status}</Badge>;
  } else if (status === CallStatus.CLUSTERED) {
    pill = <Badge color="#16a34a">{status}</Badge>;
  } else if (status) {
    pill = (
      <Badge className="animate-pulse" color="#2563eb">
        {status}
      </Badge>
    );
  } else {
    pill = <Badge>loading…</Badge>;
  }

  return (
    <div className="flex items-center justify-between gap-3 border-b border-ink-100 px-1 py-2 last:border-b-0">
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-ink-700">
          {callId.slice(0, 8)}
        </span>
        {pill}
        {!terminal && !error && status && <Spinner size={12} />}
      </div>
      <Link
        href={`/calls/${callId}`}
        className="text-sm font-medium text-brand-700 hover:underline"
      >
        View
      </Link>
    </div>
  );
}

export default CallStatusRow;
