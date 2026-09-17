// Active chat conversation in Playground
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import logoSvg from "../assets/retailgpt-logo.svg";
import PlaygroundAssistantMessage from "./shared/PlaygroundAssistantMessage";
import PlaygroundHubComposerOptions from "./shared/PlaygroundHubComposerOptions";
import HeaderNotificationsAndProfile from "./shared/HeaderNotificationsAndProfile";
import { useDemoProfile } from "../context/DemoProfileContext";
import { MIDDLEBY_PROFILE_ID } from "../domain/demo-profiles";
import { useChatSession } from "../hooks/useChatSession";
import { CHAT_SESSIONS } from "../storage/idb/chat-db";
import { getPlaygroundChatSeed } from "../storage/chat-seeds";
import { PLAYGROUND_HUB_CHAT_MESSAGE_KEY, HUB_CHAT_ATTACH_HANDOFF_KEY, BUILDER_PREFILL_FROM_TEMPLATE_KEY, PLAYGROUND_HUB_SHOW_PREVIEW_KEY } from "../storage/keys";
import { lensDisplayToHorizon, parsePlaygroundHubHandoff } from "../domain/playground-hub-handoff";
// The playground answers from R11 only -- no scripted fallback. `resolve-playground-assistant`
// remains the use-case builder's resolver; see `resolve-playground-reply.js`.
import { resolvePlaygroundReply as resolvePlaygroundAssistantReplyAsync } from "../domain/resolve-playground-reply";
import { readPlaygroundMode } from "../storage/playground-mode";
import PlaygroundModePicker from "./shared/PlaygroundModePicker";
import { collapseAdjacentDuplicateUserMessages } from "../domain/chat-message-utils";
import { useChatAttachments } from "../hooks/useChatAttachments";
import { PendingAttachmentChips, SentAttachmentChips } from "./shared/ChatAttachmentChips";
import { useDictation } from "../hooks/useDictation";
import { MicErrorMessage } from "./shared/MicButton";
import ChatComposerTextarea from "./shared/ChatComposerTextarea";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { getMiddlebyPlaygroundChatPlaceholder } from "../domain/middleby-composer-hints";
import { Sparkles, Bookmark, Calendar, Zap, FileText } from "lucide-react";
import { useSavedPrompts } from "../hooks/useSavedPrompts";
import { useScheduledRuns } from "../hooks/useScheduledRuns";
import { useChatMode } from "../hooks/useChatMode";
import { useReport } from "../hooks/useReport";
import { useChatFilters } from "../hooks/useChatFilters";
import { CHAT_MODES } from "../storage/chat-mode";
import { CalendarRange } from "lucide-react";
import SavedPromptsDrawer from "./middleby/SavedPromptsDrawer";
import ScheduledRunsPanel from "./middleby/ScheduledRunsPanel";
import ScheduleMessageModal from "./middleby/ScheduleMessageModal";
import ReportDrawer from "./middleby/ReportDrawer";
import PeriodFilterChip from "./middleby/PeriodFilterChip";

/** Pixels from bottom: if the user is within this band, new content auto-scrolls (ChatGPT-style stick-to-bottom). */
const STICKY_BOTTOM_THRESHOLD_PX = 120;

