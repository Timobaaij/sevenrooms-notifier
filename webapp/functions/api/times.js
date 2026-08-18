/* Cloudflare Pages Function / Worker route — GET /api/times
 *
 * Live availability so the app can offer a "pick a time" list, show the
 * Openings feed and run Favourites. Runs server-side (no CORS).
 *
 *   /api/times?venue=<slug>&date=YYYY-MM-DD&party=2                  (SevenRooms)
 *   /api/times?platform=opentable&venue=<rid|url|slug>&date=…&time=19:00
 *
 * Optional env:
 *   OPENTABLE_AVAILABILITY_HASH   persisted-query hash captured from opentable.com
 *   OPENTABLE_REGION              databaseRegion, if your venues need one
 */

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: { "content-type": "application/json", "cache-control": "no-store" }
  });
}

// Times arrive as ISO ("2026-09-04T19:30:00"), as "2026-09-04 19:30" or as
// "7:30 PM" depending on the platform and field — read all three.
function toHHMM(s) {
  const v = String(s || "");
  let m = /T([01]\d|2[0-3]):([0-5]\d)/.exec(v) || /\b([01]\d|2[0-3]):([0-5]\d)\b/.exec(v);
  if (m) return m[1] + ":" + m[2];
  m = /\b(\d{1,2}):([0-5]\d)\s*([AP]M)\b/i.exec(v);
  if (m) {
    let hh = parseInt(m[1], 10) % 12;
    if (m[3].toUpperCase() === "PM") hh += 12;
    return String(hh).padStart(2, "0") + ":" + m[2];
  }
  return null;
}

// {t, area, req} list -> the response both the PWA and the sheet expect.
function shape(slots) {
  const seen = {}; const uniq = [];
  for (const s of slots) { const k = s.t + "|" + s.area + "|" + s.req; if (!seen[k]) { seen[k] = 1; uniq.push(s); } }
  uniq.sort((a, b) => a.t < b.t ? -1 : a.t > b.t ? 1 : (a.area < b.area ? -1 : a.area > b.area ? 1 : 0));
  // backward-compatible flat time strings (still used by older app builds)
  const tSeen = {}; const flat = [];
  for (const s of uniq) { const label = s.t + (s.req ? " (REQUEST)" : ""); if (!tSeen[label]) { tSeen[label] = 1; flat.push(label); } }
  flat.sort();
  return { times: flat, slots: uniq };
}

async function sevenrooms(venue, date, party) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) return shape([]);
  const start = `${m[2]}-${m[3]}-${m[1]}`; // MM-DD-YYYY
  const url = `https://www.sevenrooms.com/api-yoa/availability/widget/range` +
    `?venue=${encodeURIComponent(venue)}&party_size=${party}&start_date=${start}` +
    `&num_days=1&channel=SEVENROOMS_WIDGET&selected_lang_code=en&halo_size_interval=64`;
  const r = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json,text/plain,*/*" } });
  if (!r.ok) return shape([]);
  let j;
  try { j = await r.json(); } catch (_) { return shape([]); }
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
  return shape(slots);
}

/* ---------------------------------- OpenTable ----------------------------------
 * Mirrors main.py's client: resolve the venue to a numeric restaurant id, ask the
 * availability query the booking page asks, fall back to the page's own state.
 * The persisted-query hash rotates with OpenTable's deploys, so it is env, not
 * code; with none we send the document, like Apollo's own APQ fallback.
 */
const OT_HOST = "https://www.opentable.com";
const OT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";
const OT_QUERY = `query RestaurantsAvailability($restaurantIds: [Int]!, $date: String!, $time: String!, $partySize: Int!, $forwardDays: Int, $databaseRegion: DatabaseRegion, $requireTimes: Boolean) {
  availability(restaurantIds: $restaurantIds, date: $date, time: $time, partySize: $partySize, forwardDays: $forwardDays, databaseRegion: $databaseRegion, requireTimes: $requireTimes) {
    restaurantId
    availabilityDays {
      date
      slots {
        __typename
        ... on AvailableSlot { isAvailable dayOffset timeOffsetMinutes type attributes slotHash }
      }
    }
  }
}`;
const OT_AREAS = {
  default: "", standard: "", any: "",
  bar: "Bar", counter: "Counter", highTop: "High top", hightop: "High top",
  outdoor: "Outdoor", patio: "Patio", terrace: "Terrace", indoor: "Indoors",
  communal: "Communal", booth: "Booth", window: "Window",
  chefsTable: "Chef's table", privateDining: "Private dining"
};

