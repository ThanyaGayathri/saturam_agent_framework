import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import { User, Settings, LogOut, BookOpen, ShieldCheck } from "lucide-react";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { usePrototypeAuth } from "../../context/PrototypeAuthContext";
import { demoPersona } from "../../domain/demo-profiles";
import NotificationsBell from "./NotificationsBell";

const AVATAR_COLOR_BY_ROLE = {
  executive:
    "border border-primary/28 bg-primary-light text-primary shadow-sm dark:border-primary/50 dark:bg-primary/22 dark:text-primary dark:shadow-[0_0_0_1px_rgba(255,255,255,0.06)]",
  operations:
    "border border-blue-200/90 bg-blue-100 text-blue-800 shadow-sm dark:border-blue-400/40 dark:bg-blue-500/22 dark:text-blue-50 dark:shadow-[0_0_0_1px_rgba(96,165,250,0.12)]",
  analyst:
    "border border-primary/32 bg-surface-secondary text-primary shadow-sm dark:border-primary/45 dark:bg-primary/20 dark:text-primary dark:shadow-[0_0_0_1px_rgba(167,139,250,0.14)]",
};

/**
 * Notifications bell + profile avatar and dropdown + demo settings modal.
 * Used in NavBar and full-height routes that hide the main nav (e.g. Playground chat).
 */
