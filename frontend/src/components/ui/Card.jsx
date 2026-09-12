export default function Card({
  children,
  hover = false,
  faded = false,
  className = "",
  onClick,
  ...props
}) {
  return (
    <div
      className={`rounded-xl border border-border p-5 ${
        faded ? "bg-surface-secondary dark:bg-surface-secondary" : "bg-surface dark:bg-surface-default"
      } ${hover ? "cursor-pointer transition hover:border-primary hover:shadow-sm" : ""} ${className}`}
      onClick={onClick}
      {...props}
    >
      {children}
    </div>
  );
}
