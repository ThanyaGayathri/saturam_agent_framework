import { Link } from "react-router-dom";

const TAB_BASE =
  "relative inline-flex items-center pb-3 pt-2 text-base font-semibold transition-colors";
const TAB_ACTIVE = "text-primary";
const TAB_IDLE = "text-text-muted hover:text-text-secondary";

function Tab({ to, active, children }) {
  return (
    <Link to={to} className={`${TAB_BASE} ${active ? TAB_ACTIVE : TAB_IDLE}`}>
      {children}
      <span
        aria-hidden
        className={`absolute inset-x-0 -bottom-px h-0.5 rounded-full transition-colors ${
          active ? "bg-primary" : "bg-transparent"
        }`}
      />
    </Link>
  );
}

export function UseCasePageNav({ current }) {
  return (
    <nav
      className="mb-6 flex gap-6 border-b border-border"
      aria-label="Catalog view"
    >
      <Tab to="/use-cases/templates" active={current === "templates"}>
        Pre-defined use cases
      </Tab>
      <Tab to="/use-cases/gallery" active={current === "gallery"}>
        User-built use cases
      </Tab>
    </nav>
  );
}
