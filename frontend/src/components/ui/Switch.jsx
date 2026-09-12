/**
 * Theme-aware `role="switch"` control with two widths to match KPI tables vs settings panels.
 *
 * Use `compact` (36×20) in dense tables and `comfortable` (44×24) elsewhere.
 *
 * Thumb is absolutely positioned flush to inset (`p-0.5`) edges for correct off/on ends.
 */
export default function Switch({
  checked,
  onCheckedChange,
  "aria-label": ariaLabel,
  disabled,
  size = "compact",
  /** For toggles nested in clickable rows (composer connectors): avoid toggling parent. */
  stopClickPropagation,
  className = "",
}) {
  const sizing =
    size === "comfortable"
      ? {
          track: "h-6 w-11 shrink-0 rounded-full p-0.5",
          thumb:
            "pointer-events-none absolute top-1/2 h-5 w-5 -translate-y-1/2 rounded-full bg-surface shadow-[0_1px_3px_rgba(16,24,40,0.22)] ring-1 ring-black/[0.1] transition-[left,right] duration-200 dark:bg-surface-default dark:shadow-[0_1px_2px_rgba(0,0,0,0.45)] dark:ring-white/[0.16]",
          thumbOff: "left-0.5 right-auto",
          thumbOn: "left-auto right-0.5",
        }
      : {
          track: "h-5 w-9 shrink-0 rounded-full p-0.5",
          thumb:
            "pointer-events-none absolute top-1/2 h-4 w-4 -translate-y-1/2 rounded-full bg-surface shadow-[0_1px_3px_rgba(16,24,40,0.22)] ring-1 ring-black/[0.1] transition-[left,right] duration-200 dark:bg-surface-default dark:shadow-[0_1px_2px_rgba(0,0,0,0.45)] dark:ring-white/[0.16]",
          thumbOff: "left-0.5 right-auto",
          thumbOn: "left-auto right-0.5",
        };

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={(e) => {
        if (stopClickPropagation) e.stopPropagation();
        if (disabled) return;
        onCheckedChange?.(!checked);
      }}
      className={[
        "relative isolate cursor-pointer rounded-full border outline-none transition-colors duration-200",
        "focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-surface-default",
        "disabled:pointer-events-none disabled:opacity-40",
        sizing.track,
        checked
          ? "border border-white/30 bg-primary shadow-[0_1px_3px_rgba(16,24,40,0.16)] dark:border-white/22 dark:shadow-none"
          : "border border-[color:var(--switch-track-off-border)] bg-[color:var(--switch-track-off-bg)] shadow-[inset_0_1px_1px_rgba(16,24,40,0.06)] dark:shadow-[inset_0_1px_0_rgba(0,0,0,0.28)]",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <span
        aria-hidden
        className={[sizing.thumb, checked ? sizing.thumbOn : sizing.thumbOff].join(" ")}
      />
    </button>
  );
}