export default function PlaygroundActiveChat() {
  const navigate = useNavigate();
  const [message, setMessage] = useState("");
  const [pendingHandoff, setPendingHandoff] = useState(null);
  const [hubSeedCatalogId, setHubSeedCatalogId] = useState(null);
  const [inputFocused, setInputFocused] = useState(false);
  const messagesScrollRef = useRef(null);
  const messagesEndRef = useRef(null);
  const latestAssistantRef = useRef(null);
  const inputBoxRef = useRef(null);
  const { profileId, role, sessionForChat } = useDemoProfile();
  const isMiddleby = profileId === MIDDLEBY_PROFILE_ID;
  const savedPrompts = useSavedPrompts(profileId);
  const scheduledRuns = useScheduledRuns(profileId);
  const { mode: chatMode, toggleBrief, isBrief } = useChatMode(profileId);
  const chatFilters = useChatFilters(profileId);
  const report = useReport(profileId);
  const [savedOpen, setSavedOpen] = useState(false);
  const [scheduledOpen, setScheduledOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [scheduleTarget, setScheduleTarget] = useState(null);
  const { entries: catalogEntries, hydrated: catalogHydrated } = useUseCaseCatalog();
  const sessionId = sessionForChat(CHAT_SESSIONS.PLAYGROUND);
  const chatSeed = useMemo(() => getPlaygroundChatSeed(profileId), [profileId]);
  const { messages, ready, send } = useChatSession(sessionId, chatSeed);
  const attachments = useChatAttachments();

  const appendDictation = useCallback((text) => {
    setMessage((prev) => (prev ? `${prev.trimEnd()} ${text}` : text));
  }, []);
  const dictation = useDictation({ onFinalText: appendDictation });

  /**
   * Pin the transcript to the latest turn. Same pattern as UseCaseBuilderChat: bounded column height
   * (`calc(100vh - …)`) so this overflow-y-auto region actually scrolls (flex-1 alone grows with content
   * and silently moves scroll to the window).
   */
  const pinchScrollToBottom = useCallback(() => {
    // For assistant messages (which can be tall answer-block responses), scroll
    // so the START of the latest answer is visible  -  otherwise the user lands
    // at the citations at the bottom and misses the headline.
    const scrollToLatestStart = () => {
      const node = latestAssistantRef.current;
      const scrollEl = messagesScrollRef.current;
      if (!node || !scrollEl) return false;
      const nodeTop = node.getBoundingClientRect().top;
      const scrollTop = scrollEl.getBoundingClientRect().top;
      const offset = nodeTop - scrollTop + scrollEl.scrollTop - 12;
      scrollEl.scrollTop = Math.max(0, offset);
      return true;
    };
    if (scrollToLatestStart()) return;
    const scrollEl = messagesScrollRef.current;
    if (scrollEl) {
      scrollEl.scrollTop = scrollEl.scrollHeight - scrollEl.clientHeight;
    }
    messagesEndRef.current?.scrollIntoView({ behavior: "instant", block: "end" });
  }, []);

  /**
   * Runs when chat is ready: parse the handoff and show the user bubble + thinking dots
   * immediately, then fire the LLM call after a short delay so the user can see what's happening.
   */
  useEffect(() => {
    if (!ready) return;
    try {
      const raw = sessionStorage.getItem(PLAYGROUND_HUB_CHAT_MESSAGE_KEY);
      if (!raw) return;
      sessionStorage.removeItem(PLAYGROUND_HUB_CHAT_MESSAGE_KEY);
      try { sessionStorage.removeItem(HUB_CHAT_ATTACH_HANDOFF_KEY); } catch { /* ignore */ }
      const parsed = parsePlaygroundHubHandoff(raw);
      if (!parsed?.text) return;
      const { text: qText, lens, horizon, catalogId, businessArea, subjectArea } = parsed;
      setHubSeedCatalogId(
        catalogId != null && String(catalogId).trim() ? String(catalogId).trim() : null
      );
      const effectiveHorizon =
        lensDisplayToHorizon(lens) ?? (horizon === "week" || horizon === "quarter" ? horizon : undefined);
      const lensLabel =
        lens ??
        (effectiveHorizon === "week" ? "This week" : effectiveHorizon === "quarter" ? "This quarter" : undefined);
      const userMeta =
        lensLabel || catalogId || effectiveHorizon || businessArea || subjectArea
          ? {
              source: "playground-hub",
              ...(lensLabel ? { lens: lensLabel } : {}),
              ...(effectiveHorizon ? { horizon: effectiveHorizon } : {}),
              ...(catalogId ? { catalogId } : {}),
              ...(businessArea ? { businessArea } : {}),
              ...(subjectArea ? { subjectArea } : {}),
            }
          : undefined;
      const exploreHub =
        catalogId && (effectiveHorizon || lens)
          ? {
              catalogId,
              ...(effectiveHorizon ? { horizon: effectiveHorizon } : {}),
              ...(lens ? { lens } : {}),
            }
          : undefined;
      // Scroll to bottom first so existing history is visible, then show the pending bubble
      pinchScrollToBottom();
      requestAnimationFrame(() => {
        pinchScrollToBottom();
        setTimeout(() => setPendingHandoff({ qText, lensLabel, userMeta, exploreHub }), 100);
      });
    } catch {
      sessionStorage.removeItem(PLAYGROUND_HUB_CHAT_MESSAGE_KEY);
    }
  }, [ready, pinchScrollToBottom]);

  const seedContextCatalogId = useMemo(() => {
    if (hubSeedCatalogId) return hubSeedCatalogId;
    if (!ready) return null;
    const collap = collapseAdjacentDuplicateUserMessages(messages);
    for (const m of collap) {
      if (m.role === "user" && m.meta?.catalogId) {
        const id = String(m.meta.catalogId).trim();
        if (id) return id;
      }
    }
    return null;
  }, [hubSeedCatalogId, ready, messages]);

  const linkedSeedEntry = useMemo(
    () => (seedContextCatalogId ? catalogEntries.find((e) => e.id === seedContextCatalogId) : null),
    [catalogEntries, seedContextCatalogId]
  );

  const showSeedBuilderCta =
    ready &&
    catalogHydrated &&
    profileId !== MIDDLEBY_PROFILE_ID &&
    Boolean(seedContextCatalogId) &&
    linkedSeedEntry?.source === "seed";

  const showMiddlebyInsightCta =
    ready &&
    catalogHydrated &&
    profileId === MIDDLEBY_PROFILE_ID &&
    Boolean(seedContextCatalogId) &&
    String(seedContextCatalogId).startsWith("middleby-");

  const chatPlaceholder = useMemo(() => {
    if (dictation.listening) return "Listening...";
    if (profileId === MIDDLEBY_PROFILE_ID) {
      return getMiddlebyPlaygroundChatPlaceholder(seedContextCatalogId, role);
    }
    return "Ask anything";
  }, [dictation.listening, profileId, role, seedContextCatalogId]);

  const handleOpenInsightSession = useCallback(() => {
    if (!seedContextCatalogId) return;
    navigate(`/playground/insight/${encodeURIComponent(seedContextCatalogId)}`);
  }, [seedContextCatalogId, navigate]);

  const handleContinueToBuilder = useCallback(() => {
    if (!seedContextCatalogId || !linkedSeedEntry || linkedSeedEntry.source !== "seed") return;
    const collapsed = collapseAdjacentDuplicateUserMessages(messages);
    let primaryQuestion = "";
    for (let i = collapsed.length - 1; i >= 0; i--) {
      if (collapsed[i].role === "user") {
        primaryQuestion = String(collapsed[i].text || "").trim();
        break;
      }
    }
    if (!primaryQuestion && pendingHandoff?.qText) {
      primaryQuestion = String(pendingHandoff.qText || "").trim();
    }
    try {
      sessionStorage.setItem(
        BUILDER_PREFILL_FROM_TEMPLATE_KEY,
        JSON.stringify({ catalogId: seedContextCatalogId, primaryQuestion })
      );
    } catch {
      /* ignore */
    }
    // Do not append ?new=1: the UseCaseDraftContext ?new=1 handler resets the
    // draft AFTER the prefill effect fires, wiping the template we just
    // stashed. The prefill flow starts from an empty draft on its own.
    navigate("/use-cases/builder/describe");
  }, [seedContextCatalogId, linkedSeedEntry, messages, pendingHandoff, navigate]);

  /** After the 1.2s preview delay, fire the actual LLM call. */
  useEffect(() => {
    if (!pendingHandoff) return;
    const { qText, lensLabel: _l, userMeta, exploreHub } = pendingHandoff;
    const timer = setTimeout(async () => {
      setPendingHandoff(null);
      try {
        // Enrich with the current period filter so a hand-off from the home
        // hub (or a previous session) still respects the user's period pick.
        // The active playground mode rides on the message, so a retry of this turn repeats it
        // in the same mode rather than falling back to the default. Without this `prevUser.meta`
        // never carries a mode and the retry fix above would be a no-op.
        const activeMode = readPlaygroundMode(profileId);
        const enrichedMeta = {
          ...(userMeta || {}),
          playgroundMode: userMeta?.playgroundMode ?? activeMode.mode,
          scopeId: userMeta?.scopeId ?? activeMode.scopeId,
          period: chatFilters.period,
          ...(chatFilters.period === "custom" && chatFilters.fromDate && chatFilters.toDate
            ? { fromDate: chatFilters.fromDate, toDate: chatFilters.toDate }
            : {}),
        };
        const rows = await send(qText, "user", enrichedMeta);
        const { text, meta } = await resolvePlaygroundAssistantReplyAsync(
          qText,
          profileId,
          exploreHub,
          enrichedMeta,
          rows ?? undefined
        );
        const metaWithMode = meta ? { ...meta, chatModeAtWrite: chatMode } : meta;
        await send(text, "assistant", metaWithMode);
      } catch (e) {
        console.error("[PlaygroundActiveChat] handoff send failed", e);
      }
    }, 1200);
    return () => clearTimeout(timer);
  }, [pendingHandoff, send, profileId, chatFilters.period, chatFilters.fromDate, chatFilters.toDate, chatMode]);

  useLayoutEffect(() => {
    if (!ready) return;
    pinchScrollToBottom();
    const raf = requestAnimationFrame(pinchScrollToBottom);
    return () => cancelAnimationFrame(raf);
  }, [ready, messages, pendingHandoff, pinchScrollToBottom]);

  useEffect(() => {
    if (!ready) return;
    const t1 = window.setTimeout(pinchScrollToBottom, 120);
    const t2 = window.setTimeout(pinchScrollToBottom, 320);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [ready, messages, pendingHandoff, pinchScrollToBottom]);

  /**
   * Rich reply panels resize after paint; only nudge scroll if the user was
   * already near the bottom AND the resize is close in time to a new message
   * arriving. If a user manually expands something inside an existing message
   * (e.g., "Where this came from"), we let them stay where they clicked
   * instead of yanking the scroll to the start of the latest answer.
   */
  const lastMessageChangeAtRef = useRef(0);
  useEffect(() => {
    lastMessageChangeAtRef.current = Date.now();
  }, [messages.length]);

  useEffect(() => {
    if (!ready) return;
    const pane = messagesScrollRef.current;
    const inner = pane?.firstElementChild;
    if (!inner || typeof ResizeObserver === "undefined") return;
    const USER_INTERACTION_GRACE_MS = 1500;
    const ro = new ResizeObserver(() => {
      const el = messagesScrollRef.current;
      if (!el) return;
      // If the resize happened more than ~1.5s after the last new message, it
      // came from a user-driven expansion inside an existing message. Don't
      // scroll — the user wants to stay where they clicked.
      if (Date.now() - lastMessageChangeAtRef.current > USER_INTERACTION_GRACE_MS) return;
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (dist < STICKY_BOTTOM_THRESHOLD_PX) pinchScrollToBottom();
    });
    ro.observe(inner);
    return () => ro.disconnect();
  }, [ready, pinchScrollToBottom, messages.length]);

  const [thinking, setThinking] = useState(false);

  const sendPrompt = useCallback(async (promptText, userMeta) => {
    const trimmed = String(promptText || "").trim();
    if (!trimmed) return;
    setThinking(true);
    try {
      const userMetaWithPeriod = {
        ...(userMeta || {}),
        period: chatFilters.period,
        ...(chatFilters.period === "custom" && chatFilters.fromDate && chatFilters.toDate
          ? { fromDate: chatFilters.fromDate, toDate: chatFilters.toDate }
          : {}),
      };
      const rows = await send(trimmed, "user", userMetaWithPeriod);
      const MIN_THINK_MS = 900;
      const start = Date.now();
      const [{ text, meta }] = await Promise.all([
        resolvePlaygroundAssistantReplyAsync(
          trimmed,
          profileId,
          undefined,
          userMetaWithPeriod,
          rows ?? undefined
        ),
      ]);
      const elapsed = Date.now() - start;
      if (elapsed < MIN_THINK_MS) {
        await new Promise((r) => setTimeout(r, MIN_THINK_MS - elapsed));
      }
      // Snapshot the chat mode at write time so toggling later doesn't
      // retroactively change how this answer was originally shown.
      const metaWithMode = meta ? { ...meta, chatModeAtWrite: chatMode } : meta;
      await send(text, "assistant", metaWithMode);
    } catch (e) {
      console.error("[PlaygroundActiveChat] sendPrompt failed", e);
    } finally {
      setThinking(false);
    }
  }, [send, profileId, chatFilters.period, chatMode]);

  // Inject just an assistant preview message without a user bubble.
  const showFormatPreview = useCallback(async () => {
    try {
      // Rendered locally, never sent to R11. This is a developer aid that draws one of every
      // answer-block type; routing it through the resolver POSTed the literal trigger string
      // to the real agent, which cannot produce those block kinds and would answer the string
      // as if it were a question.
      const { buildBlocksPreviewReply } = await import("../domain/answer-blocks/preview-message");
      const { text, meta } = buildBlocksPreviewReply();
      await send(text, "assistant", meta);
    } catch (e) {
      console.error("[PlaygroundActiveChat] showFormatPreview failed", e);
    }
  }, [send, profileId]);

  // Honour "Show preview" handoff from the hub  -  one-shot, cleared on read.
  useEffect(() => {
    if (!ready || !isMiddleby) return;
    let flag = null;
    try { flag = sessionStorage.getItem(PLAYGROUND_HUB_SHOW_PREVIEW_KEY); } catch { /* ignore */ }
    if (!flag) return;
    try { sessionStorage.removeItem(PLAYGROUND_HUB_SHOW_PREVIEW_KEY); } catch { /* ignore */ }
    void showFormatPreview();
  }, [ready, isMiddleby, showFormatPreview]);

  const handleSend = async () => {
    const trimmed = message.trim();
    const hasStaged = attachments.staged.length > 0;
    if (!trimmed && !hasStaged) return;
    if (dictation.listening) dictation.stop();
    setMessage("");
    const textToSend = trimmed || (hasStaged ? "(attached files)" : "");
    setThinking(true);
    try {
      let userMeta;
      let sendOptions;
      if (hasStaged) {
        const userMsgId = crypto.randomUUID?.() ?? `m-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const persisted = await attachments.persistAll(sessionId, userMsgId);
        if (persisted.length) {
          userMeta = { attachments: persisted };
          sendOptions = { id: userMsgId };
        }
      }
      const userMetaWithPeriod = {
        ...(userMeta || {}),
        period: chatFilters.period,
        ...(chatFilters.period === "custom" && chatFilters.fromDate && chatFilters.toDate
          ? { fromDate: chatFilters.fromDate, toDate: chatFilters.toDate }
          : {}),
      };
      const rows = await send(textToSend, "user", userMetaWithPeriod, sendOptions);
      const MIN_THINK_MS = 900;
      const start = Date.now();
      const { text, meta } = await resolvePlaygroundAssistantReplyAsync(
        trimmed || textToSend,
        profileId,
        undefined,
        userMetaWithPeriod,
        rows ?? undefined
      );
      const elapsed = Date.now() - start;
      if (elapsed < MIN_THINK_MS) {
        await new Promise((r) => setTimeout(r, MIN_THINK_MS - elapsed));
      }
      const metaWithMode = meta ? { ...meta, chatModeAtWrite: chatMode } : meta;
      await send(text, "assistant", metaWithMode);
    } catch (e) {
      console.error("[PlaygroundActiveChat] send failed", e);
      setMessage(trimmed);
    } finally {
      setThinking(false);
    }
  };

  return (
    <div className="flex h-full max-h-[100dvh] w-full min-w-0 min-h-0 flex-col overflow-hidden bg-transparent">
      <header className="sticky top-0 z-40 shrink-0 border-b border-border bg-surface px-3 sm:px-6 lg:px-8">
        <div className="flex h-[72px] w-full min-w-0 items-center justify-between gap-3 sm:gap-4">
          <div className="flex min-w-0 flex-1 items-center gap-3 sm:gap-4">
            <Link
              to="/playground"
              className="flex min-w-0 shrink-0 items-center gap-1.5 sm:gap-2"
              aria-label="EnterpriseGPT home"
            >
              <img
                src={logoSvg}
                alt=""
                className="h-8 w-8 shrink-0 object-contain dark:brightness-0 dark:invert"
                width={32}
                height={32}
                decoding="async"
              />
              <span className="whitespace-nowrap text-lg tracking-[-0.8px] text-text-primary sm:text-xl">
                <span className="font-semibold">Enterprise</span>
                <span className="font-bold">GPT</span>
              </span>
            </Link>
            <div className="h-6 w-px shrink-0 bg-border" aria-hidden />
            <Link
              to="/playground"
              className="inline-flex min-h-10 min-w-0 items-center justify-center gap-1.5 rounded-md px-2 py-2 text-sm font-semibold text-text-tertiary transition hover:bg-surface-secondary hover:text-text-primary sm:px-3 sm:text-base"
            >
              <ChevronLeft className="h-4 w-4 shrink-0 sm:h-5 sm:w-5" aria-hidden />
              <span>Back to Playground</span>
            </Link>
          </div>
          <HeaderNotificationsAndProfile />
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {showSeedBuilderCta ? (
          <div className="shrink-0 border-b border-border bg-primary-light dark:border-primary/25 dark:bg-gradient-to-r dark:from-surface-secondary dark:via-surface-tertiary/80 dark:to-surface-secondary">
            <div className="mx-auto w-full max-w-[1145px] px-4 py-4 sm:px-8">
              <div className="flex w-full flex-col gap-4 sm:flex-row sm:items-center sm:justify-between sm:gap-6 lg:gap-8">
                <p className="min-w-0 flex-1 text-sm leading-relaxed text-text-primary">
                  Chat helps you explore and sharpen the idea. When you are ready to productionize it, continue in
                  the Use Case Builder. We prefill from this catalog template and your latest question.
                </p>
                <button
                  type="button"
                  onClick={handleContinueToBuilder}
                  className="shrink-0 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm ring-1 ring-primary/20 transition hover:bg-primary-hover dark:ring-primary/40"
                >
                  Continue in Use Case Builder
                </button>
              </div>
            </div>
          </div>
        ) : null}
        {showMiddlebyInsightCta ? (
          <div className="shrink-0 border-b border-border bg-primary-light/60 dark:border-primary/25 dark:bg-primary/10">
            <div className="mx-auto w-full max-w-[1145px] px-4 py-3 sm:px-8">
              <div className="flex w-full flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
                <p className="min-w-0 flex-1 text-sm leading-relaxed text-text-primary">
                  Want more than the chat answer? Open this use case's full insight session to see the <span className="font-semibold">Monitor</span> (track it over time) and <span className="font-semibold">Automate</span> (run follow-up workflows) views.
                </p>
                <button
                  type="button"
                  onClick={handleOpenInsightSession}
                  className="shrink-0 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white shadow-sm ring-1 ring-primary/20 transition hover:bg-primary-hover dark:ring-primary/40"
                >
                  Open insight session
                </button>
              </div>
            </div>
          </div>
        ) : null}
        <div
          ref={messagesScrollRef}
          className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-y-contain px-4 py-6 sm:px-8"
        >
          <div className="mx-auto max-w-[768px] space-y-6">
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs font-medium text-text-muted">Today · EnterpriseGPT</span>
              <div className="h-px flex-1 bg-border" />
            </div>

            {!ready ? (
              <p className="text-sm text-text-secondary">Loading conversation…</p>
            ) : (
              collapseAdjacentDuplicateUserMessages(messages).map((msg, msgIdx, arr) => {
                const userLensLine =
                  msg.role === "user"
                    ? (msg.meta?.lens ??
                      (msg.meta?.horizon === "week"
                        ? "This week"
                        : msg.meta?.horizon === "quarter"
                          ? "This quarter"
                          : null))
                    : null;
                const userBusinessArea = msg.role === "user" ? msg.meta?.businessArea : null;
                const userSubjectArea = msg.role === "user" ? msg.meta?.subjectArea : null;
                const isLatestAssistantMsg = msg.role === "assistant" && arr.slice(msgIdx + 1).every((m) => m.role !== "assistant");
                return (
                  <div
                    key={msg.id}
                    className="space-y-6"
                    ref={isLatestAssistantMsg ? latestAssistantRef : null}
                  >
                    {msg.role === "user" ? (
                      <div className="flex w-full justify-end">
                        <div className="min-w-0 max-w-[78%] rounded-2xl border border-primary/25 bg-primary-light px-4 py-2.5 dark:border-primary/35 dark:bg-surface-secondary dark:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)]">
                          {(userBusinessArea || userSubjectArea) ? (
                            <div className="mb-1.5 flex flex-wrap gap-1">
                              {userBusinessArea ? (
                                <span className="inline-flex items-center rounded-full bg-surface/80 px-2 py-0.5 text-[10px] font-semibold text-primary ring-1 ring-primary/30 dark:bg-surface/50">
                                  {userBusinessArea}
                                </span>
                              ) : null}
                              {userSubjectArea ? (
                                <span className="inline-flex items-center rounded-full bg-surface/80 px-2 py-0.5 text-[10px] font-medium text-primary ring-1 ring-primary/30 dark:bg-surface/50">
                                  {userSubjectArea}
                                </span>
                              ) : null}
                            </div>
                          ) : null}
                          {userLensLine ? (
                            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-primary">
                              {userLensLine}
                            </p>
                          ) : null}
                          <p className="whitespace-pre-wrap break-words text-[15px] leading-relaxed text-text-primary">
                            {msg.text}
                          </p>
                          <SentAttachmentChips attachments={msg.meta?.attachments} />
                        </div>
                      </div>
                    ) : (
                      <PlaygroundAssistantMessage
                        msg={msg}
                        chatMode={msg.meta?.chatModeAtWrite ?? chatMode}
                        isLatestAssistant={msgIdx === arr.length - 1 || arr.slice(msgIdx + 1).every((m) => m.role !== "assistant")}
                        onRetry={async () => {
                          const prevUser = arr.slice(0, msgIdx).reverse().find((m) => m.role === "user");
                          if (!prevUser?.text) return;
                          // `prevUser.meta` carries the mode and scope the original turn ran
                          // in. Dropping it made a retry silently re-ask in `data` mode against
                          // a different tool set than the answer being retried.
                          // `exploreHub` is NOT bound in the component body -- its only
                          // declarations are inside two useEffect closures -- so naming it
                          // here threw ReferenceError and retry silently did nothing.
                          // `prevUser.meta` is the right source regardless: it carries the
                          // mode and scope the original turn ran in.
                          const { text: rText, meta: rMeta } = await resolvePlaygroundAssistantReplyAsync(
                            prevUser.text,
                            profileId,
                            undefined,
                            prevUser.meta
                          );
                          await send(rText, "assistant", rMeta);
                        }}
                        onFollowUpSelect={(prompt) => {
                          if (!prompt) return;
                          void sendPrompt(prompt);
                        }}
                        onBlockAction={(item) => {
                          if (!item) return;
                          if (item.kind === "prompt" && item.payload?.prompt) {
                            void sendPrompt(item.payload.prompt);
                            return;
                          }
                          if (item.kind === "drill" || item.kind === "filter") {
                            // Reuse the label as the follow-up prompt when no explicit payload.
                            const prompt = item.payload?.prompt || item.label;
                            if (prompt) void sendPrompt(prompt);
                            return;
                          }
                          if (item.kind === "external" && item.payload?.href) {
                            try { window.open(item.payload.href, "_blank", "noreferrer"); } catch { /* ignore */ }
                          }
                        }}
                        onSaveMessage={isMiddleby ? () => {
                          const prevUser = arr.slice(0, msgIdx).reverse().find((m) => m.role === "user");
                          const text = prevUser?.text?.trim();
                          if (!text) return;
                          savedPrompts.savePrompt({ text });
                          setSavedOpen(true);
                        } : undefined}
                        onScheduleMessage={isMiddleby ? () => {
                          const prevUser = arr.slice(0, msgIdx).reverse().find((m) => m.role === "user");
                          const text = prevUser?.text?.trim();
                          if (!text) return;
                          setScheduleTarget(text);
                        } : undefined}
                        activeReportTitle={report.activeReport?.title}
                        isMessagePinned={isMiddleby && report.activeItems.some((it) => (msg.meta?.blocks || []).some((b) => b.id === it.block.id))}
                        onAddMessageToReport={isMiddleby ? () => {
                          const prevUser = arr.slice(0, msgIdx).reverse().find((m) => m.role === "user");
                          const sourceQuestion = prevUser?.text?.trim() || msg.meta?.intro || "";
                          for (const block of msg.meta?.blocks || []) {
                            report.pin({ block, sourceQuestion, catalogId: msg.meta?.catalogId });
                          }
                        } : undefined}
                      />
                    )}
                  </div>
                );
              })
            )}
            {thinking && !pendingHandoff && (
              <div className="flex items-center gap-1 pl-11 py-1">
                <span className="h-2 w-2 rounded-full bg-primary/40 animate-bounce [animation-delay:-0.3s]" />
                <span className="h-2 w-2 rounded-full bg-primary/60 animate-bounce [animation-delay:-0.15s]" />
                <span className="h-2 w-2 rounded-full bg-primary animate-bounce" />
                <span className="ml-2 text-xs text-text-muted">Enterprise GPT is thinking…</span>
              </div>
            )}
            {pendingHandoff && (
              <div className="space-y-6">
                <div className="flex w-full justify-end">
                  <div className="min-w-0 max-w-[78%] rounded-2xl border border-primary/25 bg-primary-light px-4 py-2.5 dark:border-primary/35 dark:bg-surface-secondary dark:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)]">
                    {(pendingHandoff.userMeta?.businessArea || pendingHandoff.userMeta?.subjectArea) ? (
                      <div className="mb-1.5 flex flex-wrap gap-1">
                        {pendingHandoff.userMeta?.businessArea ? (
                          <span className="inline-flex items-center rounded-full bg-surface/80 px-2 py-0.5 text-[10px] font-semibold text-primary ring-1 ring-primary/30 dark:bg-surface/50">
                            {pendingHandoff.userMeta.businessArea}
                          </span>
                        ) : null}
                        {pendingHandoff.userMeta?.subjectArea ? (
                          <span className="inline-flex items-center rounded-full bg-surface/80 px-2 py-0.5 text-[10px] font-medium text-primary ring-1 ring-primary/30 dark:bg-surface/50">
                            {pendingHandoff.userMeta.subjectArea}
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                    {pendingHandoff.lensLabel && (
                      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-primary">
                        {pendingHandoff.lensLabel}
                      </p>
                    )}
                    <p className="whitespace-pre-wrap break-words text-[15px] leading-relaxed text-text-primary">
                      {pendingHandoff.qText}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-1 pl-11 py-1">
                  <span className="h-2 w-2 rounded-full bg-primary/40 animate-bounce [animation-delay:-0.3s]" />
                  <span className="h-2 w-2 rounded-full bg-primary/60 animate-bounce [animation-delay:-0.15s]" />
                  <span className="h-2 w-2 rounded-full bg-primary animate-bounce" />
                </div>
              </div>
            )}
            <div ref={messagesEndRef} className="h-px w-full shrink-0" aria-hidden />
          </div>
        </div>

        <div className="z-20 shrink-0 px-4 pb-4 sm:px-8">
          <div className="mx-auto max-w-[768px]">
            {isMiddleby ? (
              <div className="mb-2 flex flex-wrap items-center justify-center gap-2">
                <PeriodFilterChip chatFilters={chatFilters} />
                <button
                  type="button"
                  onClick={toggleBrief}
                  title={isBrief ? "Quick summary is on  -  future answers show only the one-minute read. Click to turn off." : "When on, answers show only a short headline + a few bullets. Good for a quick read."}
                  aria-pressed={isBrief}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
                    isBrief
                      ? "border-primary bg-primary text-white"
                      : "border-border bg-surface-default text-text-secondary hover:border-primary/40 hover:text-primary"
                  }`}
                >
                  <Zap size={12} />
                  {isBrief ? "Quick summary is on" : "Turn on quick summary"}
                </button>
                <button
                  type="button"
                  onClick={() => setSavedOpen(true)}
                  title="Open the list of questions you've saved"
                  className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-default px-3 py-1 text-xs font-medium text-text-secondary transition hover:border-primary/40 hover:text-primary"
                >
                  <Bookmark size={12} />
                  Saved
                  {savedPrompts.prompts.length ? (
                    <span className="ml-0.5 rounded-full bg-primary/15 px-1.5 text-[10px] font-semibold text-primary">
                      {savedPrompts.prompts.length}
                    </span>
                  ) : null}
                </button>
                <button
                  type="button"
                  onClick={() => setScheduledOpen(true)}
                  title="Open your scheduled questions"
                  className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-default px-3 py-1 text-xs font-medium text-text-secondary transition hover:border-primary/40 hover:text-primary"
                >
                  <Calendar size={12} />
                  Scheduled
                  {scheduledRuns.runs.length ? (
                    <span className="ml-0.5 rounded-full bg-primary/15 px-1.5 text-[10px] font-semibold text-primary">
                      {scheduledRuns.runs.length}
                    </span>
                  ) : null}
                </button>
                <button
                  type="button"
                  onClick={() => setReportOpen(true)}
                  title="Open your reports. 'Add to report' clicks pin blocks to whichever report is shown here."
                  className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-default px-3 py-1 text-xs font-medium text-text-secondary transition hover:border-primary/40 hover:text-primary"
                >
                  <FileText size={12} />
                  <span>Adding to:</span>
                  <span className="max-w-[140px] truncate font-semibold text-primary">
                    {report.activeReport?.title || " - "}
                  </span>
                  {report.activeReport?.items?.length ? (
                    <span className="ml-0.5 rounded-full bg-primary/15 px-1.5 text-[10px] font-semibold text-primary">
                      {report.activeReport.items.length}
                    </span>
                  ) : null}
                </button>
              </div>
            ) : null}
            <div
              ref={inputBoxRef}
              className={`w-full shadow-[0px_9px_9px_0px_rgba(0,0,0,0.01)] ${
                inputFocused ? "playground-gradient-border" : "rounded-2xl border-2 border-border"
              }`}
              onFocusCapture={() => setInputFocused(true)}
              onBlurCapture={(e) => {
                if (!inputBoxRef.current?.contains(e.relatedTarget)) setInputFocused(false);
              }}
            >
              <div className="flex flex-col justify-between rounded-[14px] bg-surface px-4 py-4 sm:px-[18px]">
                <PendingAttachmentChips
                  staged={attachments.staged}
                  onRemove={attachments.removeStaged}
                />
                <ChatComposerTextarea
                  value={message}
                  overlayValue={
                    dictation.listening && dictation.interim
                      ? `${message ? `${message.trimEnd()} ` : ""}${dictation.interim}`
                      : undefined
                  }
                  onChange={setMessage}
                  onSend={() => void handleSend()}
                  placeholder={chatPlaceholder}
                  ariaLabel={chatPlaceholder}
                />
                <div className="mt-2">
                  <PlaygroundModePicker profileId={profileId} />
                </div>
                <MicErrorMessage error={dictation.error} />
                {attachments.lastError ? (
                  <p className="mt-1 text-xs text-red-600" role="status">
                    {attachments.lastError}
                  </p>
                ) : null}
                <PlaygroundHubComposerOptions
                  attachments={attachments}
                  dictation={dictation}
                  onSend={() => void handleSend()}
                />
              </div>
            </div>
            <p className="mx-auto mt-2 max-w-[530px] text-center text-xs text-text-muted">
              AI-generated insights are based on connected data sources. Critical business decisions should be verified by domain experts.
            </p>
          </div>
        </div>
      </div>

      {isMiddleby ? (
        <>
          <SavedPromptsDrawer
            open={savedOpen}
            onClose={() => setSavedOpen(false)}
            prompts={savedPrompts.prompts}
            onUse={(p) => {
              setSavedOpen(false);
              savedPrompts.markUsed(p.id);
              void sendPrompt(p.text);
            }}
            onRemove={savedPrompts.removePrompt}
            onUpdateText={savedPrompts.updateText}
          />
          <ScheduledRunsPanel
            open={scheduledOpen}
            onClose={() => setScheduledOpen(false)}
            runs={scheduledRuns.runs}
            onPause={scheduledRuns.togglePause}
            onRemove={scheduledRuns.removeRun}
            profileId={profileId}
          />
          <ScheduleMessageModal
            open={Boolean(scheduleTarget)}
            promptText={scheduleTarget || ""}
            onClose={() => setScheduleTarget(null)}
            onSchedule={(payload) => {
              scheduledRuns.scheduleRun(payload);
              setScheduleTarget(null);
              setScheduledOpen(true);
            }}
          />
          <ReportDrawer
            open={reportOpen}
            onClose={() => setReportOpen(false)}
            reports={report.reports}
            activeReportId={report.activeReportId}
            onSetActive={report.setActive}
            onCreateReport={report.createReport}
            onRenameReport={report.renameReport}
            onDeleteReport={report.deleteReport}
            onUnpin={report.unpin}
            onClear={report.clear}
          />
        </>
      ) : null}
    </div>
  );
}
