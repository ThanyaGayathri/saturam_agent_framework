import { useEffect, useId, useRef, useState } from "react";

function ChevronDown() {
  return (
    <svg
      className="pointer-events-none shrink-0 text-text-muted"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

/** Native select - option menus cannot show icons in most browsers. */
function NativeSelect({ label, required, options, icon, className = "", ...props }) {
  return (
    <div className={className}>
      {label && (
        <label className="mb-1.5 block text-sm font-medium text-text-primary">
          {label}
          {required && <span className="text-red-500"> *</span>}
        </label>
      )}
      <div className="relative flex w-full items-center rounded-lg border border-border-light bg-surface">
        {icon && (
          <span className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-text-muted">
            {icon}
          </span>
        )}
        <select
          className={`w-full min-w-0 flex-1 cursor-pointer appearance-none rounded-lg border-0 bg-transparent py-2 pr-10 text-sm text-text-primary outline-none focus:ring-1 focus:ring-inset focus:ring-primary ${
            icon ? "pl-10" : "pl-4"
          }`}
          {...props}
        >
          {options.map((opt) => (
            <option key={String(opt.value)} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2">
          <ChevronDown />
        </span>
      </div>
    </div>
  );
}

/**
 * Custom listbox so each row can show the same field icon (or per-option `opt.icon`).
 * Native `<option>` cannot render React nodes.
 */
function IconSelect({
  label,
  required,
  options = [],
  icon,
  className = "",
  value,
  onChange,
  disabled,
  ...rest
}) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const close = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const selected = options.find((o) => o.value === value);
  const displayLabel = selected?.label ?? "";

  const pick = (v) => {
    setOpen(false);
    onChange?.({ target: { value: v } });
  };

  return (
    <div ref={rootRef} className={className}>
      {label && (
        <label className="mb-1.5 block text-sm font-medium text-text-primary">
          {label}
          {required && <span className="text-red-500"> *</span>}
        </label>
      )}
      <div className="relative">
        <button
          type="button"
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listId}
          {...rest}
          className={`relative flex w-full min-w-0 items-center rounded-lg border border-border-light bg-surface py-2 pl-10 pr-10 text-left text-sm text-text-primary outline-none transition-colors focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary disabled:cursor-not-allowed disabled:bg-surface-secondary disabled:text-text-muted ${
            open ? "ring-1 ring-inset ring-primary" : ""
          }`}
          onClick={() => {
            if (!disabled) setOpen((o) => !o);
          }}
        >
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted">
            {icon}
          </span>
          <span className="min-w-0 flex-1 truncate">{displayLabel}</span>
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2">
            <ChevronDown />
          </span>
        </button>
        {open && !disabled && (
          <ul
            id={listId}
            role="listbox"
            className="absolute left-0 right-0 top-full z-50 mt-1 max-h-60 overflow-auto rounded-lg border border-border-light bg-surface py-1 shadow-lg"
          >
            {options.map((opt, idx) => {
              const isSelected = opt.value === value;
              const rowIcon = opt.icon ?? icon;
              return (
                <li key={`${String(opt.value)}-${idx}`} role="presentation">
                  <button
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    className={`relative flex w-full items-center py-2 pl-10 pr-4 text-left text-sm text-text-primary hover:bg-surface-secondary ${
                      isSelected ? "bg-primary-light font-medium text-primary" : ""
                    }`}
                    onClick={() => pick(opt.value)}
                  >
                    {rowIcon && (
                      <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted">
                        {rowIcon}
                      </span>
                    )}
                    <span className="min-w-0 flex-1 truncate">{opt.label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

export default function Select({ icon, ...props }) {
  if (icon) {
    return <IconSelect icon={icon} {...props} />;
  }
  return <NativeSelect {...props} />;
}
