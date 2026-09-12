import {
  getCookieValue,
  isPrototypeLoginConfigured,
  SESSION_COOKIE_NAME,
  verifyPrototypeSessionToken,
} from "../_lib/prototype-auth-server.js";

export const config = { runtime: "edge" };

/**
 * Lists voices available to the tour.
 *
 * When ElevenLabs is the active provider, queries the user's account (this
 * includes any voices they've added to VoiceLab from the library) and
 * merges them with the base named-voices set. Result is a flat list with
 * {name, id, provider, labels} entries so the tour's voice picker can show
 * everything the user actually has access to.
 *
 * When Azure is active, returns the fixed six OpenAI voices.
 *
 * Gated by the prototype-auth session cookie when configured.
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

// Voices whose names contain any of these substrings are hidden from the
// dropdown. Keeps character/novelty voices out of a business demo tour.
// Case-insensitive.
const VOICE_NAME_BLOCKLIST = [
  "meditative",
  "trickster",
  "warrior",
  "quirky",
  "husky",
  "raucous",
];

function isBlocked(name) {
  const n = String(name || "").toLowerCase();
  return VOICE_NAME_BLOCKLIST.some((word) => n.includes(word));
}

async function listElevenVoices() {
  const res = await fetch("https://api.elevenlabs.io/v1/voices", {
    method: "GET",
    headers: {
      "xi-api-key": process.env.RETAILGPT_ELEVENLABS_API_KEY,
      Accept: "application/json",
    },
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => "");
    throw new Error(`ElevenLabs voices list returned ${res.status}: ${errText.slice(0, 200)}`);
  }
  const data = await res.json();
  return (data?.voices || [])
    .filter((v) => !isBlocked(v.name))
    .map((v) => ({
      name: v.name,
      id: v.voice_id,
      provider: "elevenlabs",
      // Prefer accent + gender + age when available for the dropdown label.
      labels: v.labels || {},
      category: v.category || null,
    }));
}

const AZURE_VOICES = [
  { name: "nova", id: "nova", provider: "azure", labels: { gender: "female", accent: "american" } },
  { name: "alloy", id: "alloy", provider: "azure", labels: { gender: "neutral", accent: "american" } },
  { name: "echo", id: "echo", provider: "azure", labels: { gender: "male", accent: "american" } },
  { name: "fable", id: "fable", provider: "azure", labels: { gender: "male", accent: "british" } },
  { name: "onyx", id: "onyx", provider: "azure", labels: { gender: "male", accent: "american" } },
  { name: "shimmer", id: "shimmer", provider: "azure", labels: { gender: "female", accent: "american" } },
];

export default async function handler(request) {
  if (request.method !== "GET") {
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
    return Response.json({ provider: null, voices: [] });
  }

  if (provider === "azure") {
    return Response.json({ provider, voices: AZURE_VOICES });
  }

  try {
    const voices = await listElevenVoices();
    return Response.json({ provider, voices });
  } catch (e) {
    return Response.json(
      { provider, voices: [], error: String(e.message || e) },
      { status: 502 }
    );
  }
}
