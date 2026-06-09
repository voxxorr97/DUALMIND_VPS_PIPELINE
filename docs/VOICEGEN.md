# Agent Voicegen — DualMind v2.2

`Voicegen` est le troisième agent Python du pipeline DualMind v2.2. Il transforme les scripts courts prêts en narration MP3 avec ElevenLabs pour la niche francophone **Affaires Mystérieuses Non Classées**.

## Rôle

L'agent :

1. lit les lignes de `scripts_generated` dont le statut est `ready` ;
2. limite chaque exécution à **3 audios maximum** pour contrôler les coûts ElevenLabs ;
3. retire les balises de structure Scriptwriter (`[HOOK]`, `[DÉVELOPPEMENT]`, `[RÉVÉLATION]`, `[CTA]`, etc.) avant l'envoi à l'API ;
4. génère la voix avec ElevenLabs et le modèle `eleven_multilingual_v2` ;
5. crée automatiquement `output/audio/` si le dossier n'existe pas ;
6. sauvegarde chaque fichier sous la forme `{script_id}_{topic_slug}.mp3` ;
7. met à jour `scripts_generated` avec le statut `voiced` et le champ `audio_path` ;
8. journalise l'exécution dans `logs/voicegen.log`.

## Paramètres ElevenLabs

L'agent utilise l'endpoint Text to Speech ElevenLabs via `requests` avec les paramètres suivants :

```json
{
  "model_id": "eleven_multilingual_v2",
  "voice_settings": {
    "stability": 0.5,
    "similarity_boost": 0.8,
    "style": 0.4,
    "use_speaker_boost": true
  }
}
```

Le retour API est écrit directement en MP3.

## Variables d'environnement

L'agent charge `.env` depuis la racine du dépôt et respecte aussi les variables déjà exportées par le shell, systemd, Docker ou n8n.

Variables obligatoires pour un run réel :

```bash
ELEVENLABS_API_KEY=replace_with_elevenlabs_key
ELEVENLABS_VOICE_ID=replace_with_voice_id
```

Variables optionnelles :

```bash
SQLITE_DB_PATH=./data/dualmind.db
VOICEGEN_OUTPUT_DIR=./output/audio
LOG_LEVEL=INFO
```

- `ELEVENLABS_API_KEY` authentifie l'appel API.
- `ELEVENLABS_VOICE_ID` désigne la voix française mystérieuse à utiliser, par exemple Adam ou une voix custom.
- `SQLITE_DB_PATH` permet de cibler une base SQLite alternative ; par défaut, la base est `data/dualmind.db`.
- `VOICEGEN_OUTPUT_DIR` permet de rediriger les MP3 ; par défaut, le dossier est `output/audio/`.
- `LOG_LEVEL` est optionnelle ; par défaut, le niveau est `INFO`.

## Format audio output

Chaque fichier est nommé de manière déterministe :

```text
output/audio/{script_id}_{topic_slug}.mp3
```

Exemple :

```text
output/audio/42_le-dossier-impossible-du-phare-abandonne.mp3
```

La valeur stockée dans `scripts_generated.audio_path` est le chemin relatif au dépôt quand le fichier est généré dans le dépôt, par exemple :

```text
output/audio/42_le-dossier-impossible-du-phare-abandonne.mp3
```

## Idempotence et gestion d'erreurs

- L'agent ne lit que les scripts avec le statut `ready`, donc un script déjà `voiced` n'est pas re-généré.
- Si un appel ElevenLabs échoue, l'agent réessaie une fois après une courte pause.
- Après deux échecs, l'erreur est écrite dans `logs/voicegen.log`, le script est ignoré, et l'agent continue avec le script suivant.
- Si une ancienne base SQLite ne contient pas encore `scripts_generated.audio_path`, l'agent ajoute automatiquement cette colonne au démarrage.

## Commandes de test

Depuis la racine du dépôt, lancer le test standalone sans clé API réelle :

```bash
python scripts/agents/voicegen_test.py
```

Le test :

1. crée une base SQLite temporaire dans le dossier système temporaire ;
2. insère 1 faux script dans `scripts_generated` avec le statut `ready` ;
3. exécute l'agent avec un mock ElevenLabs qui écrit un MP3 vide de test ;
4. vérifie que le statut passe à `voiced` ;
5. vérifie que `audio_path` est bien rempli dans SQLite ;
6. vérifie que les balises `[HOOK]` et `[DÉVELOPPEMENT]` ne sont pas envoyées au mock ;
7. affiche le résumé, la ligne mise à jour, et le texte envoyé au mock.

## Commandes de run réel

Préparer `.env` :

```bash
cp .env.example .env
# puis renseigner ELEVENLABS_API_KEY et ELEVENLABS_VOICE_ID dans .env
```

Exécuter l'agent avec la limite par défaut de 3 audios :

```bash
python scripts/agents/voicegen.py
```

Limiter volontairement à 1 ou 2 audios :

```bash
python scripts/agents/voicegen.py --limit 1
python scripts/agents/voicegen.py --limit 2
```

Utiliser une base SQLite alternative :

```bash
SQLITE_DB_PATH=./tmp/voicegen_dev.db python scripts/agents/voicegen.py
```

Rediriger les MP3 vers un dossier temporaire :

```bash
VOICEGEN_OUTPUT_DIR=./tmp/audio python scripts/agents/voicegen.py
```
