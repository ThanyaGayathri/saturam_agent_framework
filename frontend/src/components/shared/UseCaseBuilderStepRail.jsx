import { Check, ChevronRight } from "lucide-react";

/**
 * Where you are in the six steps, as a rail beside the work.
 *
 * The visual language is `UCBuilderLayout`'s, which the legacy builder routes already use:
 * a panel, a numbered circle per step, the current one lifted onto a white card with a
 * chevron, finished ones ticked. The connector line between markers is the one addition,
 * and the panel takes the builder colour so the rail and the sheet beside it read as a pair.
 *
 * A rail has room the horizontal bar did not, so the steps carry their screens' own titles
 * rather than abbreviations invented for a narrow slot.
 *
 * Display only: the steps gate on each other -- a spec must validate before a readiness check
 * means anything -- so the markers are not links and no navigation changes. Each screen's own
 * Back control still moves you backwards.
 */

/** Each screen's own title, verbatim. */
const STEPS = [
  "What kind of use case is this?",
  "Define it",
  "Readiness check",
  "How it will run",
  "Simulation",
  "Go live",
];

export function UseCaseBuilderStepRail({ current }) {
  return (
    // Sticky below the app header, which is 72px and sticks at top-0 with z-40. Anything less
    // than that and the rail slides underneath it on the way down.
    //
    // It fills the viewport rather than its content, so the panel reads as a column beside the
    // work instead of a card floating at the top of one. Viewport height, not the sheet's: a
    // panel as tall as a 1500px form could not stay pinned, and staying pinned is the point.
    //
    // The 180px is the 88px it sticks at, plus the frame's 32px bottom padding, plus the 57px
    // footer. A sticky box cannot leave its container, so at the very bottom of the page a
    // taller rail gets pushed up -- at 100vh-112px it ended 44px under the header. This is the
    // tallest it can be and still hold its position through the last pixel of scroll.
    <nav
      aria-label={`Step ${current} of ${STEPS.length}`}
      className="sticky top-[88px] hidden h-[calc(100vh-180px)] w-[272px] shrink-0 flex-col self-start overflow-y-auto rounded-2xl bg-builder-rail px-4 py-6 dark:bg-surface-secondary/95 lg:flex lg:w-[288px]"
    >
      <ol className="space-y-0.5">
        {STEPS.map((label, i) => {
          const n = i + 1;
          const done = n < current;
          const here = n === current;
          const last = n === STEPS.length;
          return (
            <li key={label} className="relative">
              {/* The line joins this marker to the next one, behind the active card. */}
              {!last && (
                <span
                  aria-hidden
                  className="absolute left-[24px] top-[38px] bottom-[-2px] w-px bg-builder-border dark:bg-border"
                />
              )}
              <div
                className={`relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition ${
                  here ? "border border-builder-border bg-surface shadow-sm dark:border-border-light" : ""
                }`}
              >
                <span
                  aria-hidden
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                    here
                      ? "bg-primary text-white"
                      : done
                        ? "bg-primary/25 text-primary dark:bg-primary/30"
                        : "bg-surface-tertiary text-text-muted"
                  }`}
                >
                  {done ? <Check size={11} strokeWidth={3} /> : n}
                </span>

                <span
                  className={`min-w-0 flex-1 text-[13px] leading-[18px] ${
                    here
                      ? "font-semibold text-primary"
                      : done
                        ? "font-medium text-text-primary"
                        : "font-medium text-text-muted"
                  }`}
                >
                  {label}
                </span>

                {here && <ChevronRight size={14} strokeWidth={2.5} aria-hidden className="shrink-0 text-primary/80" />}
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/**
 * The page: rail on the left, the step's own column on the right. The column keeps the 896px
 * the standards give a form, so only the rail is new width.
 */
export default function UseCaseBuilderStepFrame({ current, asideOpen = false, children }) {
  // With a side panel of its own open, the step is already two columns; a rail would make three
  // in the same width. The rail stands down and the step's own grid takes the page.
  if (asideOpen) {
    return (
      <div className="mx-auto grid w-full max-w-[1400px] gap-5 px-6 py-6 lg:grid-cols-[minmax(0,1fr)_400px]">
        {children}
      </div>
    );
  }
  return (
    // Full bleed with a page gutter, the way UCBuilderLayout does it, so the rail sits at the
    // left edge instead of being pushed inward by a centred column.
    <div className="flex w-full items-start gap-6 p-4 sm:p-6 md:gap-8 md:p-8">
      <UseCaseBuilderStepRail current={current} />
      {/* The step's work sits on its own sheet, the way the rail sits on its grey panel. Two
          surfaces on a tinted page, each held by its own fill rather than an outline: it is
          what tells you the rail is navigation and the sheet is the thing you are filling in.
          The sheet is flat white, unshadowed, and the page behind it carries the gradient --
          that is how the reference does it, sampled: its sheet is #ffffff at every height, and
          a scan across its left edge low down goes rail #e9ecf4 straight to #ffffff with no
          shadow and no border. The sheet's edge only reads where the page is still tinted. */}
      <div className="min-w-0 flex-1 rounded-2xl border border-border bg-surface px-6 py-8 dark:border-border sm:px-8 md:px-10">
        {/* The work keeps a reading column and lets the sheet carry the air around it, rather
            than running the full width of a sheet that grows with the window. */}
        <div className="mx-auto w-full max-w-3xl space-y-5">{children}</div>
      </div>
    </div>
  );
}
