// User profile page - /profile
import { useState } from "react";
import { Link } from "react-router-dom";
import {
 Mail, Briefcase, Building2, CalendarDays, Globe, Clock, Camera,
 Bell, BellOff, Monitor, Moon, Sun, MessageSquare, Shield,
 BookOpen, ChevronRight, Check, Palette,
} from "lucide-react";
import { useDemoProfile } from "../context/DemoProfileContext";
import { demoPersona, demoWorkspace } from "../domain/demo-profiles";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { THEMES } from "../domain/themes";
import { useTheme } from "../hooks/useTheme";
import { Switch } from "./ui";

// ── Colour maps ─────────────────────────────────────────────────────────────

const AVATAR_BG = {
 executive: "bg-primary-light dark:bg-primary/20 text-primary",
 operations: "bg-[#dbeafe] text-[#2563eb]",
 analyst: "bg-surface-secondary text-text-tertiary",
};

const ROLE_BADGE = {
 executive: "bg-primary-light text-primary",
 operations: "bg-[#eff6ff] text-[#2563eb]",
 analyst: "bg-surface-secondary text-text-tertiary",
};

// ── Seed data for "Your activity" ───────────────────────────────────────────

const RECENT_ACTIVITY = {
 executive: {
 chats: [
 { label: "Stockout analysis, North East region", date: "2 hours ago" },
 { label: "Q1 margin forecast vs plan", date: "Yesterday" },
 { label: "Weekly replenishment summary", date: "3 days ago" },
 ],
 useCases: [
 { label: "Revenue Leakage Detection", status: "Live" },
 { label: "Demand Forecasting", status: "Live" },
 ],
 watchedKpis: ["Gross Margin %", "Stockout Rate", "Revenue per Sq Ft"],
 },
 operations: {
 chats: [
 { label: "Staffing coverage gaps, weekend shifts", date: "1 hour ago" },
 { label: "DC service level trends", date: "Yesterday" },
 { label: "Order exception triage", date: "2 days ago" },
 ],
 useCases: [
 { label: "Smart Restock Optimization", status: "Live" },
 { label: "Store Ops & Labor Planning", status: "Draft" },
 ],
 watchedKpis: ["Fill Rate %", "On-Time Delivery", "Labor Cost / Revenue"],
 },
 analyst: {
 chats: [
 { label: "Cart abandonment funnel breakdown", date: "30 min ago" },
 { label: "POS transaction failure diagnostics", date: "Yesterday" },
 { label: "Pricing elasticity model review", date: "4 days ago" },
 ],
 useCases: [
 { label: "E-Commerce Conversion Insights", status: "Live" },
 { label: "POS Stability Monitor", status: "Live" },
 { label: "Markdown Optimization Model", status: "Draft" },
 ],
 watchedKpis: ["Conversion Rate", "Cart Abandonment %", "Sell-Through Rate", "Avg Transaction Value"],
 },
};

// ── Connected accounts seed ─────────────────────────────────────────────────

const CONNECTED_ACCOUNTS = [
 { name: "Slack", status: "connected", detail: "#alerts-retail" },
 { name: "Microsoft Teams", status: "available", detail: null },
 { name: "Email", status: "connected", detail: null },
];

// ── Toggle helper ───────────────────────────────────────────────────────────

function Toggle({ enabled, onChange, label }) {
 return (
 <Switch
 checked={enabled}
 onCheckedChange={onChange}
 size="comfortable"
 aria-label={label}
 />
 );
}

// ── Section wrapper ─────────────────────────────────────────────────────────

function Section({ title, children }) {
 return (
 <div className="mt-6 rounded-2xl border border-border bg-surface-default shadow-sm">
 <div className="border-b border-border px-6 py-4">
 <h2 className="text-base font-semibold text-text-primary">{title}</h2>
 </div>
 <div className="px-6 py-5">{children}</div>
 </div>
 );
}

function InfoRow({ label, value }) {
 return (
 <div className="flex items-start justify-between gap-4 py-2.5">
 <span className="text-sm text-text-secondary">{label}</span>
 <span className="text-right text-sm font-medium text-text-primary">{value}</span>
 </div>
 );
}

