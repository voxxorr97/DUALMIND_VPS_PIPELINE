# VideoAssembler — Agent 5 du pipeline DualMind v2.2

`VideoAssembler` est le cinquième agent Python du pipeline DualMind v2.2 pour la niche francophone **Affaires Mystérieuses Non Classées**. Il transforme un script déjà illustré par `ImageGen` en vidéo verticale MP4 prête pour YouTube Shorts, TikTok ou Reels.

## Rôle dans le pipeline

Entrée attendue dans SQLite (`data/dualmind.db`) :

- table `scripts_generated`
- `status = 'illustrated'`
- `audio_path` pointant vers l'audio de narration
- `images_path` contenant un JSON array de 4 images, par exemple :

```json
[
  "output/images/42/frame_1.png",
  "output/images/42/frame_2.png",
  "output/images/42/frame_3.png",
  "output/images/42/frame_4.png"
]
```

Sortie produite :

- vidéo MP4 dans `output/videos/{script_id}.mp4`
- mise à jour SQLite : `status = 'assembled'`, `video_path = 'output/videos/{script_id}.mp4'`
- logs dans `logs/videoassembler.log`

L'agent est idempotent au niveau du pipeline : il ne sélectionne que les scripts dont le statut est `illustrated`. Un script déjà `assembled` n'est donc pas réassemblé.

## Fonctionnement vidéo

Pour chaque script illustré :

1. vérifie que `ffmpeg` et `ffprobe` sont disponibles ;
2. lit `audio_path` et `images_path` depuis SQLite ;
3. mesure la durée réelle de l'audio avec `ffprobe` ;
4. divise cette durée en 4 segments égaux ;
5. crée une séquence vidéo avec une image par segment ;
6. remplace toute image absente par un fond noir ;
7. muxe l'audio sur toute la vidéo ;
8. écrit le MP4 final ;
9. met à jour `scripts_generated`.

Paramètres ffmpeg utilisés :

- résolution finale : `1080x1920` en 9:16
- codec vidéo : `libx264`
- preset : `fast`
- CRF : `23`
- codec audio : `aac`
- bitrate audio : `192k`
- format : `MP4`

Les fichiers temporaires créés pendant l'assemblage sont supprimés après un assemblage réussi. Si ffmpeg échoue, le stderr complet est écrit dans `logs/videoassembler.log` et le script est ignoré sans bloquer les suivants.

## Sous-titres brûlés optionnels

Si `BURN_SUBTITLES=true`, l'agent active une sous-étape optionnelle :

1. transcrit l'audio avec `openai-whisper` ;
2. écrit un fichier SRT dans `output/subtitles/{script_id}.srt` ;
3. brûle les sous-titres dans la vidéo avec le filtre ffmpeg `subtitles` ;
4. conserve la version sous-titrée comme `video_path` final.

Style des sous-titres :

- blanc ;
- centrés en bas ;
- police `Arial Bold` ;
- taille `18` ;
- outline noir.

> Note : `openai-whisper` est optionnel et n'est requis que lorsque `BURN_SUBTITLES=true`.

## Dépendances système

Sur Ubuntu 22.04 ARM64 :

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

Les deux binaires suivants doivent être dans le `PATH` :

```bash
which ffmpeg
which ffprobe
```

## Dépendances Python

Dépendance obligatoire :

```bash
pip install python-dotenv
```

Dépendance optionnelle pour les sous-titres brûlés :

```bash
pip install openai-whisper
```

## Variables d'environnement

À ajouter dans `.env` si nécessaire :

```dotenv
# false par défaut
BURN_SUBTITLES=false
```

Variables utiles aussi supportées par l'agent :

```dotenv
# optionnel : chemin SQLite alternatif
SQLITE_DB_PATH=data/dualmind.db

# optionnel : dossier de sortie vidéo alternatif
VIDEOASSEMBLER_OUTPUT_DIR=output/videos

# optionnel : dossier de sortie SRT alternatif
VIDEOASSEMBLER_SUBTITLE_DIR=output/subtitles

# optionnel si BURN_SUBTITLES=true
WHISPER_MODEL=base
```

## Commandes d'exécution

Assembler jusqu'à 3 scripts illustrés :

```bash
python scripts/agents/videoassembler.py
```

Assembler un seul script :

```bash
python scripts/agents/videoassembler.py --limit 1
```

## Test standalone

Le test ne dépend d'aucun service externe. Il crée :

- une base SQLite temporaire ;
- un WAV de 10 secondes avec une sine wave via `wave` + `math` ;
- 4 PNG solides avec la stdlib ;
- un faux script `illustrated` ;
- un MP4 complet via ffmpeg.

Commande :

```bash
python scripts/agents/videoassembler_test.py
```

Le test affiche la durée audio détectée et la taille du MP4 généré.
