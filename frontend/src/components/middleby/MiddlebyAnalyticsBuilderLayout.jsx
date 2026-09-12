import { useNavigate } from "react-router-dom";
import { ANALYTICS_BUILDER_STEPS } from "../../domain/middleby-analytics-builder";

export default function MiddlebyAnalyticsBuilderLayout({
  currentStep,
  title,
  subtitle,
  children,
  backPath,
  nextPath,
  nextLabel,
  wide,
  onNext,
  nextDisabled,
}) {
  const navigate = useNavigate();

  return (
    <div className="relative flex w-full min-h-[calc(100vh-72px)] flex-1 p-4 sm:p-6 md:p-8">
      <div className="flex min-h-0 min-w-0 flex-1 gap-4 md:gap-6 lg:gap-8">
        <aside className="flex w-[272px] shrink-0 flex-col rounded-2xl border border-border-light bg-primary-light/70 px-4 py-6 shadow-sm backdrop-blur-md dark:border-border dark:bg-surface-secondary/95 md:w-[288px]">
          <h3 className="mb-1 px-3 text-xs font-semibold uppercase tracking-wide text-text-secondary">
            Analytics use case steps
          </h3>
          <div className="mt-4 space-y-0.5">
            {ANALYTICS_BUILDER_STEPS.map((step) => {
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
                  <div
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                      isActive
                        ? "bg-primary text-white"
                        : isDone
                          ? "bg-primary/25 text-primary"
                          : "bg-surface-tertiary text-text-muted"
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
                  <div className="min-w-0 flex-1">
                    <span
                      className={`block text-[13px] leading-[18px] ${
                        isActive ? "font-semibold text-primary" : isDone ? "font-medium text-text-primary" : "font-medium text-text-muted"
                      }`}
                    >
                      {step.label}
                    </span>
                    <span className={`block text-[11px] leading-4 ${isActive ? "text-primary/80" : "text-text-muted"}`}>
                      {step.sub}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </aside>

        <main className={`flex min-h-0 min-w-0 flex-1 flex-col ${wide ? "" : "max-w-3xl"}`}>
          <div className="mb-6">
            <h1 className="text-2xl font-bold tracking-tight text-text-primary">{title}</h1>
            {subtitle ? <p className="mt-1.5 text-sm text-text-secondary">{subtitle}</p> : null}
          </div>
          <div className="min-h-0 flex-1">{children}</div>
          <div className="mt-8 flex items-center justify-between gap-4 border-t border-border pt-6">
            <button
              type="button"
              onClick={() => navigate(backPath)}
              className="rounded-lg border border-border bg-surface px-4 py-2.5 text-sm font-semibold text-text-secondary transition hover:bg-surface-secondary"
            >
              Back
            </button>
            <button
              type="button"
              disabled={nextDisabled}
              onClick={() => (onNext ? onNext() : navigate(nextPath))}
              className="rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {nextLabel || "Continue"}
            </button>
          </div>
        </main>
      </div>
    </div>
  );
}
