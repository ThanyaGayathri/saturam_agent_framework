import { Check } from "lucide-react";

/**
 * Where you are in the six steps.
 *
 * The flow already knew its own shape -- every screen printed "Step N of 6" -- but only as a
 * caption, so you could not see what was behind you, what was left, or how far a decision on
 * step 2 was from going live on step 6. This draws it.
 *
 * Display only, deliberately: the steps gate on each other (a spec must validate before the
 * readiness check means anything), so the markers are not links and the flow is unchanged.
 * Each screen's own Back control is still how you move backwards.
 */

/** Short names for the rail. The screens keep their own full titles. */
const STEPS = ["Kind", "Define", "Readiness", "Plan", "Simulation", "Go live"];

export default function UseCaseBuilderStepper({ current }) {
  return (
    <nav aria-label={`Step ${current} of ${STEPS.length}`} className="flex items-center gap-1.5">
      {STEPS.map((label, i) => {
        const n = i + 1;
        const done = n < current;
        const here = n === current;
        return (
          <div key={label} className="flex min-w-0 flex-1 items-center gap-1.5">
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              {/* The bar carries the progress; the label names it. A bar alone reads as a
                  loading indicator, a label alone gives no sense of distance. */}
              <span
                aria-hidden
                className={`h-1.5 w-full rounded-full ${
                  done ? "bg-primary/45" : here ? "bg-primary" : "bg-border"
                }`}
              />
              <span className="flex min-w-0 items-center gap-1.5">
                <span
                  aria-hidden
                  className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                    done
                      ? "bg-primary-light text-primary"
                      : here
                        ? "bg-primary text-white"
                        : "bg-surface-secondary text-text-muted"
                  }`}
                >
                  {done ? <Check size={11} strokeWidth={3} /> : n}
                </span>
                <span
                  className={`truncate text-xs ${
                    here ? "font-bold text-text-primary" : done ? "font-medium text-text-secondary" : "text-text-muted"
                  }`}
                >
                  {label}
                </span>
              </span>
            </div>
          </div>
        );
      })}
    </nav>
  );
}
