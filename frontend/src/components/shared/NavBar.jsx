import { Link, useLocation } from "react-router-dom";
import logoSvg from "../../assets/retailgpt-logo.svg";
import logoSvgDarkBg from "../../assets/retailgpt-logo-dark-bg.svg";
import { useTheme } from "../../hooks/useTheme";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { homePathForProfile, isNavItemActive } from "../../domain/profile-capabilities";
import HeaderNotificationsAndProfile from "./HeaderNotificationsAndProfile";

export default function NavBar() {
  const location = useLocation();
  const { colorMode } = useTheme();
  const { navItems, profileId } = useDemoProfile();
  const homePath = homePathForProfile(profileId);

  const isActive = (item) => isNavItemActive(item, location.pathname);

  const linkClass = (active) =>
    `shrink-0 rounded-md px-2 py-2 text-sm font-semibold transition-colors sm:px-3 sm:text-base ${
      active
        ? "bg-[#F5F5F5] text-text-primary dark:bg-surface/[0.12] dark:text-text-primary"
        : "text-text-tertiary hover:bg-surface-secondary hover:text-text-primary dark:text-text-secondary dark:hover:bg-surface/[0.06] dark:hover:text-text-primary"
    }`;

  return (
    <header className="sticky top-0 z-40 grid h-[72px] w-full min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 border-b border-border bg-[var(--app-surface-default)] px-3 shadow-[0_1px_0_0_rgba(0,0,0,0.04)] backdrop-blur-md backdrop-saturate-150 dark:shadow-[0_1px_0_0_rgba(255,255,255,0.06)] sm:gap-3 sm:px-6 lg:px-8">
        <Link
          to={homePath}
          className="retailgpt-nav-brand flex items-center gap-1.5 justify-self-start rounded-md outline-none sm:gap-2 focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--app-surface-default)]"
          aria-label="EnterpriseGPT home"
        >
          <img
            src={colorMode === "dark" ? logoSvgDarkBg : logoSvg}
            alt=""
            className="retailgpt-nav-logo-img block h-8 w-8 shrink-0 object-contain"
            width={32}
            height={32}
            decoding="async"
          />
          <span className="whitespace-nowrap text-lg tracking-[-0.8px] text-[color:var(--app-text-primary)] sm:text-xl">
            <span className="font-semibold">Enterprise</span>
            <span className="font-bold">GPT</span>
          </span>
        </Link>

        <nav
          className="min-h-0 min-w-0 justify-self-stretch overflow-x-auto overscroll-x-contain [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          aria-label="Primary"
        >
          <div className="flex w-max items-center gap-1 py-1 sm:mx-auto sm:w-full sm:max-w-full sm:flex-wrap sm:justify-center">
            {navItems.map((item) => (
              <Link key={item.path + item.label} to={item.path} className={linkClass(isActive(item))}>
                {item.label}
              </Link>
            ))}
          </div>
        </nav>

        <HeaderNotificationsAndProfile />
    </header>
  );
}
