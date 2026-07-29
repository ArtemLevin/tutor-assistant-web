#!/bin/sh
set -eu

DEPLOY_USER=${DEPLOY_USER:-tutor-deploy}
INSTALL_DIR=${INSTALL_DIR:-/opt/tutorboard-stack}
SSH_PORT=${SSH_PORT:-22}
START_STACK=false

usage() {
  cat <<'EOF'
Usage: sudo deploy/ubuntu/bootstrap.sh [options]

Options:
  --deploy-user USER   System user allowed to operate the stack (default: tutor-deploy)
  --install-dir PATH   Existing repository checkout (default: /opt/tutorboard-stack)
  --ssh-port PORT      SSH port allowed by UFW (default: 22)
  --start              Start tutorboard-stack.service after provisioning
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --deploy-user)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      DEPLOY_USER=$2
      shift 2
      ;;
    --install-dir)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      INSTALL_DIR=$2
      shift 2
      ;;
    --ssh-port)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      SSH_PORT=$2
      shift 2
      ;;
    --start)
      START_STACK=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "Run bootstrap with sudo or as root." >&2; exit 1; }
case "$INSTALL_DIR" in
  ""|/|/opt)
    echo "Refusing unsafe install directory: $INSTALL_DIR" >&2
    exit 2
    ;;
  /*) ;;
  *)
    echo "INSTALL_DIR must be an absolute path." >&2
    exit 2
    ;;
esac
case "$SSH_PORT" in
  *[!0-9]*|"") echo "SSH_PORT must be numeric." >&2; exit 2 ;;
esac
[ "$SSH_PORT" -ge 1 ] && [ "$SSH_PORT" -le 65535 ] || {
  echo "SSH_PORT must be between 1 and 65535." >&2
  exit 2
}

[ -r /etc/os-release ] || { echo "/etc/os-release is unavailable." >&2; exit 1; }
. /etc/os-release
[ "${ID:-}" = ubuntu ] || { echo "This bootstrap supports Ubuntu only." >&2; exit 1; }
case "${VERSION_ID:-}" in
  22.04|24.04) ;;
  *) echo "Supported Ubuntu releases are 22.04 and 24.04; found ${VERSION_ID:-unknown}." >&2; exit 1 ;;
esac
[ "$(uname -m)" = x86_64 ] || {
  echo "Production images are currently released for x86_64/amd64 only." >&2
  exit 1
}

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
[ "$ROOT" = "$INSTALL_DIR" ] || {
  echo "Clone the repository to $INSTALL_DIR before bootstrap; current checkout is $ROOT." >&2
  exit 1
}
[ -f "$INSTALL_DIR/compose.production.yml" ] || {
  echo "compose.production.yml is missing from $INSTALL_DIR." >&2
  exit 1
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  ca-certificates curl git gnupg iptables openssh-server openssl unattended-upgrades ufw

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  printf '%s\n' \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

systemctl enable --now docker.service containerd.service
systemctl enable --now unattended-upgrades.service
timedatectl set-ntp true

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"
chown -R "$DEPLOY_USER:docker" "$INSTALL_DIR"
chmod 2750 "$INSTALL_DIR"

authorized_key_found=false
for key_file in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do
  if [ -s "$key_file" ]; then
    authorized_key_found=true
    break
  fi
done
[ "$authorized_key_found" = true ] || {
  echo "At least one non-empty authorized_keys file is required before disabling SSH passwords." >&2
  exit 1
}
cat > /etc/ssh/sshd_config.d/99-tutorboard-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
EOF
sshd -t
systemctl reload ssh.service

ufw default deny incoming
ufw default allow outgoing
ufw allow "$SSH_PORT/tcp"
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

render_unit() {
  source_file=$1
  destination=$2
  sed \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    -e "s|@DEPLOY_USER@|$DEPLOY_USER|g" \
    "$source_file" > "$destination"
  chmod 0644 "$destination"
}

render_unit "$INSTALL_DIR/deploy/ubuntu/tutorboard-firewall.service" \
  /etc/systemd/system/tutorboard-firewall.service
render_unit "$INSTALL_DIR/deploy/ubuntu/tutorboard-stack.service" \
  /etc/systemd/system/tutorboard-stack.service

systemctl daemon-reload
systemctl enable tutorboard-firewall.service tutorboard-stack.service
systemctl restart tutorboard-firewall.service

runuser -u "$DEPLOY_USER" -- docker info >/dev/null
runuser -u "$DEPLOY_USER" -- docker compose version >/dev/null

if [ "$START_STACK" = true ]; then
  systemctl start tutorboard-stack.service
fi

cat <<EOF
Ubuntu host is provisioned.
Repository: $INSTALL_DIR
Deploy user: $DEPLOY_USER
Next:
  1. Configure deploy/production/.env.production and secrets/.
  2. Log in to GHCR as $DEPLOY_USER.
  3. Run deploy/ubuntu/preflight.sh <backend-tag> <tutorboard-tag>.
  4. Run deploy/production/deploy.sh <backend-tag> <tutorboard-tag>.
EOF
