import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * Speech pipeline for the guided tour.
 *
 * Two backends, tried in order:
 *   1. Cloud TTS via /api/llm/tts (Azure OpenAI). Voices: nova, alloy,
 *      echo, fable, onyx, shimmer. Genuinely human-sounding.
 *   2. Web Speech (browser SpeechSynthesis). Fallback when the cloud
 *      endpoint is not configured or returns an error.
 *
 * Behaviour is identical to the caller: `speak(lines)` returns a Promise
 * that resolves when the queue drains. `cancelAll()` stops immediately.
 * `muted` skips everything and resolves right away.
 *
 * Cloud audio is cached in-memory keyed by `voice + text` so re-plays are
 * instant and don't hit the API again.
 */

// ─── Cloud TTS (Azure OpenAI via /api/llm/tts) ────────────────────────

const CLOUD_CACHE = new Map(); // key -> Blob URL
const CLOUD_ENDPOINT = "/api/llm/tts";
const CLOUD_VOICES_ENDPOINT = "/api/llm/tts/voices";

// Detected on first call. `null` while unknown, `true`/`false` once the
// endpoint has answered at least once. Also records which provider the
// server picked so the client can show the right voice list.
let cloudAvailable = null;
let cloudProvider = null; // 'elevenlabs' | 'azure' | null

// Fallback voice lists per provider, used before /voices returns.
const FALLBACK_PROVIDER_VOICES = {
  elevenlabs: [
    { name: "Jessica", id: "Jessica", labels: {} },
    { name: "Bella", id: "Bella", labels: {} },
    { name: "Rachel", id: "Rachel", labels: {} },
  ],
  azure: [
    { name: "nova", id: "nova", labels: { gender: "female" } },
  ],
};

async function fetchCloudLine(text, voice, speed) {
  const key = `${voice}:${speed}:${text}`;
  if (CLOUD_CACHE.has(key)) return CLOUD_CACHE.get(key);
  const res = await fetch(CLOUD_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice, speed }),
    credentials: "include",
  });
  if (!res.ok) {
    cloudAvailable = false;
    throw new Error(`tts endpoint returned ${res.status}`);
  }
  cloudAvailable = true;
  const providerHeader = res.headers.get("X-TTS-Provider");
  if (providerHeader) cloudProvider = providerHeader;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  CLOUD_CACHE.set(key, url);
  return url;
}

// Track every active audio element so we can hard-stop all of them when
// the user cancels or switches voice mid-play. A single ref isn't enough
// because the old promise chain can still be executing when a new one
// starts, leading to overlapping voices.
const activeAudioSet = new Set();

function stopCloudAudio() {
  for (const audio of activeAudioSet) {
    try {
      audio.pause();
      audio.currentTime = 0;
      // Detach handlers so late onended events don't resolve stale promises.
      audio.onended = null;
      audio.onerror = null;
      audio.src = "";
    } catch {
      /* ignore */
    }
  }
  activeAudioSet.clear();
}

function playCloudLine(url) {
  return new Promise((resolve, reject) => {
    const audio = new Audio(url);
    let settled = false;
    const done = (fn) => (arg) => {
      if (settled) return;
      settled = true;
      activeAudioSet.delete(audio);
      fn(arg);
    };
    audio.onended = done(() => resolve());
    audio.onerror = done(() => {
      const err = audio.error;
      reject(new Error(`HTMLAudioElement error code=${err?.code} msg=${err?.message}`));
    });
    activeAudioSet.add(audio);
    const p = audio.play();
    if (p && typeof p.then === "function") {
      p.catch(done((e) => reject(new Error(`audio.play() rejected: ${e.name}: ${e.message}`))));
    }
  });
}

// Tiny silent WAV (44 bytes) as a data URI. Used to prime the browser's
// autoplay policy. Playing this synchronously inside a click handler
// unlocks HTMLAudioElement for the rest of the session so subsequent
// audio.play() calls after async fetches don't get NotAllowedError.
const SILENT_WAV =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";

let audioPrimed = false;

/**
 * Called from inside a user gesture (e.g. the "Start guided tour" click).
 * Plays a silent audio element to unlock the autoplay policy for the tab.
 */
export function primeCloudAudio() {
  if (audioPrimed) return;
  try {
    const primer = new Audio(SILENT_WAV);
    primer.volume = 0;
    const p = primer.play();
    if (p && typeof p.then === "function") {
      p.then(() => {
        audioPrimed = true;
      }).catch(() => {
        /* browser rejected — first line will surface the error */
      });
    } else {
      audioPrimed = true;
    }
  } catch {
    /* ignore */
  }
}

// ─── Web Speech fallback ──────────────────────────────────────────────