async function otGet(path, accept) {
  try {
    const r = await fetch(OT_HOST + path, { headers: { "User-Agent": OT_UA, Accept: accept } });
    return r.ok ? await r.text() : "";
  } catch (_) { return ""; }
}

function otSlug(venue) {
  const m = /\/r\/([a-zA-Z0-9-]+)/.exec(venue);
  if (m) return m[1].toLowerCase();
  return /^[a-zA-Z0-9-]{3,}$/.test(venue) ? venue.trim().toLowerCase() : "";
}

async function otRid(venue) {
  const v = String(venue || "").trim();
  if (/^\d+$/.test(v)) return v;
  const direct = /[?&](?:rid|restaurantId|restRef|restref)=(\d+)/i.exec(v) || /\/restaurant\/profile\/(\d+)/.exec(v);
  if (direct) return direct[1];
  const slug = otSlug(v);
  if (!slug) return "";
  const html = await otGet("/r/" + slug, "text/html");
  const m = /"(?:restaurantId|rid)"\s*:\s*"?(\d{3,})"?/.exec(html);
  return m ? m[1] : "";
}

async function otCsrf() {
  const html = await otGet("/", "text/html");
  const m = /"csrfToken"\s*:\s*"([^"]{8,})"/.exec(html) || /__CSRF_TOKEN__\s*=\s*"([^"]{8,})"/.exec(html);
  return m ? m[1] : "";
}

function otArea(slot) {
  let attrs = slot.attributes;
  if (typeof attrs === "string") attrs = [attrs];
  const names = [];
  for (const a of (attrs || [])) {
    const label = (a in OT_AREAS) ? OT_AREAS[a] : String(a).trim();
    if (label && names.indexOf(label) < 0) names.push(label);
  }
  if (names.length) return names.join(" · ");
  for (const k of ["seatingArea", "tableAttribute", "experienceName", "offerName"]) {
    const v = String(slot[k] || "").trim();
    if (v) return v;
  }
  return "";
}

function otBookable(s) {
  if (String(s.__typename || "").indexOf("Unavailable") >= 0) return false;
  if (s.isAvailable === false || s.available === false) return false;
  if (s.isWaitlist === true || s.waitlist === true) return false;
  return ["WAITLIST", "REQUEST", "NOTIFY"].indexOf(String(s.type || "").toUpperCase()) < 0;
}

function otLooksLikeSlot(d) {
  if (!("dateTime" in d) && !("timeOffsetMinutes" in d)) return false;
  if (String(d.__typename || "").indexOf("Slot") >= 0) return true;
  return ["slotHash", "slotAvailabilityToken", "isAvailable", "available", "attributes", "timeOffsetMinutes"]
    .some((k) => k in d);
}

// Walk any OpenTable payload for slot-shaped objects. The GraphQL answer gives
// offsets from the time we asked about; page state and the mobile API give
// absolute dateTimes — this handles both, and survives a field moving a level.
function otNormalize(payload, date, anchor) {
  const base = Date.parse(date + "T" + anchor + ":00Z");
  if (isNaN(base)) return [];
  const out = [], seen = {}, stack = [payload];
  while (stack.length) {
    const node = stack.pop();
    if (Array.isArray(node)) { for (const x of node) stack.push(x); continue; }
    if (!node || typeof node !== "object") continue;
    for (const k in node) stack.push(node[k]);
    if (!otLooksLikeSlot(node) || !otBookable(node)) continue;

    let hhmm = null, day = date;
    const raw = node.dateTime || node.time_iso || node.time;
    if (typeof raw === "string" && toHHMM(raw)) {
      hhmm = toHHMM(raw);
      const d = /^(\d{4}-\d{2}-\d{2})/.exec(raw);
      if (d) day = d[1];
    } else if (typeof node.timeOffsetMinutes === "number") {
      const when = new Date(base + ((node.dayOffset || 0) * 1440 + node.timeOffsetMinutes) * 60000);
      hhmm = String(when.getUTCHours()).padStart(2, "0") + ":" + String(when.getUTCMinutes()).padStart(2, "0");
      day = when.toISOString().slice(0, 10);
    }
    if (!hhmm) continue;
    // Only the day that was asked for: the sheet, Openings and Favourites all
    // show one date at a time.
    if (day !== date) continue;
    const area = otArea(node), k = hhmm + "|" + area;
    if (seen[k]) continue;
    seen[k] = 1; out.push({ t: hhmm, area: area, req: false });
  }
  return out;
}

// Every embedded JSON island in an OpenTable page (state + <script> data).
function otJsonBlobs(html) {
  const out = [];
  const re = /<script[^>]+type="application\/json"[^>]*>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html))) { try { out.push(JSON.parse(m[1])); } catch (_) {} }
  for (const marker of ["window.__INITIAL_STATE__", '"__INITIAL_STATE__"']) {
    const i = html.indexOf(marker);
    if (i < 0) continue;
    const start = html.indexOf("{", i + marker.length);
    if (start < 0) continue;
    let depth = 0, inStr = false, esc = false;
    for (let j = start; j < html.length; j++) {
      const c = html[j];
      if (inStr) {
        if (esc) esc = false;
        else if (c === "\\") esc = true;
        else if (c === '"') inStr = false;
        continue;
      }
      if (c === '"') inStr = true;
      else if (c === "{") depth++;
      else if (c === "}") {
        depth--;
        if (depth === 0) { try { out.push(JSON.parse(html.slice(start, j + 1))); } catch (_) {} break; }
      }
    }
  }
  return out;
}

