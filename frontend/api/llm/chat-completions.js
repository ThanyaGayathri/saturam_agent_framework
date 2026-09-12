import {
  getCookieValue,
  isPrototypeLoginConfigured,
  SESSION_COOKIE_NAME,
  verifyPrototypeSessionToken,
} from "../_lib/prototype-auth-server.js";
import {
  forwardRetailgptChatCompletion,
  isRetailgptLlmConfigured,
} from "../_lib/retailgpt-llm-server.js";

export const config = { runtime: "edge" };

function llmRequiresSession() {
  if (!isPrototypeLoginConfigured()) return false;
  if ((process.env.PROTOTYPE_AUTH_SECRET || "").length < 16) return false;
  return true;
}

export default async function handler(request) {
  if (request.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  if (llmRequiresSession()) {
    const token = getCookieValue(request.headers.get("cookie"), SESSION_COOKIE_NAME);
    if (!token) {
      return Response.json({ error: "Unauthorized" }, { status: 401 });
    }
    try {
      await verifyPrototypeSessionToken(token);
    } catch {
      return Response.json({ error: "Unauthorized" }, { status: 401 });
    }
  }

  if (!isRetailgptLlmConfigured()) {
    return Response.json({ error: "LLM not configured on server" }, { status: 503 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const messages = body.messages;
  if (!Array.isArray(messages) || messages.length === 0) {
    return Response.json({ error: "messages must be a non-empty array" }, { status: 400 });
  }

  const max_tokens = Number(body.max_tokens);
  const temperature = Number(body.temperature);
  if (!Number.isFinite(max_tokens) || max_tokens <= 0) {
    return Response.json({ error: "max_tokens required" }, { status: 400 });
  }
  if (!Number.isFinite(temperature)) {
    return Response.json({ error: "temperature required" }, { status: 400 });
  }

  /** @type {{ max_tokens: number, temperature: number, response_format?: { type: string } }} */
  const req = { max_tokens, temperature };
  if (body.response_format && typeof body.response_format === "object") {
    req.response_format = body.response_format;
  }

  try {
    const upstream = await forwardRetailgptChatCompletion(messages, req);
    const text = await upstream.text();
    const ct = upstream.headers.get("content-type") || "application/json";
    return new Response(text, {
      status: upstream.status,
      headers: { "Content-Type": ct },
    });
  } catch (e) {
    console.error("[api/llm/chat-completions]", e);
    return Response.json({ error: "Upstream LLM request failed" }, { status: 502 });
  }
}
