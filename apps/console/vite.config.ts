import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

function w0HarnessPlugin(): Plugin {
  const role = process.env.MISSIONS_W0_HARNESS_ROLE;
  const peerOrigin = process.env.MISSIONS_W0_HARNESS_PEER_ORIGIN;
  const w4PreviewHarness = process.env.MISSIONS_W4_PREVIEW_HARNESS === "1";
  const controlOrigin = "http://app.localhost:4173";

  const readBody = (request: import("node:http").IncomingMessage): Promise<string> => new Promise((resolve) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => resolve(body));
  });

  return {
    name: "missions-w0-two-origin-harness",
    configureServer(server) {
      if (!role) return;
      if (w4PreviewHarness) server.middlewares.use(async (request, response, next) => {
        const host = request.headers.host || "";
        const pathname = new URL(request.url || "/", `http://${host || "localhost"}`).pathname;
        const isPreview = host.startsWith("preview.localhost:");
        const cors = () => {
          response.setHeader("Access-Control-Allow-Origin", controlOrigin);
          response.setHeader("Access-Control-Allow-Credentials", "true");
          response.setHeader("Vary", "Origin");
        };
        if (!isPreview && request.method === "POST" && pathname === "/projects/mission_preview/preview/exchanges") {
          response.statusCode = 200;
          response.setHeader("Content-Type", "application/json");
          response.end(JSON.stringify({ exchange_id: "w4-exchange", exchange_proof: "w4-body-only-proof", preview_origin: "http://preview.localhost:4174" }));
          return;
        }
        if (!isPreview && pathname === "/__w4_control_cookie_probe__") {
          response.statusCode = 200;
          response.setHeader("Content-Type", "application/json");
          response.end(JSON.stringify({ preview_cookie_seen: (request.headers.cookie || "").includes("mission_preview_w4=") }));
          return;
        }
        if (isPreview && pathname === "/preview/exchange" && request.method === "OPTIONS") {
          if (request.headers.origin !== controlOrigin) { response.statusCode = 403; response.end(); return; }
          cors();
          response.statusCode = 204;
          response.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
          response.setHeader("Access-Control-Allow-Headers", "content-type");
          response.end();
          return;
        }
        if (isPreview && pathname === "/preview/exchange" && request.method === "POST") {
          const body = await readBody(request);
          if (request.headers.origin !== controlOrigin || !body.includes("w4-body-only-proof")) { response.statusCode = 403; response.end(); return; }
          cors();
          response.statusCode = 204;
          response.setHeader("Set-Cookie", "mission_preview_w4=capability; Path=/projects/mission_preview/preview; HttpOnly; Secure; SameSite=None");
          response.end();
          return;
        }
        if (isPreview && pathname.startsWith("/projects/mission_preview/preview")) {
          if (!(request.headers.cookie || "").includes("mission_preview_w4=capability")) { response.statusCode = 404; response.end("Preview unavailable"); return; }
          response.setHeader("Cache-Control", "private, no-store");
          response.setHeader("X-Content-Type-Options", "nosniff");
          response.setHeader("Content-Security-Policy", `default-src 'self'; script-src 'self'; style-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors ${controlOrigin}`);
          if (pathname.endsWith("/assets/nested.js")) {
            response.statusCode = 200;
            response.setHeader("Content-Type", "text/javascript; charset=utf-8");
            response.end("document.querySelector('[data-nested]').textContent='Nested asset loaded';");
            return;
          }
          response.statusCode = 200;
          response.setHeader("Content-Type", "text/html; charset=utf-8");
          response.end("<!doctype html><html><body><main>Verified Mission output</main><output data-nested>Waiting for asset</output><form action='https://external.invalid/collect' method='post'><button type='submit'>Send externally</button></form><script type='module' src='./assets/nested.js'></script></body></html>");
          return;
        }
        next();
      });
      server.middlewares.use("/__w0_harness__", (_request, response) => {
        response.statusCode = 200;
        response.setHeader("Content-Type", "text/html; charset=utf-8");
        response.end(
          role === "preview"
            ? `<!doctype html><html><body data-harness-role="preview"><main>Preview origin ready</main><script>window.parent.postMessage({ type: "missions:w0:preview-ready", origin: window.location.origin }, "*");</script></body></html>`
            : `<!doctype html><html><body data-harness-role="control"><main>Control origin ready</main><output data-peer-message>waiting</output><iframe title="Preview harness" src="${peerOrigin}/__w0_harness__"></iframe><script>window.addEventListener("message", (event) => { if (event.origin === ${JSON.stringify(peerOrigin)} && event.data?.type === "missions:w0:preview-ready") document.querySelector("[data-peer-message]").textContent = "preview-ready"; });</script></body></html>`,
        );
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  base: mode === "preview" || process.env.MISSIONS_PREVIEW_BUILD === "1" ? "./" : "/",
  plugins: [react(), w0HarnessPlugin()],
  server: {
    allowedHosts: [".localhost"],
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
}));