// ── Appearance preview thumbnails ──────────────────────────────────────────
// Tiny decorative "screenshot" used inside each Light/Dark/System card.
function AppearancePreview({ id }) {
 if (id === "light") {
 return (
 <div className="absolute inset-0 bg-surface">
 <div className="absolute inset-x-0 top-0 h-3 border-b border-border bg-surface" />
 <div className="absolute inset-y-0 left-0 w-[30%] border-r border-border bg-[#f5f7ff]" />
 <div className="absolute left-[34%] top-5 h-1.5 w-10 rounded bg-[#d5d7da]" />
 <div className="absolute left-[34%] top-8 h-1.5 w-6 rounded bg-[#d5d7da]/70" />
 <div className="absolute right-2 top-1 h-1.5 w-1.5 rounded-full bg-[#181d27]" />
 </div>
 );
 }
 if (id === "dark") {
 return (
 <div className="absolute inset-0 bg-[#0f1117]">
 <div className="absolute inset-x-0 top-0 h-3 border-b border-[#2a3042] bg-[#161922]" />
 <div className="absolute inset-y-0 left-0 w-[30%] border-r border-[#2a3042] bg-[#161922]" />
 <div className="absolute left-[34%] top-5 h-1.5 w-10 rounded bg-[#383f54]" />
 <div className="absolute left-[34%] top-8 h-1.5 w-6 rounded bg-[#383f54]/70" />
 <div className="absolute right-2 top-1 h-1.5 w-1.5 rounded-full bg-[#eef0fd]" />
 </div>
 );
 }
 // system - split light/dark
 return (
 <div className="absolute inset-0">
 <div className="absolute inset-y-0 left-0 right-1/2 bg-surface">
 <div className="absolute inset-x-0 top-0 h-3 border-b border-border" />
 <div className="absolute inset-y-0 left-0 w-[45%] border-r border-border bg-[#f5f7ff]" />
 <div className="absolute right-1 top-5 h-1.5 w-6 rounded bg-[#d5d7da]" />
 </div>
 <div className="absolute inset-y-0 left-1/2 right-0 bg-[#0f1117]">
 <div className="absolute inset-x-0 top-0 h-3 border-b border-[#2a3042] bg-[#161922]" />
 <div className="absolute inset-y-0 left-0 w-[45%] border-r border-[#2a3042] bg-[#161922]" />
 <div className="absolute right-1 top-5 h-1.5 w-6 rounded bg-[#383f54]" />
 </div>
 </div>
 );
}

// ── Component ───────────────────────────────────────────────────────────────

