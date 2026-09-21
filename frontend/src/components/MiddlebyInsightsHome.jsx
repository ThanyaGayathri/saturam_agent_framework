import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { getMiddlebyTierAUseCases } from "../domain/featured-use-cases";
import { getMiddlebyInsightsHubPlaceholder } from "../domain/middleby-composer-hints";
import { enrichCatalogEntry } from "../domain/middleby-catalog-helpers";
import { resolveMiddlebyCatalogWithConfidence } from "../domain/middleby-playground-chat";
import { logMiddlebyTelemetry } from "../domain/middleby-demo-telemetry";
import { serializePlaygroundHubHandoff } from "../domain/playground-hub-handoff";
import { PLAYGROUND_HUB_CHAT_MESSAGE_KEY, HUB_CHAT_ATTACH_HANDOFF_KEY } from "../storage/keys";
import { useChatAttachments, stashAttachmentsForHandoff } from "../hooks/useChatAttachments";
import { useDictation } from "../hooks/useDictation";
import ChatComposerTextarea from "./shared/ChatComposerTextarea";
import PlaygroundHubComposerOptions from "./shared/PlaygroundHubComposerOptions";
import { PendingAttachmentChips } from "./shared/ChatAttachmentChips";
import { MicErrorMessage } from "./shared/MicButton";
import MiddlebyPlaygroundUseCaseCarousel from "./middleby/MiddlebyPlaygroundUseCaseCarousel";
import PeriodFilterChip from "./middleby/PeriodFilterChip";
import { useDemoProfile } from "../context/DemoProfileContext";
import { useChatFilters } from "../hooks/useChatFilters";
import TourLaunchButton from "../tours/TourLaunchButton";

/**
 * Middleby Insights home: chat-first hub (retail/GFI pattern).
 * Priority use cases pre-fill the composer; Ask opens Playground chat.
 */
