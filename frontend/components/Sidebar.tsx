"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AudioWaveform,
  BarChart3,
  Boxes,
  KeyRound,
  LayoutDashboard,
  Network,
  PhoneCall,
  Search,
  TrendingUp,
} from "lucide-react";
import clsx from "clsx";

type NavItem = { href: string; label: string; icon: React.ReactNode };
type NavSection = { heading: string; items: NavItem[] };

const SECTIONS: NavSection[] = [
  {
    heading: "Monitor",
    items: [
      { href: "/", label: "Overview", icon: <LayoutDashboard className="h-4 w-4" /> },
      { href: "/calls", label: "Calls", icon: <PhoneCall className="h-4 w-4" /> },
      { href: "/analytics", label: "Analytics", icon: <BarChart3 className="h-4 w-4" /> },
      { href: "/drift", label: "Drift", icon: <TrendingUp className="h-4 w-4" /> },
    ],
  },
  {
    heading: "Explore",
    items: [
      { href: "/clusters", label: "Clusters", icon: <Boxes className="h-4 w-4" /> },
      { href: "/memory-graph", label: "Memory Graph", icon: <Network className="h-4 w-4" /> },
      { href: "/playground", label: "Playground", icon: <Search className="h-4 w-4" /> },
    ],
  },
  {
    heading: "Operate",
    items: [
      { href: "/admin", label: "API Keys", icon: <KeyRound className="h-4 w-4" /> },
    ],
  },
];

export function Sidebar(): JSX.Element {
  const pathname = usePathname() ?? "/";
  return (
    <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col border-r border-ink-200 bg-ink-100/40 px-3 py-6 backdrop-blur">
      <Link href="/" className="mb-7 flex items-center gap-2.5 px-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-500/15 text-brand-500 ring-1 ring-brand-500/30">
          <AudioWaveform className="h-[18px] w-[18px]" />
        </span>
        <span className="leading-tight">
          <span className="block font-display text-lg font-semibold tracking-tight text-ink-900">
            v2t
          </span>
          <span className="block font-mono text-[9px] uppercase tracking-[0.24em] text-ink-400">
            call intelligence
          </span>
        </span>
      </Link>

      <nav className="flex flex-1 flex-col gap-5 overflow-y-auto">
        {SECTIONS.map((section) => (
          <div key={section.heading}>
            <div className="mb-1.5 px-3 font-mono text-[9px] font-medium uppercase tracking-[0.24em] text-ink-400/80">
              {section.heading}
            </div>
            <div className="flex flex-col gap-0.5">
              {section.items.map((item) => {
                const active =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname === item.href || pathname.startsWith(`${item.href}/`);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={clsx(
                      "group relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition",
                      active
                        ? "bg-brand-500/10 font-semibold text-brand-700"
                        : "text-ink-500 hover:bg-ink-100 hover:text-ink-800",
                    )}
                  >
                    {/* Marigold active indicator */}
                    <span
                      className={clsx(
                        "absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full transition",
                        active ? "bg-brand-500" : "bg-transparent group-hover:bg-ink-300",
                      )}
                    />
                    <span className={clsx(active ? "text-brand-500" : "text-ink-400 group-hover:text-ink-600")}>
                      {item.icon}
                    </span>
                    {item.label}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="mt-6 border-t border-ink-200 px-3 pt-4 font-mono text-[9px] uppercase tracking-[0.18em] text-ink-400">
        build {process.env.NEXT_PUBLIC_BUILD_REV ?? "dev"}
      </div>
    </aside>
  );
}

export default Sidebar;
