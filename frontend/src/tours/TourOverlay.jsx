import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, ArrowRight, Captions, Check, Pause, Play, Volume2, VolumeX, X } from "lucide-react";
import { useTour } from "./TourContext";
import { listUsableVoices } from "./useSpeechQueue";

/**
 * Guided tour overlay.
 *
 * When a tour is active, renders:
 *   - A full-screen dimming backdrop with a "hole" cut out around the current
 *     step's anchor (uses an SVG mask).
 *   - A caption + controls dock at the bottom-right (or centered if there is
 *     no anchor for the current step).
 *   - A minimal spotlight ring drawn around the anchor.
 *
 * Everything else lives in TourContext. This component is purely visual.
 */

const DOCK_WIDTH = 360;

function useAnchorRect(el) {
  const [rect, setRect] = useState(null);
  useEffect(() => {
    if (!el) {
      setRect(null);
      return undefined;
    }
    const update = () => {
      const r = el.getBoundingClientRect();
      setRect({ x: r.left, y: r.top, w: r.width, h: r.height });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    return () => {
      ro.disconnect();
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, [el]);
  return rect;
}

function ControlButton({ onClick, disabled, label, children, tone = "default" }) {
  const toneCls =
    tone === "primary"
      ? "bg-primary text-white hover:bg-primary-hover"
      : tone === "danger"
      ? "text-status-danger hover:bg-status-danger-light"
      : "text-text-secondary hover:bg-surface-secondary hover:text-text-primary";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={`inline-flex h-9 min-w-[36px] items-center justify-center gap-1 rounded-lg px-2 text-sm font-semibold transition ${toneCls} disabled:cursor-not-allowed disabled:opacity-40`}
    >
      {children}
    </button>
  );
}

export default function TourOverlay() {
  const {
    activeTour,
    currentStep,
    stepIndex,
    totalSteps,
    playing,
    stopTour,
    next,
    prev,
    pause,
    resume,
    replayCurrentStep,
    anchorEl,
    captionText,
    captionsOn,
    setCaptionsOn,
    speech,
  } = useTour();

  const rect = useAnchorRect(anchorEl);

  const progressPct = totalSteps > 0 ? Math.round(((stepIndex + 1) / totalSteps) * 100) : 0;

  // Compute a padded hole around the anchor. Falls back to no hole (full dim)
  // when there's no anchor for this step.
  const hole = useMemo(() => {
    if (!rect) return null;
    const pad = 8;
    return {
      x: Math.max(0, rect.x - pad),
      y: Math.max(0, rect.y - pad),
      w: rect.w + pad * 2,
      h: rect.h + pad * 2,
    };
  }, [rect]);

  if (!activeTour || !currentStep) return null;

  const dock = (
    <div
      className="pointer-events-auto fixed bottom-6 right-6 z-[1001] w-full max-w-[360px] rounded-2xl border border-border bg-surface p-4 shadow-2xl"
      style={{ width: DOCK_WIDTH }}
    >
      {/* Progress */}
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[10px] font-semibold uppercase tracking-wide text-text-muted">
            {activeTour.title} · Step {stepIndex + 1} of {totalSteps}
          </p>
          <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-surface-secondary">
            <div
              className="h-full rounded-full bg-primary transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
        <ControlButton onClick={() => stopTour()} label="End tour" tone="default">
          <X size={16} strokeWidth={2} />
        </ControlButton>
      </div>

      {/* Caption */}
      {captionsOn && captionText ? (
        <p className="mb-3 max-h-32 overflow-y-auto text-sm leading-relaxed text-text-primary">
          {captionText}
        </p>
      ) : null}

      {/* Voice picker */}
      {speech.supported ? (
        <div className="mb-3 flex items-center gap-2">
          <label className="text-[11px] font-medium text-text-muted" htmlFor="tour-voice-select">
            Voice
          </label>
          {speech.usingCloud ? (
            <select
              id="tour-voice-select"
              value={speech.cloudVoice}
              onChange={(e) => {
                speech.setCloudVoice(e.target.value);
                // Replay the current step immediately so the new voice
                // is heard right away, not on the next step.
                replayCurrentStep?.();
              }}
              className="min-w-0 flex-1 truncate rounded-md border border-border bg-surface px-2 py-1 text-xs text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30"
            >
              {speech.cloudVoices.map((v) => {
                const value = v.id || v.name || v;
                const label = v.name || v;
                const parts = [];
                if (v.labels?.accent) parts.push(v.labels.accent);
                if (v.labels?.gender) parts.push(v.labels.gender);
                if (v.labels?.age) parts.push(v.labels.age);
                const meta = parts.length ? ` (${parts.join(", ")})` : "";
                return (
                  <option key={value} value={value}>
                    {label}
                    {meta}
                  </option>
                );
              })}
            </select>
          ) : speech.voices?.length > 1 ? (
            <select
              id="tour-voice-select"
              value={speech.voice?.name || ""}
              onChange={(e) => {
                const next = speech.voices.find((v) => v.name === e.target.value);
                if (next) speech.setVoice(next);
                replayCurrentStep?.();
              }}
              className="min-w-0 flex-1 truncate rounded-md border border-border bg-surface px-2 py-1 text-xs text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30"
            >
              {listUsableVoices(speech.voices).map((v) => (
                <option key={v.name} value={v.name}>
                  {v.name}
                </option>
              ))}
            </select>
          ) : null}
        </div>
      ) : null}

      {/* Controls row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <ControlButton onClick={prev} disabled={stepIndex === 0} label="Previous step">
            <ArrowLeft size={16} strokeWidth={2} />
          </ControlButton>
          {playing ? (
            <ControlButton onClick={pause} label="Pause">
              <Pause size={16} strokeWidth={2} />
            </ControlButton>
          ) : (
            <ControlButton onClick={resume} label="Resume" tone="primary">
              <Play size={16} strokeWidth={2} />
            </ControlButton>
          )}
          {stepIndex === totalSteps - 1 ? (
            <ControlButton onClick={next} label="Finish tour" tone="primary">
              <Check size={16} strokeWidth={2} />
              <span className="text-xs font-semibold">Done</span>
            </ControlButton>
          ) : (
            <ControlButton onClick={next} label="Next step">
              <ArrowRight size={16} strokeWidth={2} />
            </ControlButton>
          )}
        </div>
        <div className="flex items-center gap-2">
          <label
            className="flex items-center gap-1.5 text-[11px] font-medium text-text-secondary"
            title="Toggle on-screen captions"
          >
            <input
              type="checkbox"
              checked={captionsOn}
              onChange={(e) => setCaptionsOn(e.target.checked)}
              className="h-3 w-3 rounded border-border text-primary focus:ring-primary"
            />
            <Captions size={13} strokeWidth={2} aria-hidden />
            Captions
          </label>
          {speech.supported ? (
            <ControlButton
              onClick={() => speech.setMuted(!speech.muted)}
              label={speech.muted ? "Unmute voice" : "Mute voice"}
            >
              {speech.muted ? <VolumeX size={16} strokeWidth={2} /> : <Volume2 size={16} strokeWidth={2} />}
            </ControlButton>
          ) : null}
        </div>
      </div>
    </div>
  );

  return createPortal(
    <div aria-live="polite" role="dialog" aria-label={activeTour.title}>
      {/* SVG mask: dim the whole viewport except the highlighted anchor */}
      <svg
        className="pointer-events-none fixed inset-0 z-[1000] h-full w-full"
        preserveAspectRatio="none"
      >
        <defs>
          <mask id="tour-hole-mask">
            <rect width="100%" height="100%" fill="white" />
            {hole ? (
              <rect
                x={hole.x}
                y={hole.y}
                width={hole.w}
                height={hole.h}
                rx="12"
                ry="12"
                fill="black"
              />
            ) : null}
          </mask>
        </defs>
        <rect
          width="100%"
          height="100%"
          fill="rgba(15, 23, 42, 0.55)"
          mask="url(#tour-hole-mask)"
        />
        {hole ? (
          <rect
            x={hole.x}
            y={hole.y}
            width={hole.w}
            height={hole.h}
            rx="12"
            ry="12"
            fill="none"
            stroke="#7f56d9"
            strokeWidth="2"
            className="animate-pulse"
          />
        ) : null}
      </svg>
      {dock}
    </div>,
    document.body
  );
}
