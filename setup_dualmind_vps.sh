#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '\n[DUALMIND VPS] %s\n' "$1"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

require_linux() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    printf 'Erreur: ce script doit être lancé sur Linux. Système détecté: %s\n' "$(uname -s)" >&2
    exit 1
  fi
}

setup_sudo() {
  if [[ "${EUID}" -eq 0 ]]; then
    SUDO_CMD=()
  else
    if ! command_exists sudo; then
      printf 'Erreur: sudo est requis pour installer les dépendances système.\n' >&2
      exit 1
    fi
    SUDO_CMD=(sudo)
  fi
}

check_distribution() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck source=/dev/null
    . /etc/os-release
    log "Système détecté: ${PRETTY_NAME:-Linux}"

    if [[ "${ID:-}" != "ubuntu" ]]; then
      printf 'Attention: ce script cible Ubuntu 22.04 ARM64. Distribution détectée: %s\n' "${ID:-inconnue}" >&2
    fi

    if [[ "${VERSION_ID:-}" != "22.04" ]]; then
      printf 'Attention: ce script cible Ubuntu 22.04. Version détectée: %s\n' "${VERSION_ID:-inconnue}" >&2
    fi
  else
    printf 'Attention: /etc/os-release introuvable; impossible de vérifier la distribution.\n' >&2
  fi

  ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  log "Architecture détectée: ${ARCH}"
  if [[ "${ARCH}" != "arm64" && "${ARCH}" != "aarch64" ]]; then
    printf 'Attention: ce script est prévu pour ARM64. Architecture détectée: %s\n' "${ARCH}" >&2
  fi
}

install_base_packages() {
  log "Mise à jour de l'index apt"
  "${SUDO_CMD[@]}" apt-get update

  log "Installation/vérification des dépendances système de base"
  "${SUDO_CMD[@]}" apt-get install -y \
    ca-certificates \
    curl \
    git \
    gnupg \
    lsb-release \
    unzip \
    build-essential \
    python3 \
    python3-pip \
    python3-venv \
    sqlite3 \
    ffmpeg
}

install_docker() {
  log "Installation/vérification de Docker et du plugin Docker Compose"

  "${SUDO_CMD[@]}" install -m 0755 -d /etc/apt/keyrings

  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    log "Ajout de la clé GPG officielle Docker"
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | "${SUDO_CMD[@]}" gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  else
    log "Clé GPG Docker déjà présente"
  fi

  "${SUDO_CMD[@]}" chmod a+r /etc/apt/keyrings/docker.gpg

  if [[ ! -f /etc/apt/sources.list.d/docker.list ]]; then
    log "Ajout du dépôt apt officiel Docker"
    UBUNTU_CODENAME="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-jammy}")"
    ARCHITECTURE="$(dpkg --print-architecture)"
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' \
      "${ARCHITECTURE}" \
      "${UBUNTU_CODENAME}" | "${SUDO_CMD[@]}" tee /etc/apt/sources.list.d/docker.list >/dev/null
  else
    log "Dépôt apt Docker déjà présent"
  fi

  log "Mise à jour de l'index apt après configuration Docker"
  "${SUDO_CMD[@]}" apt-get update

  "${SUDO_CMD[@]}" apt-get install -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

  log "Activation du service Docker"
  "${SUDO_CMD[@]}" systemctl enable --now docker
}

create_project_directories() {
  log "Création des dossiers locaux du pipeline si absents"
  mkdir -p data output logs tmp secrets
}

setup_python_environment() {
  log "Création/vérification de l'environnement Python local .venv"
  python3 -m venv .venv

  log "Mise à jour de pip dans .venv"
  .venv/bin/python -m pip install --upgrade pip

  if [[ -f requirements.txt ]]; then
    log "Installation des dépendances Python depuis requirements.txt"
    .venv/bin/python -m pip install -r requirements.txt
  else
    log "requirements.txt absent: aucune dépendance Python projet à installer"
  fi
}

print_versions() {
  log "Versions installées"
  python3 --version
  .venv/bin/pip --version
  ffmpeg -version | head -n 1
  sqlite3 --version
  docker --version
  docker compose version
}

print_next_steps() {
  cat <<'NEXT_STEPS'

[DUALMIND VPS] Installation de base terminée.

Prochaines commandes recommandées:
  cp .env.example .env
  nano .env
  python scripts/init_sqlite.py
  docker compose up -d
  docker compose logs -f n8n

Rappel sécurité:
  - Ne jamais commiter de vraies clés API.
  - Remplir .env uniquement sur le VPS.
NEXT_STEPS
}

main() {
  require_linux
  setup_sudo
  check_distribution
  install_base_packages
  install_docker
  create_project_directories
  setup_python_environment
  print_versions
  print_next_steps
}

main "$@"
