import { next } from "@vercel/edge";
import {
  getCookieValue,
  isPrototypeLoginConfigured,
  SESSION_COOKIE_NAME,
  verifyPrototypeSessionToken,
} from "./lib/prototype-auth-server.js";

export const config = {
  matcher: ["/((?!api/).*)"],
};

export default async function middleware(request) {
  const { pathname } = new URL(request.url);

  if (pathname.startsWith("/assets/")) {
    return next();
  }

  if (!isPrototypeLoginConfigured()) {
    return next();
  }

  if (pathname.startsWith("/api/")) {
    return next();
  }

  if (pathname === "/login") {
    return next();
  }

  if (pathname.match(/\.[a-z0-9]{1,10}$/i)) {
    return next();
  }

  if ((process.env.PROTOTYPE_AUTH_SECRET || "").length < 16) {
    return Response.redirect(new URL("/login", request.url), 307);
  }

  const token = getCookieValue(request.headers.get("cookie"), SESSION_COOKIE_NAME);

  if (!token) {
    return Response.redirect(new URL("/login", request.url), 307);
  }

  try {
    await verifyPrototypeSessionToken(token);
    return next();
  } catch {
    return Response.redirect(new URL("/login", request.url), 307);
  }
}
