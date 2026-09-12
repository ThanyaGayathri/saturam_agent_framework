import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useSpeechQueue, primeCloudAudio } from "./useSpeechQueue";
import { TOURS } from "./tours-registry";

/**
 * Tour orchestrator.
 *
 * Keeps track of which tour is active, which step we're on, playing/paused,
 * and drives the speech + navigation for each step. Emits the current step
 * to the TourOverlay component, which handles rendering.
 *
 * Persistence: completed tours are recorded in localStorage under
 * `retailgpt:tours:completed:v1`. The launcher menu reads this to badge
 * tours as "Completed".
 *
 * All tour progression is driven from here — the overlay is purely visual.
 */

const TourContext = createContext(null);
const COMPLETED_STORAGE_KEY = "retailgpt:tours:completed:v1";

function readCompleted() {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(COMPLETED_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function writeCompleted(map) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(COMPLETED_STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* quota — ignore */
  }
}

function waitForSelector(selector, timeoutMs = 3000) {
  if (!selector) return Promise.resolve(null);
  return new Promise((resolve) => {
    const t0 = Date.now();
    const tick = () => {
      const el = document.querySelector(selector);
      if (el) return resolve(el);
      if (Date.now() - t0 >= timeoutMs) return resolve(null);
      window.requestAnimationFrame(tick);
    };
    tick();
  });
}

export function TourProvider({ children }) {
  const navigate = useNavigate();
  const location = useLocation();
  const speech = useSpeechQueue();
  const [activeTourId, setActiveTourId] = useState(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [captionText, setCaptionText] = useState("");
  const [captionsOn, setCaptionsOn] = useState(true);
  const [autoAdvance, setAutoAdvance] = useState(true);
  const [anchorEl, setAnchorEl] = useState(null);
  const [completed, setCompleted] = useState(() => readCompleted());
  // Counter that increments whenever we want the current step to re-fire
  // its speech + effect, without changing stepIndex. Used by the voice
  // picker so switching voice mid-tour restarts the current line with the
  // new voice.
  const [replayNonce, setReplayNonce] = useState(0);
  const advanceTokenRef = useRef(0); // guards against stale onEnd callbacks

  const activeTour = useMemo(() => TOURS.find((t) => t.id === activeTourId) || null, [activeTourId]);
  const currentStep = activeTour?.steps?.[stepIndex] || null;

  const markCompleted = useCallback((tourId) => {
    setCompleted((prev) => {
      const next = { ...prev, [tourId]: new Date().toISOString() };
      writeCompleted(next);
      return next;
    });
  }, []);

  const stopTour = useCallback(
    (reason = "closed") => {
      speech.cancelAll();
      if (reason === "completed" && activeTourId) markCompleted(activeTourId);
      setActiveTourId(null);
      setStepIndex(0);
      setPlaying(false);
      setCaptionText("");
      setAnchorEl(null);
    },
    [speech, activeTourId, markCompleted]
  );

  const startTour = useCallback(
    (tourId, opts = {}) => {
      const tour = TOURS.find((t) => t.id === tourId);
      if (!tour) {
        console.warn("[TourContext] unknown tour id", tourId);
        return;
      }
      // Called from inside a user click. Prime the browser's audio
      // autoplay policy by playing a silent WAV synchronously here.
      // Without this, later audio.play() calls (after the async fetch
      // of cloud TTS audio) get NotAllowedError and we fall back to
      // browser voice.
      primeCloudAudio();
      speech.cancelAll();
      setActiveTourId(tourId);
      setStepIndex(opts.startIndex ?? 0);
      setPlaying(true);
    },
    [speech]
  );

  const goToStep = useCallback(
    (nextIndex) => {
      if (!activeTour) return;
      if (nextIndex < 0) return;
      if (nextIndex >= activeTour.steps.length) {
        stopTour("completed");
        return;
      }
      advanceTokenRef.current += 1;
      speech.cancelAll();
      setStepIndex(nextIndex);
      setPlaying(true);
    },
    [activeTour, speech, stopTour]
  );

  const next = useCallback(() => goToStep(stepIndex + 1), [goToStep, stepIndex]);
  const prev = useCallback(() => goToStep(stepIndex - 1), [goToStep, stepIndex]);
  const pause = useCallback(() => {
    setPlaying(false);
    speech.cancelAll();
  }, [speech]);
  const resume = useCallback(() => {
    setPlaying(true);
    // Re-speaks the current step from the start of its lines.
    advanceTokenRef.current += 1;
  }, []);

  // Effect: whenever step or playing state changes, drive that step.
  //  1) navigate ONCE if the step wants a specific route (guarded against
  //     redirect loops — if the target never settles, skip the step)
  //  2) wait for the target anchor selector (up to 3s)
  //  3) reveal the anchor + start speech
  //  4) when speech ends, optionally auto-advance
  //
  // The `attemptedRoute` ref tracks whether we've already navigated for this
  // (stepIndex, activeTourId) pair. Without it, a route guard that redirects
  // (`/processes` → `/playground` on some profiles) causes an infinite loop
  // of navigate → redirect → effect re-run → navigate again.
  const attemptedRouteRef = useRef({ tourId: null, stepIndex: -1 });
  useEffect(() => {
    if (!activeTour || !currentStep || !playing) {
      setAnchorEl(null);
      setCaptionText("");
      return;
    }
    const token = ++advanceTokenRef.current;
    const wantsRoute = currentStep.route && currentStep.route !== location.pathname;
    const alreadyAttempted =
      attemptedRouteRef.current.tourId === activeTourId &&
      attemptedRouteRef.current.stepIndex === stepIndex;
    if (wantsRoute && !alreadyAttempted) {
      attemptedRouteRef.current = { tourId: activeTourId, stepIndex };
      navigate(currentStep.route);
      // Bail and let the next effect run once location updates. If a route
      // guard bounces us somewhere else, the alreadyAttempted check below
      // stops us from re-navigating in a loop.
      return;
    }
    if (wantsRoute && alreadyAttempted) {
      // Router refused the navigation (redirected). Skip this step so the
      // tour keeps moving instead of hanging.
      console.warn(
        `[Tour] Step "${currentStep.id}" wanted ${currentStep.route} but stayed on ${location.pathname}. Skipping.`
      );
      const skipTimer = window.setTimeout(() => {
        if (token === advanceTokenRef.current) goToStep(stepIndex + 1);
      }, 300);
      return () => window.clearTimeout(skipTimer);
    }

    let cancelled = false;
    (async () => {
      // Wait for the anchor (if any) so the highlight ring lands correctly.
      const el = currentStep.anchor
        ? await waitForSelector(currentStep.anchor)
        : null;
      if (cancelled || token !== advanceTokenRef.current) return;
      // Scroll the anchor into view so the highlight isn't drawn off-screen.
      // "center" keeps the tour dock at the bottom-right from covering the
      // element. Skipped for full-page steps (no anchor).
      if (el) {
        try {
          el.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
          // Give the smooth scroll a moment to settle before measuring.
          await new Promise((r) => window.setTimeout(r, 350));
          if (cancelled || token !== advanceTokenRef.current) return;
        } catch {
          /* ignore — older browsers may not support smooth scroll options */
        }
      }
      setAnchorEl(el);

      // Caption text — first line so the reader has something to read
      // immediately, then updated as each line starts speaking.
      const lines = currentStep.lines || [];
      setCaptionText(lines[0]?.text || "");

      try {
        // If speech is muted or unsupported, just wait a beat then advance
        // so the user can still watch the tour progress.
        const speaker = speech.muted || !speech.supported
          ? new Promise((r) => window.setTimeout(r, Math.max(1500, lines.length * 1800)))
          : speech.speak(lines);
        await speaker;
      } catch (e) {
        // Speech was cancelled (user hit next/prev) — that's fine.
        return;
      }

      if (cancelled || token !== advanceTokenRef.current) return;
      if (currentStep.advance === "manual") return; // wait for user
      if (autoAdvance) {
        window.setTimeout(() => {
          if (token === advanceTokenRef.current) {
            goToStep(stepIndex + 1);
          }
        }, 400);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTourId, stepIndex, playing, location.pathname, replayNonce]);

  const replayCurrentStep = useCallback(() => {
    speech.cancelAll();
    setReplayNonce((n) => n + 1);
  }, [speech]);

  // Reset the attempted-route marker whenever the tour starts fresh or a
  // step changes, so a step re-visited later can navigate again.
  useEffect(() => {
    attemptedRouteRef.current = { tourId: null, stepIndex: -1 };
  }, [activeTourId]);

  const value = useMemo(
    () => ({
      activeTour,
      currentStep,
      stepIndex,
      totalSteps: activeTour?.steps?.length || 0,
      playing,
      startTour,
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
      autoAdvance,
      setAutoAdvance,
      completed,
      speech,
    }),
    [
      activeTour,
      currentStep,
      stepIndex,
      playing,
      startTour,
      stopTour,
      next,
      prev,
      pause,
      resume,
      replayCurrentStep,
      anchorEl,
      captionText,
      captionsOn,
      autoAdvance,
      completed,
      speech,
    ]
  );

  return <TourContext.Provider value={value}>{children}</TourContext.Provider>;
}

export function useTour() {
  const ctx = useContext(TourContext);
  if (!ctx) throw new Error("useTour must be used within TourProvider");
  return ctx;
}
