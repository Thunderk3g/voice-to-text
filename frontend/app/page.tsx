"use client";

import dynamic from "next/dynamic";
import useSWR from "swr";
import { StatCard, Card } from "@/components/Card";
import { LoadingBlock } from "@/components/Spinner";
import { DARK_LAYOUT, CHART_COLORS, type PlotData } from "@/lib/plotly";
import {
  INTENT_COLOR,
  INTENT_LABEL,
  LANGUAGE_LABEL,
  type AnalyticsSummary,
  type Intent,
  type Language,
} from "@/lib/types";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

export default function OverviewPage(): JSX.Element {
  const { data, error, isLoading } = useSWR<AnalyticsSummary>("/analytics");

  if (isLoading) return <LoadingBlock label="Loading analytics..." />;
  if (error) {
    return (
      <Card title="Overview">
        <p className="text-sm text-danger-400">
          Failed to load /analytics: {String(error.message ?? error)}
        </p>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card title="Overview">
        <p className="text-sm text-ink-400">No data.</p>
      </Card>
    );
  }

  const intentEntries = Object.entries(data.intent_distribution) as Array<
    [Intent, number]
  >;
  const sortedIntents = intentEntries
    .filter(([, v]) => typeof v === "number" && v > 0)
    .sort((a, b) => b[1] - a[1]);
  const topIntents = sortedIntents.slice(0, 8);

  const langEntries = Object.entries(data.language_distribution) as Array<
    [Language, number]
  >;

  const growth = data.cluster_growth ?? [];
  const growthDates = growth
    .map((g) => String((g as { date?: string }).date ?? ""))
    .filter(Boolean);
  const newClusters = growth.map((g) =>
    Number((g as { new_clusters?: number }).new_clusters ?? 0),
  );
  const churned = growth.map((g) =>
    Number((g as { churned_clusters?: number }).churned_clusters ?? 0),
  );

  const intentBarData: PlotData[] = [
    {
      type: "bar",
      x: topIntents.map(([k]) => INTENT_LABEL[k]),
      y: topIntents.map(([, v]) => v),
      marker: { color: topIntents.map(([k]) => INTENT_COLOR[k]) },
      name: "Intent count",
    },
  ];

  const langPieData: PlotData[] = [
    {
      type: "pie",
      labels: langEntries.map(([k]) => LANGUAGE_LABEL[k]),
      values: langEntries.map(([, v]) => v),
      marker: { colors: CHART_COLORS },
      hole: 0.55,
      textinfo: "label+percent",
    },
  ];

  const growthLineData: PlotData[] = [
    {
      type: "scatter",
      mode: "lines+markers",
      x: growthDates,
      y: newClusters,
      name: "New",
      line: { color: "#E9A83D", width: 2 },
    },
    {
      type: "scatter",
      mode: "lines+markers",
      x: growthDates,
      y: churned,
      name: "Churned",
      line: { color: "#F2807B", width: 2 },
    },
  ];

  return (
    <div className="flex animate-fade-up flex-col gap-6">
      <header>
        <div className="kicker">Monitor</div>
        <h1 className="page-title">Overview</h1>
        <p className="page-sub">
          What customers across India are asking — aggregated from every call.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Total Calls" value={data.total_calls.toLocaleString()} />
        <StatCard
          label="Total Questions"
          value={data.total_questions.toLocaleString()}
        />
        <StatCard
          label="Total Clusters"
          value={data.total_clusters.toLocaleString()}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Top intents" subtitle="Distribution of extracted customer intents.">
          <div className="h-72">
            <Plot
              data={intentBarData}
              layout={{ ...DARK_LAYOUT, showlegend: false }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", height: "100%" }}
              useResizeHandler
            />
          </div>
        </Card>
        <Card title="Language distribution" subtitle="Detected languages across all questions.">
          <div className="h-72">
            <Plot
              data={langPieData}
              layout={DARK_LAYOUT}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", height: "100%" }}
              useResizeHandler
            />
          </div>
        </Card>
      </div>

      <Card title="Cluster growth" subtitle="New vs. churned clusters per day.">
        <div className="h-72">
          <Plot
            data={growthLineData}
            layout={DARK_LAYOUT}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%", height: "100%" }}
            useResizeHandler
          />
        </div>
      </Card>
    </div>
  );
}
