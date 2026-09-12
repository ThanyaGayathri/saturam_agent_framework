/**
 * Plain-language automation clarity for Middleby playbooks.
 */

export function AutomationClarityBlocks({ playbook, compact = false }) {
  if (!playbook?.automationSummary) return null;

  const actions = playbook.automatedActions || [];
  const human = playbook.staysHuman || [];

  return (
    <div className={compact ? "space-y-4" : "space-y-5"}>
      <section className="rounded-xl border border-primary/25 bg-primary-light/30 px-4 py-4 dark:bg-primary/10">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-primary">What gets automated</p>
        <p className="mt-2 text-sm font-medium leading-relaxed text-text-primary">
          {playbook.automationSummary}
        </p>
      </section>

      {actions.length > 0 ? (
        <section className="rounded-xl border border-border bg-surface-default p-4 shadow-sm">
          <h3 className="text-sm font-semibold text-text-primary">System actions on approval</h3>
          <p className="mt-1 text-xs text-text-secondary">
            Enterprise GPT runs these steps. No buyer calls or deal closes happen automatically.
          </p>
          <ul className="mt-3 space-y-2.5">
            {actions.map((row) => (
              <li
                key={row.what}
                className="rounded-lg border border-border bg-surface-secondary/40 px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-text-primary">{row.what}</span>
                  {row.where ? (
                    <span className="rounded-md bg-surface-default px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-text-muted ring-1 ring-border">
                      {row.where}
                    </span>
                  ) : null}
                </div>
                {row.detail ? (
                  <p className="mt-1 text-xs leading-relaxed text-text-secondary">{row.detail}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {human.length > 0 ? (
        <section className="rounded-xl border border-border bg-surface-secondary/50 px-4 py-4">
          <h3 className="text-sm font-semibold text-text-primary">What stays with your team</h3>
          <ul className="mt-2 space-y-1.5 text-sm text-text-secondary">
            {human.map((line) => (
              <li key={line} className="flex gap-2">
                <span className="text-text-muted" aria-hidden>
                  ·
                </span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
