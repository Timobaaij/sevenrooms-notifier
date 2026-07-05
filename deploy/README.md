# Running the watcher every minute — for free

GitHub Actions **cannot** poll reliably every minute: its `cron` is best-effort
and routinely runs 15–20 minutes late. To get a *real* one-minute cadence you
need an always-on process. The cheapest way to get one is a **free VM**.

This folder turns any Ubuntu/Debian box into that watcher in about 5 minutes.
Your searches are still edited in the existing web UI (which commits
`config.json` to GitHub); the VM `git pull`s those edits every loop.

---

## What you get

- `setup.sh` — one-shot installer (Python venv, deps, systemd service).
- `run-loop.sh` — the loop: `git pull` → run `main.py` → `sleep 60` → repeat.
- `notifier.env.example` — template for your secrets (never committed).

The watcher runs under **systemd**, so it restarts on crash and on reboot, and
`main.py` never drifts from the once-a-minute schedule.

---

## Step 1 — Get a free VM (pick one)

**Oracle Cloud — Always Free (recommended: free *forever*, most generous)**
1. Sign up at <https://www.oracle.com/cloud/free/> (a card is required for
   identity verification — Always Free resources are **never billed**).
2. *Create a VM instance* → Image **Canonical Ubuntu 22.04** → Shape
   **VM.Standard.E2.1.Micro** (marked *Always Free-eligible*).
3. Add your SSH key, create, and note the public IP.

**Google Cloud — free e2-micro** (alternative)
- Create an `e2-micro` VM in a free-tier region (`us-west1`, `us-central1`,
  `us-east1`), Ubuntu 22.04. Free within the Always Free limits.

**Any other Ubuntu box** (an old laptop, a Raspberry Pi, a home server) works
too — skip to Step 2.

## Step 2 — Install the watcher

SSH into the VM, then:

```bash
git clone https://github.com/timobaaij/sevenrooms-notifier.git
cd sevenrooms-notifier
bash deploy/setup.sh
```

> Private repo? Clone over SSH (`git@github.com:…`) with a deploy key, or use a
> read-only token. The loop's `git pull` is best-effort — if it can't auth it
> just keeps using the last `config.json`, so the watcher still runs.

## Step 3 — Add your secrets

```bash
nano ~/.maitre/notifier.env
```

Fill in the same values you use in GitHub today:

| Variable         | What it is                                             |
|------------------|--------------------------------------------------------|
| `EMAIL_USER`     | Gmail address that sends alerts                        |
| `EMAIL_PASS`     | Gmail **App Password** (not your login password)       |
| `PUSHOVER_EMAIL` | Your Pushover email-to-push bridge (blank = no push)   |
| `EMAIL_TO`       | Optional fallback address for "email" alerts           |
| `POLL_INTERVAL`  | Seconds between polls — `60` = every minute            |

Then restart so it picks them up:

```bash
sudo systemctl restart maitre-notifier
```

## Step 4 — Confirm it's working

```bash
journalctl -u maitre-notifier -f
```

You'll see a poll roughly every 60 seconds. That's it — it now runs 24/7,
free, and survives reboots.

---

## Handy commands

```bash
systemctl status maitre-notifier      # is it alive?
journalctl -u maitre-notifier -f      # live logs
sudo systemctl restart maitre-notifier# after editing secrets
git pull && bash deploy/setup.sh      # update to the latest code
sudo systemctl stop maitre-notifier   # pause it
```

## Notes

- **State** (which openings you've already been alerted about) lives in
  `~/.maitre/state.json`, outside the git checkout, so `git pull` never
  conflicts with it.
- **Turn off GitHub Actions** once this is running, so you're not polling from
  two places: in the repo, *Settings → Actions → Disable*, or delete
  `.github/workflows/sevenrooms-notifier.yml`.
- Polling every minute is gentle, but be a good citizen — SevenRooms/OpenTable
  see one lightweight request per venue per minute.
