import { Link } from "react-router-dom";

const variants = {
  primary:
    "bg-primary text-white shadow-sm hover:bg-primary-hover",
  secondary:
    "border border-border-light bg-surface text-text-tertiary hover:bg-surface-secondary",
  ghost:
    "text-primary hover:text-primary-hover",
  outline:
    "border border-border-light text-primary hover:bg-surface-secondary",
  danger:
    "bg-red-600 text-white hover:bg-red-700",
  icon:
    "text-text-muted hover:bg-surface-secondary",
};

const sizes = {
  sm: "px-3 py-1.5 text-xs",
  md: "px-4 py-2 text-sm",
  lg: "px-5 py-2.5 text-sm",
  icon: "p-2",
  "icon-sm": "p-1.5",
};

export default function Button({
  variant = "primary",
  size = "md",
  to,
  children,
  className = "",
  ...props
}) {
  const base = "inline-flex items-center justify-center gap-1 rounded-lg font-semibold transition-colors";
  const classes = `${base} ${variants[variant] || ""} ${sizes[size] || ""} ${className}`;

  if (to) {
    return (
      <Link to={to} className={classes} {...props}>
        {children}
      </Link>
    );
  }

  return (
    <button className={classes} {...props}>
      {children}
    </button>
  );
}