export default function MiddlebyInsightsHome() {
  const navigate = useNavigate();
  const composerRef = useRef(null);
  const [query, setQuery] = useState("");
  const [hubCatalogId, setHubCatalogId] = useState(null);
  const { entries } = useUseCaseCatalog();
  const attachments = useChatAttachments();
  const { profileId } = useDemoProfile();
  const chatFilters = useChatFilters(profileId);
  const appendDictation = useCallback((text) => {
    setQuery((prev) => (prev ? `${prev.trimEnd()} ${text}` : text));
  }, []);
  const dictation = useDictation({ onFinalText: appendDictation });

  const tierAUseCases = useMemo(
    () => getMiddlebyTierAUseCases(entries).map(enrichCatalogEntry),
    [entries]
  );

  const handlePrefill = useCallback(({ text, catalogId }) => {
    setQuery(text);
    if (catalogId) setHubCatalogId(catalogId);
    window.scrollTo({ top: 0, behavior: "smooth" });
    setTimeout(() => composerRef.current?.focus(), 50);
  }, []);

  const submitFromHub = () => {
    if (dictation.listening) dictation.stop();
    const t = query.trim();
    if (!t && attachments.staged.length === 0) return;
    const question = t || "Summarize the top analytics signals for this period.";
    const routed = resolveMiddlebyCatalogWithConfidence(question);
    const catalogId =
      hubCatalogId || routed.catalogId || routed.suggestions[0]?.catalogId || tierAUseCases[0]?.id;
    logMiddlebyTelemetry({
      type: "hub_question",
      question_text: question,
      uc_id: catalogId,
      confidence: routed.confidence,
      band: routed.band,
    });
    const staged = attachments.staged;
    const hasStaged = staged.length > 0;
    // Map the persisted chat filter to the horizon/lens carried in the handoff
    // so the chat page respects whatever period the user picked on home.
    const handoffPeriod = chatFilters.period && chatFilters.period !== "custom"
      ? {
          horizon: chatFilters.period,
          lens: chatFilters.displayLabel || "",
        }
      : chatFilters.period === "custom" && chatFilters.customRangeLabel
      ? { lens: chatFilters.customRangeLabel }
      : {};
    try {
      sessionStorage.setItem(
        PLAYGROUND_HUB_CHAT_MESSAGE_KEY,
        serializePlaygroundHubHandoff({
          text: question,
          ...handoffPeriod,
          ...(catalogId ? { catalogId } : {}),
        })
      );
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
    setQuery("");
    setHubCatalogId(null);
    navigate("/playground/chat");
  };

  const hasInput = query.trim().length > 0 || attachments.staged.length > 0;

  const hubPlaceholder = useMemo(
    () => getMiddlebyInsightsHubPlaceholder("executive", tierAUseCases[0]?.id),
    [tierAUseCases]
  );

  const hubRoutePreview = useMemo(() => {
    const t = query.trim();
    if (t.length < 6) return null;
    return resolveMiddlebyCatalogWithConfidence(t);
  }, [query]);

  const showHubSuggestions =
    hubRoutePreview &&
    (hubRoutePreview.band === "low" || hubRoutePreview.band === "none") &&
    hubRoutePreview.suggestions.length > 0;

  return (
    <div className="relative flex w-full flex-col items-center overflow-x-hidden pb-16">
      <div className="relative z-10 mt-10 w-full max-w-6xl px-4 text-center sm:mt-14 sm:px-6">
        <p className="text-sm font-semibold text-primary">Middleby · Digital & RFQ analytics</p>
        <div className="mt-2 flex items-center justify-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary sm:text-3xl">
            What do you need help with?
          </h1>
          <TourLaunchButton tourId="middleby-home" label="Take a quick tour" />
        </div>
        <p className="mx-auto mt-2 max-w-xl text-sm text-text-secondary">
          Ask a question, or pick a priority use case below to fill it in.
        </p>
      </div>

      <div className="relative z-30 mt-8 w-full max-w-3xl px-4 sm:px-6" data-tour-mb-home="composer">
        <div className="playground-hub-composer-shell playground-gradient-border w-full shadow-[0px_9px_9px_0px_rgba(0,0,0,0.01)]">
          <div className="flex min-h-[124px] flex-col justify-between rounded-[14px] bg-surface px-4 py-4 sm:px-[18px]">
            <PendingAttachmentChips staged={attachments.staged} onRemove={attachments.removeStaged} />
            <ChatComposerTextarea
              ref={composerRef}
              value={query}
              onChange={setQuery}
              onSubmit={submitFromHub}
              placeholder={hubPlaceholder}
              rows={3}
            />
            <MicErrorMessage error={dictation.error} />
            <PlaygroundHubComposerOptions
              attachments={attachments}
              dictation={dictation}
              onSend={submitFromHub}
              sendButtonTitle={hasInput ? "Ask" : "Ask EnterpriseGPT"}
            />
          </div>
        </div>
        <div className="mt-3 flex justify-center">
          <PeriodFilterChip chatFilters={chatFilters} />
        </div>
        {showHubSuggestions ? (
          <div className="mt-3 rounded-lg border border-border bg-surface-secondary px-4 py-3">
            <p className="text-xs font-semibold text-text-muted">Try one of these:</p>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              {hubRoutePreview.suggestions.map((s) => (
                <button
                  key={s.catalogId}
                  type="button"
                  onClick={() =>
                    handlePrefill({
                      text: s.sampleQuestion || query.trim(),
                      catalogId: s.catalogId,
                    })
                  }
                  className="rounded-lg border border-border bg-surface-default px-3 py-2 text-left text-sm hover:border-primary/40"
                >
                  <span className="font-medium text-text-primary">{s.title}</span>
                  {s.sampleQuestion ? (
                    <span className="mt-0.5 block text-xs text-text-muted line-clamp-2">
                      {s.sampleQuestion}
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      {/* Full-bleed: the track scrolls edge to edge, so capping it here is what cut the cards
          off on both sides. The carousel keeps its own heading inside the page's gutter. */}
      <div className="relative z-10 mt-10 w-full" data-tour-mb-home="carousel">
        <MiddlebyPlaygroundUseCaseCarousel useCases={tierAUseCases} onPrefill={handlePrefill} />
      </div>

      <div className="relative z-10 mt-8 px-4 text-center" data-tour-mb-home="browse">
        <Link
          to="/analytics"
          className="inline-flex items-center gap-2 text-sm font-semibold text-primary hover:underline"
        >
          Browse all use cases
          <ArrowRight className="h-4 w-4" aria-hidden />
        </Link>
      </div>

    </div>
  );
}
