"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  Boxes,
  GitBranch,
  LayoutDashboard,
  Network,
  PhoneCall,
  Search,
  TrendingUp,
} from "lucide-react";
import clsx from "clsx";

type NavItem = { href: string; label: string; icon: React.ReactNode };

const NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: <LayoutDashboard className="h-4 w-4" /> },
  { href: "/clusters", label: "Clusters", icon: <Boxes className="h-4 w-4" /> },
  { href: "/memory-graph", label: "Memory Graph", icon: <Network className="h-4 w-4" /> },
  { href: "/playground", label: "Playground", icon: <Search className="h-4 w-4" /> },
  { href: "/calls", label: "Calls", icon: <PhoneCall className="h-4 w-4" /> },
  { href: "/drift", label: "Drift", icon: <TrendingUp className="h-4 w-4" /> },
  { href: "/analytics", label: "Analytics", icon: <BarChart3 className="h-4 w-4" /> },
];

export function Sidebar(): JSX.Element {
  const pathname = usePathname() ?? "/";
  return (
    <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col gap-1 border-r border-ink-200 bg-white px-3 py-5">
      <div className="mb-5 flex items-center gap-2 px-2">
        <GitBranch className="h-5 w-5 text-brand-600" />
        <div className="leading-tight">
          <div className="text-sm font-bold text-ink-900">v2t</div>
          <div className="text-[10px] uppercase tracking-wider text-ink-500">
            call intelligence
          </div>
        </div>
      </div>
      <nav className="flex flex-col gap-0.5">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition",
                active
                  ? "bg-brand-50 font-semibold text-brand-700"
                  : "text-ink-700 hover:bg-ink-100",
              )}
            >
              {item.icon}
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto px-2 text-[10px] text-ink-400">
        v2t-dashboard {process.env.NEXT_PUBLIC_BUILD_REV ?? "dev"}
      </div>
    </aside>
  );
}

export default Sidebar;
