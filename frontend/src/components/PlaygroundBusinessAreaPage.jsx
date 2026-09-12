import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  Database,
  ExternalLink,
  LayoutGrid,
  Paperclip,
  Plus,
  Search,
  X,
} from "lucide-react";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { USE_CASE_BUSINESS_AREA_ORDER } from "../domain/use-case-catalog-seed";
import { entryMatchesSearchQuery, resolveGallerySectionArea } from "../storage/use-cases-catalog";
import { isPlaygroundAreaSlug, playgroundSlugToAreaLabel } from "../domain/playground-areas";
import { getExplorePromptsForCatalogEntry } from "../domain/demo-use-case-prompts";
import { FEATURED_IMPACT } from "../domain/featured-use-cases";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { parseUseCaseProcessIds } from "../domain/use-case-process-links";
import { useDictation } from "../hooks/useDictation";
import { MicButton, MicErrorMessage } from "./shared/MicButton";
import ChatComposerTextarea from "./shared/ChatComposerTextarea";
import { PaperPlaneSendIcon } from "./icons/PaperPlaneSendIcon";
import { useChatAttachments, stashAttachmentsForHandoff } from "../hooks/useChatAttachments";
import { PendingAttachmentChips } from "./shared/ChatAttachmentChips";
import { SubjectAreaIcon } from "./shared/SubjectAreaIcon";
import { PLAYGROUND_HUB_CHAT_MESSAGE_KEY, HUB_CHAT_ATTACH_HANDOFF_KEY } from "../storage/keys";
import { serializePlaygroundHubHandoff } from "../domain/playground-hub-handoff";
import { useProcesses } from "../hooks/useProcesses";
import { formatProcessFieldForUi } from "../storage/processes";
import snowflakeSvg from "../assets/snowflake.svg";
import airflowSvg from "../assets/airflow.svg";
import awsGlueSvg from "../assets/aws-glue.svg";
import adfSvg from "../assets/azure-data-factory.svg";
import salesforceSvg from "../assets/salesforce.svg";
import sapSvg from "../assets/sap.svg";
import netsuiteSvg from "../assets/netsuite.svg";
import {
  catalogPageShell,
  catalogPageInner,
  catalogH1,
  catalogSubtitle,
  catalogSectionBelowNav,
} from "./shared/useCaseCatalogPageStyles";
import { playgroundUseCaseCardSurfaceClasses } from "./shared/playgroundUseCaseCardSurface";
import { CatalogTagPill } from "./shared/catalogUseCaseTagPill";
import Switch from "./ui/Switch";

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

function catalogEntryMatchesSubjectArea(entry, subjectAreaLabel, processById) {
  const needle = String(subjectAreaLabel || "").trim();
  if (!needle) return true;
  const ids = parseUseCaseProcessIds(entry);
  if (ids.length === 0) return false;
  return ids.some((id) => {
    const p = processById[id];
    return p && String(p.subjectArea || "").trim() === needle;
  });
}

/** Subject dropdown "All" (empty string): full catalog for this business area; no subject-area filter on linked processes. */
function isAllSubjectAreas(subjectAreaState) {
  return !String(subjectAreaState ?? "").trim();
}

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

