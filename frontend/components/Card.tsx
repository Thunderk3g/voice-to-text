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
    <section className={clsx("card", padded && "p-4", className)}>
      {(title || right) && (
        <header className="mb-3 flex items-start justify-between gap-3">
          <div>
            {title && (
              <h3 className="text-sm font-semibold text-ink-900">{title}</h3>
            )}
            {subtitle && (
              <p className="mt-0.5 text-xs text-ink-500">{subtitle}</p>
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
    <div className="card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-ink-500">
        {label}
      </div>
      <div className="mt-1 text-3xl font-bold tabular-nums text-ink-900">
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-ink-500">{hint}</div>}
    </div>
  );
}

export default Card;
