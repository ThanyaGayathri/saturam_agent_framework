// Shared layout for the 9-step Use Case Builder wizard
import { useNavigate } from "react-router-dom";

const STEPS = [
  { num: 1, label: "Welcome to use case", sub: "Basic Details",    path: "/use-cases/builder/step-1" },
  { num: 2, label: "User Question",        sub: "Define intent",   path: "/use-cases/builder/step-2" },
  { num: 3, label: "Data Scope",           sub: "AI Strategy",     path: "/use-cases/builder/step-3" },
  { num: 4, label: "AI Generates Plan",    sub: "AI Planning",     path: "/use-cases/builder/step-4" },
  { num: 5, label: "Plan Breakdown",       sub: "Step details",    path: "/use-cases/builder/step-5" },
  { num: 6, label: "Orchestration Agent",  sub: "Agent flow",      path: "/use-cases/builder/step-6" },
  { num: 7, label: "Pre-flight Check",     sub: "Data sources",    path: "/use-cases/builder/step-7" },
  { num: 8, label: "Activation",           sub: "Deploy",          path: "/use-cases/builder/step-8" },
  { num: 9, label: "Execution Summary",    sub: "AI Insights",     path: "/use-cases/builder/step-9" },
];

export default function UCBuilderLayout({ currentStep, title, subtitle, children, backPath, nextPath, nextLabel, wide }) {
  const navigate = useNavigate();

  return (
    <div className="relative flex w-full min-h-[calc(100vh-72px)] flex-1 p-4 sm:p-6 md:p-8">
      <div className="flex min-h-0 min-w-0 flex-1 gap-4 md:gap-6 lg:gap-8">
        {/* Left - stepper container */}
        <aside className="flex w-[272px] shrink-0 flex-col rounded-2xl border border-border-light bg-primary-light/70 px-4 py-6 shadow-sm backdrop-blur-md dark:border-border dark:bg-surface-secondary/95 md:w-[288px]">
          <h3 className="mb-1 px-3 text-xs font-semibold uppercase tracking-wide text-text-secondary">
            Use Case create steps
          </h3>
          <div className="mt-4 space-y-0.5">
            {STEPS.map((step) => {
              const isActive = step.num === currentStep;
              const isDone = step.num < currentStep;
              return (
                <button
                  key={step.num}
                  type="button"
                  onClick={() => navigate(step.path)}
                  className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition ${
                    isActive
                      ? "bg-surface shadow-sm ring-1 ring-border dark:ring-border-light"
                      : "hover:bg-surface/50 dark:hover:bg-surface-tertiary/60"
                  }`}
                >
                  {/* Step circle */}
                  <div
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                      isActive
                        ? "bg-primary text-white dark:bg-primary dark:text-white"
                        : isDone
                          ? "bg-primary/25 text-primary dark:bg-primary/30 dark:text-primary"
                          : "bg-surface-tertiary text-text-muted dark:bg-surface-tertiary"
                    }`}
                  >
                    {isDone ? (
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    ) : (
                      step.num
                    )}
                  </div>

                  {/* Labels */}
                  <div className="min-w-0 flex-1">
                    <span
                      className={`block text-[13px] leading-[18px] ${
                        isActive
                          ? "font-semibold text-primary"
                          : isDone
                            ? "font-medium text-text-primary"
                            : "font-medium text-text-muted"
                      }`}
                    >
                      {step.label}
                    </span>
                    <span className={`block text-[11px] leading-4 ${isActive ? "text-primary/80 dark:text-primary" : "text-text-muted"}`}>
                      {step.sub}
                    </span>
                  </div>

                  {/* Active chevron */}
                  {isActive && (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-primary/80">
                      <polyline points="9 18 15 12 9 6" />
                    </svg>
                  )}
                </button>
              );
            })}
          </div>
        </aside>

        {/* Right - white sheet, centered inner column */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col rounded-2xl border border-border-light bg-surface shadow-sm">
          <div className={`mx-auto flex w-full ${wide ? "max-w-5xl" : "max-w-2xl"} flex-1 flex-col px-6 py-8 sm:px-8 sm:py-10 md:px-10 md:py-10`}>
            <div className="mb-8 shrink-0">
              <span className="text-xs font-semibold uppercase tracking-[0.12em] text-primary">
                Step {currentStep}
              </span>
              <h1 className="mt-2 text-2xl font-semibold tracking-tight text-text-primary sm:text-[26px] sm:leading-8">
                {title}
              </h1>
              {subtitle ? <p className="mt-2 text-base leading-relaxed text-text-secondary">{subtitle}</p> : null}
            </div>

            <div className="min-w-0 flex-1">{children}</div>

            <div className="mt-10 flex shrink-0 items-center justify-between border-t border-border pt-6">
              <div>
                {backPath ? (
                  <button
                    type="button"
                    onClick={() => navigate(backPath)}
                    className="flex items-center gap-1 rounded-lg border border-border-light px-4 py-2 text-sm font-semibold text-text-tertiary hover:bg-surface-secondary"
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="19" y1="12" x2="5" y2="12" />
                      <polyline points="12 19 5 12 12 5" />
                    </svg>
                    Back
                  </button>
                ) : null}
              </div>
              <div>
                {nextPath ? (
                  <button
                    type="button"
                    onClick={() => navigate(nextPath)}
                    className="flex items-center gap-1 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-primary-hover"
                  >
                    {nextLabel || "Next Step"}
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="5" y1="12" x2="19" y2="12" />
                      <polyline points="12 5 19 12 12 19" />
                    </svg>
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
