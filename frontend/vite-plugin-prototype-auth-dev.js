/**
 * Implements /api/auth/* and /api/llm/* during `vite` dev (no `vercel dev` required).
 * Loads env via Vite's loadEnv (prefix '' = all keys).
 */
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { loadEnv } from "vite";

const rootDir = fileURLToPath(new URL(".", import.meta.url));

/** Env keys injected into process.env while handling API routes in dev. */
const DEV_API_PATCH_KEYS = [
  "PROTOTYPE_LOGIN_USER",
  "PROTOTYPE_LOGIN_PASSWORD",
  "PROTOTYPE_AUTH_SECRET",
  "RETAILGPT_LLM_API_BASE_URL",
  "RETAILGPT_LLM_API_KEY",
  "RETAILGPT_LLM_MODEL",
  "RETAILGPT_LLM_PROVIDER",
  "RETAILGPT_LLM_AZURE_DEPLOYMENT",
  "RETAILGPT_LLM_AZURE_API_VERSION",
  "RETAILGPT_LLM_TOKENS_PARAM",
  "RETAILGPT_LLM_TTS_DEPLOYMENT",
  "RETAILGPT_LLM_TTS_API_VERSION",
  "RETAILGPT_ELEVENLABS_API_KEY",
  "RETAILGPT_ELEVENLABS_MODEL",
];

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

/** @param {import("http").ServerResponse} res */
async function sendWebResponse(res, webResponse) {
  res.statusCode = webResponse.status;
  webResponse.headers.forEach((value, key) => {
    if (key.toLowerCase() === "set-cookie") {
      res.appendHeader(key, value);
    } else {
      res.setHeader(key, value);
    }
  });
  // Read as ArrayBuffer so binary responses (audio/mpeg, images, etc.) aren't
  // corrupted by UTF-8 text conversion. Node's res.end accepts Buffer.
  const buf = Buffer.from(await webResponse.arrayBuffer());
  res.end(buf);
}

/** @param {Record<string, string>} loaded */
function patchProcessEnv(loaded) {
  const backup = {};
  for (const k of DEV_API_PATCH_KEYS) {
    backup[k] = process.env[k];
    if (loaded[k] !== undefined) process.env[k] = loaded[k];
  }
  return () => {
    for (const k of DEV_API_PATCH_KEYS) {
      if (backup[k] === undefined) delete process.env[k];
      else process.env[k] = backup[k];
    }
  };
}

function normalizePathname(rawUrl) {
  const pathOnly = (rawUrl.split("?")[0] || "").trim();
  if (!pathOnly || pathOnly === "/") return "/";
  return pathOnly.replace(/\/+$/, "") || "/";
}

export function prototypeAuthDevPlugin() {
  return {
    name: "prototype-auth-dev",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const rawUrl = req.url || "";
        const pathname = normalizePathname(rawUrl);

        const isAuth = pathname.startsWith("/api/auth/");
        const isLlm = pathname.startsWith("/api/llm/");
        if (!isAuth && !isLlm) {
          return next();
        }

        const env = loadEnv(server.config.mode, rootDir, "");
        const restoreEnv = patchProcessEnv(env);

        let bodyText = "";
        if (req.method === "POST") {
          try {
            bodyText = await readBody(req);
          } catch {
            restoreEnv();
            res.statusCode = 400;
            res.end();
            return;
          }
        }

        const host = req.headers.host || "localhost";
        const proto = (req.headers["x-forwarded-proto"] || "http").split(",")[0].trim();
        const base = `${proto}://${host}`;
        const request = new Request(base + rawUrl, {
          method: req.method,
          headers: {
            cookie: req.headers.cookie || "",
            "content-type": req.headers["content-type"] || "application/json",
          },
          body: req.method === "POST" ? bodyText : undefined,
        });

        // Bust Node's ESM cache per-request so edits to `api/**` or `lib/**`
        // (which live outside the Vite module graph) show up without a full
        // dev server restart. The API handlers re-import `lib/*` with the
        // same nonce so their transitive static imports don't stay cached.
        const cacheBust = `?t=${Date.now()}`;
        process.env.__RETAILGPT_DEV_MODULE_BUST__ = String(Date.now());
        const load = (rel) =>
          import(pathToFileURL(path.join(rootDir, rel)).href + cacheBust);

        try {
          let mod;
          if (isAuth) {
            if (pathname === "/api/auth/session") {
              mod = await load("api/auth/session.js");
            } else if (pathname === "/api/auth/login") {
              mod = await load("api/auth/login.js");
            } else if (pathname === "/api/auth/logout") {
              mod = await load("api/auth/logout.js");
            } else {
              restoreEnv();
              return next();
            }
          } else if (pathname === "/api/llm/status" && req.method === "GET") {
            mod = await load("api/llm/status.js");
          } else if (pathname === "/api/llm/chat-completions" && req.method === "POST") {
            mod = await load("api/llm/chat-completions.js");
          } else if (pathname === "/api/llm/tts" && req.method === "POST") {
            mod = await load("api/llm/tts.js");
          } else if (pathname === "/api/llm/tts/voices" && req.method === "GET") {
            mod = await load("api/llm/tts-voices.js");
          } else {
            restoreEnv();
            return next();
          }

          const webResponse = await mod.default(request);
          await sendWebResponse(res, webResponse);
        } catch (e) {
          console.error("[prototype-dev-api]", e);
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify({ error: "API handler failed in dev" }));
        } finally {
          restoreEnv();
        }
      });
    },
  };
}
