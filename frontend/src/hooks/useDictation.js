// Web Speech API wrapper for voice dictation.
//
// Cross-platform coverage:
//   - Windows: Chrome and Edge expose `webkitSpeechRecognition`.
//   - iOS / iPadOS: Safari (and Chrome/Edge on iOS, which wrap WebKit) expose
//     `webkitSpeechRecognition` starting in iOS 14.5.
//   - Firefox on any platform: no support; `supported` is false and the caller
//     should hide or disable the mic button.
//
// iOS quirks handled here:
//   - Safari stops recognition after ~10s of silence. If the user is still
//     holding the session open (wantListeningRef), we auto-restart.
//   - Permission prompt only appears after a user gesture, so `start()` must
//     be triggered from a click/tap handler.

import { useCallback, useEffect, useRef, useState } from "react";

function getSpeechRecognitionCtor() {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export function isDictationSupported() {
  return !!getSpeechRecognitionCtor();
}

/**
 * @param {object} [options]
 * @param {(text: string) => void} [options.onFinalText] Called with each final
 *   transcript segment (trimmed). Fires multiple times during a single session.
 * @param {string} [options.lang] BCP-47 language tag (default "en-US").
 */
export function useDictation({ onFinalText, lang = "en-US" } = {}) {
  const Ctor = getSpeechRecognitionCtor();
  const supported = !!Ctor;

  const recRef = useRef(null);
  const wantListeningRef = useRef(false);
  const onFinalTextRef = useRef(onFinalText);

  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState(null);

  useEffect(() => {
    onFinalTextRef.current = onFinalText;
  }, [onFinalText]);

  useEffect(() => {
    if (!Ctor) return undefined;

    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = lang;

    rec.onresult = (event) => {
      let interimText = "";
      let finalText = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const r = event.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interimText += r[0].transcript;
      }
      if (finalText) {
        setInterim("");
        const trimmed = finalText.trim();
        if (trimmed && typeof onFinalTextRef.current === "function") {
          onFinalTextRef.current(trimmed);
        }
      } else {
        setInterim(interimText);
      }
    };

    rec.onerror = (e) => {
      const code = e?.error || "speech-error";
      setError(code);
      if (code === "not-allowed" || code === "service-not-allowed") {
        wantListeningRef.current = false;
      }
    };

    rec.onend = () => {
      if (wantListeningRef.current) {
        try {
          rec.start();
          return;
        } catch {
          // fall through to idle state
        }
      }
      setListening(false);
      setInterim("");
    };

    recRef.current = rec;
    return () => {
      wantListeningRef.current = false;
      try {
        rec.onresult = null;
        rec.onerror = null;
        rec.onend = null;
        rec.stop();
      } catch {
        // ignore
      }
      recRef.current = null;
    };
  }, [Ctor, lang]);

  const start = useCallback(() => {
    const rec = recRef.current;
    if (!rec) return;
    setError(null);
    wantListeningRef.current = true;
    try {
      rec.start();
      setListening(true);
    } catch {
      setError("start-failed");
    }
  }, []);

  const stop = useCallback(() => {
    const rec = recRef.current;
    if (!rec) return;
    wantListeningRef.current = false;
    try {
      rec.stop();
    } catch {
      // ignore
    }
    setListening(false);
  }, []);

  const toggle = useCallback(() => {
    if (listening) stop();
    else start();
  }, [listening, start, stop]);

  return { supported, listening, interim, error, start, stop, toggle };
}
