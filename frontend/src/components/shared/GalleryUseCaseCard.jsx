import {
  Activity,
  Briefcase,
  Gauge,
  Globe,
  PenLine,
  Sparkles,
  Trash2,
  Zap,
} from "lucide-react";
import { EnterpriseProductMark } from "./EnterpriseProductMark";

export function cardVariant(actionType) {
  const t = String(actionType || "").toLowerCase();
  if (t === "analyze" || t === "diagnose") return "operational";
  return "customer";
}

const MAX_TRY_ASKING = 2;

// Card type 1: surface base with subtle purple gradient overlay (operational)
const CARD_TYPE_1 =
  "border border-border bg-surface-default bg-[linear-gradient(45deg,transparent_-0.12%,rgba(189,161,255,0.10)_56.37%,rgba(105,65,198,0.10)_91.36%)]";

// Card type 2: solid light indigo (customer-facing)
const CARD_TYPE_2 = "border border-primary/25 bg-primary-light dark:bg-primary/[0.15] dark:border-primary/35";

/** Your Use Cases: gradient over semantic surface */
const GALLERY_YOURS_SURFACE =
  "border border-border bg-surface-default bg-[linear-gradient(45deg,transparent_-0.12%,rgba(189,161,255,0.10)_56.37%,rgba(105,65,198,0.10)_91.36%)]";
const GALLERY_YOURS_HOVER = "hover:shadow-[0_10px_24px_-10px_rgba(105,65,198,0.22)]";

/** Pre-built catalog */
const GALLERY_PREBUILT_SURFACE =
  "border border-primary/30 bg-primary-light dark:border-primary/40 dark:bg-surface-secondary";
const GALLERY_PREBUILT_HOVER = "hover:shadow-[0_10px_24px_-10px_rgba(99,102,241,0.18)]";

function actionTypeIconComponent(actionType) {
  const t = String(actionType || "").toLowerCase();
  if (t === "analyze" || t === "diagnose") return Activity;
  if (t === "automate" || t === "execute") return Zap;
  return Sparkles;
}

function formatActionLabel(actionType) {
  const t = String(actionType || "").trim();
  if (!t) return "";
  return t.charAt(0).toUpperCase() + t.slice(1);
}

const TAG_PILL_BASE =
  "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold leading-4 shadow-sm";
const TAG_ACTION =
  `${TAG_PILL_BASE} border-primary/25 bg-primary-light text-primary [&>svg]:shrink-0 [&>svg]:text-primary dark:border-primary/35 dark:bg-surface-tertiary dark:text-primary dark:ring-1 dark:ring-primary/25 dark:[&>svg]:text-primary`;
const TAG_PROCESS =
  `${TAG_PILL_BASE} border-border bg-surface-secondary text-text-secondary [&>svg]:shrink-0 [&>svg]:text-text-muted`;

/** Small chip surfacing a single KPI this use case moves. */
const KPI_CHIP =
  "inline-flex max-w-full items-center gap-1 rounded-md border border-[#a9f0d1] bg-[#ecfdf3] px-1.5 py-0.5 text-[11px] font-medium text-[#067647] dark:border-[#067647]/35 dark:bg-[#067647]/10 dark:text-[#6ce9a6]";

