export default function SlidePanel({
  title,
  subtitle,
  children,
  onClose,
  footer,
  icon,
  headerActions,
  widthClass = "max-w-[420px]",
}) {
  const defaultIcon = (
    <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-border-light bg-surface dark:bg-surface-default">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-secondary">
        <ellipse cx="12" cy="5" rx="9" ry="3" />
        <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
        <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
      </svg>
    </div>
  );

  // Allow callers to pass `icon={null}` to suppress the icon entirely;
  // only fall back to the default when the prop is omitted.
  const renderedIcon = icon === undefined ? defaultIcon : icon;

  return (
    <div className="fixed inset-x-0 bottom-0 top-[72px] z-30 flex justify-end" onClick={onClose}>
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/20" />

      {/* Panel */}
      <div
        className={`relative flex h-full w-full ${widthClass} flex-col bg-surface shadow-xl`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b border-border px-6 py-5">
          <div className="flex min-w-0 items-start gap-3">
            {renderedIcon}
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
              {subtitle && (
                <p className="mt-0.5 text-sm text-text-secondary">{subtitle}</p>
              )}
            </div>
          </div>
          {headerActions ? (
            <div className="flex shrink-0 items-center gap-2">{headerActions}</div>
          ) : (
            <button
              onClick={onClose}
              className="rounded p-1 text-text-muted hover:bg-surface-secondary"
              aria-label="Close"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {children}
        </div>

        {/* Footer */}
        {footer && (
          <div className="flex items-center justify-end gap-3 border-t border-border px-6 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
