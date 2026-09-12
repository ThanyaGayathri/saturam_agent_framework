import { Outlet, useLocation } from "react-router-dom";
import NavBar from "./NavBar";

export default function Layout() {
  const { pathname } = useLocation();
  const hideFooter = pathname.includes("/chat");
  const hideNav = pathname === "/playground/chat";

  return (
    <div className="flex min-h-screen flex-col">
      {!hideNav && <NavBar />}
      <div className="relative isolate flex min-h-0 w-full flex-1 flex-col bg-[var(--app-surface-page)] text-text-primary">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 -z-10 overflow-hidden dark:hidden"
        >
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_115%_68%_at_50%_-20%,#eef0fd_0%,#f4f7fe_34%,rgba(248,249,255,0.5)_56%,transparent_76%)]" />
          <div className="absolute inset-0 bg-gradient-to-b from-transparent from-[26%] via-white/25 via-[58%] to-white to-[96%]" />
        </div>
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 -z-10 hidden bg-[radial-gradient(ellipse_90%_55%_at_50%_-8%,rgba(105,65,198,0.14)_0%,transparent_58%)] dark:block"
        />
        <main className="relative z-0 flex min-h-0 w-full flex-1 flex-col bg-gradient-to-b from-transparent to-white text-text-primary antialiased dark:to-[var(--app-surface-page)]">
          <Outlet />
        </main>
        {!hideFooter && (
          <footer className="relative border-t border-border bg-transparent py-4 text-center text-sm text-text-secondary">
            &copy; Saturam Inc 2026. All rights reserved.
          </footer>
        )}
      </div>
    </div>
  );
}
