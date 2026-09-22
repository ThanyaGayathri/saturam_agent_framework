const variants = {
  default: "bg-surface-secondary border-border text-text-tertiary",
  primary: "bg-primary-light text-primary border-primary/20",
  success: "bg-status-success-light border-status-success-border text-status-success",
  // The other two status families, so a badge can carry any of the four states the theme
  // defines rather than callers hand-rolling the amber and the red.
  warning: "bg-status-warning-light border-status-warning-border text-status-warning",
  critical: "bg-status-critical-light border-status-critical-border text-status-critical",
  outline: "border border-primary text-primary bg-transparent",
};

export default function Badge({
  variant = "default",
  dot,
  children,
  className = "",
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${
        variants[variant] || variants.default
      } ${className}`}
    >
      {dot && (
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            variant === "success"
              ? "bg-status-success-dot"
              : variant === "warning"
                ? "bg-status-warning-dot"
                : variant === "critical"
                  ? "bg-status-critical-dot"
                  : "bg-text-muted"
          }`}
        />
      )}
      {children}
    </span>
  );
}
