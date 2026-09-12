import { isPrototypeLoginConfigured } from "../_lib/prototype-auth-server.js";
import { isRetailgptLlmConfigured } from "../_lib/retailgpt-llm-server.js";

export const config = { runtime: "edge" };

function llmRequiresSession() {
  if (!isPrototypeLoginConfigured()) return false;
  if ((process.env.PROTOTYPE_AUTH_SECRET || "").length < 16) return false;
  return true;
}

export default async function handler(request) {
  if (request.method !== "GET") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  return Response.json({
    configured: isRetailgptLlmConfigured(),
    authRequired: llmRequiresSession(),
  });
}
