/* Cloudflare Worker entry point (used by `wrangler deploy`).
 *
 * Serves the static PWA via the ASSETS binding, and handles /api/config with
 * the same proxy handlers used by the Pages Function — so the GitHub token
 * stays server-side and never reaches the phone.
 */
import { onRequestGet, onRequestPut } from "./functions/api/config.js";
import { onRequestGet as timesGet } from "./functions/api/times.js";

function methodNotAllowed() {
  return new Response(JSON.stringify({ error: "method not allowed" }), {
    status: 405,
    headers: { "content-type": "application/json" }
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/config") {
      if (request.method === "GET") return onRequestGet({ request, env });
      if (request.method === "PUT") return onRequestPut({ request, env });
      return methodNotAllowed();
    }
    if (url.pathname === "/api/times") {
      if (request.method === "GET") return timesGet({ request, env });
      return methodNotAllowed();
    }
    // Everything else is a static asset (index.html, icons, manifest, sw.js…)
    return env.ASSETS.fetch(request);
  }
};
