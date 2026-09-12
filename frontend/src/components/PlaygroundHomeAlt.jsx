// Playground - Browse use-case cards (alt hub; still under /playground)
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  Paperclip,
  Database,
  Plus,
  ArrowRight,
  ArrowUpLeft,
  ChevronsRight,
  Store,
  ShoppingCart,
  Monitor,
  Package,
  Headphones,
  LayoutGrid,
  ExternalLink,
  Tag,
  TrendingUp,
  BarChart3,
  Users,
  Briefcase,
  Info,
} from "lucide-react";
import { useTheme } from "../context/ThemeContext";
import { useDictation } from "../hooks/useDictation";
import { MicButton, MicErrorMessage } from "./shared/MicButton";
import ChatComposerTextarea from "./shared/ChatComposerTextarea";
import { PaperPlaneSendIcon } from "./icons/PaperPlaneSendIcon";
import { useChatAttachments, stashAttachmentsForHandoff } from "../hooks/useChatAttachments";
import { PendingAttachmentChips } from "./shared/ChatAttachmentChips";
import { ExplorePromptSeverityIcon } from "./shared/ExplorePromptSeverityIcon";
import snowflakeSvg from "../assets/snowflake.svg";
import airflowSvg from "../assets/airflow.svg";
import awsGlueSvg from "../assets/aws-glue.svg";
import adfSvg from "../assets/azure-data-factory.svg";
import salesforceSvg from "../assets/salesforce.svg";
import sapSvg from "../assets/sap.svg";
import netsuiteSvg from "../assets/netsuite.svg";
import { PLAYGROUND_HUB_CHAT_MESSAGE_KEY, HUB_CHAT_ATTACH_HANDOFF_KEY } from "../storage/keys";
import { serializePlaygroundHubHandoff } from "../domain/playground-hub-handoff";
import {
  formatExplorePromptForComposer,
  getExplorePromptsForCatalogEntry,
  EXPLORE_PROMPT_HUB_HELP,
} from "../domain/demo-use-case-prompts";
import { PLAYGROUND_HUB_BUSINESS_AREAS_INITIAL_COUNT } from "../domain/playground-areas";
import { useDemoProfile } from "../context/DemoProfileContext";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { getUseCasesForRole, FEATURED_IMPACT, ROLE_STYLE } from "../domain/featured-use-cases";
import Switch from "./ui/Switch";
import TourLaunchButton from "../tours/TourLaunchButton";

// Icon per business-area id. Keeps pill visuals consistent with the Business
// Areas catalog, while the list itself is derived from BUSINESS_AREAS.
const CATEGORY_ICON_BY_AREA_ID = {
  inventory: Package,
  orders: ShoppingCart,
  merchandise: Tag,
  "store-ops": Store,
  "e-com": ShoppingCart,
  pos: Monitor,
  support: Headphones,
  pricing: TrendingUp,
  finance: BarChart3,
  workforce: Users,
};

// Categories are now computed inside the component from the live
// business-area store (useBusinessAreas). Any add / rename / delete on the
// Business hierarchy tab reflows the pills below.

/** Lowercase hex so `index.css` html.dark remaps match (Tailwind classes are case-sensitive). */
const PILL_STYLE =
  "border border-primary/18 bg-[#eff2ff] text-text-tertiary hover:border-[rgba(82,90,112,0.42)] hover:bg-[#e4e8ff] dark:border-white/[0.14] dark:bg-surface/[0.08] dark:text-text-secondary dark:hover:border-border-light dark:hover:bg-surface/[0.12] dark:hover:text-text-primary";

const CARD_ICON_GRADIENT =
  "linear-gradient(to left, rgba(49,188,255,0.15), rgba(150,118,255,0.15) 10%, rgba(190,100,254,0.15) 25%, rgba(239,87,148,0.15) 50%, rgba(253,104,63,0.15) 75%, rgba(255,161,11,0.15) 100%)";

