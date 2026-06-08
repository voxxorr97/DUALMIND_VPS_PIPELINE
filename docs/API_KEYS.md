# API Keys and Secrets

This project must never store real API keys, tokens, passwords, OAuth files, or service-account JSON files in git.

Use `.env.example` as a template only:

```bash
cp .env.example .env
chmod 600 .env
```

Then edit `.env` directly on the VPS or in your deployment secret manager.

## Required providers

### Claude Sonnet

Variables:

- `ANTHROPIC_API_KEY`
- `ANTHROPIC_MODEL`

Use an Anthropic API key with access to the Claude Sonnet model selected for the pipeline.

### ElevenLabs

Variables:

- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID`
- `ELEVENLABS_MODEL_ID`

Use these values for text-to-speech generation. Keep voice IDs configurable so workflows can change voices without code edits.

### Replicate

Variables:

- `REPLICATE_API_TOKEN`
- `REPLICATE_MODEL`

Use these values for image, video, or other model calls that will be defined in later phases.

### Whisper

Variables:

- `WHISPER_PROVIDER`
- `WHISPER_MODEL`
- `OPENAI_API_KEY` only if an OpenAI-hosted Whisper API path is used

For local transcription, install ffmpeg and use a local Whisper-compatible package in a later phase. For API-backed transcription, keep the provider token in `.env` only.

### Google Drive

Variables:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_DRIVE_FOLDER_ID`

Recommended VPS path for Google credentials:

```text
/opt/dualmind/secrets/google-service-account.json
```

Do not place this JSON file inside the repository.

### YouTube API

Variables:

- `YOUTUBE_API_KEY`
- `YOUTUBE_CLIENT_SECRET_FILE`
- `YOUTUBE_UPLOAD_PRIVACY_STATUS`

Recommended VPS path for OAuth client credentials:

```text
/opt/dualmind/secrets/youtube-client-secret.json
```

Start with `YOUTUBE_UPLOAD_PRIVACY_STATUS=private` until upload workflows have been reviewed.

### WhatsApp Business Cloud API

Variables:

- `WHATSAPP_BUSINESS_PHONE_NUMBER_ID`
- `WHATSAPP_BUSINESS_ACCOUNT_ID`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_WEBHOOK_VERIFY_TOKEN`

Use a long random webhook verify token. Store it in `.env` and configure the same value in the WhatsApp webhook settings.

## n8n secrets

Variables:

- `N8N_BASIC_AUTH_USER`
- `N8N_BASIC_AUTH_PASSWORD`
- `N8N_ENCRYPTION_KEY`
- `WEBHOOK_URL`

For production, replace every `change_me` value before exposing n8n to the internet. `N8N_ENCRYPTION_KEY` should be a long random string and must remain stable after workflows have been created.

## VPS secret hygiene

On Ubuntu 22.04 ARM64:

```bash
sudo mkdir -p /opt/dualmind/secrets
sudo chmod 700 /opt/dualmind/secrets
chmod 600 .env
```

Recommended rules:

- Keep `.env` out of git.
- Keep provider JSON files outside the repository.
- Rotate a key immediately if it is pasted into logs, commits, screenshots, or chat.
- Do not print secrets in scripts or n8n workflow logs.
- Prefer least-privilege API scopes for Google Drive, YouTube, and WhatsApp.
