import {
  BarChart2,
  CreditCard,
  Globe2,
  Headphones,
  LineChart,
  Package,
  Percent,
  ScanLine,
  ShoppingBag,
  Store,
  Tag,
  Tags,
  Target,
  TrendingUp,
  Users,
  Workflow,
} from "lucide-react";

/** Lucide icon per canonical `BUSINESS_AREAS` label. */
export const BUSINESS_AREA_TAG_ICON = {
  Inventory: Package,
  Orders: ShoppingBag,
  Merchandise: Tags,
  "Store Ops": Store,
  "E-Com": Globe2,
  POS: CreditCard,
  Support: Headphones,
  Pricing: Percent,
  Finance: LineChart,
  Workforce: Users,
};

const ACTION_THEME_TAG_ICON = {
  Analyze: BarChart2,
  Improve: TrendingUp,
  Diagnose: ScanLine,
  Convert: Target,
};

function baseWrap() {
  return "inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium leading-snug transition";
}

/**
 * @param {{ text: string, surface?: 'template' | 'playground' | 'builder', kind?: 'auto' | 'process' }} props
 */
export function CatalogTagPill({ text, surface = "template", kind = "auto" }) {
  if (kind === "process") {
    return (
      <span
        className={`${baseWrap()} border-border bg-gradient-to-b from-surface-secondary to-surface-tertiary text-text-tertiary shadow-sm group-hover:border-primary/25 dark:from-surface-secondary dark:to-surface-tertiary`}
      >
        <Workflow size={14} strokeWidth={2.25} className="shrink-0 text-text-secondary" aria-hidden />
        <span className="min-w-0 break-words text-left">{text}</span>
      </span>
    );
  }

  const AreaIcon = BUSINESS_AREA_TAG_ICON[text] ?? null;
  const ThemeIcon = ACTION_THEME_TAG_ICON[text] ?? null;
  const isTemplate = surface === "template";
  const isBuilder = surface === "builder";

  if (AreaIcon) {
    const A = AreaIcon;
    return (
      <span
        className={`${baseWrap()} ${
          isBuilder
            ? "border-border-light bg-surface-secondary text-text-tertiary shadow-sm group-hover:border-primary/25"
            : isTemplate
              ? "border-primary/20 bg-surface/90 text-text-tertiary shadow-sm group-hover:border-primary/40"
              : "border-primary/25 bg-primary-light text-text-tertiary shadow-sm group-hover:border-primary/40 dark:border-border dark:bg-surface-secondary dark:ring-1 dark:ring-primary/25 dark:group-hover:border-border-light"
        }`}
      >
        <A size={14} strokeWidth={2.25} className="shrink-0 text-primary" aria-hidden />
        <span className="min-w-0 break-words text-left">{text}</span>
      </span>
    );
  }

  if (ThemeIcon) {
    const T = ThemeIcon;
    return (
      <span
        className={`${baseWrap()} ${
          isBuilder
            ? "border-primary/25 bg-primary-light text-text-tertiary shadow-sm group-hover:border-primary/40 dark:border-border dark:bg-surface-secondary dark:ring-1 dark:ring-primary/25 dark:group-hover:border-border-light"
            : isTemplate
              ? "border-primary/20 bg-surface/85 text-text-tertiary shadow-sm group-hover:border-primary/35"
              : "border-primary/20 bg-surface/90 text-text-tertiary shadow-sm group-hover:border-primary/35"
        }`}
      >
        <T
          size={14}
          strokeWidth={2.25}
          className="shrink-0 text-primary"
          aria-hidden
        />
        <span className="min-w-0 break-words text-left">{text}</span>
      </span>
    );
  }

  return (
    <span
      className={`${baseWrap()} ${
        isBuilder
          ? "border-border-light bg-surface text-text-tertiary shadow-sm group-hover:border-border"
          : isTemplate
            ? "border-border-light bg-surface/80 text-text-tertiary shadow-sm group-hover:border-primary/20"
            : "border-primary/25 bg-surface/90 text-text-tertiary shadow-sm group-hover:border-primary/30 dark:border-border dark:bg-surface-secondary dark:ring-1 dark:ring-primary/20 dark:group-hover:border-border-light"
      }`}
    >
      <Tag size={14} strokeWidth={2.25} className="shrink-0 text-text-muted" aria-hidden />
      <span className="min-w-0 break-words text-left">{text}</span>
    </span>
  );
}
