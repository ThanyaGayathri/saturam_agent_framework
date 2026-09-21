import http from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.join(__dirname, "dist");
const apiDir = path.join(__dirname, "api");
const PORT = process.env.PORT || 8080;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".map": "application/json; charset=utf-8",
};

const handlerCache = new Map();

async function loadApiHandler(pathname) {
  const rel = pathname.replace(/^\/api\//, "");
  if (rel.includes("..")) return null;
  const filePath = path.join(apiDir, `${rel}.js`);
  if (!existsSync(filePath)) return null;
  if (handlerCache.has(filePath)) return handlerCache.get(filePath);
  const mod = await import(pathToFileURL(filePath).href);
  const handler = mod.default;
  handlerCache.set(filePath, handler);
  return handler;
}

async function handleApi(req, res, url) {
  const handler = await loadApiHandler(url.pathname);
  if (!handler) {
    res.statusCode = 404;
    res.end("Not Found");
    return;
  }

  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const bodyBuf = chunks.length ? Buffer.concat(chunks) : undefined;

  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) {
    if (v == null) continue;
    headers.set(k, Array.isArray(v) ? v.join(", ") : v);
  }

  const hasBody = !(req.method === "GET" || req.method === "HEAD");
  const request = new Request(url, {
    method: req.method,
    headers,
    body: hasBody && bodyBuf && bodyBuf.length ? bodyBuf : undefined,
  });

  try {
    const response = await handler(request);
    res.statusCode = response.status;
    for (const [k, v] of response.headers.entries()) {
      if (k.toLowerCase() === "set-cookie") {
        res.setHeader("Set-Cookie", response.headers.get("set-cookie"));
      } else {
        res.setHeader(k, v);
      }
    }
    const buf = Buffer.from(await response.arrayBuffer());
    res.end(buf);
  } catch (e) {
    console.error(`[api ${url.pathname}]`, e);
    res.statusCode = 500;
    res.end("Internal Server Error");
  }
}

async function serveStatic(req, res, url) {
  let filePath = path.join(distDir, decodeURIComponent(url.pathname));
  let stat = existsSync(filePath) ? statSync(filePath) : null;

  if (!stat || stat.isDirectory()) {
    filePath = path.join(distDir, "index.html");
    stat = statSync(filePath);
  }

  const ext = path.extname(filePath);
  res.setHeader("Content-Type", MIME[ext] || "application/octet-stream");
  if (filePath.endsWith("index.html")) {
    res.setHeader("Cache-Control", "public, max-age=0, must-revalidate");
  } else if (url.pathname.startsWith("/assets/")) {
    res.setHeader("Cache-Control", "public, max-age=31536000, immutable");
  }
  createReadStream(filePath).pipe(res);
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);
    if (url.pathname.startsWith("/api/")) {
      await handleApi(req, res, url);
    } else {
      await serveStatic(req, res, url);
    }
  } catch (e) {
    console.error("[server]", e);
    res.statusCode = 500;
    res.end("Internal Server Error");
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`egpt frontend listening on 127.0.0.1:${PORT}`);
});
