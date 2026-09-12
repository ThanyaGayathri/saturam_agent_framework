import { useId } from "react";
import { Search, X } from "lucide-react";

const INPUT_CLS =
  "box-border w-full min-w-0 rounded-lg border border-border bg-surface-default py-2 pl-8 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary";

/**
 * Search field aligned with Configure data sources and models tabs (clear control when text is present).
 * @param {{ value: string, onValueChange: (v: string) => void, placeholder?: string, className?: string }} props
 */
export function CatalogSearchToolbar({
  value = "",
  onValueChange,
  placeholder = "Search",
  className = "",
}) {
  const id = useId();
  const hasValue = String(value ?? "").trim().length > 0;

  return (
    <div className={`relative w-full min-w-0 sm:w-64 ${className}`}>
      <Search
        size={15}
        className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
        aria-hidden
      />
      <input
        id={id}
        type="search"
        autoComplete="off"
        value={value ?? ""}
        onChange={(e) => onValueChange(e.target.value)}
        placeholder={placeholder}
        className={INPUT_CLS}
        aria-label={placeholder}
      />
      {hasValue ? (
        <button
          type="button"
          onClick={() => onValueChange("")}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-muted transition hover:bg-surface-secondary hover:text-text-secondary"
          aria-label="Clear search"
        >
          <X size={15} />
        </button>
      ) : null}
    </div>
  );
}
