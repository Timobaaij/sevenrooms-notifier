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

## Deploy to Cloudflare Pages (free, automatic HTTPS)

### 1. Create a GitHub token
At <https://github.com/settings/tokens?type=beta> → **Generate new token** →
- **Repository access:** Only select repositories → `timobaaij/sevenrooms-notifier`
- **Permissions:** Repository permissions → **Contents: Read and write**

Copy the token (starts with `github_pat_…`).

### 2. Create the Pages project
1. Sign in at <https://dash.cloudflare.com> (free; you can log in with GitHub).
2. **Workers & Pages → Create → Pages → Connect to Git**, and pick the
   `sevenrooms-notifier` repo.
3. Set up builds and deployments:
   | Setting | Value |
   |---|---|
   | Production branch | `main` |
   | Framework preset | **None** |
   | Build command | *(leave empty)* |
   | Build output directory | *(leave empty / `/`)* |
   | **Root directory (advanced)** | `webapp` |

   > The **Root directory = `webapp`** is what makes the app files and the
   > `functions/` proxy resolve correctly. With it set, `functions/api/config.js`
   > is served at `/api/config`.

4. **Save and Deploy.**

### 3. Set environment variables
In the new project → **Settings → Environment variables → Production** → add:

| Name | Value |
|---|---|
| `GITHUB_TOKEN` | the token from step 1 (mark as **Secret**) |
| `GH_REPO` | `timobaaij/sevenrooms-notifier` |
| `GH_BRANCH` | `main` |
| `ACCESS_KEY` | a passphrase you choose (recommended — gates the app) |

Then **Deployments → Retry deployment** so the variables take effect.

### 4. Open & install on your phone
1. Visit your `https://<project>.pages.dev` URL.
2. If you set `ACCESS_KEY`, the app asks for it once and remembers it.
3. **iPhone:** Share → **Add to Home Screen**. **Android:** the browser offers
   **Install app**. It now opens fullscreen with its own icon — like a native app.

---

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
