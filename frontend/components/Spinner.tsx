import clsx from "clsx";

export function Spinner({
  size = 16,
  className,
}: {
  size?: number;
  className?: string;
}): JSX.Element {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={clsx(
        "inline-block animate-spin rounded-full border-2 border-ink-300/50 border-t-brand-500",
        className,
      )}
      style={{ width: size, height: size }}
    />
  );
}

export function LoadingBlock({ label }: { label?: string }): JSX.Element {
  return (
    <div className="flex items-center gap-2.5 p-4 text-sm text-ink-500">
      <Spinner />
      <span>{label ?? "Loading..."}</span>
    </div>
  );
}

export default Spinner;
