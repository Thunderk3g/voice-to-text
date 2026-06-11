"use client";

import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { Card } from "@/components/Card";
import { IntentBadge } from "@/components/IntentBadge";
import { LanguageBadge } from "@/components/LanguageBadge";
import { LoadingBlock } from "@/components/Spinner";
import { apiPost } from "@/lib/api";
import {
  FeedbackAction,
  INTENT_COLOR,
  INTENT_LABEL,
  LANGUAGE_LABEL,
  type ClusterDetail,
  type FeedbackAnnotation,
  type Intent,
  type Language,
} from "@/lib/types";
import {
  CHART_COLORS,
  DARK_LAYOUT,
  type PlotData,
  type PlotLayout,
} from "@/lib/plotly";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const COMMON_LAYOUT: PlotLayout = {
  ...DARK_LAYOUT,
  margin: { l: 30, r: 16, t: 16, b: 30 },
};

export default function ClusterDetailPage(): JSX.Element {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";

  const { data, error, isLoading, mutate } = useSWR<ClusterDetail>(
    id ? `/cluster/${id}` : null,
  );

  const [busy, setBusy] = useState<string | null>(null);
  const [mergeTarget, setMergeTarget] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  async function submitFeedback(
    action: FeedbackAnnotation["action"],
    payload: Record<string, unknown>,
  ): Promise<void> {
    setBusy(action);
    setMsg(null);
    try {
      await apiPost<unknown, FeedbackAnnotation>("/feedback", {
        action,
        payload,
        author: "dashboard",
      });
      setMsg(`Submitted: ${action}`);
      await mutate();
    } catch (e) {
      setMsg(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  }

  if (isLoading) return <LoadingBlock label="Loading cluster..." />;
  if (error) {
    return (
      <Card>
        <p className="text-sm text-danger-400">
          Failed to load cluster: {String(error.message ?? error)}
        </p>
      </Card>
    );
  }
  if (!data) return <LoadingBlock />;

  const intentEntries = Object.entries(data.intent_distribution) as Array<
    [Intent, number]
  >;
  const langEntries = Object.entries(data.language_distribution) as Array<
    [Language, number]
  >;

  const intentPie: PlotData[] = [
    {
      type: "pie",
      labels: intentEntries.map(([k]) => INTENT_LABEL[k]),
      values: intentEntries.map(([, v]) => v),
      marker: { colors: intentEntries.map(([k]) => INTENT_COLOR[k]) },
      hole: 0.55,
      textinfo: "label+percent",
    },
  ];

  const langPie: PlotData[] = [
    {
      type: "pie",
      labels: langEntries.map(([k]) => LANGUAGE_LABEL[k]),
      values: langEntries.map(([, v]) => v),
      marker: { colors: CHART_COLORS },
      hole: 0.55,
      textinfo: "label+percent",
    },
  ];

  const c = data.cluster;
  const faq = data.canonical_faq;

  return (
    <div className="flex animate-fade-up flex-col gap-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <div className="kicker">Cluster {c.id.slice(0, 8)}</div>
          <h1 className="page-title">
            {c.canonical_question ?? c.label ?? "(unlabeled cluster)"}
          </h1>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <LanguageBadge language={c.dominant_language} />
            {c.dominant_intents.map((i) => (
              <IntentBadge key={i} intent={i} />
            ))}
            <span className="rounded-full border border-ink-200 bg-ink-100 px-2 py-0.5 font-mono text-[10px] font-medium text-ink-600">
              freq {c.frequency}
            </span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <button
            type="button"
            className="btn-primary"
            disabled={busy !== null}
            onClick={() =>
              submitFeedback(FeedbackAction.REGENERATE_FAQ, { cluster_id: c.id })
            }
          >
            {busy === FeedbackAction.REGENERATE_FAQ
              ? "Submitting..."
              : "Regenerate FAQ"}
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={busy !== null}
            onClick={() =>
              submitFeedback(FeedbackAction.SPLIT_CLUSTER, { cluster_id: c.id })
            }
          >
            Split cluster
          </button>
          <div className="flex items-center gap-1">
            <input
              type="text"
              placeholder="Target cluster id..."
              value={mergeTarget}
              onChange={(e) => setMergeTarget(e.target.value)}
              className="input w-48"
            />
            <button
              type="button"
              className="btn-ghost"
              disabled={busy !== null || !mergeTarget.trim()}
              onClick={() =>
                submitFeedback(FeedbackAction.MERGE_CLUSTERS, {
                  source_cluster_id: c.id,
                  target_cluster_id: mergeTarget.trim(),
                })
              }
            >
              Merge with...
            </button>
          </div>
          {msg && <div className="text-xs text-ink-500">{msg}</div>}
        </div>
      </header>

      <Card title="Canonical FAQ">
        {faq ? (
          <div className="flex flex-col gap-2">
            <div className="text-base font-semibold text-ink-900">
              {faq.canonical_question}
            </div>
            {faq.canonical_question_en && (
              <div className="text-sm italic text-ink-500">
                {faq.canonical_question_en}
              </div>
            )}
            {faq.suggested_answer && (
              <div className="mt-2 whitespace-pre-wrap rounded-lg border border-ink-200 bg-ink-100/70 p-3.5 text-sm leading-relaxed text-ink-700">
                {faq.suggested_answer}
              </div>
            )}
            <div className="mt-2 font-mono text-[10px] uppercase tracking-[0.12em] text-ink-400">
              v{faq.version} · confidence {(faq.confidence * 100).toFixed(1)}%
            </div>
          </div>
        ) : (
          <p className="text-sm text-ink-400">No canonical FAQ yet.</p>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Intent distribution">
          <div className="h-64">
            <Plot
              data={intentPie}
              layout={COMMON_LAYOUT}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", height: "100%" }}
              useResizeHandler
            />
          </div>
        </Card>
        <Card title="Language distribution">
          <div className="h-64">
            <Plot
              data={langPie}
              layout={COMMON_LAYOUT}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", height: "100%" }}
              useResizeHandler
            />
          </div>
        </Card>
      </div>

      <Card title="Examples" padded={false}>
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>Text</th>
                <th>Intent</th>
                <th>Language</th>
                <th className="text-right">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {data.examples.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-6 text-center text-ink-400">
                    No example questions.
                  </td>
                </tr>
              )}
              {data.examples.map((q, i) => (
                <tr key={q.id ?? `${i}-${q.raw_text.slice(0, 12)}`}>
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
                    <IntentBadge intent={q.intent} />
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
    </div>
  );
}
