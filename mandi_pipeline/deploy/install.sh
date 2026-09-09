#!/usr/bin/env bash
# Deploy mandi_pipeline (Approach 1) on the Linux VM.
# Idempotent: safe to re-run for upgrades.
set -euo pipefail

APP_DIR=/opt/mandi_pipeline
SVC_USER=azureuser
REPO_SUBDIR=mandi_pipeline   # this folder inside the checkout

echo ">> syncing code to ${APP_DIR}"
sudo mkdir -p "${APP_DIR}"
sudo rsync -a --delete \
  --exclude '.env' --exclude 'state/' --exclude '__pycache__/' \
  "$(dirname "$0")/.." "${APP_DIR}/${REPO_SUBDIR}/"
sudo chown -R "${SVC_USER}:${SVC_USER}" "${APP_DIR}"

echo ">> python venv + deps"
sudo -u "${SVC_USER}" python3 -m venv "${APP_DIR}/venv"
sudo -u "${SVC_USER}" "${APP_DIR}/venv/bin/pip" install --upgrade pip
sudo -u "${SVC_USER}" "${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/${REPO_SUBDIR}/requirements.txt"

if [[ ! -f "${APP_DIR}/${REPO_SUBDIR}/.env" ]]; then
  echo ">> creating .env from template -- EDIT IT before starting the timer"
  sudo -u "${SVC_USER}" cp "${APP_DIR}/${REPO_SUBDIR}/.env.example" "${APP_DIR}/${REPO_SUBDIR}/.env"
  sudo chmod 600 "${APP_DIR}/${REPO_SUBDIR}/.env"
fi

echo ">> applying database schema"
sudo -u "${SVC_USER}" bash -c "cd ${APP_DIR} && ./venv/bin/python -m mandi_pipeline.run --init-db"

echo ">> installing systemd units"
sudo cp "${APP_DIR}/${REPO_SUBDIR}/deploy/mandi-pipeline.service"          /etc/systemd/system/
sudo cp "${APP_DIR}/${REPO_SUBDIR}/deploy/mandi-pipeline.timer"            /etc/systemd/system/
sudo cp "${APP_DIR}/${REPO_SUBDIR}/deploy/mandi-pipeline-console.service"  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mandi-pipeline-console.service
sudo systemctl enable --now mandi-pipeline.timer

echo
echo "Done. Next:"
echo "  1. edit ${APP_DIR}/${REPO_SUBDIR}/.env   (DB creds, OPERATOR_CONSOLE_TOKEN, ...)"
echo "  2. sudo systemctl restart mandi-pipeline-console.service"
echo "  3. one-off first run:  sudo -u ${SVC_USER} bash -c 'cd ${APP_DIR} && ./venv/bin/python -m mandi_pipeline.run --dump-sample'"
echo "  4. watch:  journalctl -fu mandi-pipeline.service"
echo "  5. tunnel: ssh -L 8770:127.0.0.1:8770 ${SVC_USER}@<vm>  then open http://127.0.0.1:8770/?token=<TOKEN>"
