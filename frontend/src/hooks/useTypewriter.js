import { useEffect, useRef, useState } from "react";

/**
 * Reveals finished text progressively, the way a streaming assistant reads.
 *
 * The answer arrives whole from the backend, so nothing here is streamed: this is the
 * presentation of it. Revealing by characters-per-frame rather than a timer per character
 * keeps long answers from taking minutes, and ties the pace to the display's refresh rate
 * instead of to how busy the main thread is.
 *
 * It respects `prefers-reduced-motion`, where the text simply appears.
 */
export function useTypewriter(text, { enabled = true, charsPerSecond = 160, maxSeconds = 4 } = {}) {
  const full = String(text || "");
  const [shown, setShown] = useState(() => (enabled ? "" : full));
  const frameRef = useRef(0);

  useEffect(() => {
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    if (!enabled || reduced || !full) {
      setShown(full);
      return undefined;
    }

    setShown("");
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
      }
    };
    frameRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frameRef.current);
  }, [full, enabled, charsPerSecond, maxSeconds]);

  return { shown, done: shown.length >= full.length };
}
