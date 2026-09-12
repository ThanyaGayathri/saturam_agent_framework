/**
 * Server-only prototype auth (Vercel Edge / Node). Secrets stay off the client bundle.
 */
import { SignJWT, jwtVerify } from "jose";

export const SESSION_COOKIE_NAME = "retailgpt_prototype_session";

/** @param {string | null | undefined} header */
export function getCookieValue(header, name) {
  if (!header) return null;
  const parts = header.split(";").map((s) => s.trim());
  for (const p of parts) {
    if (p.startsWith(`${name}=`)) {
      return decodeURIComponent(p.slice(name.length + 1));
    }
  }
  return null;
}

export function isPrototypeLoginConfigured() {
  return Boolean((process.env.PROTOTYPE_LOGIN_PASSWORD || "").trim());
}

export function getExpectedLoginUser() {
  const u = (process.env.PROTOTYPE_LOGIN_USER || "").trim();
  return u || "demo";
}

export function getExpectedLoginPassword() {
  return (process.env.PROTOTYPE_LOGIN_PASSWORD || "").trim();
}

function getSecretKey() {
  const raw = process.env.PROTOTYPE_AUTH_SECRET || "";
  if (raw.length < 16) {
    throw new Error("PROTOTYPE_AUTH_SECRET must be at least 16 characters when prototype login is enabled");
  }
  return new TextEncoder().encode(raw);
}

/**
 * @param {boolean} remember
 */
export async function signPrototypeSessionToken(remember) {
  const secret = getSecretKey();
  const exp = remember ? "30d" : "12h";
  return new SignJWT({ sub: "retailgpt-prototype" })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime(exp)
    .sign(secret);
}

/**
 * @param {string} token
 */
export async function verifyPrototypeSessionToken(token) {
  const secret = getSecretKey();
  await jwtVerify(token, secret);
}

/**
 * @param {boolean} isHttps
 */
export function buildSetSessionCookie(token, remember, isHttps) {
  const maxAge = remember ? 30 * 24 * 60 * 60 : 12 * 60 * 60;
  const parts = [
    `${SESSION_COOKIE_NAME}=${encodeURIComponent(token)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${maxAge}`,
  ];
  if (isHttps) parts.push("Secure");
  return parts.join("; ");
}

/**
 * @param {boolean} isHttps
 */
export function buildClearSessionCookie(isHttps) {
  const parts = [
    `${SESSION_COOKIE_NAME}=`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    "Max-Age=0",
  ];
  if (isHttps) parts.push("Secure");
  return parts.join("; ");
}

export function requestIsHttps(request) {
  const url = new URL(request.url);
  if (url.protocol === "https:") return true;
  const fwd = request.headers.get("x-forwarded-proto");
  return fwd === "https";
}
