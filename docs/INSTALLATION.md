# Installation Notes

These notes prepare the Phase 1.1 baseline only. They do not install the full pipeline and intentionally avoid a large installation script.

Target server:

- Hetzner CAX41
- Ubuntu 22.04 LTS
- ARM64 architecture
- Docker-based services
- Python automation scripts
- n8n workflow orchestration
- ffmpeg media processing
- SQLite local metadata storage

## 1. System packages

Update the server and install the basic runtime packages:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y \
  ca-certificates \
  curl \
  git \
  python3 \
  python3-venv \
  python3-pip \
  ffmpeg \
  sqlite3
```

Check ffmpeg and SQLite:

```bash
ffmpeg -version
ffprobe -version
sqlite3 --version
```

## 2. Docker

Install Docker using the official Docker packages for Ubuntu ARM64. After installation, verify the daemon:

```bash
docker --version
docker compose version
sudo systemctl status docker
```

If the deployment user should run Docker without `sudo`, add it to the Docker group and reconnect:

```bash
sudo usermod -aG docker "$USER"
```

## 3. Project checkout

Clone the repository on the VPS:

```bash
git clone <repository-url> DUALMIND_VPS_PIPELINE
cd DUALMIND_VPS_PIPELINE
```

Create local runtime directories:

```bash
mkdir -p data output tmp
```

## 4. Python environment

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

The current `requirements.txt` is intentionally minimal and can be expanded when the pipeline scripts are added.

## 5. Environment configuration

Create the local `.env` file from the template:

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and replace placeholder values with real local secrets only on the VPS. Never commit `.env`.

Recommended external secret folder:

```bash
sudo mkdir -p /opt/dualmind/secrets
sudo chmod 700 /opt/dualmind/secrets
```

Place Google and YouTube credential JSON files in that folder, not in the repository.

## 6. n8n baseline

n8n is planned as the workflow orchestrator. A Docker Compose file is not created in Phase 1.1, but the future service should use these `.env` values:

- `N8N_HOST`
- `N8N_PORT`
- `N8N_PROTOCOL`
- `N8N_BASIC_AUTH_ACTIVE`
- `N8N_BASIC_AUTH_USER`
- `N8N_BASIC_AUTH_PASSWORD`
- `N8N_ENCRYPTION_KEY`
- `WEBHOOK_URL`

Before exposing n8n publicly, put it behind HTTPS, set strong basic-auth credentials, and keep `N8N_ENCRYPTION_KEY` stable.

## 7. Planned integrations

The Phase 1.1 configuration reserves variables for:

- Claude Sonnet through Anthropic
- ElevenLabs text-to-speech
- Replicate model calls
- Whisper transcription
- Google Drive storage
- YouTube API uploads or metadata workflows
- WhatsApp Business Cloud API notifications or webhooks

Implementation scripts and workflow exports will be added in later phases.

## 8. Quick validation

Run these checks after setup:

```bash
python --version
pip check
ffmpeg -version
sqlite3 --version
```

If every command succeeds and `.env` contains only local values, the VPS is ready for the next phase.