export function GalleryUseCaseCard({
  variant,
  galleryCategory,
  title,
  desc,
  actionType,
  process,
  isDraft,
  kpiNames,
  onOpen,
  onMenuClick,
  explorePrompts,
  onExplorePrompt,
}) {
  const tryList = Array.isArray(explorePrompts) ? explorePrompts : [];
  const tryPrompts = tryList.slice(0, MAX_TRY_ASKING);
  const moreTryPrompts = Math.max(0, tryList.length - tryPrompts.length);

  const isOperational = variant === "operational";
  const inGallery = galleryCategory === "yours" || galleryCategory === "prebuilt";
  const cardStyle = inGallery
    ? galleryCategory === "yours"
      ? GALLERY_YOURS_SURFACE
      : GALLERY_PREBUILT_SURFACE
    : isOperational
      ? CARD_TYPE_1
      : CARD_TYPE_2;
  const hoverShadow = inGallery
    ? galleryCategory === "yours"
      ? GALLERY_YOURS_HOVER
      : GALLERY_PREBUILT_HOVER
    : isOperational
      ? "hover:shadow-[0_10px_24px_-10px_rgba(105,65,198,0.25)]"
      : "hover:shadow-[0_10px_24px_-10px_rgba(79,70,229,0.25)]";

  const ActionIcon = actionTypeIconComponent(actionType);

  const footerRule = "border-border/55";

  const actionLabel = formatActionLabel(actionType);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className={`group relative flex h-full min-h-0 w-full cursor-pointer flex-col rounded-xl ${cardStyle} p-5 text-left outline-none transition-all duration-200 hover:-translate-y-0.5 ${hoverShadow} focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 dark:focus-visible:ring-offset-[var(--app-surface-page)]`}
    >
      {/* Delete affordance — shown on hover; parent decides whether the action fires */}
      <div
        className="absolute right-3 top-3 z-10"
        onClick={(e) => e.stopPropagation()}
        role="presentation"
      >
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onMenuClick?.();
          }}
          className="rounded-md p-1.5 text-text-muted opacity-0 transition group-hover:opacity-100 hover:bg-status-danger-light hover:text-status-danger"
          title="Delete use case"
          aria-label="Delete use case"
        >
          <Trash2 size={17} strokeWidth={2} />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col pr-7">
        <h3 className="text-lg font-semibold leading-7 tracking-[-0.2px] text-text-primary">
          {title}
        </h3>
        <p className="mt-2.5 line-clamp-3 text-sm leading-6 text-text-secondary">
          {desc}
        </p>

        {(actionLabel || process) && (
          <div className="mt-3.5 flex flex-wrap gap-2">
            {actionLabel && (
              <span className={`${TAG_ACTION} uppercase tracking-wide`}>
                <ActionIcon className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
                {actionLabel}
              </span>
            )}
            {process && (
              <span className={TAG_PROCESS}>
                <Briefcase className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
                {process}
              </span>
            )}
          </div>
        )}

        {Array.isArray(kpiNames) && kpiNames.length > 0 && (
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
            <span
              className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.05em] text-text-muted"
              title="KPIs this use case moves"
            >
              <Gauge className="h-3 w-3" strokeWidth={2} aria-hidden />
              Moves
            </span>
            {kpiNames.map((k) => (
              <span key={k} className={KPI_CHIP} title={k}>
                <span className="truncate">{k}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* "Try asking" prompts */}
      {tryPrompts.length > 0 && typeof onExplorePrompt === "function" && (
        <div
          className={`mt-4 border-t ${footerRule} pt-3`}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
          role="presentation"
        >
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.4px] text-primary">
            Try asking
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            {tryPrompts.map((p, i) => (
              <button
                key={`${p.lens}-${i}`}
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onExplorePrompt(p);
                }}
                className="rounded-md border border-border bg-surface-secondary px-2.5 py-1 text-sm font-medium text-text-primary transition hover:border-primary/40 hover:text-primary"
              >
                {p.lens}
              </button>
            ))}
            {moreTryPrompts > 0 && (
              <span className="text-sm font-medium text-text-muted">+{moreTryPrompts}</span>
            )}
          </div>
        </div>
      )}

      <div className={`mt-4 flex items-center justify-between border-t ${footerRule} pt-3.5`}>
        {inGallery ? (
          isDraft ? (
            <span className="inline-flex items-center gap-1.5 rounded-lg border border-sky-200 bg-sky-50 px-2.5 py-1.5 text-xs font-semibold uppercase tracking-wide text-sky-900 dark:border-sky-500/35 dark:bg-sky-500/10 dark:text-sky-100">
              <PenLine className="h-3.5 w-3.5 shrink-0 text-sky-700 dark:text-sky-300" strokeWidth={2} aria-hidden />
              Draft
            </span>
          ) : isOperational ? (
            <div
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-primary/35 bg-primary/10 text-primary shadow-sm dark:border-primary/45 dark:bg-primary/20"
              aria-hidden
            >
              <Globe className="h-5 w-5" strokeWidth={1.75} />
            </div>
          ) : (
            <div
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-sky-200/90 bg-sky-50/95 text-sky-800 shadow-sm dark:border-sky-500/35 dark:bg-sky-500/10 dark:text-sky-200"
              aria-hidden
            >
              <Sparkles className="h-5 w-5" strokeWidth={1.75} />
            </div>
          )
        ) : isDraft ? (
          <span className="inline-flex items-center gap-1.5 rounded-md border border-amber-200/90 bg-amber-50/90 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100">
            <PenLine className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
            Draft
          </span>
        ) : (
          <EnterpriseProductMark />
        )}
        <span
          className={`text-xs font-semibold opacity-0 transition group-hover:opacity-100 ${
            inGallery && !isDraft && !isOperational ? "text-sky-800 dark:text-sky-300" : "text-primary"
          }`}
        >
          Open
        </span>
      </div>
    </div>
  );
}
