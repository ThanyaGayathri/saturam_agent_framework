/** `maxWidth` is a Tailwind `max-w-*` class; defaults to the original 480px so every existing
 * caller is unchanged. A wider flow (e.g. the create-dashboard builder, with metric/filter
 * sections that cramp at 480px) passes a bigger one instead of forking this component. */
export default function Modal({ title, children, onClose, maxWidth = "max-w-[480px]" }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className={`w-full ${maxWidth} max-h-[calc(100vh-2rem)] overflow-y-auto rounded-xl bg-surface p-6 shadow-xl`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-text-muted hover:bg-surface-secondary"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
