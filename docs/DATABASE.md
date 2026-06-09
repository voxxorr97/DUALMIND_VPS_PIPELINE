# Archive Noire SQLite Memory Core

La base mémoire SQLite stocke les premières données persistantes du pipeline Archive Noire sans service externe, sans clé API et sans secret.

## Chemin de la base

Le fichier SQLite est créé automatiquement ici :

```text
data/dualmind.db
```

Le dossier `data/` est créé par le code Python s'il n'existe pas encore.

## Tables

### `trends_raw`

Stocke les tendances brutes collectées avant nettoyage ou scoring.

Champs principaux :

- `id` : identifiant unique auto-incrémenté.
- `source` : origine de la tendance.
- `raw_title` : titre brut collecté.
- `raw_text` : contenu brut optionnel.
- `url` : lien source optionnel.
- `collected_at` : date de collecte, remplie automatiquement par défaut.

### `topics_hot`

Stocke les sujets considérés comme chauds après analyse ou sélection.

Champs principaux :

- `id` : identifiant unique auto-incrémenté.
- `topic` : sujet retenu.
- `niche` : niche ou catégorie.
- `viral_score` : score numérique de potentiel viral.
- `status` : état du sujet dans le workflow.
- `created_at` : date de création, remplie automatiquement par défaut.

### `scripts_generated`

Stocke les scripts générés à partir des sujets chauds.

Champs principaux :

- `id` : identifiant unique auto-incrémenté.
- `topic_id` : référence optionnelle vers `topics_hot.id`.
- `title` : titre du script.
- `script_text` : texte complet du script.
- `platform` : plateforme cible.
- `duration_seconds` : durée prévue en secondes.
- `status` : état du script dans le workflow.
- `audio_path` : chemin optionnel vers la narration générée par Voicegen.
- `images_path` : JSON array optionnel des images générées par ImageGen.
- `video_path` : chemin optionnel vers le MP4 assemblé par VideoAssembler.
- `created_at` : date de création, remplie automatiquement par défaut.

### `prompts_history`

Conserve l'historique des prompts utilisés pour suivre les générations et faciliter les audits.

Champs principaux :

- `id` : identifiant unique auto-incrémenté.
- `prompt_type` : type ou usage du prompt.
- `model` : modèle indiqué pour la génération.
- `prompt_text` : prompt envoyé ou préparé.
- `output_summary` : résumé de la sortie obtenue.
- `created_at` : date de création, remplie automatiquement par défaut.

### `video_performance`

Stocke les métriques de performance des vidéos publiées.

Champs principaux :

- `id` : identifiant unique auto-incrémenté.
- `script_id` : référence optionnelle vers `scripts_generated.id`.
- `platform` : plateforme de publication.
- `video_url` : URL de la vidéo.
- `views` : nombre de vues.
- `likes` : nombre de likes.
- `comments` : nombre de commentaires.
- `watch_time_avg` : temps moyen de visionnage.
- `published_at` : date de publication optionnelle.
- `analyzed_at` : date d'analyse, remplie automatiquement par défaut.

## Initialiser la base

Depuis la racine du dépôt, lancer :

```bash
python scripts/init_sqlite.py
```

Cette commande :

1. crée le dossier `data/` si besoin ;
2. crée le fichier `data/dualmind.db` si besoin ;
3. crée les tables SQLite ;
4. insère un petit jeu de données de démonstration ;
5. affiche un message de succès.

## Vérifier que tout fonctionne

Exécuter l'initialisation :

```bash
python scripts/init_sqlite.py
```

Puis vérifier que le fichier existe :

```bash
test -f data/dualmind.db
```

Optionnellement, inspecter les tables avec SQLite si l'outil `sqlite3` est installé sur la machine :

```bash
sqlite3 data/dualmind.db ".tables"
```
