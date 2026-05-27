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
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
          className,
        )}
        style={{
          backgroundColor: `${color}1f`,
          color,
          border: `1px solid ${color}40`,
        }}
      >
        {children}
      </span>
    );
  }
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full bg-ink-100 px-2 py-0.5 text-[11px] font-medium text-ink-700",
        className,
      )}
    >
      {children}
    </span>
  );
}

export default Badge;
