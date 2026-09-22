import { useLayoutEffect, useRef, useState } from "react";

/**
 * Reveals finished text progressively, the way a streaming assistant reads.
 *
 * The answer arrives whole from the backend, so nothing here is streamed: this is the
 * presentation of it. Revealing by characters-per-frame rather than a timer per character
 * keeps long answers from taking minutes, and ties the pace to the display's refresh rate
 * instead of to how busy the main thread is.
 *
 * `enabled` typically flips to true one render AFTER the text arrives, because the page
 * decides what is "new" in an effect. The reset therefore runs in a layout effect and the
 * phase starts as "running": with a plain effect the first painted frame showed the whole
 * text, and `done` was briefly true, which told callers the reveal had finished before it
 * had begun.
 *
 * Honours `prefers-reduced-motion`, where the text simply appears.
 */
export function useTypewriter(text, { enabled = true, charsPerSecond = 160, maxSeconds = 4, onDone } = {}) {
  const full = String(text || "");
  const [shown, setShown] = useState(() => (enabled ? "" : full));
  const [phase, setPhase] = useState(() => (enabled && full ? "running" : "done"));
  const frameRef = useRef(0);
  // Held in a ref so a caller passing an inline function cannot restart the reveal.
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useLayoutEffect(() => {
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    if (!enabled || reduced || !full) {
      setShown(full);
      setPhase("done");
      onDoneRef.current?.();
      return undefined;
    }

    setShown("");
    setPhase("running");
    let start = null;
    // Short answers read as typed; long ones speed up so a wall of text never takes longer
    // than `maxSeconds` to land.
    const pace = Math.max(charsPerSecond, full.length / maxSeconds);
    const step = (ts) => {
      if (start === null) start = ts;
      const elapsed = (ts - start) / 1000;
      const count = Math.min(full.length, Math.ceil(elapsed * pace));
      setShown(full.slice(0, count));
      if (count < full.length) {
        frameRef.current = requestAnimationFrame(step);
        return;
      }
      setPhase("done");
      onDoneRef.current?.();
    };
    frameRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frameRef.current);
  }, [full, enabled, charsPerSecond, maxSeconds]);

  return { shown, done: phase === "done" };
}
