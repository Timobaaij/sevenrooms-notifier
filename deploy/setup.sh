#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup.sh — one-shot installer for the every-minute watcher on any Linux VM
# (Ubuntu/Debian or Oracle Linux/RHEL; Oracle Cloud Always Free, GCP e2-micro,
# a Raspberry Pi, whatever). Auto-detects apt / dnf / yum.
#
# It: installs Python + git, creates a venv, installs deps, drops an env file
# for your secrets, and installs a systemd service that runs the watcher loop
# 24/7 and restarts it on reboot/crash.
#
# Usage (on the VM):
#     git clone https://github.com/timobaaij/sevenrooms-notifier.git
#     cd sevenrooms-notifier
#     bash deploy/setup.sh
#     nano ~/.maitre/notifier.env      # paste your secrets
#     sudo systemctl restart maitre-notifier
#     journalctl -u maitre-notifier -f # watch it work
#
# Re-running is safe (idempotent) — use it to update after a `git pull`.
# ---------------------------------------------------------------------------
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${STATE_DIR:-$HOME/.maitre}"
ENV_FILE="$STATE_DIR/notifier.env"
VENV="$APP_DIR/.venv"
SERVICE_NAME="maitre-notifier"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="$(id -un)"

echo "==> Repo:        $APP_DIR"
echo "==> State dir:   $STATE_DIR"
echo "==> Run as user: $RUN_USER"

echo "==> [1/5] Installing system packages (needs sudo)…"
if command -v apt-get >/dev/null 2>&1; then
    # Ubuntu / Debian
    sudo apt-get update -y
    sudo apt-get install -y python3 python3-venv python3-pip git
elif command -v dnf >/dev/null 2>&1; then
    # Oracle Linux / RHEL / Fedora
    sudo dnf install -y python3 python3-pip git
elif command -v yum >/dev/null 2>&1; then
    # older Oracle Linux / CentOS
    sudo yum install -y python3 python3-pip git
else
    echo "ERROR: no supported package manager (apt-get / dnf / yum) found." >&2
    exit 1
fi

echo "==> [2/5] Creating Python virtualenv + installing dependencies…"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> [3/5] Preparing state dir + env file…"
mkdir -p "$STATE_DIR"
if [ ! -f "$ENV_FILE" ]; then
    cp "$APP_DIR/deploy/notifier.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "    Created $ENV_FILE — FILL IN YOUR SECRETS before the service will alert."
else
    echo "    $ENV_FILE already exists — leaving it untouched."
fi

echo "==> [4/5] Installing systemd service…"
sudo tee "$SERVICE_PATH" >/dev/null <<UNIT
[Unit]
Description=Maitre / SevenRooms table notifier — every-minute watcher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHON=${VENV}/bin/python
Environment=CONFIG_PATH=${APP_DIR}/config.json
Environment=STATE_PATH=${STATE_DIR}/state.json
ExecStart=/usr/bin/env bash ${APP_DIR}/deploy/run-loop.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

echo "==> [5/5] Enabling + starting service…"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

echo ""
echo "Done. The watcher is running and will start on every boot."
echo ""
echo "  Next:   nano $ENV_FILE          # paste EMAIL_USER / EMAIL_PASS / PUSHOVER_EMAIL"
echo "          sudo systemctl restart $SERVICE_NAME"
echo ""
echo "  Logs:   journalctl -u $SERVICE_NAME -f"
echo "  Status: systemctl status $SERVICE_NAME"
