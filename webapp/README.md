# Maître — the mobile web app (PWA)

A luxurious, installable phone app for managing your table watches. It reads and
writes your searches in `config.json` (the same file the Streamlit dashboard and
your VM watcher use), so anything you change here shows up on the watcher within
a minute.

It talks to a **tiny proxy** (`functions/api/config.js`) that holds your GitHub
token server-side — the token never touches your phone.

```
Phone (PWA)  ──HTTPS──▶  Cloudflare Pages  ──token──▶  GitHub (config.json)
                          (static app + proxy)              ▲
                                                            │ git pull every 60s
                                                     Your Oracle VM watcher
```

---

## Deploy to Cloudflare (free, automatic HTTPS)

This deploys as a **Cloudflare Worker with static assets**. `wrangler.toml` and
`worker.js` in this folder pin the config, so Cloudflare won't try to auto-guess
a framework (that guess is what produced the `npx hugo` build error).

### 1. Create a GitHub token
At <https://github.com/settings/tokens?type=beta> → **Generate new token** →
- **Repository access:** Only select repositories → `timobaaij/sevenrooms-notifier`
- **Permissions:** Repository permissions → **Contents: Read and write**

Copy the token (starts with `github_pat_…`).

### 2. Connect the repo
Sign in at <https://dash.cloudflare.com> → **Workers & Pages → Create → Import a
repository** → pick `sevenrooms-notifier`.

### 3. Build settings — the important bit
| Setting | Value |
|---|---|
| **Root directory** | **`webapp`**  ← critical: where `wrangler.toml` lives |
| Build command | *(leave empty)* |
| Deploy command | `npx wrangler deploy` *(the default)* |
| Production branch | `main` |

> **Already created it and the build failed with `npx hugo`?** Open the project
> → **Settings → Build** → set **Root directory = `webapp`**, clear any build
> command, save, then **Retry deployment**. With `wrangler.toml` present it will
> stop guessing Hugo and deploy the app.

### 4. Set variables & secrets
Project → **Settings → Variables and Secrets** → add:

| Name | Type | Value |
|---|---|---|
| `GITHUB_TOKEN` | Secret | the token from step 1 |
| `GH_REPO` | Text | `timobaaij/sevenrooms-notifier` |
| `GH_BRANCH` | Text | `main` |
| `ACCESS_KEY` | Secret | a passphrase you choose (gates the app) |

Optional, for OpenTable (both blank is fine — see **Platforms** below):

| Name | Type | Value |
|---|---|---|
| `OPENTABLE_AVAILABILITY_HASH` | Text | persisted-query hash, if the query document stops being accepted |
| `OPENTABLE_REGION` | Text | `databaseRegion` to send, if your venues need one |

Then **redeploy** so they take effect.

### 5. Open & install on your phone
1. Visit your `https://<name>.workers.dev` URL.
2. If you set `ACCESS_KEY`, the app asks for it once and remembers it.
3. **iPhone:** Share → **Add to Home Screen**. **Android:** the browser offers
   **Install app**. It now opens fullscreen with its own icon — like a native app.

*(Prefer Cloudflare **Pages** instead? This also works there: create a Pages
project, Root directory `webapp`, no build command — the `functions/` folder is
picked up automatically and `wrangler.toml`/`worker.js` are ignored.)*

---

## Platforms

Every watch names the platform it books through, and the rest of the app —
windows, dates, party size, dedupe, alerts, the Openings feed, Favourites — works
the same either way.

| | SevenRooms | OpenTable |
|---|---|---|
| Venue is identified by | slug, e.g. `vesperrestaurant` | numeric restaurant id, e.g. `211123` |
| Paste-friendly | the reservation link (`?venue=…`) | any OpenTable link (`rid=…`, `/restaurant/profile/…`, or `/r/<name>`) |
| Availability comes from | the widget range API | the availability query the booking page uses |

A `/r/<name>` link is stored as-is and resolved to its numeric id on the first
check, so it keeps working if you only have the pretty URL.

**About the OpenTable query.** OpenTable's site sends its availability call as a
*persisted query* — a sha256 of the query rather than the query itself — and that
hash changes whenever they redeploy. Both the watcher and this app therefore send
the query document, which is what OpenTable's own front end falls back to. If a
redeploy ever makes them refuse the document, capture the current hash (DevTools →
Network → `gql?…opname=RestaurantsAvailability` → request body → `sha256Hash`) and
set `OPENTABLE_AVAILABILITY_HASH` here and on the watcher. Nothing else changes.
If neither route answers, the app reads the slots out of the booking page itself.

## Notes

- **Where changes go:** the app writes `config.json` on `main`. Your VM watcher
  `git pull`s `main` every minute, so new/edited/paused searches take effect
  within ~60 seconds. The Streamlit dashboard still works too — they edit the
  same file.
- **Pause** flips a search's notifications off (`notify: none`) and remembers the
  previous setting so resuming restores it. **Delete** removes it entirely.
- **Security:** the GitHub token lives only in Cloudflare's encrypted env vars.
  `ACCESS_KEY` stops strangers who find the URL from editing your searches. To
  rotate access, change `ACCESS_KEY` and re-enter it on your phone.
- **Local preview (optional):** `npx wrangler pages dev webapp` runs it locally
  with the functions, if you want to try before deploying.
- **What's next:** a live in-app feed of found tables (v2). For now alerts arrive
  through your existing push/email, exactly as before.
