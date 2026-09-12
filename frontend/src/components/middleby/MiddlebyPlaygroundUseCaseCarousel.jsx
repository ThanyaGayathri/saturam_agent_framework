import { useCallback, useRef } from "react";
import { ArrowUpLeft, BarChart3, TrendingUp, Users, Zap } from "lucide-react";
import { useTheme } from "../../context/ThemeContext";
import { FEATURED_IMPACT } from "../../domain/featured-use-cases";

const CARD_ICON_GRADIENT =
  "linear-gradient(to left, rgba(49,188,255,0.15), rgba(150,118,255,0.15) 10%, rgba(190,100,254,0.15) 25%, rgba(239,87,148,0.15) 50%, rgba(253,104,63,0.15) 75%, rgba(255,161,11,0.15) 100%)";

const CARD_ICON_GRADIENT_DARK =
  "linear-gradient(145deg, rgba(139,92,246,0.26) 0%, rgba(99,102,241,0.17) 42%, rgba(22,25,34,0.94) 100%)";

const ICONS = [BarChart3, TrendingUp, Users, Zap];

function deckIcon(index) {
  return ICONS[index % ICONS.length];
}

/** Same sample question as the analytics catalog card (one per use case). */
function hubQuestionForEntry(entry) {
  const text = String(entry?.sampleQuestion || "").trim();
  return text || null;
}

/**
 * Scrolling priority use-case decks (retail Playground hub pattern).
 * Clicks pre-fill the hub composer; they do not navigate away.
 */
export default function MiddlebyPlaygroundUseCaseCarousel({ useCases, onPrefill }) {
  const { colorMode } = useTheme();
  const exploreGridRef = useRef(null);
  const useCasesRef = useRef(useCases);
  useCasesRef.current = useCases;

  const commitPrefill = useCallback(
    (text, catalogId) => {
      const question = String(text || "").trim();
      if (!question) return;
      onPrefill?.({ text: question, catalogId });
    },
    [onPrefill]
  );

  const onExploreSectionClick = useCallback(
    (e) => {
      const el = e.target.closest("[data-explore-deck]");
      if (!el || !exploreGridRef.current?.contains(el)) return;
      const deckIndex = Number(el.getAttribute("data-explore-deck"), 10);
      if (!Number.isInteger(deckIndex)) return;
      const ucs = useCasesRef.current;
      if (deckIndex < 0 || deckIndex >= ucs.length) return;
      const uc = ucs[deckIndex];
      const question = hubQuestionForEntry(uc);
      if (!question) return;
      commitPrefill(question, uc.id);
    },
    [commitPrefill]
  );

  if (!useCases?.length) return null;

  return (
    <div className="w-full">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 px-4 sm:px-0">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-medium text-text-primary sm:text-lg">Priority use cases</h2>
          <span className="rounded-full border border-primary/22 bg-primary-light px-2 py-0.5 text-[11px] font-semibold text-primary dark:border-white/20 dark:bg-white/[0.1]">
            Demo picks
          </span>
        </div>
        <p className="text-xs text-text-muted">Click a question to fill the box above</p>
      </div>

      <div className="use-case-carousel-outer relative z-10 mt-4 w-full overflow-hidden pb-4">
        <div
          ref={exploreGridRef}
          className="use-case-carousel flex w-max gap-6 py-2 pl-4 sm:pl-8"
          onClick={onExploreSectionClick}
          role="presentation"
        >
          {[0, 1].map((setIdx) =>
            useCases.map((uc, deckIndex) => {
              const question = hubQuestionForEntry(uc);
              const Icon = deckIcon(deckIndex);
              const subtitle = FEATURED_IMPACT[uc.id] || uc.problemSignal || "";
              return (
                <div
                  key={`${setIdx}-${uc.id}`}
                  aria-hidden={setIdx === 1 ? "true" : undefined}
                  className="flex w-[320px] shrink-0 flex-col justify-between rounded-2xl border border-border bg-surface p-5 transition-all duration-300 ease-out hover:-translate-y-1 hover:border-primary/40 hover:shadow-[0_8px_24px_rgba(105,65,198,0.12)] dark:bg-surface-default sm:w-[340px] sm:p-6"
                >
                  <div>
                    <div
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[10px] border border-[rgba(0,0,0,0.06)] dark:border-border/70"
                      style={{
                        background: colorMode === "dark" ? CARD_ICON_GRADIENT_DARK : CARD_ICON_GRADIENT,
                      }}
                    >
                      <Icon size={20} className="text-primary" />
                    </div>
                    <h3 className="mt-3 text-base font-semibold text-text-primary sm:text-lg">{uc.title}</h3>
                    <p className="mt-1 line-clamp-2 text-sm text-text-secondary">{subtitle}</p>
                    {question ? (
                      <div className="group/query relative mt-3 rounded-lg transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[0_6px_18px_rgba(105,65,198,0.1)]">
                        <button
                          type="button"
                          data-explore-deck={String(deckIndex)}
                          className="flex w-full items-start rounded-lg border border-border bg-surface-secondary px-3 py-2.5 pb-9 pr-10 text-left text-sm text-text-secondary transition-colors group-hover/query:border-primary/35 group-hover/query:bg-primary-light/50 group-hover/query:text-text-primary dark:bg-surface-tertiary"
                        >
                          <span className="min-w-0 flex-1 leading-snug line-clamp-4">{question}</span>
                        </button>
                        <button
                          type="button"
                          className="absolute bottom-2 right-3 rounded p-0.5 text-text-muted transition hover:text-primary"
                          aria-label="Fill question in chat box"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            commitPrefill(question, uc.id);
                          }}
                        >
                          <ArrowUpLeft size={16} strokeWidth={2} aria-hidden />
                        </button>
                      </div>
                    ) : null}
                  </div>
                  {uc.automationReady ? (
                    <p className="mt-4 text-xs font-medium leading-relaxed text-primary line-clamp-3">
                      {uc.automationLine
                        ? `Example workflow: ${uc.automationLine}`
                        : "Automation available on this use case"}
                    </p>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
