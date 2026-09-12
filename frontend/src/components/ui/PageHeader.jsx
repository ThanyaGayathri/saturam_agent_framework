export default function PageHeader({ title, description, titleAction, children }) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold text-text-primary">{title}</h1>
          {titleAction}
        </div>
        {description && (
          <p className="mt-1 text-base text-text-secondary">{description}</p>
        )}
      </div>
      {children && <div className="flex items-center gap-3">{children}</div>}
    </div>
  );
}
