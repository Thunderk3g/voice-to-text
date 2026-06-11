import clsx from "clsx";
import type { ReactNode } from "react";

export function Badge({
  children,
  color,
  className,
}: {
  children: ReactNode;
  color?: string;
  className?: string;
}): JSX.Element {
  if (color) {
    return (
      <span
        className={clsx(
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[10px] font-medium tracking-wide",
          className,
        )}
        style={{
          backgroundColor: `${color}1a`,
          color,
          border: `1px solid ${color}3d`,
        }}
      >
        {children}
      </span>
    );
  }
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full border border-ink-200 bg-ink-100 px-2 py-0.5 font-mono text-[10px] font-medium tracking-wide text-ink-600",
        className,
      )}
    >
      {children}
    </span>
  );
}

export default Badge;
