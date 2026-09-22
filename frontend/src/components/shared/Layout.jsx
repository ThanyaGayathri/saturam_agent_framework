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
        {/* No `z-0` here: it would make <main> its own stacking context, trapping every fixed
            overlay a page renders (side panels, backdrops) beneath the footer that follows it
            in DOM order -- the footer's border and padding then sit over an overlay's bottom
            edge and swallow clicks there. The wrapper's `isolate` is the stacking boundary. */}
        {/* The page keeps its own tint all the way down. Fading to white meant that below the
            fold a white card sat on a white page, leaving a 1px #e9e9eb border as the only
            thing separating them -- every screen read flat there. Both stops are tokens, so
            dark mode follows without an override. */}
        <main className="relative flex min-h-0 w-full flex-1 flex-col bg-gradient-to-b from-surface-page-top to-surface-page text-text-primary antialiased">
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
