"use client";

import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import clsx from "clsx";
import { Card } from "@/components/Card";
import { IntentBadge } from "@/components/IntentBadge";
import { LanguageBadge } from "@/components/LanguageBadge";
import { Badge } from "@/components/Badge";
import { LoadingBlock } from "@/components/Spinner";
import type {
  CallRead,
  ExtractedQuestion,
  UtteranceSchema,
} from "@/lib/types";
import type { Data, Layout } from "plotly.js";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

type Tab = "utterances" | "questions" | "embeddings";

function fmtTs(s: number): string {
  const total = Math.max(0, Math.floor(s));
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return `${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
}

export default function CallDetailPage(): JSX.Element {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const [tab, setTab] = useState<Tab>("utterances");

  const { data: call, error, isLoading } = useSWR<CallRead>(
    id ? `/calls/${id}` : null,
  );
  const { data: utterances } = useSWR<UtteranceSchema[]>(
    tab === "utterances" && id ? `/calls/${id}/utterances` : null,
  );
  const { data: questions } = useSWR<ExtractedQuestion[]>(
    tab === "questions" && id ? `/calls/${id}/questions` : null,
  );

  if (isLoading) return <LoadingBlock label="Loading call..." />;
  if (error) {
    return (
      <Card>
        <p className="text-sm text-red-600">
          Failed to load call: {String(error.message ?? error)}
        </p>
      </Card>
    );
  }
  if (!call) return <LoadingBlock />;

  return (
    <div className="flex flex-col gap-6">
      <header>
        <div className="text-xs uppercase tracking-wide text-ink-500">
          Call {call.id.slice(0, 8)}
        </div>
        <h1 className="text-2xl font-bold tracking-tight">{call.source_uri}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-sm text-ink-500">
          <Badge>{call.status}</Badge>
          {call.detected_language && (
            <LanguageBadge language={call.detected_language} />
          )}
          {call.is_transcript ? (
            <Badge>transcript</Badge>
          ) : (
            <Badge>audio</Badge>
          )}
          {call.duration_seconds != null && (
            <span>· {call.duration_seconds.toFixed(1)}s</span>
          )}
          <span>· created {new Date(call.created_at).toLocaleString()}</span>
        </div>
      </header>

      <Card title="Metadata">
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          {Object.entries(call.metadata).map(([k, v]) => (
            <div key={k}>
              <dt className="text-xs uppercase tracking-wide text-ink-500">
                {k}
              </dt>
              <dd className="font-mono text-ink-800 break-all">
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

      <div className="flex gap-2 border-b border-ink-200">
        {(["utterances", "questions", "embeddings"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            className={clsx(
              "border-b-2 px-3 py-2 text-sm font-medium capitalize",
              tab === t
                ? "border-brand-600 text-brand-700"
                : "border-transparent text-ink-500 hover:text-ink-800",
            )}
            onClick={() => setTab(t)}
          >
            {t === "questions" ? "Extracted Questions" : t}
          </button>
        ))}
      </div>

      {tab === "utterances" && (
        <Card padded={false}>
          <div className="max-h-[600px] overflow-y-auto p-2">
            {!utterances && <LoadingBlock />}
            {utterances && utterances.length === 0 && (
              <div className="p-6 text-center text-sm text-ink-500">
                No utterances.
              </div>
            )}
            {utterances?.map((u, i) => (
              <div
                key={u.id ?? `${i}-${u.start_ts}`}
                className={clsx(
                  "mx-2 my-1.5 max-w-[80%] rounded-xl border px-3 py-2 text-sm",
                  u.speaker === "AGENT"
                    ? "ml-auto border-brand-200 bg-brand-50 text-ink-900"
                    : u.speaker === "CUSTOMER"
                      ? "border-ink-200 bg-white text-ink-900"
                      : "border-ink-100 bg-ink-50 text-ink-700",
                )}
              >
                <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wide text-ink-500">
                  <span className="font-semibold">{u.speaker}</span>
                  <span>·</span>
                  <span>
                    {fmtTs(u.start_ts)} – {fmtTs(u.end_ts)}
                  </span>
                  <LanguageBadge language={u.language} />
                </div>
                <div>{u.text}</div>
              </div>
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
                      className="px-3 py-6 text-center text-ink-500"
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
                          <div className="text-xs italic text-ink-500">
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
                    <td className="text-right tabular-nums">
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
  const trace: Data[] = [
    {
      type: "scatter",
      mode: "markers",
      x: points.map((p) => p.x),
      y: points.map((p) => p.y),
      marker: { size: 10, color: "#1f5cf5", opacity: 0.7 },
      name: "questions",
    },
  ];
  const layout: Partial<Layout> = {
    margin: { l: 30, r: 16, t: 16, b: 30 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "Inter, ui-sans-serif", size: 11, color: "#3f4858" },
    showlegend: false,
    xaxis: { zeroline: false, showgrid: true, gridcolor: "#e5e7eb" },
    yaxis: { zeroline: false, showgrid: true, gridcolor: "#e5e7eb" },
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
