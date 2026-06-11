"use client";

import { useState, type FormEvent } from "react";
import { Card } from "@/components/Card";
import { ResultsList } from "@/components/ResultsList";
import { Spinner } from "@/components/Spinner";
import { apiPost, ApiError } from "@/lib/api";
import {
  ALL_INTENTS,
  ALL_LANGUAGES,
  INTENT_LABEL,
  LANGUAGE_LABEL,
  type Intent,
  type Language,
  type SearchRequest,
  type SearchResponse,
} from "@/lib/types";

export default function PlaygroundPage(): JSX.Element {
  const [query, setQuery] = useState("");
  const [language, setLanguage] = useState<Language | "">("");
  const [intents, setIntents] = useState<Intent[]>([]);
  const [topK, setTopK] = useState(10);
  const [minScore, setMinScore] = useState(0);

  const [data, setData] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function toggleIntent(i: Intent): void {
    setIntents((prev) =>
      prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i],
    );
  }

  async function handleSubmit(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const body: SearchRequest = {
        query: query.trim(),
        top_k: topK,
        min_score: minScore,
        ...(language ? { language } : {}),
        ...(intents.length ? { intents } : {}),
      };
      const res = await apiPost<SearchResponse, SearchRequest>("/search", body);
      setData(res);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `${e.message} ${e.body}`
          : (e as Error).message;
      setErr(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex animate-fade-up flex-col gap-6">
      <header>
        <div className="kicker">Explore</div>
        <h1 className="page-title">Retrieval Playground</h1>
        <p className="page-sub">
          POST /search — cross-lingual semantic search over extracted questions.
        </p>
      </header>

      <Card>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="label">Query</label>
            <input
              type="text"
              value={query}
              placeholder="e.g. policy lapse hone par kya hota hai?"
              onChange={(e) => setQuery(e.target.value)}
              className="input"
              required
            />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <label className="label">Language</label>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value as Language | "")}
                className="input"
              >
                <option value="">Any</option>
                {ALL_LANGUAGES.map((l) => (
                  <option key={l} value={l}>
                    {LANGUAGE_LABEL[l]}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Top K: {topK}</label>
              <input
                type="range"
                min={1}
                max={50}
                step={1}
                value={topK}
                onChange={(e) => setTopK(Number(e.target.value))}
                className="w-full"
              />
            </div>
            <div>
              <label className="label">Min score: {minScore.toFixed(2)}</label>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
                className="w-full"
              />
            </div>
          </div>
          <div>
            <label className="label">Intents (multi-select)</label>
            <div className="flex flex-wrap gap-1.5">
              {ALL_INTENTS.map((i) => {
                const active = intents.includes(i);
                return (
                  <button
                    key={i}
                    type="button"
                    onClick={() => toggleIntent(i)}
                    className={
                      active
                        ? "rounded-full border border-brand-500/60 bg-brand-500/15 px-2.5 py-0.5 font-mono text-[10px] font-medium text-brand-600 transition"
                        : "rounded-full border border-ink-200 bg-ink-100 px-2.5 py-0.5 font-mono text-[10px] font-medium text-ink-500 transition hover:border-ink-300 hover:text-ink-700"
                    }
                  >
                    {INTENT_LABEL[i]}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy && (
                <Spinner size={14} className="border-[#1D1407]/30 border-t-[#1D1407]" />
              )}
              {busy ? "Searching..." : "Search"}
            </button>
            {err && <span className="text-xs text-danger-400">{err}</span>}
          </div>
        </form>
      </Card>

      {data && (
        <section className="flex flex-col gap-3">
          <h2 className="font-sans text-sm font-semibold text-ink-700">
            {data.hits.length} hit{data.hits.length === 1 ? "" : "s"} for{" "}
            <span className="font-mono text-brand-600">"{data.query}"</span>
          </h2>
          <ResultsList hits={data.hits} />
        </section>
      )}
    </div>
  );
}
