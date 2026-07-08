/* Cloudflare Pages Function / Worker route — GET /api/times
 *
 * Live SevenRooms availability so the app can offer a "pick a time" list, like
 * the Streamlit "Load available times" button. Runs server-side (no CORS).
 *
 *   /api/times?venue=<slug>&date=YYYY-MM-DD&party=2
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
  const empty = { times: [], slots: [] };
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) return empty;
  const start = `${m[2]}-${m[3]}-${m[1]}`; // MM-DD-YYYY
  const url = `https://www.sevenrooms.com/api-yoa/availability/widget/range` +
    `?venue=${encodeURIComponent(venue)}&party_size=${party}&start_date=${start}` +
    `&num_days=1&channel=SEVENROOMS_WIDGET&selected_lang_code=en&halo_size_interval=64`;
  const r = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json,text/plain,*/*" } });
  if (!r.ok) return empty;
  let j;
  try { j = await r.json(); } catch (_) { return empty; }
  const avail = ((j.data || {}).availability) || {};
  const slots = [];
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
        if (!hhmm) continue;
        // seating area / room, e.g. "Restaurant Terrace", "Restaurant - Indoors"
        const area = String(t.public_time_slot_description || "").trim();
        slots.push({ t: hhmm, area: area, req: (isReq && !isAvail) });
      }
    }
  }
  // de-dupe by time+area+req; sort by time then area
  const seen = {}; const uniq = [];
  for (const s of slots) { const k = s.t + "|" + s.area + "|" + s.req; if (!seen[k]) { seen[k] = 1; uniq.push(s); } }
  uniq.sort((a, b) => a.t < b.t ? -1 : a.t > b.t ? 1 : (a.area < b.area ? -1 : a.area > b.area ? 1 : 0));
  // backward-compatible flat time strings (still used by the Favourites view)
  const tSeen = {}; const flat = [];
  for (const s of uniq) { const label = s.t + (s.req ? " (REQUEST)" : ""); if (!tSeen[label]) { tSeen[label] = 1; flat.push(label); } }
  flat.sort();
  return { times: flat, slots: uniq };
}

export async function onRequestGet({ request, env }) {
  if (env.ACCESS_KEY && request.headers.get("x-access-key") !== env.ACCESS_KEY) {
    return json({ error: "unauthorized" }, 401);
  }
  const u = new URL(request.url);
  const venue = u.searchParams.get("venue") || "";
  const date = u.searchParams.get("date") || "";
  const party = parseInt(u.searchParams.get("party") || "2", 10) || 2;
  if (!venue || !date) return json({ error: "venue and date are required" }, 400);
  try {
    return json(await sevenrooms(venue, date, party));
  } catch (e) {
    return json({ error: String((e && e.message) || e) }, 502);
  }
}