// Preferred voice list, ranked best-to-worst. Robotic Microsoft SAPI
// defaults (David / Zira / Mark) are blacklisted.
const PREFERRED_VOICE_NAMES = [
  "Microsoft Ava Online (Natural) - English (United States)",
  "Microsoft Emma Online (Natural) - English (United States)",
  "Microsoft Aria Online (Natural) - English (United States)",
  "Microsoft Jenny Online (Natural) - English (United States)",
  "Microsoft Andrew Online (Natural) - English (United States)",
  "Microsoft Nova Online (Natural) - English (United States)",
  "Microsoft Ava Online (Natural)",
  "Microsoft Aria Online (Natural)",
  "Microsoft Jenny Online (Natural)",
  "Samantha",
  "Ava (Enhanced)",
  "Ava (Premium)",
  "Allison",
  "Allison (Premium)",
  "Serena (Premium)",
  "Karen (Premium)",
  "Google US English",
  "Google UK English Female",
];

const BLACKLIST_PATTERNS = [
  /Microsoft David/i,
  /Microsoft Zira/i,
  /Microsoft Mark/i,
  /Microsoft Hazel/i,
  /Microsoft Susan/i,
  /Microsoft Heera/i,
  /Microsoft Ravi/i,
  /eSpeak/i,
];

function isBlacklisted(name) {
  return BLACKLIST_PATTERNS.some((rx) => rx.test(name || ""));
}

function qualityScore(v) {
  const n = (v?.name || "").toLowerCase();
  let s = 0;
  if (/natural|neural/.test(n)) s += 10;
  if (/online/.test(n)) s += 6;
  if (/premium|enhanced|deluxe/.test(n)) s += 5;
  if (/google/.test(n)) s += 3;
  if (/female|samantha|ava|aria|jenny|allison|emma|serena|karen/.test(n)) s += 2;
  if (isBlacklisted(v?.name)) s -= 100;
  return s;
}

function pickBrowserVoice(voices) {
  if (!voices?.length) return null;
  const eng = voices.filter((v) => (v.lang || "").toLowerCase().startsWith("en"));
  const pool = (eng.length > 0 ? eng : voices).filter((v) => !isBlacklisted(v.name));
  for (const wanted of PREFERRED_VOICE_NAMES) {
    const hit = pool.find((v) => v.name === wanted);
    if (hit) return hit;
  }
  return [...pool].sort((a, b) => qualityScore(b) - qualityScore(a))[0] || voices[0] || null;
}

export function listUsableVoices(voices) {
  if (!voices?.length) return [];
  return voices
    .filter((v) => (v.lang || "").toLowerCase().startsWith("en"))
    .filter((v) => !isBlacklisted(v.name))
    .sort((a, b) => qualityScore(b) - qualityScore(a));
}

// ─── Hook ─────────────────────────────────────────────────────────────