/** Dark: deeper base with brand wash so the well reads on surface-default without neon pastels. */
const CARD_ICON_GRADIENT_DARK =
  "linear-gradient(145deg, rgba(139,92,246,0.26) 0%, rgba(99,102,241,0.17) 42%, rgba(22,25,34,0.94) 100%)";

const DATABASES = [
  { name: "Snowflake", img: snowflakeSvg },
  { name: "Airflow", img: airflowSvg },
  { name: "Amazon Glue", img: awsGlueSvg },
  { name: "Azure Data Factory", img: adfSvg },
];

const CONNECTOR_ITEMS = [
  { id: "salesforce", name: "Salesforce", img: salesforceSvg },
  { id: "sap", name: "SAP", img: sapSvg },
  { id: "netsuite", name: "Oracle NetSuite", img: netsuiteSvg },
];

function ConnectorToggle({ enabled, onChange, name }) {
  return (
    <Switch
      checked={enabled}
      onCheckedChange={onChange}
      size="comfortable"
      stopClickPropagation
      aria-label={enabled ? `Disable connection for ${name}` : `Enable connection for ${name}`}
    />
  );
}

export default function PlaygroundHomeAlt() {
  const { colorMode } = useTheme();
  const [query, setQuery] = useState("");
  const appendDictation = useCallback((text) => {
    setQuery((prev) => (prev ? `${prev.trimEnd()} ${text}` : text));
  }, []);
  const dictation = useDictation({ onFinalText: appendDictation });
  const attachments = useChatAttachments();
  const [showDbDropdown, setShowDbDropdown] = useState(false);
  const [showConnDropdown, setShowConnDropdown] = useState(false);
  const [connectorEnabled, setConnectorEnabled] = useState({
    salesforce: true,
    sap: false,
    netsuite: false,
  });
  const [businessAreasExpanded, setBusinessAreasExpanded] = useState(false);
  const [pendingPromptMeta, setPendingPromptMeta] = useState(null);

  const dbDropdownRef = useRef(null);
  const connDropdownRef = useRef(null);
  const composerRef = useRef(null);
  const navigate = useNavigate();
  const location = useLocation();
  const { profileId, role, isMediaProfile, isMiddlebyProfile } = useDemoProfile();
  const { areas: businessAreas } = useBusinessAreas();
  const CATEGORIES = useMemo(
    () =>
      businessAreas.map((a) => ({
        label: a.label,
        areaId: a.id,
        icon: CATEGORY_ICON_BY_AREA_ID[a.id] || Briefcase,
      })),
    [businessAreas]
  );
  const { entries: catalogEntries } = useUseCaseCatalog();
  const roleUseCases = useMemo(
    () => getUseCasesForRole(catalogEntries, profileId, role),
    [catalogEntries, profileId, role]
  );
  const roleStyle = ROLE_STYLE[role] ?? ROLE_STYLE.executive;
  const exploreGridRef = useRef(null);
  const roleUseCasesRef = useRef(roleUseCases);
  roleUseCasesRef.current = roleUseCases;

  useEffect(() => {
    function handleClick(e) {
      if (dbDropdownRef.current && !dbDropdownRef.current.contains(e.target)) {
        setShowDbDropdown(false);
      }
      if (connDropdownRef.current && !connDropdownRef.current.contains(e.target)) {
        setShowConnDropdown(false);
      }
    }

    if (showDbDropdown || showConnDropdown) {
      document.addEventListener("mousedown", handleClick);
      return () => document.removeEventListener("mousedown", handleClick);
    }
  }, [showDbDropdown, showConnDropdown]);

  const enabledConnectorCount = Object.values(connectorEnabled).filter(Boolean).length;
  const visibleCategories =
    businessAreasExpanded || CATEGORIES.length <= PLAYGROUND_HUB_BUSINESS_AREAS_INITIAL_COUNT
      ? CATEGORIES
      : CATEGORIES.slice(0, PLAYGROUND_HUB_BUSINESS_AREAS_INITIAL_COUNT);
  const hasMoreBusinessAreas = CATEGORIES.length > PLAYGROUND_HUB_BUSINESS_AREAS_INITIAL_COUNT;

  const submitFromHub = () => {
    if (dictation.listening) dictation.stop();
    const t = query.trim();
    const staged = attachments.staged;
    const hasStaged = staged.length > 0;
    if (!t && !hasStaged) return;
    const textForHandoff = t || "(attached files)";
    // Serialize the full context so /playground/chat can restore catalogId +
    // horizon and route to the scripted structured reply (e.g. a media
    // explore-hub reply that resolves to a MEDIA_TOPIC_PANELS panel).
    const payload = serializePlaygroundHubHandoff({
      text: textForHandoff,
      lens: pendingPromptMeta?.lens,
      horizon: pendingPromptMeta?.horizon,
      catalogId: pendingPromptMeta?.catalogId,
    });
    try {
      sessionStorage.setItem(PLAYGROUND_HUB_CHAT_MESSAGE_KEY, payload);
      if (hasStaged) {
        const handoffId = stashAttachmentsForHandoff(staged);
        if (handoffId) sessionStorage.setItem(HUB_CHAT_ATTACH_HANDOFF_KEY, handoffId);
      } else {
        sessionStorage.removeItem(HUB_CHAT_ATTACH_HANDOFF_KEY);
      }
    } catch {
      /* ignore */
    }
    attachments.clearStaged();
    navigate("/playground/chat");
  };

  // Pre-fill from navigation state (e.g. clicked a use case in Templates/Gallery)
  useEffect(() => {
    const prefill = location.state?.prefillQuery;
    if (prefill && typeof prefill === "string" && prefill.trim()) {
      setQuery(prefill.trim());
      window.history.replaceState(
        { ...window.history.state, usr: { ...window.history.state?.usr, prefillQuery: undefined } },
        ""
      );
      setTimeout(() => composerRef.current?.focus(), 0);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const commitPlaygroundExploreHandoff = useCallback((p, catalogId) => {
    const t = formatExplorePromptForComposer(p);
    if (!t) return;
    setQuery(t);
    setPendingPromptMeta({
      lens: p?.lens,
      horizon: p?.horizon,
      catalogId,
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
    setTimeout(() => composerRef.current?.focus(), 50);
  }, []);

  /** Resolve deck + prompt at click time from data attributes (avoids stale closure / overlay hit bugs). */
  const onExploreSectionClick = useCallback(
    (e) => {
      const el = e.target.closest("[data-explore-deck]");
      if (!el || !exploreGridRef.current?.contains(el)) return;
      const deckIndex = Number(el.getAttribute("data-explore-deck"), 10);
      const promptIndex = Number(el.getAttribute("data-explore-prompt"), 10);
      if (!Number.isInteger(deckIndex) || !Number.isInteger(promptIndex)) return;
      const ucs = roleUseCasesRef.current;
      if (deckIndex < 0 || deckIndex >= ucs.length) return;
      const uc = ucs[deckIndex];
      const promptsRow = getExplorePromptsForCatalogEntry(uc);
      if (promptIndex < 0 || promptIndex >= promptsRow.length) return;
      commitPlaygroundExploreHandoff(promptsRow[promptIndex], uc.id);
    },
    [commitPlaygroundExploreHandoff]
  );

  const hasInput = query.trim().length > 0 || attachments.staged.length > 0;

  return (
    <div className="relative flex w-full flex-col items-center overflow-x-hidden bg-transparent pb-16 text-text-primary">

      <div className="relative z-10 mt-14 sm:mt-[88px] mb-6 sm:mb-8 px-6 sm:px-8">
        <div className="flex items-center justify-center gap-3">
          <h1 className="text-2xl font-normal leading-tight tracking-[-0.5px] text-[color:var(--app-text-primary)] sm:text-[36px] sm:leading-[44px] sm:tracking-[-0.72px]">
            Unlock Enterprise Use-Cases with EnterpriseGPT
          </h1>
          <TourLaunchButton tourId="playground" label="Take a quick Playground tour" />
        </div>
      </div>

      <div className="relative z-40 w-full max-w-[768px] px-4 sm:px-2" data-tour-pg="composer">
        <div className="playground-hub-composer-shell w-full playground-gradient-border shadow-[0px_9px_9px_0px_rgba(0,0,0,0.01)]">
        <div className="rounded-[14px] bg-surface px-4 py-4 sm:px-[18px] min-h-[124px] flex flex-col justify-between">
          <PendingAttachmentChips staged={attachments.staged} onRemove={attachments.removeStaged} />
          <ChatComposerTextarea
            ref={composerRef}
            value={query}
            overlayValue={
              dictation.listening && dictation.interim
                ? `${query ? `${query.trimEnd()} ` : ""}${dictation.interim}`
                : undefined
            }
            onChange={setQuery}
            onSend={submitFromHub}
            placeholder={dictation.listening ? "Listening..." : "Ask anything"}
            ariaLabel="Ask anything"
          />
          <MicErrorMessage error={dictation.error} />
          {attachments.lastError ? (
            <p className="mt-1 text-xs text-red-600">{attachments.lastError}</p>
          ) : null}
          <div className="mt-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <label
                className="flex cursor-pointer items-center rounded-xl p-2 text-text-muted hover:bg-surface-secondary hover:text-primary"
                aria-label="Attach files"
                title="Attach files"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    attachments.openPicker();
                  }
                }}
              >
                <input ref={attachments.inputRef} {...attachments.fileInputProps} />
                <Paperclip size={18} className="pointer-events-none" />
              </label>

              {/* Database - dropdown */}
              <div ref={dbDropdownRef} className="relative">
                <button
                  type="button"
                  onClick={() => {
                    setShowConnDropdown(false);
                    setShowDbDropdown(!showDbDropdown);
                  }}
                  className={`rounded p-1.5 hover:bg-surface-secondary ${
                    showDbDropdown ? "text-primary bg-surface-tertiary" : "text-text-muted hover:text-primary"
                  }`}
                  title="Database"
                >
                  <Database size={18} />
                </button>

                {showDbDropdown && (
                  <div className="absolute left-0 top-full z-30 mt-2 w-[280px] rounded-xl border border-border bg-surface p-3 shadow-xl">
                    <p className="mb-2 px-1 text-sm font-semibold text-text-primary">Database</p>
                    <div className="space-y-1">
                      {DATABASES.map((db) => (
                        <div
                          key={db.name}
                          className="flex items-center justify-between rounded-lg px-2 py-2 hover:bg-surface-secondary"
                        >
                          <div className="flex items-center gap-3">
                            <img src={db.img} alt={db.name} className="h-6 w-6" />
                            <span className="text-sm text-text-primary">{db.name}</span>
                          </div>
                          <button
                            type="button"
                            onClick={() => {
                              setShowDbDropdown(false);
                              navigate("/configure/database");
                            }}
                            className="text-sm font-semibold text-primary hover:text-primary-hover"
                          >
                            Configure
                          </button>
                        </div>
                      ))}
                    </div>
                    <div className="my-2 h-px bg-border" />
                    <button
                      type="button"
                      onClick={() => {
                        setShowDbDropdown(false);
                        navigate("/configure/create-datasource");
                      }}
                      className="flex w-full items-center justify-between rounded-lg px-2 py-2.5 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
                    >
                      <span className="flex items-center gap-2">
                        <Plus size={18} className="text-text-muted" />
                        Add New Database
                      </span>
                      <ExternalLink size={18} className="shrink-0 text-text-muted" aria-hidden />
                    </button>
                  </div>
                )}
              </div>

              {/* Connectors - dropdown */}
              <div ref={connDropdownRef} className="relative">
                <button
                  type="button"
                  onClick={() => {
                    setShowDbDropdown(false);
                    setShowConnDropdown(!showConnDropdown);
                  }}
                  className={`flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-sm transition ${
                    showConnDropdown
                      ? "border-primary-light bg-surface-tertiary text-primary"
                      : "border-border-light bg-surface-secondary text-text-muted hover:border-border hover:text-text-tertiary"
                  }`}
                  title="Connectors"
                >
                  <LayoutGrid size={18} />
                  <span className="font-medium tabular-nums">{enabledConnectorCount} of {CONNECTOR_ITEMS.length}</span>
                </button>

                {showConnDropdown && (
                  <div className="absolute left-0 top-full z-30 mt-2 w-[300px] rounded-xl border border-border bg-surface p-3 shadow-xl">
                    <p className="mb-2 px-1 text-sm font-semibold text-text-primary">Connectors</p>
                    <div className="space-y-0.5">
                      {CONNECTOR_ITEMS.map((c) => (
                        <div
                          key={c.id}
                          className="flex items-center justify-between rounded-lg px-2 py-2.5 hover:bg-surface-secondary"
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            <img src={c.img} alt="" className="h-7 w-7 shrink-0 object-contain" />
                            <span className="truncate text-sm text-text-primary">{c.name}</span>
                          </div>
                          <ConnectorToggle
                            name={c.name}
                            enabled={connectorEnabled[c.id]}
                            onChange={(next) =>
                              setConnectorEnabled((prev) => ({ ...prev, [c.id]: next }))
                            }
                          />
                        </div>
                      ))}
                    </div>
                    <div className="my-2 h-px bg-border" />
                    <button
                      type="button"
                      onClick={() => {
                        setShowConnDropdown(false);
                        navigate("/configure/external");
                      }}
                      className="flex w-full items-center justify-between rounded-lg px-2 py-2.5 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
                    >
                      <span className="flex items-center gap-2">
                        <Plus size={18} className="text-text-muted" />
                        Manage Connectors
                      </span>
                      <ExternalLink size={18} className="shrink-0 text-text-muted" aria-hidden />
                    </button>
                  </div>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <MicButton
                supported={dictation.supported}
                listening={dictation.listening}
                onToggle={dictation.toggle}
                size={24}
              />
              <button
                type="button"
                onClick={submitFromHub}
                className={`flex h-10 w-10 items-center justify-center rounded-[8px] transition-colors ${
                  hasInput
                    ? "bg-primary text-white hover:bg-primary-hover"
                    : "bg-surface-tertiary text-[#c8cdd8] cursor-default"
                }`}
                title="Send"
              >
                <PaperPlaneSendIcon size={24} />
              </button>
            </div>
          </div>
        </div>
        </div>
        <div className="relative flex flex-col items-center">
          <p className="mt-2 max-w-[530px] text-center text-xs text-text-muted">
            AI-generated insights are based on connected data sources. Critical business decisions should be verified by domain experts.
          </p>
        </div>
      </div>

      <div className="relative z-20 mx-auto mt-8 w-full max-w-[1145px] px-4 sm:px-8">
        <div className="flex flex-wrap justify-center gap-[10px]" data-tour-pg="areas">
          {visibleCategories.map((cat) => {
            const slug = cat.areaId;
            const isActive =
              Boolean(slug) && location.pathname === `/playground/area/${slug}`;
            return (
            <button
              key={cat.areaId || cat.label}
              type="button"
              className={`flex min-h-[40px] sm:min-h-[44px] items-center gap-[6px] rounded-[12px] border px-4 py-2 text-sm font-medium transition sm:px-5 sm:py-2.5 sm:text-base ${
                isActive
                  ? "border-[rgba(82,90,112,0.5)] bg-[#e4e8ff] text-text-tertiary dark:border-border-light dark:bg-surface/[0.12] dark:text-text-primary"
                  : PILL_STYLE
              }`}
              onClick={() => {
                if (slug) navigate(`/playground/area/${slug}`);
              }}
            >
              <cat.icon size={15} className="shrink-0 text-primary" />
              {cat.label}
            </button>
          );
          })}
          {hasMoreBusinessAreas ? (
            <button
              type="button"
              onClick={() => setBusinessAreasExpanded((v) => !v)}
              className="flex min-h-[40px] items-center gap-1.5 rounded-[12px] border border-border bg-surface-secondary px-4 py-2 text-sm font-medium text-text-secondary transition hover:border-border-light hover:bg-surface-tertiary dark:border-border dark:bg-surface-default dark:text-text-secondary dark:hover:border-border-light dark:hover:bg-surface-secondary sm:min-h-[44px] sm:px-5 sm:py-2.5 sm:text-base"
              aria-expanded={businessAreasExpanded}
            >
              {businessAreasExpanded ? "See less" : "See more"}
              <ChevronsRight
                size={16}
                strokeWidth={2}
                className={`shrink-0 text-text-muted dark:text-text-muted ${businessAreasExpanded ? "rotate-180" : ""} transition-transform`}
                aria-hidden
              />
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => navigate("/playground/queries")}
            data-tour-pg="all-queries"
            className="flex min-h-[40px] items-center gap-[6px] rounded-[12px] border border-primary/30 bg-primary-light px-4 py-2 text-sm font-semibold text-primary transition hover:border-primary/50 hover:bg-primary-light-hover dark:border-primary/40 dark:bg-surface-tertiary dark:text-primary dark:hover:border-primary/55 sm:min-h-[44px] sm:px-5 sm:py-2.5 sm:text-base"
            title="Browse the master list of frequent queries"
          >
            All queries
            <ArrowRight size={15} strokeWidth={2} className="shrink-0" aria-hidden />
          </button>
        </div>

        <div
          className="mt-10 sm:mt-14 flex flex-wrap items-center justify-between gap-x-4 gap-y-3"
          data-tour-pg="help"
        >
          <div className="flex items-center gap-2">
            <h2 className="text-base font-medium leading-[28px] text-[color:var(--app-text-primary)] sm:text-lg">
              What do you need help with?
            </h2>
            {!isMediaProfile && !isMiddlebyProfile ? (
              <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${roleStyle.badgeCls}`}>
                {roleStyle.badge}
              </span>
            ) : null}
            <span className="group relative z-10 inline-flex">
              <span
                className="relative z-10 inline-flex h-5 w-5 cursor-help items-center justify-center rounded-full border border-border-light bg-surface-secondary text-text-muted transition group-hover:border-primary/40 group-hover:text-primary"
                aria-label="What do these questions mean?"
              >
                <Info size={12} strokeWidth={2.25} />
              </span>
              <span
                role="tooltip"
                className="pointer-events-none absolute bottom-full left-1/2 z-[100] mb-1.5 w-[min(36rem,calc(100vw-2rem))] -translate-x-1/2 rounded-lg border border-border-light bg-surface-default px-3 py-2 text-left text-sm font-normal leading-snug text-text-secondary opacity-0 shadow-xl transition-opacity duration-150 group-hover:opacity-100"
              >
                {EXPLORE_PROMPT_HUB_HELP}
              </span>
            </span>
          </div>
        </div>
      </div>

      {/* Full-width carousel */}
      <div
        className="use-case-carousel-outer relative z-10 mt-6 w-full overflow-hidden pb-4"
        data-tour-pg="use-cases"
      >
        <div
          ref={exploreGridRef}
          className="use-case-carousel flex gap-6 w-max pl-8 py-2"
          onClick={onExploreSectionClick}
          onKeyDown={(e) => {
            if (e.key !== "Enter" && e.key !== " ") return;
            const t = e.target;
            if (!(t instanceof HTMLElement)) return;
            const el = t.closest("[data-explore-deck]");
            if (!el || !exploreGridRef.current?.contains(el)) return;
            e.preventDefault();
            el.click();
          }}
          role="presentation"
        >
          {[0, 1].map((setIdx) =>
            roleUseCases.slice(0, 4).map((uc, deckIndex) => {
              const prompts = getExplorePromptsForCatalogEntry(uc);
              const areaSlug = uc.businessArea?.toLowerCase().replace(/\s+/g, "-") ?? "";
              const Icon = CATEGORY_ICON_BY_AREA_ID[areaSlug] || Briefcase;
              return (
                <div
                  key={`${setIdx}-${uc.id}`}
                  aria-hidden={setIdx === 1 ? "true" : undefined}
                  className="flex w-[340px] shrink-0 flex-col justify-between rounded-2xl border border-border bg-surface p-6 transition-all duration-300 ease-out hover:-translate-y-1.5 hover:border-primary/40 hover:shadow-[0_8px_24px_rgba(105,65,198,0.12)] dark:border-border dark:bg-surface-default dark:hover:border-primary/45 dark:hover:shadow-[0_12px_32px_rgba(0,0,0,0.45)]"
                >
                  <div>
                    <div
                      className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] border border-[rgba(0,0,0,0.06)] bg-surface dark:border-border/70 dark:bg-surface-secondary"
                      style={{
                        background: colorMode === "dark" ? CARD_ICON_GRADIENT_DARK : CARD_ICON_GRADIENT,
                      }}
                    >
                      <Icon size={22} className="text-primary" />
                    </div>
                    <h3 className="mt-4 text-lg font-semibold text-[color:var(--app-text-primary)]">{uc.title}</h3>
                    <p className="mt-1 text-sm text-[color:var(--app-text-secondary)]">{FEATURED_IMPACT[uc.id] || uc.problemSignal || ""}</p>
                    <ul className="mt-4 space-y-2">
                      {prompts.map((p, j) => (
                        <li key={`${uc.id}-${p.horizon ?? j}`} className="list-none">
                          <div className="group/query relative rounded-lg transition-all duration-200 ease-out hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[0_6px_18px_rgba(105,65,198,0.1)] dark:hover:shadow-[0_8px_22px_rgba(0,0,0,0.35)]">
                            <button
                              type="button"
                              data-explore-deck={String(deckIndex)}
                              data-explore-prompt={String(j)}
                              className="flex w-full items-start rounded-lg border border-border bg-surface-secondary px-3 py-2.5 pb-9 pr-10 text-left text-sm text-[color:var(--app-text-secondary)] transition-colors duration-200 ease-out group-hover/query:border-primary/35 group-hover/query:bg-primary-light/50 group-hover/query:text-[color:var(--app-text-primary)] dark:border-border dark:bg-surface-tertiary dark:group-hover/query:border-primary/50 dark:group-hover/query:bg-surface-secondary dark:group-hover/query:text-[color:var(--app-text-primary)]"
                            >
                              <span className="min-w-0 flex-1">
                                <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                                  <span>{p.lens}</span>
                                  <ExplorePromptSeverityIcon prompt={p} indexInCard={j} />
                                </span>
                                <span className="mt-0.5 block leading-snug">{p.text}</span>
                              </span>
                            </button>
                            <button
                              type="button"
                              className="absolute bottom-2 right-3 rounded p-0.5 text-text-muted transition-all duration-200 ease-out hover:bg-surface-default/95 hover:text-primary group-hover/query:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 dark:hover:bg-surface-secondary"
                              aria-label="Fill Playground input with this question"
                              onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                commitPlaygroundExploreHandoff(p);
                              }}
                            >
                              <ArrowUpLeft
                                size={16}
                                strokeWidth={2}
                                className="transition-transform duration-200 ease-out group-hover/query:-translate-x-0.5 group-hover/query:-translate-y-0.5"
                                aria-hidden
                              />
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <button
                    type="button"
                    data-explore-deck={String(deckIndex)}
                    data-explore-prompt="0"
                    className="mt-5 flex w-fit items-center gap-[6px] text-base font-semibold text-primary hover:text-primary-hover"
                  >
                    {uc.actionType || "Explore"} <ArrowRight size={16} />
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
