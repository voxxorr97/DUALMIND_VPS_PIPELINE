# Agent Imagegen — DualMind v2.2

`Imagegen` est le quatrième agent Python du pipeline DualMind v2.2. Il transforme les scripts déjà narrés en quatre images verticales avec Flux.1 via Replicate pour la niche francophone **Affaires Mystérieuses Non Classées**.

## Rôle

L'agent :

1. lit les lignes de `scripts_generated` dont le statut est `voiced` ;
2. limite chaque exécution à **2 scripts maximum**, soit **8 images maximum**, pour contrôler les coûts Replicate ;
3. extrait les quatre sections Scriptwriter attendues : `HOOK`, `DÉVELOPPEMENT`, `RÉVÉLATION`, `CTA` ;
4. utilise le titre du script comme fallback lorsqu'une section est vide ou manquante ;
5. construit un prompt visuel en anglais pour chaque segment, car Flux.1 réagit mieux aux prompts anglais ;
6. génère les images avec le modèle Replicate `black-forest-labs/flux-schnell` ;
7. crée automatiquement `output/images/{script_id}/` si le dossier n'existe pas ;
8. sauvegarde les fichiers sous `frame_1.png`, `frame_2.png`, `frame_3.png`, `frame_4.png` ;
9. met à jour `scripts_generated` avec le statut `illustrated` et le champ `images_path` au format JSON array ;
10. journalise l'exécution dans `logs/imagegen.log`.

## Format des prompts visuels

Chaque script produit exactement quatre prompts, dans cet ordre :

| Image | Segment | Fichier |
| --- | --- | --- |
| 1 | `HOOK` | `output/images/{script_id}/frame_1.png` |
| 2 | `DÉVELOPPEMENT` | `output/images/{script_id}/frame_2.png` |
| 3 | `RÉVÉLATION` | `output/images/{script_id}/frame_3.png` |
| 4 | `CTA` | `output/images/{script_id}/frame_4.png` |

Les prompts sont générés en anglais à partir de mots-clés visuels détectés dans le segment, puis terminés par le style fixe suivant :

```text
cinematic, dark atmosphere, mysterious, french countryside or urban noir, dramatic lighting, photorealistic, 9:16 vertical format, no text, no watermark
```

Exemple de prompt final :

```text
lonely lighthouse on a stormy coast, cold sea spray and fog, compelling mystery hook, cinematic, dark atmosphere, mysterious, french countryside or urban noir, dramatic lighting, photorealistic, 9:16 vertical format, no text, no watermark
```

## Paramètres Replicate Flux.1

L'agent appelle l'endpoint Replicate `POST /v1/models/black-forest-labs/flux-schnell/predictions` via `requests` avec les paramètres suivants :

```json
{
  "prompt": "<prompt anglais du segment>",
  "width": 768,
  "height": 1344,
  "num_inference_steps": 4,
  "output_format": "png",
  "output_quality": 90
}
```

Le ratio `768x1344` cible le format vertical 9:16 pour YouTube Shorts, TikTok et Reels.

## Variables d'environnement

L'agent charge `.env` depuis la racine du dépôt et respecte aussi les variables déjà exportées par le shell, systemd, Docker ou n8n.

Variable obligatoire pour un run réel :

```bash
REPLICATE_API_TOKEN=replace_with_replicate_token
```

Variables optionnelles :

```bash
SQLITE_DB_PATH=./data/dualmind.db
IMAGEGEN_OUTPUT_DIR=./output/images
LOG_LEVEL=INFO
```

- `REPLICATE_API_TOKEN` authentifie l'appel API Replicate.
- `SQLITE_DB_PATH` permet de cibler une base SQLite alternative ; par défaut, la base est `data/dualmind.db`.
- `IMAGEGEN_OUTPUT_DIR` permet de rediriger les PNG ; par défaut, le dossier est `output/images/`.
- `LOG_LEVEL` règle la verbosité des logs ; par défaut, le niveau est `INFO`.

## Format de sortie SQLite

Le champ `scripts_generated.images_path` contient un JSON array de quatre chemins, par exemple :

```json
[
  "output/images/42/frame_1.png",
  "output/images/42/frame_2.png",
  "output/images/42/frame_3.png",
  "output/images/42/frame_4.png"
]
```

Les chemins sont stockés relativement au dépôt lorsque les images sont générées dans le dépôt.

## Idempotence et gestion d'erreurs

- L'agent ne lit que les scripts avec le statut `voiced`, donc un script déjà `illustrated` n'est pas re-généré.
- Chaque appel Replicate est réessayé une fois après un échec.
- L'agent interroge Replicate toutes les 2 secondes jusqu'au statut `succeeded`, avec un timeout de 60 secondes.
- Après deux échecs sur une image, l'erreur est écrite dans `logs/imagegen.log`, le script est ignoré, et l'agent continue avec le script suivant.
- Si une ancienne base SQLite ne contient pas encore `scripts_generated.images_path`, l'agent ajoute automatiquement cette colonne au démarrage.

## Commandes de test

Depuis la racine du dépôt, lancer le test standalone sans clé API réelle :

```bash
python scripts/agents/imagegen_test.py
```

Le test :

1. crée une base SQLite temporaire dans le dossier système temporaire ;
2. crée un PNG placeholder local ;
3. insère 1 faux script dans `scripts_generated` avec le statut `voiced` ;
4. exécute l'agent avec un mock Replicate qui copie le PNG placeholder ;
5. vérifie que le statut passe à `illustrated` ;
6. vérifie que `images_path` est bien rempli avec quatre chemins dans SQLite ;
7. vérifie que chaque PNG existe ;
8. affiche le résumé, la ligne mise à jour, les chemins JSON, et les prompts envoyés au mock.

## Commandes de run réel

Préparer `.env` :

```bash
cp .env.example .env
# puis renseigner REPLICATE_API_TOKEN dans .env
```

Exécuter l'agent avec la limite par défaut de 2 scripts :

```bash
python scripts/agents/imagegen.py
```

Limiter volontairement à 1 script :

```bash
python scripts/agents/imagegen.py --limit 1
```

Utiliser une base SQLite alternative :

```bash
SQLITE_DB_PATH=./tmp/imagegen_dev.db python scripts/agents/imagegen.py
```

Rediriger les PNG vers un dossier temporaire :

```bash
IMAGEGEN_OUTPUT_DIR=./tmp/images python scripts/agents/imagegen.py
```