export default function HeaderNotificationsAndProfile() {
  const navigate = useNavigate();
  const dialogTitleId = useId();
  const { profileId, profiles, setProfileId, role, roles, setRole, isMiddlebyProfile, isMediaProfile } = useDemoProfile();
  const { authEnabled, logout } = usePrototypeAuth();
  const [demoSettingsOpen, setDemoSettingsOpen] = useState(false);
  const [avatarMenuOpen, setAvatarMenuOpen] = useState(false);
  const avatarMenuRef = useRef(null);
  const persona = demoPersona(role);
  const avatarColorCls = AVATAR_COLOR_BY_ROLE[role] ?? AVATAR_COLOR_BY_ROLE.executive;

  useEffect(() => {
    if (!avatarMenuOpen) return;
    function handleClick(e) {
      if (avatarMenuRef.current && !avatarMenuRef.current.contains(e.target)) {
        setAvatarMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [avatarMenuOpen]);

  useEffect(() => {
    if (!demoSettingsOpen) return;
    const onKey = (e) => {
      if (e.key === "Escape") setDemoSettingsOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [demoSettingsOpen]);

  const modal =
    typeof document !== "undefined" && demoSettingsOpen
      ? createPortal(
          <div
            className="fixed inset-0 z-[100] flex items-start justify-center pt-[88px] sm:pt-24"
            role="presentation"
          >
            <button
              type="button"
              className="absolute inset-0 bg-black/40"
              aria-label="Close demo settings"
              onClick={() => setDemoSettingsOpen(false)}
            />
            <div
              role="dialog"
              aria-modal="true"
              aria-labelledby={dialogTitleId}
              className="relative z-[101] mx-4 w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-xl"
            >
              <h2 id={dialogTitleId} className="text-lg font-semibold text-text-primary">
                Demo settings
              </h2>
              <p className="mt-1 text-sm text-text-secondary">
                {isMiddlebyProfile
                  ? "Switch customer profile to open a different product demo. Middleby analytics uses one shared catalog for all viewers."
                  : "Switch customer profile or role to change which use cases, KPIs, and data are surfaced."}
              </p>

              <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-text-muted">Customer profile</p>
              <fieldset className="mt-2 space-y-2">
                <legend className="sr-only">Customer profile</legend>
                {profiles.map((p) => (
                  <label
                    key={p.id}
                    className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 text-left text-sm transition-colors ${
                      profileId === p.id ? "border-primary bg-primary-light dark:bg-primary/20" : "border-border hover:border-border-light"
                    }`}
                  >
                    <input
                      type="radio"
                      name="demo-profile"
                      className="h-4 w-4 border-border-light text-primary focus:ring-primary"
                      checked={profileId === p.id}
                      onChange={() => setProfileId(p.id)}
                    />
                    <span className="font-medium text-text-primary">{p.label}</span>
                  </label>
                ))}
              </fieldset>

              {!isMiddlebyProfile && !isMediaProfile ? (
                <>
                  <p className="mt-5 text-xs font-semibold uppercase tracking-wide text-text-muted">Viewer role</p>
                  <p className="mt-0.5 text-xs text-text-secondary">
                    Changes which use cases are highlighted and sorted first.
                  </p>
                  <fieldset className="mt-2 space-y-2">
                    <legend className="sr-only">Viewer role</legend>
                    {roles.map((r) => (
                      <label
                        key={r.id}
                        className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 text-left text-sm transition-colors ${
                          role === r.id
                            ? "border-primary bg-primary-light dark:bg-primary/20"
                            : "border-border hover:border-border-light"
                        }`}
                      >
                        <input
                          type="radio"
                          name="demo-role"
                          className="h-4 w-4 border-border-light text-primary focus:ring-primary"
                          checked={role === r.id}
                          onChange={() => setRole(r.id)}
                        />
                        <div className="min-w-0">
                          <span className="block font-medium text-text-primary">{r.label}</span>
                          <span className="block text-xs text-text-secondary">{r.description}</span>
                        </div>
                      </label>
                    ))}
                  </fieldset>
                </>
              ) : null}
              <button
                type="button"
                className="mt-5 w-full rounded-lg bg-brand-dark px-4 py-2.5 text-sm font-semibold text-white hover:opacity-90 dark:bg-primary dark:hover:opacity-95"
                onClick={() => setDemoSettingsOpen(false)}
              >
                Done
              </button>
            </div>
          </div>,
          document.body
        )
      : null;

  return (
    <>
      <div className="flex shrink-0 items-center gap-2 sm:gap-3">
        {!isMiddlebyProfile && <NotificationsBell />}

        <div className="relative" ref={avatarMenuRef}>
          <button
            type="button"
            onClick={() => setAvatarMenuOpen((v) => !v)}
            className={`flex h-9 w-9 items-center justify-center rounded-full text-sm font-bold transition ring-2 ring-transparent hover:ring-primary/35 focus-visible:outline-none focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--app-surface-default)] ${avatarColorCls}`}
            aria-label="Profile menu"
            aria-haspopup="true"
            aria-expanded={avatarMenuOpen}
          >
            {persona.initials}
          </button>

          {avatarMenuOpen ? (
            <div className="absolute right-0 top-full z-50 mt-2 w-64 rounded-xl border border-border bg-surface shadow-xl">
              <div className="flex items-center gap-3 border-b border-border px-4 py-3">
                <div
                  className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-bold ${avatarColorCls}`}
                >
                  {persona.initials}
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-text-primary">{persona.name}</p>
                  <p className="truncate text-xs text-text-secondary">{persona.email}</p>
                  <p className="truncate text-xs text-text-muted">{persona.title}</p>
                </div>
              </div>

              <div className="py-1">
                <Link
                  to="/profile"
                  onClick={() => setAvatarMenuOpen(false)}
                  className="flex items-center gap-2.5 px-4 py-2.5 text-sm text-text-primary hover:bg-surface-secondary"
                >
                  <User size={16} className="text-text-muted" />
                  View Profile
                </Link>
                <Link
                  to="/how-it-works"
                  onClick={() => setAvatarMenuOpen(false)}
                  className="flex items-center gap-2.5 px-4 py-2.5 text-sm text-text-primary hover:bg-surface-secondary"
                >
                  <BookOpen size={16} className="text-text-muted" />
                  {"How Enterprise GPT works"}
                </Link>
                <button
                  type="button"
                  onClick={() => {
                    setAvatarMenuOpen(false);
                    setDemoSettingsOpen(true);
                  }}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-text-primary hover:bg-surface-secondary"
                >
                  <Settings size={16} className="text-text-muted" />
                  Demo Settings
                </button>
                <Link
                  to="/admin/seed-data"
                  onClick={() => setAvatarMenuOpen(false)}
                  className="flex items-center gap-2.5 px-4 py-2.5 text-sm text-text-primary hover:bg-surface-secondary"
                >
                  <ShieldCheck size={16} className="text-text-muted" />
                  Manage seed data
                </Link>
                {authEnabled ? (
                  <button
                    type="button"
                    onClick={() => {
                      logout();
                      navigate("/login", { replace: true });
                    }}
                    className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-text-secondary hover:bg-surface-secondary"
                  >
                    <LogOut size={16} className="text-text-muted" />
                    Sign out
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      </div>
      {modal}
    </>
  );
}
