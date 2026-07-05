/* Cloudflare Pages Function — the "tiny proxy".
 *
 * Route: /api/config   (GET reads config.json, PUT writes it)
 *
 * The GitHub token lives here as an environment variable and NEVER reaches the
 * browser. The PWA calls this endpoint; this endpoint calls GitHub.
 *
 * Required env vars (Pages → Settings → Environment variables):
 *   GITHUB_TOKEN   fine-grained token with Contents: Read and write on the repo
 *   GH_REPO        e.g. timobaaij/sevenrooms-notifier
 * Optional:
 *   GH_BRANCH      default "main"
 *   GH_CONFIG_PATH default "config.json"
 *   ACCESS_KEY     if set, callers must send a matching  x-access-key  header
 */

const GH = "https://api.github.com";

function b64encode(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}
function b64decode(b64) {
  const bin = atob((b64 || "").replace(/\s/g, ""));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}
function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: { "content-type": "application/json", "cache-control": "no-store" }
  });
}
function ghHeaders(env) {
  return {
    Authorization: "Bearer " + env.GITHUB_TOKEN,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "maitre-pwa"
  };
}
function guard(request, env) {
  if (env.ACCESS_KEY && request.headers.get("x-access-key") !== env.ACCESS_KEY) {
    return json({ error: "unauthorized" }, 401);
  }
  return null;
}
function settings(env) {
  return {
    repo: env.GH_REPO,
    branch: env.GH_BRANCH || "main",
    path: env.GH_CONFIG_PATH || "config.json"
  };
}
function notConfigured(env) {
  if (!env.GITHUB_TOKEN || !env.GH_REPO) {
    return json({ error: "Proxy not configured: set GITHUB_TOKEN and GH_REPO." }, 500);
  }
  return null;
}

export async function onRequestGet({ request, env }) {
  const blocked = guard(request, env); if (blocked) return blocked;
  const nc = notConfigured(env); if (nc) return nc;
  const { repo, branch, path } = settings(env);
  const r = await fetch(`${GH}/repos/${repo}/contents/${path}?ref=${branch}`, { headers: ghHeaders(env) });
  if (!r.ok) return json({ error: `GitHub GET ${r.status}`, detail: await r.text() }, r.status === 404 ? 404 : 502);
  const d = await r.json();
  let config;
  try { config = JSON.parse(b64decode(d.content)); } catch (_) { config = { searches: [] }; }
  return json({ config, sha: d.sha });
}

export async function onRequestPut({ request, env }) {
  const blocked = guard(request, env); if (blocked) return blocked;
  const nc = notConfigured(env); if (nc) return nc;
  const { repo, branch, path } = settings(env);

  let body;
  try { body = await request.json(); } catch (_) { return json({ error: "invalid JSON body" }, 400); }
  if (!body || !body.config) return json({ error: "missing config" }, 400);

  const put = await fetch(`${GH}/repos/${repo}/contents/${path}`, {
    method: "PUT",
    headers: ghHeaders(env),
    body: JSON.stringify({
      message: "Update searches via Maître PWA",
      content: b64encode(JSON.stringify(body.config, null, 2) + "\n"),
      sha: body.sha,
      branch
    })
  });
  if (put.status === 409) return json({ error: "sha conflict" }, 409);
  if (!put.ok) return json({ error: `GitHub PUT ${put.status}`, detail: await put.text() }, 502);
  const res = await put.json();
  return json({ ok: true, sha: res.content && res.content.sha });
}