export function useSpeechQueue() {
  const synthRef = useRef(typeof window !== "undefined" ? window.speechSynthesis : null);
  const supported = Boolean(synthRef.current);
  const currentUtteranceRef = useRef(null);
  const resolveRef = useRef(null);
  const rejectRef = useRef(null);
  const [voices, setVoices] = useState([]);
  const [voice, setVoice] = useState(null);
  // Cloud voice defaults to Jessica. Actual voice list updates once the
  // first fetchCloudLine call reveals which provider is active on the server.
  const [cloudVoice, setCloudVoice] = useState("Jessica");
  const [muted, setMutedState] = useState(false);
  const [usingCloud, setUsingCloud] = useState(cloudAvailable === true);
  const [providerName, setProviderName] = useState(cloudProvider);
  // Full list of voices available on the active provider (fetched from
  // /api/llm/tts/voices on mount). Includes any voices the user added to
  // their ElevenLabs VoiceLab. Populated after the first fetch resolves.
  const [availableVoices, setAvailableVoices] = useState([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(CLOUD_VOICES_ENDPOINT, { credentials: "include" });
        // Read the body even on 502 — the server still tells us which
        // provider is configured even if the voices list call itself
        // failed (e.g. ElevenLabs /v1/voices is restricted for the key
        // but /v1/text-to-speech works).
        let data = null;
        try {
          data = await res.json();
        } catch {
          /* not JSON — leave data null */
        }
        if (cancelled) return;

        // If the server said a provider is configured, cloud TTS is
        // available. Flip usingCloud even if the voices list is empty,
        // so the picker doesn't fall back to browser voices when cloud
        // audio is actually working.
        if (data?.provider) {
          cloudProvider = data.provider;
          setProviderName(data.provider);
          setUsingCloud(true);
          cloudAvailable = true;
        }

        if (data?.voices?.length) {
          setAvailableVoices(data.voices);
          // Prefer Jessica by name if the account has her. Substring match so
          // variants like "Jessica Anne" or "jessica_v2" still hit. Falls
          // through to a female American voice, then any female voice, then
          // first available.
          const norm = (v) => (v || "").toString().toLowerCase();
          const preferred =
            data.voices.find(
              (v) => norm(v.name).includes("jessica") || norm(v.id).includes("jessica")
            ) ||
            data.voices.find(
              (v) =>
                norm(v.labels?.gender) === "female" &&
                norm(v.labels?.accent).includes("american")
            ) ||
            data.voices.find((v) => norm(v.labels?.gender) === "female") ||
            data.voices[0];
          if (preferred?.id) setCloudVoice(preferred.id);
        }
      } catch {
        /* endpoint not reachable — fall back to browser voice */
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!supported) return undefined;
    const load = () => setVoices(synthRef.current.getVoices() || []);
    load();
    synthRef.current.addEventListener?.("voiceschanged", load);
    return () => synthRef.current.removeEventListener?.("voiceschanged", load);
  }, [supported]);

  useEffect(() => {
    if (!voice && voices.length > 0) setVoice(pickBrowserVoice(voices));
  }, [voices, voice]);

  const cancelAll = useCallback(() => {
    stopCloudAudio();
    if (synthRef.current) synthRef.current.cancel();
    currentUtteranceRef.current = null;
    if (rejectRef.current) rejectRef.current(new Error("cancelled"));
    resolveRef.current = null;
    rejectRef.current = null;
  }, []);

  const setMuted = useCallback(
    (next) => {
      setMutedState(next);
      if (next) cancelAll();
    },
    [cancelAll]
  );

  const speak = useCallback(
    (lines) => {
      if (muted || !lines?.length) return Promise.resolve();
      cancelAll();
      return new Promise((resolve, reject) => {
        resolveRef.current = resolve;
        rejectRef.current = reject;

        const runCloud = async () => {
          for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            const speed = line.rate || 1.0;
            console.log(`[tour-tts] line ${i + 1}/${lines.length} fetching, voice=${cloudVoice}`);
            const buffer = await fetchCloudLine(line.text, cloudVoice, speed);
            if (!resolveRef.current) return; // cancelled
            console.log(`[tour-tts] line ${i + 1} got ${buffer.byteLength} bytes; playing`);
            if (cloudProvider && cloudProvider !== providerName) {
              setProviderName(cloudProvider);
            }
            if (!usingCloud) setUsingCloud(true);
            await playCloudLine(buffer);
            console.log(`[tour-tts] line ${i + 1} done`);
            const gap = typeof line.pauseAfter === "number" ? line.pauseAfter : 220;
            await new Promise((r) => window.setTimeout(r, gap));
            if (!resolveRef.current) return;
          }
          resolveRef.current = null;
          rejectRef.current = null;
          resolve();
        };

        const runBrowser = (i) => {
          if (i >= lines.length) {
            currentUtteranceRef.current = null;
            resolveRef.current = null;
            rejectRef.current = null;
            resolve();
            return;
          }
          const line = lines[i];
          const utter = new SpeechSynthesisUtterance(line.text);
          if (voice) utter.voice = voice;
          utter.rate = typeof line.rate === "number" ? line.rate : 1.0;
          utter.pitch = typeof line.pitch === "number" ? line.pitch : 1.0;
          utter.volume = 1;
          utter.onend = () => {
            currentUtteranceRef.current = null;
            const gap = typeof line.pauseAfter === "number" ? line.pauseAfter : 220;
            window.setTimeout(() => runBrowser(i + 1), gap);
          };
          utter.onerror = (e) => {
            if (e?.error !== "canceled") {
              console.warn("[useSpeechQueue] utterance error", e);
            }
          };
          currentUtteranceRef.current = utter;
          synthRef.current.speak(utter);
        };

        // Try cloud first; on failure, fall back to browser speech FOR THIS
        // LINE ONLY. Do NOT flip usingCloud to false — the voice picker
        // relies on that flag to show the cloud voice list, and one
        // transient audio-play failure (autoplay policy, network hiccup)
        // shouldn't demote the picker back to browser voices.
        runCloud().catch((err) => {
          if (err?.message === "cancelled") return;
          console.error(
            "%c[tour-tts] cloud playback FAILED — falling back to browser voice",
            "color: red; font-weight: bold",
            err
          );
          if (supported) {
            runBrowser(0);
          } else {
            resolveRef.current = null;
            rejectRef.current = null;
            resolve();
          }
        });
      }).catch((err) => {
        if (err?.message !== "cancelled") throw err;
      });
    },
    [muted, cloudVoice, voice, supported, cancelAll]
  );

  useEffect(() => cancelAll, [cancelAll]);

  const cloudSupportedFlag = useMemo(() => cloudAvailable !== false, []);

  return {
    // Overall support: cloud OR browser
    supported: cloudSupportedFlag || supported,
    // Browser-specific fields (kept for the voice picker in the overlay)
    voices,
    voice,
    setVoice,
    // Cloud voice controls
    cloudVoice,
    setCloudVoice,
    // Full voice list from /api/llm/tts/voices (or a small fallback).
    // Objects: { name, id, labels }. Jessica is sorted to the top so she's
    // both the default selection and the first entry in the picker.
    cloudVoices: (() => {
      const list =
        availableVoices.length > 0
          ? availableVoices
          : FALLBACK_PROVIDER_VOICES[providerName || "elevenlabs"] || [];
      const norm = (v) => (v || "").toString().toLowerCase();
      const isJessica = (v) => norm(v.name).includes("jessica") || norm(v.id).includes("jessica");
      const jessica = list.filter(isJessica);
      const rest = list.filter((v) => !isJessica(v));
      return [...jessica, ...rest];
    })(),
    cloudProviderName: providerName,
    usingCloud,
    // Common
    speak,
    cancelAll,
    muted,
    setMuted,
  };
}
