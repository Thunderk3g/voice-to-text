"use client";

import clsx from "clsx";
import { AlertTriangle, CheckCircle2, Clock3 } from "lucide-react";
import {
  PIPELINE_STAGES,
  stageForStatus,
  type CallStatus,
} from "@/lib/types";
import { Spinner } from "./Spinner";

/** Friendly status chip: queued → transcribing → … → done / failed. */
export function StatusChip({
  status,
  className,
}: {
  status: CallStatus | undefined;
  className?: string;
}): JSX.Element {
  const stage = stageForStatus(status);

  const styles: Record<typeof stage.kind, string> = {
    queued: "border-ink-300/60 bg-ink-100 text-ink-500",
    running: "border-warn-500/40 bg-warn-500/10 text-warn-400",
    done: "border-ok-500/40 bg-ok-500/10 text-ok-400",
    failed: "border-danger-500/40 bg-danger-500/10 text-danger-400",
  };

  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.08em]",
        styles[stage.kind],
        className,
      )}
    >
      {stage.kind === "running" && (
        <Spinner size={10} className="border-warn-500/30 border-t-warn-400" />
      )}
      {stage.kind === "queued" && <Clock3 className="h-3 w-3" />}
      {stage.kind === "done" && <CheckCircle2 className="h-3 w-3" />}
      {stage.kind === "failed" && <AlertTriangle className="h-3 w-3" />}
      {stage.label}
    </span>
  );
}

/** Tiny pipeline progress pips: one per stage, filled as stages complete. */
export function StagePips({
  status,
  className,
}: {
  status: CallStatus | undefined;
  className?: string;
}): JSX.Element {
  const stage = stageForStatus(status);
  return (
    <span
      className={clsx("inline-flex items-center gap-1", className)}
      title={PIPELINE_STAGES.map(
        (s, i) =>
          `${s}: ${
            i < stage.completed
              ? "done"
              : i === stage.activeIndex
                ? "running"
                : "pending"
          }`,
      ).join(" · ")}
    >
      {PIPELINE_STAGES.map((s, i) => {
        const isDone = i < stage.completed;
        const isActive = i === stage.activeIndex && stage.kind === "running";
        return (
          <span
            key={s}
            className={clsx(
              "h-1.5 w-4 rounded-full transition-colors",
              stage.kind === "failed"
                ? "bg-danger-500/30"
                : isDone
                  ? "bg-ok-500"
                  : isActive
                    ? "animate-pulse bg-warn-500"
                    : "bg-ink-300/50",
            )}
          />
        );
      })}
    </span>
  );
}

export default StatusChip;
