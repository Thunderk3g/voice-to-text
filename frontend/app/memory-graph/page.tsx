"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { Card } from "@/components/Card";
import { LoadingBlock } from "@/components/Spinner";
import { IntentBadge } from "@/components/IntentBadge";
import { LanguageBadge } from "@/components/LanguageBadge";
import type { ClusterDetail, MemoryGraph } from "@/lib/types";
import Link from "next/link";

// Cytoscape must run client-only.
const CytoscapeGraph = dynamic(
  () => import("@/components/CytoscapeGraph").then((m) => m.CytoscapeGraph),
  { ssr: false },
);

export default function MemoryGraphPage(): JSX.Element {
  const [minWeight, setMinWeight] = useState(0.2);
  const [highlight, setHighlight] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data, error, isLoading } = useSWR<MemoryGraph>(
    `/memory-graph?min_weight=${minWeight}&limit=500`,
  );

  const { data: selected } = useSWR<ClusterDetail>(
    selectedId ? `/cluster/${selectedId}` : null,
  );

  const filtered = useMemo<MemoryGraph>(() => {
    if (!data) return { nodes: [], edges: [] };
    const edges = data.edges.filter((e) => e.weight >= minWeight);
    const keepIds = new Set<string>();
    edges.forEach((e) => {
      keepIds.add(e.source_cluster_id);
      keepIds.add(e.target_cluster_id);
    });
    // Keep all nodes if no edges survive — fall back to top-frequency nodes.
    const nodes =
      edges.length === 0
        ? [...data.nodes].sort((a, b) => b.frequency - a.frequency).slice(0, 50)
        : data.nodes.filter((n) => keepIds.has(n.id));
    return { nodes, edges };
  }, [data, minWeight]);

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Memory Graph</h1>
          <p className="text-sm text-ink-500">
            Cluster-to-cluster semantic relations (Cytoscape, cose-bilkent).
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <div>
            <label className="label">Min weight: {minWeight.toFixed(2)}</label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={minWeight}
              onChange={(e) => setMinWeight(Number(e.target.value))}
              className="w-48"
            />
          </div>
          <div>
            <label className="label">Search</label>
            <input
              type="text"
              placeholder="Highlight by question text..."
              value={highlight}
              onChange={(e) => setHighlight(e.target.value)}
              className="input w-64"
            />
          </div>
        </div>
      </header>

      {isLoading && <LoadingBlock label="Loading graph..." />}
      {error && (
        <Card>
          <p className="text-sm text-red-600">
            Failed to load /memory-graph: {String(error.message ?? error)}
          </p>
        </Card>
      )}

      {!isLoading && !error && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px]">
          <Card padded={false} className="overflow-hidden">
            <div className="h-[640px] w-full">
              <CytoscapeGraph
                nodes={filtered.nodes}
                edges={filtered.edges}
                highlight={highlight || null}
                onNodeClick={(id) => setSelectedId(id)}
              />
            </div>
          </Card>

          <Card title="Selected cluster">
            {!selectedId && (
              <p className="text-sm text-ink-500">
                Click a node in the graph to inspect its cluster.
              </p>
            )}
            {selectedId && !selected && <LoadingBlock />}
            {selected && (
              <div className="flex flex-col gap-3">
                <div>
                  <div className="text-xs uppercase tracking-wide text-ink-500">
                    Cluster {selected.cluster.id.slice(0, 8)}
                  </div>
                  <div className="text-base font-semibold text-ink-900">
                    {selected.cluster.canonical_question ??
                      selected.cluster.label ??
                      "(unlabeled)"}
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <LanguageBadge language={selected.cluster.dominant_language} />
                  {selected.cluster.dominant_intents.slice(0, 4).map((i) => (
                    <IntentBadge key={i} intent={i} />
                  ))}
                </div>
                <div className="text-xs text-ink-500">
                  Frequency: {selected.cluster.frequency} · Examples:{" "}
                  {selected.examples.length}
                </div>
                <Link
                  href={`/clusters/${selected.cluster.id}`}
                  className="btn-primary justify-center"
                >
                  Open cluster page
                </Link>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
