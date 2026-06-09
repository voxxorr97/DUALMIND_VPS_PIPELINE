# Uploader — Agent 6 du pipeline DualMind v2.2

`Uploader` est le sixième et dernier agent Python du pipeline DualMind v2.2 pour la niche francophone **Affaires Mystérieuses Non Classées**. Il publie sur YouTube les vidéos MP4 assemblées par `VideoAssembler`, notifie WhatsApp, puis initialise le suivi de performance.

## Rôle dans le pipeline

Entrée attendue dans SQLite (`data/dualmind.db`) :

- table `scripts_generated`
- `status = 'assembled'`
- `video_path` pointant vers `output/videos/{script_id}.mp4`
- `script_text` au format segmenté : `[HOOK]`, `[DÉVELOPPEMENT]`, `[RÉVÉLATION]`, `[CTA]`

Sorties produites :

- upload YouTube Data API v3 en mode **resumable upload**
- mise à jour SQLite : `scripts_generated.status = 'published'`, `scripts_generated.youtube_url = ...`
- insertion dans `video_performance` : `video_id`, `youtube_url`, `published_at`, `views=0`, `likes=0`, `comments=0`
- notification WhatsApp Business API
- logs dans `logs/uploader.log`

L'agent est idempotent au niveau pipeline : il ne sélectionne que les lignes `status = 'assembled'` dont `youtube_url` est vide. Une vidéo déjà `published` n'est donc pas re-uploadée.

## Métadonnées YouTube

Pour chaque vidéo :

- **Titre** : segment `[HOOK]` nettoyé et tronqué à 80 caractères maximum.
- **Description** : segment `[DÉVELOPPEMENT]` + hashtags automatiques.
- **Tags** : `mystère`, `inexpliqué`, `faits divers`, `affaires non classées`, `true crime france`.
- **Catégorie** : `22` par défaut (`People & Blogs`). Vous pouvez définir `YOUTUBE_CATEGORY_ID=24` pour `Entertainment`.
- **Privacy** : `public` par défaut.
- **Made for Kids** : `false` via `selfDeclaredMadeForKids`.

Message WhatsApp envoyé après succès :

```text
✅ Vidéo publiée : {titre} | {youtube_url} | Views cible : 10k en 48h
```

TikTok et Instagram sont volontairement hors scope de cet agent. La notification WhatsApp suffit pour déclencher une publication manuelle si nécessaire.

## Setup OAuth YouTube sans auth interactive

L'agent n'ouvre jamais de navigateur et n'utilise pas de consentement interactif sur le VPS. Il attend un `refresh_token` déjà généré.

Préparation recommandée hors VPS :

1. Créer ou sélectionner un projet Google Cloud.
2. Activer **YouTube Data API v3**.
3. Configurer l'écran de consentement OAuth.
4. Créer un client OAuth 2.0 adapté à votre workflow de génération du refresh token.
5. Obtenir un refresh token avec le scope YouTube upload/statistiques requis, par exemple `https://www.googleapis.com/auth/youtube.upload` pour l'upload et un scope lecture YouTube Data API pour le tracker si votre configuration l'exige.
6. Copier uniquement les valeurs finales dans `.env` sur le VPS.

À l'exécution, `scripts/agents/uploader.py` échange `YOUTUBE_REFRESH_TOKEN` contre un `access_token` court-vécu via `https://oauth2.googleapis.com/token`, puis démarre une session resumable sur `https://www.googleapis.com/upload/youtube/v3/videos`.

## Variables d'environnement requises

À mettre dans `.env` :

```dotenv
YOUTUBE_CLIENT_ID=replace_with_youtube_oauth_client_id
YOUTUBE_CLIENT_SECRET=replace_with_youtube_oauth_client_secret
YOUTUBE_REFRESH_TOKEN=replace_with_youtube_refresh_token

WHATSAPP_API_URL=https://graph.facebook.com/v20.0/<PHONE_NUMBER_ID>/messages
WHATSAPP_PHONE_NUMBER_ID=<PHONE_NUMBER_ID>
WHATSAPP_ACCESS_TOKEN=replace_with_whatsapp_access_token
WHATSAPP_RECIPIENT_NUMBER=33600000000
```

Variables optionnelles :

```dotenv
SQLITE_DB_PATH=data/dualmind.db
YOUTUBE_UPLOAD_PRIVACY_STATUS=public
YOUTUBE_CATEGORY_ID=22
LOG_LEVEL=INFO
```

`WHATSAPP_API_URL` peut être omis si `WHATSAPP_PHONE_NUMBER_ID` est renseigné : l'agent construit alors l'URL Graph API automatiquement.

## Dépendances Python

L'agent utilise seulement la stdlib Python plus :

```bash
pip install python-dotenv requests google-api-python-client
```

> La version actuelle de l'agent effectue l'upload resumable directement avec `requests`, ce qui garde le flux OAuth explicite et non interactif. `google-api-python-client` reste listé comme dépendance autorisée du pipeline YouTube.

## Commandes d'exécution

Publier jusqu'à 3 vidéos assemblées :

```bash
python scripts/agents/uploader.py
```

Publier une seule vidéo :

```bash
python scripts/agents/uploader.py --limit 1
```

## Test standalone sans réseau

Le test `scripts/agents/uploader_test.py` :

- crée une base SQLite temporaire dans `tmp/uploader_test/dualmind.db` ;
- écrit un faux MP4 local ;
- insère une ligne `scripts_generated.status = 'assembled'` ;
- mock l'upload YouTube avec `https://www.youtube.com/watch?v=TEST123` ;
- mock WhatsApp avec un `print` ;
- vérifie `scripts_generated.youtube_url`, `status = 'published'` et la ligne `video_performance`.

Commande :

```bash
python scripts/agents/uploader_test.py
```

## PerformanceTracker bonus

`PerformanceTracker` rafraîchit les métriques des vidéos YouTube publiées depuis moins de 7 jours.

Entrée attendue :

- table `video_performance`
- `platform = 'youtube'`
- `published_at` non nul et récent
- `video_id` ou `youtube_url`

Sorties :

- mise à jour `views`, `likes`, `comments`, `analyzed_at`
- logs dans `logs/performance_tracker.log`
- notification WhatsApp si la vidéo passe le seuil des 10k vues :

```text
🔥 VIRAL : {titre} — {views} vues
```

Commandes :

```bash
python scripts/agents/performance_tracker.py
python scripts/agents/performance_tracker.py --limit 20
```

## Gestion erreurs et quota

- L'upload YouTube est retry jusqu'à 2 fois après le premier échec réseau/API.
- Si l'API renvoie `403 quotaExceeded`, l'erreur est loggée clairement avec `quotaExceeded`, puis la vidéo est skip sans bloquer les suivantes.
- Une erreur sur une vidéo n'empêche pas le traitement des autres vidéos `assembled`.
- WhatsApp n'est envoyé qu'après un upload YouTube réussi et une mise à jour SQLite réussie.
