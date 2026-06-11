"use client";

import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import clsx from "clsx";
import { AlertTriangle, Bot, HelpCircle, User } from "lucide-react";
import { Card } from "@/components/Card";
import { IntentBadge } from "@/components/IntentBadge";
import { LanguageBadge } from "@/components/LanguageBadge";
import { Badge } from "@/components/Badge";
import { StatusChip, StagePips } from "@/components/StatusChip";
import { LoadingBlock } from "@/components/Spinner";
import { DARK_LAYOUT, type PlotData, type PlotLayout } from "@/lib/plotly";
import {
  isTerminalStatus,
  type CallRead,
  type ExtractedQuestion,
  type UtteranceSchema,
} from "@/lib/types";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

type Tab = "transcript" | "questions" | "embeddings";

function fmtTs(s: number): string {
  const total = Math.max(0, Math.floor(s));
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return `${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
}

function fmtDuration(s: number | null | undefined): string | null {
  if (s == null) return null;
  return fmtTs(s);
}

/** Subtle 3-dot confidence meter (low/med/high). */
function ConfidenceDots({ value }: { value: number }): JSX.Element {
  const filled = value >= 0.85 ? 3 : value >= 0.6 ? 2 : 1;
  return (
    <span
      className="inline-flex items-center gap-0.5"
      title={`confidence ${(value * 100).toFixed(0)}%`}
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className={clsx(
            "h-1 w-1 rounded-full",
            i < filled ? "bg-current opacity-70" : "bg-current opacity-20",
          )}
        />
      ))}
    </span>
  );
}

function UtteranceBubble({ u }: { u: UtteranceSchema }): JSX.Element {
  const isAgent = u.speaker === "AGENT";
  const isCustomer = u.speaker === "CUSTOMER";

  return (
    <div
      className={clsx(
        "flex w-full",
        isAgent ? "justify-start" : isCustomer ? "justify-end" : "justify-center",
      )}
    >
      <div
        className={clsx(
          "max-w-[78%] rounded-2xl border px-4 py-2.5 text-sm leading-relaxed",
          isAgent &&
            "rounded-bl-md border-jade-200 bg-jade-100/70 text-ink-800",
          isCustomer &&
            "rounded-br-md border-brand-200 bg-brand-100/60 text-ink-800",
          !isAgent &&
            !isCustomer &&
            "border-ink-200 bg-ink-100 text-ink-600",
        )}
      >
        <div
          className={clsx(
            "mb-1 flex flex-wrap items-center gap-2 font-mono text-[10px] uppercase tracking-[0.1em]",
            isAgent ? "text-jade-600" : isCustomer ? "text-brand-600" : "text-ink-400",
          )}
        >
          <span className="inline-flex items-center gap-1 font-semibold">
            {isAgent ? (
              <Bot className="h-3 w-3" />
            ) : isCustomer ? (
              <User className="h-3 w-3" />
            ) : (
              <HelpCircle className="h-3 w-3" />
            )}
            {u.speaker.toLowerCase()}
            {u.speaker_id ? ` ${u.speaker_id}` : ""}
          </span>
          <span className="tabular-nums opacity-80">{fmtTs(u.start_ts)}</span>
          <LanguageBadge language={u.language} />
          <ConfidenceDots value={u.confidence} />
        </div>
        <div className="whitespace-pre-wrap break-words">{u.text}</div>
      </div>
    </div>
  );
}

export default function CallDetailPage(): JSX.Element {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const [tab, setTab] = useState<Tab>("transcript");

  const { data: call, error, isLoading } = useSWR<CallRead>(
    id ? `/calls/${id}` : null,
    {
      // Keep the header live while the pipeline is still running.
      refreshInterval: (latest) =>
        isTerminalStatus(latest?.status) ? 0 : 4000,
    },
  );
  const { data: utterances } = useSWR<UtteranceSchema[]>(
    tab === "transcript" && id ? `/calls/${id}/utterances` : null,
  );
  const { data: questions } = useSWR<ExtractedQuestion[]>(
    tab === "questions" && id ? `/calls/${id}/questions` : null,
  );

  if (isLoading) return <LoadingBlock label="Loading call..." />;
  if (error) {
    return (
      <Card>
        <p className="text-sm text-danger-400">
          Failed to load call: {String(error.message ?? error)}
        </p>
      </Card>
    );
  }
  if (!call) return <LoadingBlock />;

  const duration = fmtDuration(call.duration_seconds);
  const failed = call.status === "failed";

  return (
    <div className="flex animate-fade-up flex-col gap-6">
      <header>
        <div className="kicker">Call {call.id.slice(0, 8)}</div>
        <h1 className="page-title break-all">{call.source_uri}</h1>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-ink-500">
          <StatusChip status={call.status} />
          <StagePips status={call.status} />
          {call.detected_language && (
            <LanguageBadge language={call.detected_language} />
          )}
          <Badge>{call.is_transcript ? "transcript" : "audio"}</Badge>
          {duration && (
            <span className="font-mono text-xs tabular-nums text-ink-500">
              {duration} min
            </span>
          )}
          <span className="text-xs text-ink-400">
            created {new Date(call.created_at).toLocaleString()}
          </span>
        </div>
      </header>

      {failed && call.error_message && (
        <div className="flex items-start gap-3 rounded-xl border border-danger-500/40 bg-danger-500/10 px-4 py-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger-400" />
          <div>
            <div className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-danger-400">
              Pipeline failed
            </div>
            <p className="mt-1 break-words text-sm text-danger-300">
              {call.error_message}
            </p>
          </div>
        </div>
      )}

      <Card title="Metadata">
        <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3">
          {Object.entries(call.metadata).map(([k, v]) => (
            <div key={k}>
              <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400">
                {k}
              </dt>
              <dd className="mt-0.5 break-all font-mono text-xs text-ink-700">
                {v == null
                  ? "—"
                  : typeof v === "object"
                    ? JSON.stringify(v)
                    : String(v)}
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      <div className="flex gap-1 border-b border-ink-200">
        {(["transcript", "questions", "embeddings"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            className={clsx(
              "-mb-px border-b-2 px-3.5 py-2 text-sm font-medium capitalize transition",
              tab === t
                ? "border-brand-500 text-brand-600"
                : "border-transparent text-ink-400 hover:text-ink-700",
            )}
            onClick={() => setTab(t)}
          >
            {t === "questions" ? "Extracted Questions" : t}
          </button>
        ))}
      </div>

      {tab === "transcript" && (
        <Card padded={false}>
          <div className="flex items-center justify-between border-b border-ink-200 px-5 py-3">
            <div className="flex items-center gap-4 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400">
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-jade-500" /> agent
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-brand-500" /> customer
              </span>
            </div>
            {utterances && (
              <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400">
                {utterances.length} utterances
              </span>
            )}
          </div>
          <div className="flex max-h-[640px] flex-col gap-2.5 overflow-y-auto p-5">
            {!utterances && <LoadingBlock />}
            {utterances && utterances.length === 0 && (
              <div className="p-6 text-center text-sm text-ink-400">
                No utterances yet.
              </div>
            )}
            {utterances?.map((u, i) => (
              <UtteranceBubble key={u.id ?? `${i}-${u.start_ts}`} u={u} />
            ))}
          </div>
        </Card>
      )}

      {tab === "questions" && (
        <Card padded={false}>
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Question</th>
                  <th>Type</th>
                  <th>Intent</th>
                  <th>Language</th>
                  <th className="text-right">Confidence</th>
                </tr>
              </thead>
              <tbody>
                {!questions && (
                  <tr>
                    <td colSpan={5}>
                      <LoadingBlock />
                    </td>
                  </tr>
                )}
                {questions?.length === 0 && (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-3 py-6 text-center text-ink-400"
                    >
                      No extracted questions.
                    </td>
                  </tr>
                )}
                {questions?.map((q, i) => (
                  <tr key={q.id ?? `${i}-${q.raw_text.slice(0, 8)}`}>
                    <td>
                      <div className="font-medium text-ink-900">
                        {q.normalized_text}
                      </div>
                      {q.english_gloss &&
                        q.english_gloss !== q.normalized_text && (
                          <div className="mt-0.5 text-xs italic text-ink-400">
                            {q.english_gloss}
                          </div>
                        )}
                    </td>
                    <td>
                      <Badge>{q.question_type}</Badge>
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        <IntentBadge intent={q.intent} />
                        {q.secondary_intents.slice(0, 2).map((si, idx) => (
                          <IntentBadge key={`${si}-${idx}`} intent={si} />
                        ))}
                      </div>
                    </td>
                    <td>
                      <LanguageBadge language={q.language} />
                    </td>
                    <td className="text-right font-mono text-xs tabular-nums">
                      {(q.confidence * 100).toFixed(0)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {tab === "embeddings" && <EmbeddingsTab />}
    </div>
  );
}

function EmbeddingsTab(): JSX.Element {
  // TODO: backend currently has no 2D projection endpoint.
  // Render a placeholder scatter (random) until /calls/{id}/embeddings/projection lands.
  const points = Array.from({ length: 24 }, (_, i) => ({
    x: Math.cos((i / 24) * Math.PI * 2) + (Math.random() - 0.5) * 0.4,
    y: Math.sin((i / 24) * Math.PI * 2) + (Math.random() - 0.5) * 0.4,
  }));
  const trace: PlotData[] = [
    {
      type: "scatter",
      mode: "markers",
      x: points.map((p) => p.x),
      y: points.map((p) => p.y),
      marker: { size: 10, color: "#E9A83D", opacity: 0.7 },
      name: "questions",
    },
  ];
  const layout: PlotLayout = {
    ...DARK_LAYOUT,
    margin: { l: 30, r: 16, t: 16, b: 30 },
    showlegend: false,
  };
  return (
    <Card
      title="Embeddings (placeholder)"
      subtitle="A 2D projection (PCA/UMAP) will be wired in once the backend exposes it."
    >
      <div className="h-80">
        <Plot
          data={trace}
          layout={layout}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%", height: "100%" }}
          useResizeHandler
        />
      </div>
    </Card>
  );
}
