/**
 * One card design, shared by every surface that shows a use case.
 *
 * Pre-defined templates, user-built use cases, pre-built analytics and the automation
 * workflow grid each had their own card before: a flat purple fill, a white `rounded-2xl`
 * one and a borderless one, with titles from 14px to 18px and tags in three different
 * shapes. These constants are the single definition, so a change lands on all of them.
 *
 * The surface is white with a soft lavender wash toward the top-right corner, which is why
 * the gradient runs `to-tr` and only reaches the tint at the very end of the ramp.
 */

export const useCaseCardSurface =
  "group relative flex h-full min-h-0 cursor-pointer flex-col rounded-xl border border-border " +
  "bg-gradient-to-tr from-surface via-surface to-primary-light/70 p-5 text-left " +
  "transition-all duration-200 hover:-translate-y-0.5 " +
  "hover:shadow-[0_10px_24px_-10px_rgba(105,65,198,0.25)] " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--app-surface-default)] " +
  "dark:from-surface-secondary dark:via-surface-secondary dark:to-primary/12 " +
  "dark:hover:shadow-[0_10px_24px_-10px_rgba(105,65,198,0.2)]";

/** Everything above the footer, so the footer can be pinned to the bottom of the card. */
export const useCaseCardBody = "flex min-h-0 flex-1 flex-col";

/**
 * Badge row. Rendered only when there is at least one badge: an empty row still reserved
 * its height before, which is the gap that appeared above the title on badge-less cards.
 */
export const useCaseCardBadgeRow = "mb-2.5 flex flex-wrap items-center gap-2";

export const useCaseCardBadge =
  "inline-flex min-h-[26px] items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold leading-[1.2]";

export const useCaseCardTitle =
  "text-lg font-bold leading-7 tracking-[-0.2px] text-text-primary";

export const useCaseCardDescription =
  "mt-2 line-clamp-3 text-sm leading-6 text-text-secondary";

/** Tags, KPI chips and any other chip row all sit on the same rhythm. */
export const useCaseCardChipRow = "mt-3 flex flex-wrap items-center gap-2";

export const useCaseCardTag =
  "inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-lg border border-border bg-surface " +
  "px-2.5 py-1 text-xs font-medium leading-snug text-text-secondary transition " +
  "dark:bg-surface-default";

export const useCaseCardFooter =
  "mt-4 flex items-center justify-between gap-3 border-t border-border pt-3.5";

/** Bottom-right affordance: nudges right on hover. */
export const useCaseCardArrow =
  "h-4 w-4 shrink-0 text-primary transition-transform duration-200 group-hover:translate-x-1";
