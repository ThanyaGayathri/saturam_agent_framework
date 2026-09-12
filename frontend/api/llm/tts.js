import {
  getCookieValue,
  isPrototypeLoginConfigured,
  SESSION_COOKIE_NAME,
  verifyPrototypeSessionToken,
} from "../_lib/prototype-auth-server.js";

export const config = { runtime: "edge" };

/**
 * Server-side TTS endpoint for the guided tour.
 *
 * Provider is auto-selected by which env vars are set. Preference:
 *   1. ElevenLabs                (RETAILGPT_ELEVENLABS_API_KEY)
 *   2. Azure OpenAI TTS          (RETAILGPT_LLM_TTS_DEPLOYMENT)
 *
 * If neither is set, returns 503 and the client falls back to
 * browser Web Speech.
 *
 * Request body:
 *   { text: string, voice?: string, speed?: number }
 *
 * Response: raw audio/mpeg (MP3) bytes.
 *
 * Gated by the prototype-auth session cookie when configured so
 * anonymous visitors can't burn quota.
 */

function requiresSession() {
  if (!isPrototypeLoginConfigured()) return false;
  if ((process.env.PROTOTYPE_AUTH_SECRET || "").length < 16) return false;
  return true;
}

function activeProvider() {
  if (process.env.RETAILGPT_ELEVENLABS_API_KEY) return "elevenlabs";
  if (
    process.env.RETAILGPT_LLM_API_BASE_URL &&
    process.env.RETAILGPT_LLM_API_KEY &&
    process.env.RETAILGPT_LLM_TTS_DEPLOYMENT
  ) return "azure";
  return null;
}

// ─── ElevenLabs ───────────────────────────────────────────────────────

// Named voices from ElevenLabs' shared library. IDs are stable across
// accounts. Rachel is the default (warm American female, most-used for
// narration).
const BASE_ELEVEN_VOICES = {
  Rachel: "21m00Tcm4TlvDq8ikWAM",
  Bella: "EXAVITQu4vr4xnSDxMaL",
  Domi: "AZnzlk1XvdvUeBnXmlld",
  Elli: "MF3mGyEYCl7XYWbV9V6O",
  Adam: "pNInz6obpgDQGcFmaJgB",
  Antoni: "ErXwobaYiN019PkySvjV",
  Sam: "yoZ06aMxZJJ28mfd3POQ",
};
const DEFAULT_ELEVEN_VOICE = "Rachel";

// Extra voices from an env-var-configured list. Format:
//   RETAILGPT_ELEVENLABS_EXTRA_VOICES="Meera:xxxxxx,Aakash:yyyyyy"
// Each entry is "Name:voice_id". Comma-separated. Names appear alongside the
// base voices in the tour's voice picker.
//
// How to find a voice_id:
//   1. Go to https://elevenlabs.io/app/voice-library
//   2. Filter by accent (Indian, British, etc.)
//   3. Click "Add to VoiceLab" on the voices you want
//   4. Go to https://elevenlabs.io/app/voices (My Voices)
//   5. Click a voice -> the ID is in the URL or the "View" panel
function parseExtraVoices(raw) {
  if (!raw) return {};
  const out = {};
  for (const part of String(raw).split(",")) {
    const [name, id] = part.split(":").map((s) => s.trim());
    if (name && id) out[name] = id;
  }
  return out;
}

function allElevenVoices() {
  return {
    ...BASE_ELEVEN_VOICES,
    ...parseExtraVoices(process.env.RETAILGPT_ELEVENLABS_EXTRA_VOICES),
  };
}

// Detect if a string looks like an ElevenLabs voice_id (20+ chars, alphanumeric).
// Voice names in our map are short human words. Voice IDs are opaque hashes.
function looksLikeVoiceId(str) {
  return typeof str === "string" && str.length >= 15 && /^[A-Za-z0-9]+$/.test(str);
}

