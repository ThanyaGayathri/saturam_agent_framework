const variants = {
  default: "bg-surface-secondary border-border text-text-tertiary",
  primary: "bg-primary-light text-primary border-primary/20",
  success: "bg-status-success-light border-status-success-border text-status-success",
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
            variant === "success" ? "bg-status-success-dot" : "bg-text-muted"
          }`}
        />
      )}
      {children}
    </span>
  );
}
