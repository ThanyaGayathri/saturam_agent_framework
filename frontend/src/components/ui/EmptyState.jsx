import Button from "./Button";

export default function EmptyState({ icon, title, description, actionLabel, onAction }) {
  return (
    <div className="flex flex-col items-center gap-4 py-24">
      {icon && (
        <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-border-light bg-surface dark:bg-surface-default">
          {icon}
        </div>
      )}
      <div className="text-center">
        <p className="text-base font-semibold text-text-primary">{title}</p>
        {description && (
          <p className="mt-1 text-sm text-text-secondary">{description}</p>
        )}
      </div>
      {actionLabel && (
        <Button onClick={onAction}>{actionLabel}</Button>
      )}
    </div>
  );
}
