"use client";

import { AlertTriangle, AlertCircle } from "lucide-react";
import { Card } from "@/components/Card";

export interface Violation {
  id: string;
  type: string;
  severity: "critical" | "high" | "medium" | "low";
  message: string;
  timestamp: number; // seconds
  segment?: string;
}

export interface ViolationsListProps {
  violations?: Violation[];
  isLoading?: boolean;
}

function getSeverityColor(
  severity: "critical" | "high" | "medium" | "low",
): string {
  switch (severity) {
    case "critical":
    case "high":
      return "#F4837F"; // Red
    case "medium":
      return "#EFC368"; // Amber
    case "low":
      return "#9BA3AF"; // Gray
    default:
      return "#9BA3AF";
  }
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export function ViolationsList({
  violations,
  isLoading,
}: ViolationsListProps): JSX.Element {
  if (isLoading) {
    return (
      <Card title="Violations">
        <div className="flex h-32 items-center justify-center text-sm text-ink-400">
          Scanning...
        </div>
      </Card>
    );
  }

  if (!violations || violations.length === 0) {
    return (
      <Card title="Violations">
        <div className="flex items-center gap-3 py-6">
          <AlertCircle className="h-5 w-5 text-ok-400 shrink-0" />
          <p className="text-sm text-ink-500">No violations detected.</p>
        </div>
      </Card>
    );
  }

  // Sort by severity, then by timestamp
  const sorted = [...violations].sort((a, b) => {
    const severityOrder = { critical: 0, high: 1, medium: 2, low: 3 };
    const severityDiff =
      severityOrder[a.severity] - severityOrder[b.severity];
    return severityDiff !== 0 ? severityDiff : a.timestamp - b.timestamp;
  });

  return (
    <Card
      title="Violations"
      right={
        <span className="font-mono text-[10px] text-warn-400">
          {violations.length} found
        </span>
      }
    >
      <div className="space-y-2.5">
        {sorted.map((violation) => {
          const color = getSeverityColor(violation.severity);
          return (
            <div
              key={violation.id}
              className="rounded-lg border p-3 transition hover:bg-ink-100/50"
              style={{
                borderColor: `${color}40`,
                backgroundColor: `${color}0a`,
              }}
            >
              <div className="flex items-start gap-3">
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0"
                  style={{ color }}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-ink-800">
                      {violation.type}
                    </span>
                    <span
                      className="inline-block rounded-full px-2 py-0.5 font-mono text-[9px] font-medium uppercase tracking-[0.08em]"
                      style={{
                        backgroundColor: `${color}26`,
                        color,
                        border: `1px solid ${color}4d`,
                      }}
                      aria-label={`Severity: ${violation.severity}`}
                    >
                      {violation.severity}
                    </span>
                    <span className="font-mono text-[9px] text-ink-400 ml-auto">
                      {formatTime(violation.timestamp)}
                    </span>
                  </div>
                  <p className="mt-1.5 text-xs text-ink-600 leading-relaxed">
                    {violation.message}
                  </p>
                  {violation.segment && (
                    <div className="mt-2 rounded bg-ink-200/50 px-2 py-1 font-mono text-[9px] text-ink-600 break-words">
                      "{violation.segment}"
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

export default ViolationsList;
