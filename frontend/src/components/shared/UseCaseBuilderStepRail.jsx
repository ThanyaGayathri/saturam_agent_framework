import { Check, ChevronRight } from "lucide-react";

/**
 * Where you are in the six steps, as a rail beside the work.
 *
 * The visual language is `UCBuilderLayout`'s, which the legacy builder routes already use:
 * a lavender panel, a numbered circle per step, the current one lifted onto a white card
 * with a ring and a chevron, finished ones ticked. Same tokens, so the two builders look
 * like one product. The connector line between markers is the one addition.
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
    <nav
      aria-label={`Step ${current} of ${STEPS.length}`}
      className="hidden w-[272px] shrink-0 flex-col self-start rounded-2xl border border-border-light bg-primary-light/70 px-4 py-6 shadow-sm backdrop-blur-md dark:border-border dark:bg-surface-secondary/95 lg:flex lg:w-[288px]"
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
                  className="absolute left-[24px] top-[38px] bottom-[-2px] w-px bg-border-light dark:bg-border"
                />
              )}
              <div
                className={`relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition ${
                  here ? "bg-surface shadow-sm ring-1 ring-border dark:ring-border-light" : ""
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
    <div className="mx-auto flex w-full max-w-[1256px] items-start gap-6 px-6 py-6">
      <UseCaseBuilderStepRail current={current} />
      <div className="min-w-0 flex-1 space-y-5 lg:max-w-4xl">{children}</div>
    </div>
  );
}