async function opentable(venue, date, party, anchor, env) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return shape([]);
  const rid = await otRid(venue);
  if (!rid) return shape([]);

  const variables = {
    restaurantIds: [parseInt(rid, 10)],
    date: date,
    time: anchor,
    partySize: party,
    forwardDays: 0,
    requireTimes: false
  };
  const region = (env && env.OPENTABLE_REGION) || "";
  if (region) variables.databaseRegion = region;

  const headers = {
    "User-Agent": OT_UA,
    Accept: "application/json",
    "Content-Type": "application/json",
    Origin: OT_HOST,
    Referer: OT_HOST + "/"
  };
  const csrf = await otCsrf();
  if (csrf) headers["x-csrf-token"] = csrf;

  const hash = (env && env.OPENTABLE_AVAILABILITY_HASH) || "";
  const attempts = [];
  if (hash) attempts.push({ extensions: { persistedQuery: { version: 1, sha256Hash: hash } } });
  attempts.push({ query: OT_QUERY });

  for (const extra of attempts) {
    const body = Object.assign({ operationName: "RestaurantsAvailability", variables: variables }, extra);
    let j = null;
    try {
      const r = await fetch(OT_HOST + "/dapi/fe/gql?optype=query&opname=RestaurantsAvailability",
        { method: "POST", headers: headers, body: JSON.stringify(body) });
      if (!r.ok) continue;
      j = await r.json();
    } catch (_) { continue; }
    if (!j || j.errors) continue;
    const slots = otNormalize(j, date, anchor);
    if (slots.length) return shape(slots);
  }

  // Fallback: the booking page itself, which ships its slots as page state.
  const q = `?dateTime=${date}T${anchor}&covers=${party}`;
  const slug = otSlug(String(venue || ""));
  const paths = [];
  if (slug && !/^\d+$/.test(slug)) paths.push("/r/" + slug + q);
  paths.push(`/restaurant/profile/${rid}` + q);
  for (const path of paths) {
    const html = await otGet(path, "text/html");
    if (!html) continue;
    for (const blob of otJsonBlobs(html)) {
      const slots = otNormalize(blob, date, anchor);
      if (slots.length) return shape(slots);
    }
  }
  return shape([]);
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
  // OpenTable answers around a time rather than for a whole day; the app sends
  // the middle of the window it cares about.
  const anchor = toHHMM(u.searchParams.get("time") || "") || "19:00";
  if (!venue || !date) return json({ error: "venue and date are required" }, 400);
  try {
    if (platform === "opentable") return json(await opentable(venue, date, party, anchor, env));
    return json(await sevenrooms(venue, date, party));
  } catch (e) {
    return json({ error: String((e && e.message) || e) }, 502);
  }
}
