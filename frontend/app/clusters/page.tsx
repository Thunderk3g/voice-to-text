"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { Card } from "@/components/Card";
import { SearchBar } from "@/components/SearchBar";
import { IntentBadge } from "@/components/IntentBadge";
import { LanguageBadge } from "@/components/LanguageBadge";
import { LoadingBlock } from "@/components/Spinner";
import type { AnalyticsSummary, ClusterRecord } from "@/lib/types";

interface ClusterRow {
  id: string;
  canonical_question: string;
  dominant_language: ClusterRecord["dominant_language"];
  dominant_intents: ClusterRecord["dominant_intents"];
  frequency: number;
}

const PAGE_SIZE = 20;

export default function ClustersPage(): JSX.Element {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);

  // The backend's /analytics returns `top_clusters` with cluster summary blobs.
  // Use those as the canonical list view; full pagination endpoint TBD.
  const { data, error, isLoading } = useSWR<AnalyticsSummary>("/analytics");

  const rows = useMemo<ClusterRow[]>(() => {
    const raw = (data?.top_clusters ?? []) as Array<Record<string, unknown>>;
    return raw.map((c) => ({
      id: String(c.id ?? c.cluster_id ?? ""),
      canonical_question: String(
        c.canonical_question ?? c.label ?? "(no canonical question)",
      ),
      dominant_language: (c.dominant_language ?? "other") as ClusterRow["dominant_language"],
      dominant_intents: (c.dominant_intents ?? []) as ClusterRow["dominant_intents"],
      frequency: Number(c.frequency ?? 0),
    }));
  }, [data]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((r) =>
      r.canonical_question.toLowerCase().includes(needle),
    );
  }, [rows, q]);

  const pageRows = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));

  if (isLoading) return <LoadingBlock label="Loading clusters..." />;

  return (
    <div className="flex flex-col gap-4">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Cluster Explorer</h1>
          <p className="text-sm text-ink-500">
            Browse semantic clusters of customer questions.
          </p>
        </div>
        <div className="w-72">
          <SearchBar
            placeholder="Search canonical question..."
            onSubmit={(v) => {
              setQ(v);
              setPage(0);
            }}
          />
        </div>
      </header>

      {error && (
        <Card>
          <p className="text-sm text-red-600">
            Failed to load: {String(error.message ?? error)}
          </p>
        </Card>
      )}

      <Card padded={false}>
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>Canonical question</th>
                <th>Language</th>
                <th>Top intents</th>
                <th className="text-right">Frequency</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-6 text-center text-ink-500">
                    No clusters found.
                  </td>
                </tr>
              )}
              {pageRows.map((r) => (
                <tr key={r.id} className="hover:bg-ink-50">
                  <td>
                    <Link
                      href={`/clusters/${r.id}`}
                      className="font-medium text-brand-700 hover:underline"
                    >
                      {r.canonical_question}
                    </Link>
                  </td>
                  <td>
                    <LanguageBadge language={r.dominant_language} />
                  </td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {r.dominant_intents.slice(0, 3).map((i) => (
                        <IntentBadge key={`${r.id}-${i}`} intent={i} />
                      ))}
                    </div>
                  </td>
                  <td className="text-right tabular-nums">{r.frequency}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="flex items-center justify-between text-sm text-ink-500">
        <span>
          {filtered.length} cluster{filtered.length === 1 ? "" : "s"}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn-ghost disabled:opacity-50"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </button>
          <span className="tabular-nums">
            {page + 1} / {totalPages}
          </span>
          <button
            type="button"
            className="btn-ghost disabled:opacity-50"
            disabled={page + 1 >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
