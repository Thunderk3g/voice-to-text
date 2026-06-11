"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";
import useSWR from "swr";
import { Card } from "@/components/Card";
import { LoadingBlock } from "@/components/Spinner";
import { DARK_LAYOUT, type PlotData, type PlotLayout } from "@/lib/plotly";
import type { AnalyticsSummary } from "@/lib/types";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface EmergingRow {
  cluster_id: string;
  label: string;
  first_seen: string;
  growth_rate: number;
  current_size: number;
}

export default function DriftPage(): JSX.Element {
  const { data, error, isLoading } = useSWR<AnalyticsSummary>("/analytics");

  const rows = useMemo<EmergingRow[]>(() => {
    const raw = (data?.emerging_topics ?? []) as Array<Record<string, unknown>>;
    return raw.map((r) => ({
      cluster_id: String(r.cluster_id ?? r.id ?? ""),
      label: String(r.label ?? r.canonical_question ?? "(unlabeled)"),
      first_seen: String(r.first_seen ?? ""),
      growth_rate: Number(r.growth_rate ?? 0),
      current_size: Number(r.current_size ?? r.frequency ?? 0),
    }));
  }, [data]);

  const bubble: PlotData[] = [
    {
      type: "scatter",
      mode: "markers",
      x: rows.map((r) => r.first_seen),
      y: rows.map((r) => r.growth_rate),
      text: rows.map((r) => r.label),
      marker: {
        size: rows.map((r) => Math.max(8, Math.sqrt(r.current_size) * 4)),
        color: "#E9A83D",
        opacity: 0.65,
        line: { color: "#F8C97E", width: 1 },
      },
      hovertemplate:
        "<b>%{text}</b><br>first seen: %{x}<br>growth: %{y:.2f}<br>size: %{marker.size}<extra></extra>",
    },
  ];

  const layout: PlotLayout = {
    ...DARK_LAYOUT,
    margin: { l: 50, r: 16, t: 16, b: 50 },
    xaxis: { ...DARK_LAYOUT.xaxis, title: { text: "First seen" } },
    yaxis: { ...DARK_LAYOUT.yaxis, title: { text: "Growth rate" } },
    showlegend: false,
  };

  return (
    <div className="flex animate-fade-up flex-col gap-6">
      <header>
        <div className="kicker">Monitor</div>
        <h1 className="page-title">Drift</h1>
        <p className="page-sub">
          Emerging topics — bubble size = current cluster size.
        </p>
      </header>

      {isLoading && <LoadingBlock label="Loading drift..." />}
      {error && (
        <Card>
          <p className="text-sm text-danger-400">
            Failed: {String(error.message ?? error)}
          </p>
        </Card>
      )}

      {!isLoading && !error && (
        <>
          <Card title="Emerging topics">
            <div className="h-96">
              <Plot
                data={bubble}
                layout={layout}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%", height: "100%" }}
                useResizeHandler
              />
            </div>
          </Card>

          <Card padded={false} title="Drift table">
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Cluster</th>
                    <th>First seen</th>
                    <th className="text-right">Growth rate</th>
                    <th className="text-right">Current size</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 && (
                    <tr>
                      <td
                        colSpan={4}
                        className="px-3 py-6 text-center text-ink-400"
                      >
                        No emerging topics.
                      </td>
                    </tr>
                  )}
                  {rows.map((r, i) => (
                    <tr key={r.cluster_id || `${i}-${r.label}`}>
                      <td className="font-medium text-ink-900">{r.label}</td>
                      <td className="font-mono text-xs">{r.first_seen}</td>
                      <td className="text-right font-mono text-xs tabular-nums">
                        {r.growth_rate.toFixed(2)}
                      </td>
                      <td className="text-right font-mono text-xs tabular-nums">
                        {r.current_size}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
