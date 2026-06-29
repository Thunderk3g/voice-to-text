"use client";

import { useState } from "react";
import clsx from "clsx";
import { Search, X } from "lucide-react";

type FilterType = "all" | "flagged" | "clean";

export interface ListHeaderProps {
  onFilterChange: (filter: FilterType) => void;
  onSearchChange: (query: string) => void;
  stats: {
    totalCalls: number;
    avgRisk: number;
    avgConfidence: number;
    flaggedPercent: number;
  };
  currentFilter: FilterType;
  searchQuery: string;
}

export function VoiceListHeader({
  onFilterChange,
  onSearchChange,
  stats,
  currentFilter,
  searchQuery,
}: ListHeaderProps): JSX.Element {
  const [isSearchOpen, setIsSearchOpen] = useState(false);

  const filters: { id: FilterType; label: string }[] = [
    { id: "all", label: "All" },
    { id: "flagged", label: "Flagged" },
    { id: "clean", label: "Clean" },
  ];

  return (
    <div className="flex flex-col gap-4">
      {/* Filter Buttons */}
      <div className="flex items-center gap-2">
        {filters.map((filter) => (
          <button
            key={filter.id}
            type="button"
            onClick={() => onFilterChange(filter.id)}
            aria-label={`Filter by: ${filter.label} ${filter.id === "all" ? "calls" : filter.id === "flagged" ? "flagged calls" : "clean calls"}`}
            className={clsx(
              "px-3.5 py-2 rounded-lg font-mono text-[10px] font-medium uppercase tracking-[0.1em] transition",
              currentFilter === filter.id
                ? "bg-brand-500 text-[#1D1407]"
                : "border border-ink-200 bg-ink-100/50 text-ink-600 hover:border-ink-300 hover:bg-ink-100",
            )}
          >
            {filter.label}
          </button>
        ))}
      </div>

      {/* Search Bar */}
      <div className="relative">
        <div className="flex items-center gap-2 rounded-lg border border-ink-200 bg-ink-100/50 px-3 py-2 transition focus-within:border-brand-400/60 focus-within:ring-2 focus-within:ring-brand-500/25">
          <Search className="h-4 w-4 text-ink-400" />
          <input
            type="text"
            placeholder="Search calls (⌘K)"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            onFocus={() => setIsSearchOpen(true)}
            onBlur={() => {
              setTimeout(() => setIsSearchOpen(false), 100);
            }}
            className="flex-1 bg-transparent text-sm text-ink-800 outline-none placeholder:text-ink-400"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => onSearchChange("")}
              className="p-1 text-ink-400 transition hover:text-ink-600"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-3">
          <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-400">
            Total Calls
          </div>
          <div className="mt-1.5 text-xl font-semibold text-ink-900">
            {stats.totalCalls}
          </div>
        </div>
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-3">
          <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-400">
            Avg Risk
          </div>
          <div className="mt-1.5 text-xl font-semibold text-brand-600">
            {(stats.avgRisk * 100).toFixed(0)}%
          </div>
        </div>
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-3">
          <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-400">
            Avg Confidence
          </div>
          <div className="mt-1.5 text-xl font-semibold text-jade-600">
            {(stats.avgConfidence * 100).toFixed(0)}%
          </div>
        </div>
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-3">
          <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-400">
            Flagged
          </div>
          <div className="mt-1.5 text-xl font-semibold text-warn-500">
            {stats.flaggedPercent.toFixed(1)}%
          </div>
        </div>
      </div>
    </div>
  );
}

export default VoiceListHeader;
