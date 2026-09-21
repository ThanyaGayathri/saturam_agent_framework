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
  // Both use-case card surfaces share one tag look: white with a light border. They used to
  // differ by fill (lavender, 80% white, tinted) and by icon, which made the same tag read
  // as three different things across the three tabs.
  const onCard = isTemplate || isBuilder;
  const cardTag =
    "border-border-light bg-surface text-text-tertiary shadow-sm group-hover:border-primary/30 dark:bg-surface-default";

  if (AreaIcon) {
    const A = AreaIcon;
    return (
      <span
        className={`${baseWrap()} ${
          onCard
            ? cardTag
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
          onCard
            ? cardTag
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
        onCard
          ? cardTag
          : "border-primary/25 bg-surface/90 text-text-tertiary shadow-sm group-hover:border-primary/30 dark:border-border dark:bg-surface-secondary dark:ring-1 dark:ring-primary/20 dark:group-hover:border-border-light"
      }`}
    >
      <Tag size={14} strokeWidth={2.25} className="shrink-0 text-text-muted" aria-hidden />
      <span className="min-w-0 break-words text-left">{text}</span>
    </span>
  );
}