async function synthElevenLabs(text, voiceParam) {
  const voices = allElevenVoices();
  // Accept either a friendly name (Rachel, Bella, ...) or a raw voice ID
  // pulled from the /api/llm/tts/voices endpoint.
  let voiceId;
  if (looksLikeVoiceId(voiceParam)) {
    voiceId = voiceParam;
  } else {
    voiceId = voices[voiceParam] || voices[DEFAULT_ELEVEN_VOICE];
  }
  // Default to eleven_multilingual_v2 because it's the model tier the free
  // plan supports reliably. Turbo v2.5 was moved behind paid tiers in
  // late 2025. Set RETAILGPT_ELEVENLABS_MODEL to override.
  const modelId = process.env.RETAILGPT_ELEVENLABS_MODEL || "eleven_multilingual_v2";
  const url = `https://api.elevenlabs.io/v1/text-to-speech/${voiceId}`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "xi-api-key": process.env.RETAILGPT_ELEVENLABS_API_KEY,
      "Content-Type": "application/json",
      Accept: "audio/mpeg",
    },
    body: JSON.stringify({
      text,
      model_id: modelId,
      voice_settings: {
        stability: 0.5,
        similarity_boost: 0.75,
        style: 0.15,
        use_speaker_boost: true,
      },
    }),
  });
  return res;
}

// ─── Azure OpenAI ─────────────────────────────────────────────────────

const AZURE_VOICES = new Set(["nova", "alloy", "echo", "fable", "onyx", "shimmer"]);

async function synthAzure(text, voice, speed) {
  const chosenVoice = AZURE_VOICES.has(voice) ? voice : "nova";
  const baseUrl = process.env.RETAILGPT_LLM_API_BASE_URL.replace(/\/+$/, "");
  const deployment = encodeURIComponent(process.env.RETAILGPT_LLM_TTS_DEPLOYMENT);
  const apiVersion = process.env.RETAILGPT_LLM_TTS_API_VERSION || "2024-05-01-preview";
  const url = `${baseUrl}/openai/deployments/${deployment}/audio/speech?api-version=${apiVersion}`;
  return fetch(url, {
    method: "POST",
    headers: {
      "api-key": process.env.RETAILGPT_LLM_API_KEY,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: process.env.RETAILGPT_LLM_TTS_DEPLOYMENT,
      input: text,
      voice: chosenVoice,
      speed: Number.isFinite(speed) ? Math.min(4, Math.max(0.25, speed)) : 1.0,
      response_format: "mp3",
    }),
  });
}

// ─── Handler ──────────────────────────────────────────────────────────

export default async function handler(request) {
  if (request.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  if (requiresSession()) {
    const token = getCookieValue(request.headers.get("cookie"), SESSION_COOKIE_NAME);
    if (!token) return Response.json({ error: "Unauthorized" }, { status: 401 });
    try {
      await verifyPrototypeSessionToken(token);
    } catch {
      return Response.json({ error: "Unauthorized" }, { status: 401 });
    }
  }

  const provider = activeProvider();
  if (!provider) {
    return Response.json(
      {
        error:
          "TTS not configured. Set RETAILGPT_ELEVENLABS_API_KEY (recommended) or RETAILGPT_LLM_TTS_DEPLOYMENT.",
      },
      { status: 503 }
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const text = String(body.text || "").trim();
  if (!text) return Response.json({ error: "text is required" }, { status: 400 });
  if (text.length > 4000) {
    return Response.json({ error: "text is too long (max 4000 chars)" }, { status: 400 });
  }

  let upstream;
  try {
    if (provider === "elevenlabs") {
      upstream = await synthElevenLabs(text, body.voice);
    } else {
      upstream = await synthAzure(text, body.voice, body.speed);
    }
  } catch (e) {
    return Response.json({ error: `Upstream fetch failed: ${e.message || e}` }, { status: 502 });
  }

  if (!upstream.ok) {
    const errText = await upstream.text().catch(() => "");
    return Response.json(
      {
        error: `TTS upstream (${provider}) returned ${upstream.status}`,
        detail: errText.slice(0, 400),
      },
      { status: upstream.status }
    );
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "audio/mpeg",
      "Cache-Control": "public, max-age=31536000, immutable",
      "X-TTS-Provider": provider,
    },
  });
}

// Exported so a status/introspection endpoint can list what's available.
export const TTS_PROVIDERS = { elevenlabs: { voices: Object.keys(BASE_ELEVEN_VOICES) } };
