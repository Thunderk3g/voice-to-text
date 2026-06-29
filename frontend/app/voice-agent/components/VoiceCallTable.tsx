"use client";

import clsx from "clsx";
import { Clock, AlertTriangle } from "lucide-react";
import type { CallRead } from "@/lib/types";

/** Extended call row with analysis data (using snake_case to match backend). */
export interface CallTableRow extends CallRead {
  agent_name?: string | null;
  customer_name?: string | null;
  sentiment?: "positive" | "neutral" | "negative";
  risk_score?: number;
  violation_count?: number;
  flags?: Array<{ id: string; label: string }>;
}

export interface VoiceCallTableProps {
  calls: CallTableRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  isLoading?: boolean;
}

function formatDuration(seconds?: number | null): string {
  if (!seconds) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function getSentimentColor(
  sentiment?: "positive" | "neutral" | "negative",
): string {
  switch (sentiment) {
    case "positive":
      return "#6FD195";
    case "negative":
      return "#F4837F";
    default:
      return "#EFC368";
  }
}

function RiskMeter({ score }: { score?: number }): JSX.Element {
  if (score === undefined) return <span className="text-ink-400">—</span>;

  const percent = Math.min(100, Math.max(0, score * 100));
  const color =
    percent > 66 ? "#F4837F" : percent > 33 ? "#EFC368" : "#6FD195";

  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-ink-200">
        <div
          className="h-full transition-all"
          style={{ width: `${percent}%`, backgroundColor: color }}
        />
      </div>
      <span
        className="font-mono text-[10px] font-semibold tabular-nums"
        style={{ color }}
      >
        {Math.round(percent)}%
      </span>
    </div>
  );
}

export function VoiceCallTable({
  calls,
  selectedId,
  onSelect,
  isLoading,
}: VoiceCallTableProps): JSX.Element {
  if (isLoading) {
    return (
      <div className="rounded-xl border border-ink-200 overflow-hidden">
        <table className="table-base">
          <thead>
            <tr>
              <th>Call ID</th>
              <th>Agent</th>
              <th>Customer</th>
              <th>Duration</th>
              <th className="w-8">Sentiment</th>
              <th>Risk</th>
              <th>Flags</th>
            </tr>
          </thead>
          <tbody>
            {[1, 2, 3].map((i) => (
              <tr key={i}>
                <td colSpan={7}>
                  <div className="skeleton h-8" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (calls.length === 0) {
    return (
      <div className="rounded-xl border border-ink-200 bg-ink-100/50 p-8 text-center">
        <p className="text-sm text-ink-500">No calls found.</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-ink-200 overflow-hidden">
      <table className="table-base">
        <thead>
          <tr>
            <th>Call ID</th>
            <th>Agent</th>
            <th>Customer</th>
            <th>Duration</th>
            <th className="w-8">Sentiment</th>
            <th>Risk</th>
            <th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((call) => (
            <tr
              key={call.id}
              onClick={() => onSelect(call.id)}
              tabIndex={0}
              role="button"
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onSelect(call.id);
                }
              }}
              className={clsx(
                "cursor-pointer transition-colors",
                selectedId === call.id
                  ? "bg-brand-500/15 hover:bg-brand-500/20"
                  : "",
              )}
            >
              <td>
                <span className="font-mono text-[11px] font-semibold text-brand-600">
                  {call.id.slice(0, 8)}
                </span>
              </td>
              <td>
                <span className="text-xs text-ink-700">
                  {call.agent_name ?? "Unknown"}
                </span>
              </td>
              <td>
                <span className="text-xs text-ink-700">
                  {call.customer_name ?? "Unknown"}
                </span>
              </td>
              <td>
                <div className="flex items-center gap-1 text-xs text-ink-600">
                  <Clock className="h-3 w-3 opacity-60" />
                  {formatDuration(call.duration_seconds)}
                </div>
              </td>
              <td>
                <div
                  className="h-2.5 w-2.5 rounded-full"
                  style={{
                    backgroundColor: getSentimentColor(call.sentiment),
                  }}
                  title={call.sentiment}
                  aria-label={`Sentiment: ${call.sentiment || 'neutral'}`}
                />
              </td>
              <td>
                <RiskMeter score={call.risk_score} />
              </td>
              <td>
                <div className="flex flex-wrap gap-1">
                  {call.flags && call.flags.length > 0 ? (
                    call.flags.slice(0, 2).map((flag) => (
                      <span
                        key={flag.id}
                        className="inline-flex items-center gap-1 rounded-full bg-warn-500/15 px-2 py-0.5 font-mono text-[9px] text-warn-400"
                        aria-label={`Flag: ${flag.label}`}
                      >
                        <AlertTriangle className="h-2.5 w-2.5" />
                        {flag.label}
                      </span>
                    ))
                  ) : (
                    <span className="text-[9px] text-ink-400">—</span>
                  )}
                  {call.flags && call.flags.length > 2 && (
                    <span className="text-[9px] text-ink-400" aria-label={`Plus ${call.flags.length - 2} more flags`}>
                      +{call.flags.length - 2}
                    </span>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default VoiceCallTable;
