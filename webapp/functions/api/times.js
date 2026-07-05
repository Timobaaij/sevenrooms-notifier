/* Cloudflare Pages Function / Worker route — GET /api/times
 *
 * Live availability lookup so the app can offer a "pick a time" list, like the
 * Streamlit "Load available times" button. Runs server-side, so no browser CORS.
 *
 *   /api/times?platform=sevenrooms&venue=<slug>&date=YYYY-MM-DD&party=2
 *
 * SevenRooms uses the public widget JSON API (reliable). OpenTable is scraped
 * best-effort (its anti-bot may block a plain server fetch — the UI falls back
 * to manual entry if nothing comes back).
 */

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: { "content-type": "application/json", "cache-control": "no-store" }
  });
}

function toHHMM(s) {
  const m = /\b([01]\d|2[0-3]):([0-5]\d)\b/.exec(String(s || ""));
  return m ? m[1] + ":" + m[2] : null;
}

async function sevenrooms(venue, date, party) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) return [];
  const start = `${m[2]}-${m[3]}-${m[1]}`; // MM-DD-YYYY
  const url = `https://www.sevenrooms.com/api-yoa/availability/widget/range` +
    `?venue=${encodeURIComponent(venue)}&party_size=${party}&start_date=${start}` +
    `&num_days=1&channel=SEVENROOMS_WIDGET&selected_lang_code=en&halo_size_interval=64`;
  const r = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json,text/plain,*/*" } });
  if (!r.ok) return [];
  let j;
  try { j = await r.json(); } catch (_) { return []; }
  const avail = ((j.data || {}).availability) || {};
  const out = [];
  for (const key in avail) {
    const day = avail[key];
    if (!Array.isArray(day)) continue;
    for (const block of day) {
      const times = (block && block.times) || [];
      for (const t of times) {
        if (!t || typeof t !== "object") continue;
        const isReq = t.is_requestable === true;
        if (t.is_waitlist === true) continue;
        const isAvail = ("is_available" in t) ? t.is_available === true : !!t.access_persistent_id;
        if (!(isAvail || isReq)) continue;
        const hhmm = toHHMM(t.time_iso || t.date_time || t.time);
        if (hhmm) out.push(hhmm + (isReq && !isAvail ? " (REQUEST)" : ""));
      }
    }
  }
  return [...new Set(out)].sort();
}

async function opentable(venue, date, party) {
  const url = `https://www.opentable.co.uk/r/${encodeURIComponent(venue)}?covers=${party}&dateTime=${date}T19:00:00`;
  let html;
  try {
    const r = await fetch(url, {
      headers: {
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
      }
    });
    html = await r.text();
  } catch (_) { return []; }
  const out = [];
  const blocks = html.match(/\{[^{}]*\}/g) || [];
  for (const b of blocks) {
    const clean = b.replace(/\s/g, "").replace(/\\"/g, '"');
    if (clean.includes('"isAvailable":true') && clean.includes('"time":')) {
      const mm = /"time"\s*:\s*"([^"]+)"/.exec(b);
      if (mm) { const hhmm = toHHMM(mm[1]); if (hhmm) out.push(hhmm); }
    }
  }
  return [...new Set(out)].sort();
}

export async function onRequestGet({ request, env }) {
  if (env.ACCESS_KEY && request.headers.get("x-access-key") !== env.ACCESS_KEY) {
    return json({ error: "unauthorized" }, 401);
  }
  const u = new URL(request.url);
  const platform = (u.searchParams.get("platform") || "sevenrooms").toLowerCase();
  const venue = u.searchParams.get("venue") || "";
  const date = u.searchParams.get("date") || "";
  const party = parseInt(u.searchParams.get("party") || "2", 10) || 2;
  if (!venue || !date) return json({ error: "venue and date are required" }, 400);
  try {
    const times = platform === "opentable"
      ? await opentable(venue, date, party)
      : await sevenrooms(venue, date, party);
    return json({ times });
  } catch (e) {
    return json({ error: String((e && e.message) || e) }, 502);
  }
}
