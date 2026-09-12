import { Link } from "react-router-dom";
import { ArrowLeft, Bell, Settings } from "lucide-react";
import logoSvg from "../../assets/retailgpt-logo.svg";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { demoPersona } from "../../domain/demo-profiles";

const AVATAR_COLOR_BY_ROLE = {
  executive: "bg-primary-light dark:bg-primary/20 text-primary",
  operations: "bg-[#dbeafe] text-[#2563eb]",
  analyst: "bg-surface-secondary text-text-tertiary",
};

/**
 * Top bar shown above the chat transcript (replaces the app NavBar on chat routes).
 * Layout: EnterpriseGPT logo on the left + Back link, minimal utility icons on the right.
 * @param {{ variant: "playground" | "builder" }} props
 */
export function DemoChatContextBanner({ variant }) {
  const isPlayground = variant === "playground";
  const backTo = isPlayground ? "/playground" : "/use-cases";
  const backLabel = isPlayground ? "Back to Playground" : "Back to Templates";
  const { role } = useDemoProfile();
  const persona = demoPersona(role);
  const avatarColorCls = AVATAR_COLOR_BY_ROLE[role] ?? AVATAR_COLOR_BY_ROLE.executive;

  return (
    <div className="shrink-0 border-b border-border bg-surface px-4 py-3 sm:px-6 lg:px-8">
      <div className="mx-auto flex w-full items-center justify-between gap-4">
        <div className="flex items-center gap-5">
          <Link to="/playground" className="flex items-center gap-2">
            <img src={logoSvg} alt="EnterpriseGPT" className="h-7 w-7" />
            <span className="text-[15px] font-semibold text-text-primary">EnterpriseGPT</span>
          </Link>
          <Link
            to={backTo}
            className="flex items-center gap-1.5 text-sm font-medium text-primary transition hover:text-[#7f56d9]"
          >
            <ArrowLeft size={15} />
            {backLabel}
          </Link>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="Settings"
            className="flex h-9 w-9 items-center justify-center rounded-full text-text-secondary transition hover:bg-surface-tertiary hover:text-text-primary"
          >
            <Settings size={18} />
          </button>
          <button
            type="button"
            aria-label="Notifications"
            className="flex h-9 w-9 items-center justify-center rounded-full text-text-secondary transition hover:bg-surface-tertiary hover:text-text-primary"
          >
            <Bell size={18} />
          </button>
          <div
            className={`flex h-9 w-9 items-center justify-center rounded-full text-sm font-semibold ${avatarColorCls}`}
            aria-label={persona.initials}
          >
            {persona.initials}
          </div>
        </div>
      </div>
    </div>
  );
}
