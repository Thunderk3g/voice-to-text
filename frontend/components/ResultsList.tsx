import Link from "next/link";
import { IntentBadge } from "./IntentBadge";
import { LanguageBadge } from "./LanguageBadge";
import type { SearchHit } from "@/lib/types";

export function ResultsList({ hits }: { hits: SearchHit[] }): JSX.Element {
  if (hits.length === 0) {
    return (
      <div className="card p-6 text-center text-sm text-ink-400">
        No results.
      </div>
    );
  }
  return (
    <ul className="flex flex-col gap-3">
      {hits.map((h, i) => {
        const key = h.question.id ?? `${i}-${h.question.normalized_text.slice(0, 12)}`;
        return (
          <li key={key} className="card p-4">
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <div className="text-sm font-medium text-ink-900">
                  {h.question.normalized_text}
                </div>
                {h.question.english_gloss &&
                  h.question.english_gloss !== h.question.normalized_text && (
                    <div className="mt-1 text-xs italic text-ink-500">
                      {h.question.english_gloss}
                    </div>
                  )}
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <IntentBadge intent={h.question.intent} />
                  <LanguageBadge language={h.question.language} />
                  {h.question.secondary_intents.slice(0, 3).map((si, idx) => (
                    <IntentBadge key={`${si}-${idx}`} intent={si} />
                  ))}
                </div>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1.5">
                <div className="rounded-md border border-brand-500/30 bg-brand-500/10 px-2 py-0.5 font-mono text-xs font-semibold tabular-nums text-brand-600">
                  {(h.score * 100).toFixed(1)}%
                </div>
                {h.cluster_id && (
                  <Link
                    href={`/clusters/${h.cluster_id}`}
                    className="text-xs font-medium text-brand-600 underline-offset-4 transition hover:text-brand-700 hover:underline"
                  >
                    View cluster →
                  </Link>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export default ResultsList;