export default function ProfilePage() {
 const { profileId, role, roleLabel } = useDemoProfile();
 const { areas: BUSINESS_AREAS } = useBusinessAreas();
 const persona = demoPersona(role);
 const workspace = demoWorkspace(profileId);
 const avatarCls = AVATAR_BG[role] ?? AVATAR_BG.executive;
 const badgeCls = ROLE_BADGE[role] ?? ROLE_BADGE.executive;
 const activity = RECENT_ACTIVITY[role] ?? RECENT_ACTIVITY.executive;

 // Assigned business areas from persona → resolve labels
 const assignedAreaLabels = (persona.assignedAreas ?? [])
 .map((id) => BUSINESS_AREAS.find((b) => b.id === id)?.label)
 .filter(Boolean);

 // Preferences state (local only - just for interactive demo feel)
 const [prefs, setPrefs] = useState({
 language: "en-US",
 dateFormat: "MM/DD/YYYY",
 landingPage: "/playground",
 notifyEmail: true,
 notifyInApp: true,
 notifySeverity: "high",
 });

 const updatePref = (key, value) => setPrefs((p) => ({ ...p, [key]: value }));

 // Color theme + appearance (light/dark) - persisted to localStorage and
 // applied to <html> by the useTheme hook.
 const {
 themeId: activeThemeId, selectTheme,
 appearance, selectAppearance,
 } = useTheme();

 return (
 <div className="mx-auto max-w-[760px] px-6 py-10">

 {/* ── 1. Header card ──────────────────────────────────────────── */}
 <div className="rounded-2xl border border-border bg-surface-default p-6 shadow-sm">
 <div className="flex items-start gap-5">
 <div className="relative">
 <div className={`flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded-2xl text-2xl font-bold ${avatarCls}`}>
 {persona.initials}
 </div>
 <button
 type="button"
 className="absolute -bottom-1 -right-1 flex h-7 w-7 items-center justify-center rounded-full border-2 border-white bg-[#181d27] text-white shadow-sm hover:bg-[#353642]"
 title="Change photo"
 >
 <Camera size={13} />
 </button>
 </div>
 <div className="min-w-0 flex-1">
 <div className="flex flex-wrap items-center gap-2">
 <h1 className="text-xl font-semibold text-text-primary">{persona.name}</h1>
 <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${badgeCls}`}>{roleLabel}</span>
 </div>
 <p className="mt-0.5 text-sm text-text-secondary">{persona.title}</p>
 <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-sm text-text-secondary">
 <span className="flex items-center gap-1.5"><Mail size={14} className="text-text-muted" />{persona.email}</span>
 <span className="flex items-center gap-1.5"><Building2 size={14} className="text-text-muted" />{workspace.companyName}</span>
 <span className="flex items-center gap-1.5"><Clock size={14} className="text-text-muted" />{persona.timezoneLabel}</span>
 <span className="flex items-center gap-1.5"><Globe size={14} className="text-text-muted" />{persona.locale}</span>
 <span className="flex items-center gap-1.5"><CalendarDays size={14} className="text-text-muted" />Member since {persona.memberSince}</span>
 </div>
 </div>
 </div>
 </div>

 {/* ── 2. Preferences ──────────────────────────────────────────── */}
 <Section title="Preferences">
 <div className="divide-y divide-border">
 <div className="flex items-center justify-between gap-4 py-3">
 <div>
 <p className="text-sm font-medium text-text-primary">Language</p>
 <p className="text-xs text-text-secondary">Display language for the interface</p>
 </div>
 <select
 value={prefs.language}
 onChange={(e) => updatePref("language", e.target.value)}
 className="rounded-lg border border-border bg-surface-default px-3 py-1.5 text-sm text-text-primary"
 >
 <option value="en-US">English (US)</option>
 <option value="en-GB">English (UK)</option>
 <option value="es">Spanish</option>
 <option value="fr">French</option>
 </select>
 </div>

 <div className="flex items-center justify-between gap-4 py-3">
 <div>
 <p className="text-sm font-medium text-text-primary">Timezone</p>
 <p className="text-xs text-text-secondary">Used for scheduling and alert timestamps</p>
 </div>
 <span className="text-sm text-text-primary">{persona.timezoneLabel}</span>
 </div>

 <div className="flex items-center justify-between gap-4 py-3">
 <div>
 <p className="text-sm font-medium text-text-primary">Date format</p>
 </div>
 <select
 value={prefs.dateFormat}
 onChange={(e) => updatePref("dateFormat", e.target.value)}
 className="rounded-lg border border-border bg-surface-default px-3 py-1.5 text-sm text-text-primary"
 >
 <option value="MM/DD/YYYY">MM/DD/YYYY</option>
 <option value="DD/MM/YYYY">DD/MM/YYYY</option>
 <option value="YYYY-MM-DD">YYYY-MM-DD</option>
 </select>
 </div>

 <div className="flex items-center justify-between gap-4 py-3">
 <div>
 <p className="text-sm font-medium text-text-primary">Default landing page</p>
 <p className="text-xs text-text-secondary">Where you land after signing in</p>
 </div>
 <select
 value={prefs.landingPage}
 onChange={(e) => updatePref("landingPage", e.target.value)}
 className="rounded-lg border border-border bg-surface-default px-3 py-1.5 text-sm text-text-primary"
 >
 <option value="/playground">Playground</option>
 <option value="/business-areas">Business Areas</option>
 <option value="/use-cases">Use Cases</option>
 <option value="/kpis">KPI Registry</option>
 </select>
 </div>

 <div className="flex items-center justify-between gap-4 py-3">
 <div>
 <p className="text-sm font-medium text-text-primary">Email notifications</p>
 <p className="text-xs text-text-secondary">Receive alert digests via email</p>
 </div>
 <Toggle enabled={prefs.notifyEmail} onChange={(v) => updatePref("notifyEmail", v)} label="Email notifications" />
 </div>

 <div className="flex items-center justify-between gap-4 py-3">
 <div>
 <p className="text-sm font-medium text-text-primary">In-app notifications</p>
 <p className="text-xs text-text-secondary">Show alerts in the notification bell</p>
 </div>
 <Toggle enabled={prefs.notifyInApp} onChange={(v) => updatePref("notifyInApp", v)} label="In-app notifications" />
 </div>

 <div className="flex items-center justify-between gap-4 py-3">
 <div>
 <p className="text-sm font-medium text-text-primary">Alert severity threshold</p>
 <p className="text-xs text-text-secondary">Only notify for alerts at or above this level</p>
 </div>
 <select
 value={prefs.notifySeverity}
 onChange={(e) => updatePref("notifySeverity", e.target.value)}
 className="rounded-lg border border-border bg-surface-default px-3 py-1.5 text-sm text-text-primary"
 >
 <option value="critical">Critical only</option>
 <option value="high">High and above</option>
 <option value="medium">Medium and above</option>
 <option value="low">All alerts</option>
 </select>
 </div>

 </div>
 </Section>

 {/* ── 2b. Theme ──────────────────────────────────────────────── */}
 <Section title="Theme">

 {/* Appearance - Light / Dark / System cards */}
 <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Appearance</p>
 <p className="mb-4 mt-1 text-sm text-text-secondary">
 Choose light, dark, or match your device setting.
 </p>
 <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
 {[
 { id: "light", label: "Light", icon: Sun },
 { id: "dark", label: "Dark", icon: Moon },
 { id: "system", label: "System", icon: Monitor },
 ].map(({ id, label, icon: Icon }) => {
 const selected = appearance === id;
 return (
 <button
 key={id}
 type="button"
 onClick={() => selectAppearance(id)}
 aria-pressed={selected}
 className={`group relative flex flex-col gap-2.5 rounded-xl border p-3 text-left transition ${
 selected
 ? "border-primary bg-primary-light/50 ring-2 ring-primary/25 dark:bg-primary-light/15"
 : "border-border bg-surface-default hover:border-border-light hover:bg-surface-secondary"
 }`}
 >
 <div
 className="relative h-20 w-full overflow-hidden rounded-md ring-1 ring-inset ring-border/70"
 aria-hidden="true"
 >
 <AppearancePreview id={id} />
 </div>
 <div className="flex items-center justify-between gap-2">
 <span className="flex items-center gap-1.5 text-sm font-medium text-text-primary">
 <Icon size={13} className="text-text-muted" />
 {label}
 </span>
 <span
 className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 transition ${
 selected
 ? "border-primary bg-primary text-white"
 : "border-border-light bg-surface-default text-transparent"
 }`}
 aria-hidden="true"
 >
 <Check size={11} strokeWidth={3.5} />
 </span>
 </div>
 </button>
 );
 })}
 </div>

 {/* Color theme - Classic / Soft cards */}
 <div className="mt-7 border-t border-border pt-6">
 <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Color theme</p>
 <p className="mb-4 mt-1 text-sm text-text-secondary">
 Brand and status palette used across charts, status indicators, and brand accents.
 </p>
 </div>
 <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
 {THEMES.map((theme) => {
 const selected = theme.id === activeThemeId;
 return (
 <button
 key={theme.id}
 type="button"
 onClick={() => selectTheme(theme.id)}
 aria-pressed={selected}
 className={`group relative flex flex-col gap-3 rounded-xl border p-4 text-left transition ${
 selected
 ? "border-primary bg-primary-light/50 ring-2 ring-primary/25 dark:bg-primary-light/15"
 : "border-border bg-surface-default hover:border-border-light hover:bg-surface-secondary"
 }`}
 >
 {theme.tokens.accent ? (
 <div className="flex h-12 w-full overflow-hidden rounded-lg" aria-hidden="true">
 <div className="min-w-[58%] flex-1" style={{ backgroundColor: theme.tokens.brand }} />
 <div className="min-w-0 flex-1" style={{ backgroundColor: theme.tokens.accent }} />
 </div>
 ) : (
 <div
 className="h-12 w-full rounded-lg"
 style={{ backgroundColor: theme.tokens.brand }}
 aria-hidden="true"
 />
 )}
 <div className="flex items-center gap-1.5" aria-hidden="true">
 {[
 ["Success", theme.tokens.success],
 ["Warning", theme.tokens.warning],
 ["Danger", theme.tokens.danger],
 ["Info", theme.tokens.info],
 ].map(([label, color]) => (
 <span
 key={label}
 title={`${label}: ${color}`}
 className="h-5 w-5 rounded-full ring-1 ring-inset ring-black/5"
 style={{ backgroundColor: color }}
 />
 ))}
 </div>
 <div className="flex items-start justify-between gap-3">
 <div className="min-w-0">
 <div className="flex flex-wrap items-center gap-2">
 <p className="text-sm font-semibold text-text-primary">{theme.name}</p>
 {selected && (
 <span className="rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
 Active
 </span>
 )}
 </div>
 <p className="mt-0.5 text-xs text-text-secondary">{theme.description}</p>
 </div>
 <span
 className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 transition ${
 selected
 ? "border-primary bg-primary text-white"
 : "border-border-light bg-surface-default text-transparent"
 }`}
 aria-hidden="true"
 >
 <Check size={13} strokeWidth={3} />
 </span>
 </div>
 </button>
 );
 })}
 </div>
 <p className="mt-3 flex items-center gap-1.5 text-xs text-text-muted">
 <Palette size={12} />
 Your theme is saved to this browser and applied across all sessions.
 </p>
 </Section>

 {/* ── 3. Your workspace ───────────────────────────────────────── */}
 <Section title="Your workspace">
 <div className="divide-y divide-border">
 <InfoRow label="Company" value={workspace.companyName} />
 <InfoRow label="Industry" value={workspace.industry} />
 <InfoRow label="Region" value={workspace.region} />
 <InfoRow label="Plan" value={workspace.plan} />
 <div className="py-3">
 <p className="text-sm text-text-secondary">Assigned business areas</p>
 <div className="mt-2 flex flex-wrap gap-2">
 {assignedAreaLabels.map((label) => (
 <span key={label} className="rounded-full bg-primary-light px-3 py-1 text-xs font-medium text-primary">
 {label}
 </span>
 ))}
 </div>
 </div>
 </div>
 </Section>

 {/* ── 4. Your activity ────────────────────────────────────────── */}
 <Section title="Your activity">
 {/* Recent chats */}
 <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Recent chats</p>
 <ul className="mt-2 space-y-2">
 {activity.chats.map((c) => (
 <li key={c.label} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-surface px-3 py-2.5 dark:bg-surface-default">
 <div className="flex items-center gap-2 min-w-0">
 <MessageSquare size={14} className="shrink-0 text-text-muted" />
 <span className="truncate text-sm text-text-primary">{c.label}</span>
 </div>
 <span className="shrink-0 text-xs text-text-muted">{c.date}</span>
 </li>
 ))}
 </ul>

 {/* Use cases launched */}
 <p className="mt-5 text-xs font-semibold uppercase tracking-wide text-text-muted">Use cases launched</p>
 <ul className="mt-2 space-y-2">
 {activity.useCases.map((uc) => (
 <li key={uc.label} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-surface px-3 py-2.5 dark:bg-surface-default">
 <span className="truncate text-sm text-text-primary">{uc.label}</span>
 <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${uc.status === "Live" ? "bg-status-success-light text-status-success" : "bg-primary-light text-primary"}`}>
 {uc.status}
 </span>
 </li>
 ))}
 </ul>

 {/* Watched KPIs */}
 <p className="mt-5 text-xs font-semibold uppercase tracking-wide text-text-muted">Watched KPIs</p>
 <div className="mt-2 flex flex-wrap gap-2">
 {activity.watchedKpis.map((kpi) => (
 <span key={kpi} className="rounded-full border border-border bg-surface-secondary px-3 py-1 text-xs font-medium text-text-tertiary">
 {kpi}
 </span>
 ))}
 </div>
 </Section>

 {/* ── 5. Connected accounts ───────────────────────────────────── */}
 <Section title="Connected accounts">
 <p className="mb-3 text-sm text-text-secondary">Channels for receiving alerts and notifications.</p>
 <div className="space-y-2">
 {CONNECTED_ACCOUNTS.map((acct) => (
 <div key={acct.name} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-surface px-4 py-3 dark:bg-surface-default">
 <div className="flex items-center gap-3 min-w-0">
 {acct.status === "connected" ? (
 <Bell size={16} className="shrink-0 text-status-success" />
 ) : (
 <BellOff size={16} className="shrink-0 text-text-muted" />
 )}
 <div className="min-w-0">
 <p className="text-sm font-medium text-text-primary">{acct.name}</p>
 {acct.detail && <p className="text-xs text-text-secondary">{acct.detail}</p>}
 </div>
 </div>
 {acct.status === "connected" ? (
 <span className="rounded-full bg-status-success-light px-2.5 py-0.5 text-[11px] font-semibold text-status-success">Connected</span>
 ) : (
 <button type="button" className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-semibold text-text-primary hover:bg-surface-secondary dark:bg-surface-default">
 Connect
 </button>
 )}
 </div>
 ))}
 </div>
 </Section>

 {/* ── 6. Help & reference ─────────────────────────────────────── */}
 <Section title="Help & reference">
 <Link
 to="/hierarchy"
 className="group flex items-center justify-between gap-3 rounded-lg border border-border bg-surface px-4 py-3 transition hover:border-primary hover:bg-surface-secondary dark:bg-surface-default"
 >
 <div className="flex items-center gap-3 min-w-0">
 <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-light text-primary">
 <BookOpen size={16} />
 </div>
 <div className="min-w-0">
 <p className="text-sm font-medium text-text-primary">How EnterpriseGPT is organized</p>
 <p className="text-xs text-text-secondary">Business areas, processes, use cases, and KPIs, and how they connect.</p>
 </div>
 </div>
 <ChevronRight size={16} className="shrink-0 text-text-muted transition group-hover:text-primary" />
 </Link>
 </Section>

 </div>
 );
}
