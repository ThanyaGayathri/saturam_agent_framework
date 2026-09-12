import {
  getCookieValue,
  isPrototypeLoginConfigured,
  verifyPrototypeSessionToken,
  SESSION_COOKIE_NAME,
} from "../_lib/prototype-auth-server.js";

export const config = { runtime: "edge" };

export default async function handler(request) {
  if (request.method !== "GET") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  if (!isPrototypeLoginConfigured()) {
    return Response.json({ authenticated: true, authDisabled: true });
  }

  const secretOk = (process.env.PROTOTYPE_AUTH_SECRET || "").length >= 16;
  if (!secretOk) {
    return Response.json(
      { authenticated: false, error: "Server misconfiguration: PROTOTYPE_AUTH_SECRET missing or too short" },
      { status: 503 }
    );
  }

  const token = getCookieValue(request.headers.get("cookie"), SESSION_COOKIE_NAME);
  if (!token) {
    return Response.json({ authenticated: false });
  }

  try {
    await verifyPrototypeSessionToken(token);
    return Response.json({ authenticated: true });
  } catch {
    return Response.json({ authenticated: false });
  }
}
