#!/usr/bin/env bash
set -euo pipefail

APP_NAME="streamsquirrel"
INSTALL_DIR="/opt/streamsquirrel"
SERVICE_NAME="streamsquirrel.service"
ENV_FILE="/etc/default/streamsquirrel"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}"
RUN_USER="streamsquirrel"
PORT="${PORT:-8080}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo ./install.sh"
  exit 1
fi

echo "== Stream Squirrel installer =="

echo "[1/7] Installing OS dependencies..."
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  ca-certificates curl rsync

echo "[2/7] Ensuring user '${RUN_USER}' exists..."
if ! id -u "${RUN_USER}" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "${RUN_USER}"
fi

echo "[3/7] Installing app to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
  --exclude ".git" \
  --exclude "__pycache__" \
  --exclude ".venv" \
  ./ndi_aes67_pi/ "${INSTALL_DIR}/"

chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"

echo "[4/7] Creating venv and installing Python dependencies..."
sudo -u "${RUN_USER}" python3 -m venv "${INSTALL_DIR}/.venv"
sudo -u "${RUN_USER}" "${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip wheel
sudo -u "${RUN_USER}" "${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "[5/7] Writing ${ENV_FILE} (preserving existing if present)..."
if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" <<EOF
# Stream Squirrel environment overrides
# If libndi.so is not in the default loader path, set NDI_LIB:
# NDI_LIB=/usr/local/lib/libndi.so

# Optional: force multicast outbound interface (e.g. eth0)
# MCAST_IFACE=eth0

# Host/port for the web UI
HOST=0.0.0.0
PORT=${PORT}

# Optional: extra uvicorn args
# UVICORN_ARGS=--workers 1
EOF
  chmod 0644 "${ENV_FILE}"
fi

echo "[6/7] Installing systemd unit ${UNIT_FILE}..."
cat > "${UNIT_FILE}" <<'EOF'
[Unit]
Description=Stream Squirrel (NDI to AES67 Bridge)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=streamsquirrel
Group=streamsquirrel
WorkingDirectory=/opt/streamsquirrel

EnvironmentFile=-/etc/default/streamsquirrel
ExecStart=/opt/streamsquirrel/.venv/bin/uvicorn app:app --host ${HOST:-0.0.0.0} --port ${PORT:-8080} ${UVICORN_ARGS:-}

Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

chmod 0644 "${UNIT_FILE}"

echo "[7/7] Enabling and starting service..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo ""
echo "✅ Installed Stream Squirrel."
echo "Service: ${SERVICE_NAME}"
echo "Logs:    journalctl -u ${SERVICE_NAME} -f"
echo "Config:  ${ENV_FILE}"
echo "UI:      http://<device-ip>:${PORT}"
echo ""
echo "NOTE: NDI runtime must be installed separately if libndi.so is missing."
