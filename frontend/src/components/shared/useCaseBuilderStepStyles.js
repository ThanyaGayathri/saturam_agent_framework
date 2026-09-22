/**
 * Shared frame for the six-step Use Case Builder: compose (steps 1 and 2), preflight, plan,
 * rehearse, activate.
 *
 * The six steps had grown three different column widths (5xl, 4xl, 3xl), three vertical
 * rhythms and two footer paddings, so the page frame shifted twice while you walked one
 * flow. One definition here keeps them in step. The title tokens are the catalog's own, so
 * the wizard reads as the same product as the screen it is entered from.
 */
import { catalogEyebrow, catalogH1, catalogSubtitle } from "./useCaseCatalogPageStyles";

/** One column for every step. Prose inside a step stays capped at `max-w-2xl`. */
export const builderStepShell = "mx-auto w-full max-w-4xl space-y-5 px-6 py-6";

/** Kept for a step that wants a caption above its title; the six steps use the stepper. */
export const builderStepEyebrow = catalogEyebrow;
export const builderStepH1 = catalogH1;
export const builderStepSubtitle = catalogSubtitle;

/**
 * A label naming the group of cards under it. The margins are part of the token on purpose:
 * the rhythm is the point. A group label needs more air above it than its cards have between
 * them, or the groups stop reading as groups -- step 3 had 20px above the label and 4px
 * below it, so each label sat against the card it named and the sections ran together.
 */
export const builderGroupLabel =
  "mb-2 mt-6 text-[11px] font-bold uppercase tracking-[0.12em] text-text-muted";

/** The heading of a card within a step. Steps 5 and 6 share it; 6 puts an icon before it. */
export const builderCardHeading = "text-sm font-bold text-text-primary";

/** Back on the left, the step's own action on the right. */
export const builderStepFooter =
  "flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4";

export const builderStepBackButton =
  "rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary";
