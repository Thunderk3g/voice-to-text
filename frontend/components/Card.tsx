import clsx from "clsx";
import type { ReactNode } from "react";

export function Card({
  title,
  subtitle,
  right,
  children,
  className,
  padded = true,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  children?: ReactNode;
  className?: string;
  padded?: boolean;
}): JSX.Element {
  return (
    <section className={clsx("card", padded && "p-5", className)}>
      {(title || right) && (
        <header
          className={clsx(
            "flex items-start justify-between gap-3",
            padded ? "mb-4" : "border-b border-ink-200 px-5 py-4",
          )}
        >
          <div>
            {title && (
              <h3 className="text-sm font-semibold tracking-tight text-ink-900">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="mt-0.5 text-xs text-ink-400">{subtitle}</p>
            )}
          </div>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}): JSX.Element {
  return (
    <div className="card relative overflow-hidden p-5">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-brand-500/40 to-transparent" />
      <div className="font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-ink-400">
        {label}
      </div>
      <div className="mt-2 font-display text-4xl font-semibold tabular-nums tracking-tight text-ink-900">
        {value}
      </div>
      {hint && <div className="mt-1.5 text-xs text-ink-500">{hint}</div>}
    </div>
  );
}

export default Card;