export default function PlaygroundBusinessAreaPage() {
  const { areaSlug } = useParams();
  const navigate = useNavigate();
  const { entries, hydrated } = useUseCaseCatalog();
  const { processes } = useProcesses();
  const attachments = useChatAttachments();
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const appendDictation = useCallback((text) => {
    setQuery((prev) => (prev ? `${prev.trimEnd()} ${text}` : text));
  }, []);
  const dictation = useDictation({ onFinalText: appendDictation });
  const composerRef = useRef(null);
  const dbDropdownRef = useRef(null);
  const connDropdownRef = useRef(null);
  const [showDbDropdown, setShowDbDropdown] = useState(false);
  const [showConnDropdown, setShowConnDropdown] = useState(false);
  const [connectorEnabled, setConnectorEnabled] = useState({
    salesforce: true,
    sap: false,
    netsuite: false,
  });
  const [subjectArea, setSubjectArea] = useState("");
  const [pendingPromptMeta, setPendingPromptMeta] = useState(null);
  const { areas: BUSINESS_AREAS } = useBusinessAreas();

  const currentBusinessArea = useMemo(
    () => BUSINESS_AREAS.find((area) => area.id === areaSlug) ?? null,
    [areaSlug, BUSINESS_AREAS]
  );
  const areaLabel = currentBusinessArea?.label || playgroundSlugToAreaLabel(areaSlug);
  const businessAreaSubjectAreas = currentBusinessArea?.subjectAreas ?? [];

  const allCatalogSorted = useMemo(() => {
    const list = [...(entries || [])];
    list.sort((a, b) => {
      const ga = a.galleryOrder ?? Number.MAX_SAFE_INTEGER;
      const gb = b.galleryOrder ?? Number.MAX_SAFE_INTEGER;
      if (ga !== gb) return ga - gb;
      return String(a.updatedAt || "").localeCompare(String(b.updatedAt || ""));
    });
    return list;
  }, [entries]);

  const areaEntries = useMemo(() => {
    if (!areaLabel) return [];
    return allCatalogSorted.filter(
      (e) => resolveGallerySectionArea(e, USE_CASE_BUSINESS_AREA_ORDER) === areaLabel
    );
  }, [allCatalogSorted, areaLabel]);

  const processById = useMemo(() => {
    const map = {};
    for (const p of processes || []) {
      if (p?.id) map[String(p.id)] = p;
    }
    return map;
  }, [processes]);

  const subjectScopedEntries = useMemo(() => {
    if (isAllSubjectAreas(subjectArea)) {
      return areaEntries;
    }
    const sa = String(subjectArea).trim();
    return areaEntries.filter((e) => catalogEntryMatchesSubjectArea(e, sa, processById));
  }, [areaEntries, subjectArea, processById]);

  const hasInput = query.trim().length > 0 || attachments.staged.length > 0;
  const enabledConnectorCount = Object.values(connectorEnabled).filter(Boolean).length;

  const searchFiltered = useMemo(() => {
    if (!search.trim()) return subjectScopedEntries;
    return subjectScopedEntries.filter((e) => entryMatchesSearchQuery(e, search));
  }, [subjectScopedEntries, search]);

  const commitExploreHandoff = useCallback(
    (p, opts = {}) => {
      const text = (p?.text || "").trim();
      if (!text) return;
      const t = p?.lens ? `${p.lens}: ${text}` : text;
      setQuery(t);
      setPendingPromptMeta({
        text,
        lens: p?.lens,
        horizon: p?.horizon,
        catalogId: opts.catalogId,
      });
      window.scrollTo({ top: 0, behavior: "smooth" });
      setTimeout(() => composerRef.current?.focus(), 50);
    },
    []
  );

  const submitFromHub = useCallback(() => {
    if (dictation.listening) dictation.stop();
    const text = query.trim();
    const staged = attachments.staged;
    const hasStaged = staged.length > 0;
    if (!text && !hasStaged) return;
    const textForHandoff = text || "(attached files)";
    try {
      const payload = serializePlaygroundHubHandoff({
        text: textForHandoff,
        lens: pendingPromptMeta?.lens,
        horizon: pendingPromptMeta?.horizon,
        catalogId: pendingPromptMeta?.catalogId,
        businessArea: areaLabel,
        subjectArea: isAllSubjectAreas(subjectArea) ? undefined : subjectArea.trim(),
      });
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
    setPendingPromptMeta(null);
    navigate("/playground/chat");
  }, [attachments, dictation, navigate, query, areaLabel, subjectArea, pendingPromptMeta]);

  useEffect(() => {
    setSubjectArea("");
  }, [areaSlug]);

  useEffect(() => {
    setSearch("");
  }, [areaSlug]);

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
    return undefined;
  }, [showDbDropdown, showConnDropdown]);

  if (!isPlaygroundAreaSlug(areaSlug)) {
    return <Navigate to="/playground" replace />;
  }

  if (!hydrated) {
    return (
      <div className={catalogPageShell}>
        <div className={catalogPageInner}>
          <p className="text-[17px] leading-[26px] text-text-secondary">Loading use cases…</p>
        </div>
      </div>
    );
  }

  return (
    <div className={catalogPageShell}>
      <div className={catalogPageInner}>
        <Link
          to="/playground"
          className="mb-2 inline-flex items-center gap-2 text-sm font-semibold text-primary hover:text-primary-hover"
        >
          <ArrowLeft size={16} strokeWidth={2} aria-hidden />
          Back to Playground
        </Link>
        <div className="mx-auto w-full max-w-[1440px]">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 flex-1">
              <h1 className={catalogH1}>Frequent queries in {areaLabel}</h1>
              <p className={catalogSubtitle}>
                Quick questions you can ask the Enterprise Assistant to start exploring {areaLabel.toLowerCase()} data.
              </p>
            </div>
            <div className="mt-4 flex items-start justify-end">
          <button
            type="button"
            onClick={() => navigate("/use-cases/builder/step-1?new=1")}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#7f56d9] px-4 py-2 text-sm font-semibold text-white transition hover:bg-primary"
          >
            <Plus size={14} strokeWidth={2.5} />
            Add / Edit Usecase
          </button>
        </div>
          </div>
        </div>

        

        <div className="relative mx-auto mt-8 mb-2 w-full max-w-[752px] rounded-2xl p-2 pb-10">
          <div className="flex flex-wrap items-center gap-2">
            <div className="absolute left-0 top-3 z-0 rounded-2xl border border-border-light bg-surface-secondary pb-8 dark:bg-surface-tertiary/80">
              <select
                value={areaSlug}
                onChange={(e) => navigate(`/playground/area/${e.target.value}`)}
                className="appearance-none rounded-lg border-0 bg-transparent py-3 pl-4 pr-8 text-md font-medium text-text-primary outline-none transition"
              >
                {BUSINESS_AREAS.map((area) => (
                  <option key={area.id} value={area.id}>
                    {area.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={14} className="pointer-events-none absolute right-2.5 top-1/3 -translate-y-1/2 text-text-muted" />
            </div>
          </div>
          <div className="playground-hub-composer-shell playground-gradient-border relative z-10 -mx-2 left-0 right-0 top-12 w-[calc(100%+1rem)] pb-8 shadow-[0px_9px_9px_0px_rgba(0,0,0,0.01)]">
            <div className="rounded-[14px] bg-surface px-4 py-4">
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
              {attachments.lastError ? <p className="mt-1 text-xs text-red-600">{attachments.lastError}</p> : null}
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
                  <div ref={dbDropdownRef} className="relative">
                    <button
                      type="button"
                      onClick={() => {
                        setShowConnDropdown(false);
                        setShowDbDropdown(!showDbDropdown);
                      }}
                      className={`rounded p-1.5 hover:bg-surface-secondary ${showDbDropdown ? "text-primary bg-surface-secondary" : "text-text-muted hover:text-primary"}`}
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
                  <div ref={connDropdownRef} className="relative">
                    <button
                      type="button"
                      onClick={() => {
                        setShowDbDropdown(false);
                        setShowConnDropdown(!showConnDropdown);
                      }}
                      className={`flex w-fit items-center gap-1.5 rounded-lg border px-2 py-1.5 text-sm transition ${
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
                  <div className="relative w-fit min-w-0 max-w-[16rem] sm:max-w-xs">
                    <div className="inline-flex h-[34px] w-fit min-w-0 max-w-full items-center gap-2 rounded-[8px] border border-primary/30 bg-primary-light py-0 pl-2.5 pr-7 shadow-sm transition hover:border-primary/50 hover:bg-primary-light-hover focus-within:ring-2 focus-within:ring-primary/40 focus-within:ring-offset-1 dark:border-border dark:bg-surface-secondary dark:ring-1 dark:ring-primary/25 dark:hover:border-border-light dark:hover:bg-surface-tertiary dark:focus-within:ring-primary/35 dark:focus-within:ring-offset-[var(--app-surface-page)]">
                      {subjectArea.trim() ? (
                        <SubjectAreaIcon
                          subject={subjectArea}
                          size={16}
                          className="shrink-0 text-primary"
                        />
                      ) : null}
                      <select
                        value={subjectArea}
                        onChange={(e) => setSubjectArea(e.target.value)}
                        aria-label="Subject area"
                        className="min-w-0 max-w-full flex-1 cursor-pointer appearance-none bg-transparent py-0 text-left text-sm font-semibold text-primary outline-none focus:ring-0"
                      >
                        <option value="">All subject areas</option>
                        {businessAreaSubjectAreas.map((subject) => (
                          <option key={subject} value={subject}>
                            {subject}
                          </option>
                        ))}
                      </select>
                    </div>
                    <ChevronDown
                      size={14}
                      className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-primary"
                      aria-hidden
                    />
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
        </div>
        <div className="relative flex flex-col items-center mb-8">
          <p className="mt-2 text-center text-xs text-text-muted max-w-[530px]">
            AI-generated insights are based on connected data sources. Critical business decisions should be verified by domain experts.
          </p>
        </div>
        <div className={`${catalogSectionBelowNav} space-y-10`}>
          <section>
            <div className="mb-4 flex flex-col gap-4 md:flex-row md:items-center md:justify-between md:gap-8">
              <div className="md:shrink-0">
                <h2 className="text-[15px] font-semibold leading-snug text-text-primary">
                  Browse <span className="font-semibold">{areaLabel}</span> queries
                </h2>
                {areaEntries.length > 0 ? (
                  <p className="mt-2 text-sm leading-5 text-text-secondary">
                    {search.trim()
                      ? `${searchFiltered.length} of ${subjectScopedEntries.length} queries match your search`
                      : `${subjectScopedEntries.length} quer${subjectScopedEntries.length === 1 ? "y" : "ies"} ${subjectArea.trim() ? `for ${subjectArea.trim()}` : `in this area`}`}
                  </p>
                ) : null}
              </div>
              <div className="relative w-full md:max-w-[420px] md:flex-1">
                <Search
                  size={16}
                  className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[#9da4ae]"
                />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={`Search ${areaLabel} queries`}
                  className="w-full rounded-xl border border-border bg-surface py-3 pl-11 pr-11 text-sm leading-5 text-text-primary placeholder:text-text-muted shadow-sm outline-none transition hover:border-border-light focus:border-primary focus:ring-4 focus:ring-primary/30 dark:focus:ring-primary/25"
                />
                {search ? (
                  <button
                    type="button"
                    onClick={() => setSearch("")}
                    className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full p-1 text-text-muted transition hover:bg-surface-tertiary hover:text-text-secondary"
                    aria-label="Clear search"
                  >
                    <X size={15} />
                  </button>
                ) : null}
              </div>
            </div>

            {searchFiltered.length === 0 ? (
              <p className="text-sm leading-5 text-text-secondary">
                {search.trim()
                  ? `No queries match "${search.trim()}".`
                  : areaEntries.length === 0
                    ? "No queries for this business area yet."
                    : subjectArea.trim() && subjectScopedEntries.length === 0
                      ? `No queries linked to "${subjectArea.trim()}" yet. Try another subject area.`
                      : "No queries match your filters."}
              </p>
            ) : (
              <div className="mt-6 grid grid-cols-1 gap-4 pb-2 sm:grid-cols-2 xl:grid-cols-4">
                {searchFiltered.map((uc) => {
                  const prompts = getExplorePromptsForCatalogEntry(uc);
                  const desc = FEATURED_IMPACT[uc.id] || uc.problemSignal || "";
                  const processLabel = formatProcessFieldForUi(uc.process, processes);
                  const titleNorm = String(uc.title || "")
                    .trim()
                    .toLowerCase();
                  const processNorm = String(processLabel || "")
                    .trim()
                    .toLowerCase();
                  const showProcessTag = Boolean(
                    processLabel &&
                      processNorm &&
                      processNorm !== titleNorm &&
                      !(titleNorm && processNorm.startsWith(`${titleNorm} `))
                  );
                  const areaTag = uc.businessArea ? String(uc.businessArea).trim() : "";
                  const hasTagRow = Boolean(areaTag || (showProcessTag && processLabel));
                  const openFirstPrompt = () => {
                    const first = prompts[0];
                    if (first) commitExploreHandoff(first, { catalogId: uc.id });
                  };
                  return (
                    <div
                      key={uc.id}
                      role="button"
                      tabIndex={0}
                      onClick={openFirstPrompt}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          openFirstPrompt();
                        }
                      }}
                      className={`group relative isolate z-0 h-full min-h-0 w-full cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 ${playgroundUseCaseCardSurfaceClasses}`}
                    >
                      <div className="flex min-h-0 flex-1 flex-col gap-4">
                        <h3 className="text-lg font-semibold text-text-primary">
                          {uc.title}
                        </h3>
                        {desc ? (
                          <p className="min-h-0 line-clamp-3 text-sm text-text-secondary">
                            {desc}
                          </p>
                        ) : null}
                        {hasTagRow ? (
                          <div className="flex min-h-0 min-w-0 flex-wrap items-start gap-2">
                            {areaTag ? (
                              <CatalogTagPill key={`${uc.id}-area`} text={areaTag} surface="playground" />
                            ) : null}
                            {showProcessTag && processLabel ? (
                              <CatalogTagPill
                                key={`${uc.id}-process`}
                                text={processLabel}
                                surface="playground"
                                kind="process"
                              />
                            ) : null}
                          </div>
                        ) : null}
                      </div>

                      <div
                        className={`flex w-full min-w-0 items-center ${uc.status === "draft" ? "justify-between gap-2" : "justify-end"}`}
                      >
                        {uc.status === "draft" ? (
                          <span className="inline-flex items-center rounded-full bg-[#fffaeb] px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[#b54708] ring-1 ring-inset ring-[#fedf89]">
                            Draft
                          </span>
                        ) : null}
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            openFirstPrompt();
                          }}
                          className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-primary transition hover:text-primary-hover"
                        >
                          {uc.actionType || "Explore"}
                          <ArrowRight
                            size={14}
                            className="transition-transform group-hover:translate-x-0.5"
                            aria-hidden
                          />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
