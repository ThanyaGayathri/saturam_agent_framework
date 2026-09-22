export default function Card({
  children,
  hover = false,
  faded = false,
  className = "",
  onClick,
  ...props
}) {
  // Only apply the default padding when the caller has not asked for its own.
  const hasOwnPadding = /(^|\s)p-\S+/.test(className);

  return (
    <div
      className={`rounded-xl border border-border ${hasOwnPadding ? "" : "p-5"} ${
        faded ? "bg-surface-secondary dark:bg-surface-secondary" : "bg-surface dark:bg-surface-default"
      } ${hover ? "cursor-pointer transition hover:border-primary hover:shadow-sm" : ""} ${className}`}
      onClick={onClick}
      {...props}
    >
      {children}
    </div>
  );
}
