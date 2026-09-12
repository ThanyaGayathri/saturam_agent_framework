import {
  buildSetSessionCookie,
  getExpectedLoginPassword,
  getExpectedLoginUser,
  isPrototypeLoginConfigured,
  requestIsHttps,
  signPrototypeSessionToken,
} from "../_lib/prototype-auth-server.js";

export const config = { runtime: "edge" };

export default async function handler(request) {
  if (request.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  if (!isPrototypeLoginConfigured()) {
    return Response.json({ ok: true, authDisabled: true });
  }

  if ((process.env.PROTOTYPE_AUTH_SECRET || "").length < 16) {
    return Response.json(
      { ok: false, error: "Server misconfiguration: PROTOTYPE_AUTH_SECRET required (min 16 chars)" },
      { status: 503 }
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: "Invalid JSON" }, { status: 400 });
  }

  const u = String(body.username || "")
    .trim()
    .toLowerCase();
  const p = String(body.password || "");
  const remember = body.remember === true;

  const okUser = u === getExpectedLoginUser().toLowerCase();
  const okPass = p === getExpectedLoginPassword();

  if (!okUser || !okPass) {
    return Response.json({ ok: false, error: "Invalid credentials" }, { status: 401 });
  }

  try {
    const token = await signPrototypeSessionToken(remember);
    const cookie = buildSetSessionCookie(token, remember, requestIsHttps(request));
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Set-Cookie": cookie,
      },
    });
  } catch (e) {
    console.error("[prototype-auth/login]", e);
    return Response.json({ ok: false, error: "Could not create session" }, { status: 500 });
  }
}
