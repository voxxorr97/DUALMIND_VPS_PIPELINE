# Agent Trendspotter — DualMind v2.2

`Trendspotter` est le premier agent Python opérationnel du pipeline DualMind v2.2. Il transforme un webhook JSON de tendances hebdomadaires Grok en sujets exploitables pour la niche francophone **Affaires Mystérieuses Non Classées**.

## Rôle

L'agent :

1. lit un payload JSON depuis `stdin` ou depuis un fichier ;
2. extrait les tendances depuis des structures courantes (`trends`, `weekly_trends`, `items`, `results`, `data`) ;
3. insère les tendances brutes dans `trends_raw` ;
4. calcule un score de potentiel viral entre `0` et `100` ;
5. insère les 5 meilleurs sujets dans `topics_hot` avec le statut `pending` ;
6. écrit les événements d'exécution dans `logs/trendspotter.log`.

Le code utilise uniquement la bibliothèque standard Python (`json`, `sqlite3`, `logging`, etc.). Aucune dépendance externe n'est requise.

## Base SQLite

Par défaut, l'agent utilise :

```text
data/dualmind.db
```

Il est aussi compatible avec la variable existante de `.env.example` :

```bash
SQLITE_DB_PATH=./data/dualmind.db
```

Sans chargeur `.env` externe, cette variable doit être exportée par le shell, systemd, Docker ou n8n avant l'exécution du script.

## Format d'entrée

### Exemple recommandé

```json
{
  "source": "grok_weekly_trends",
  "generated_at": "2026-06-09T00:00:00Z",
  "trends": [
    {
      "title": "Cold case: le suspect oublié revient après 27 ans",
      "summary": "La police rouvre un dossier criminel non classé.",
      "url": "https://example.com/cold-case-27-ans"
    }
  ]
}
```

### Champs reconnus

Pour chaque tendance, l'agent accepte plusieurs noms de champs afin de rester tolérant avec les variantes de webhooks :

- titre : `title`, `topic`, `name`, `query`, `headline`, `raw_title` ;
- texte : `text`, `summary`, `description`, `content`, `raw_text` ;
- URL : `url`, `link`, `source_url`, `permalink` ;
- source : `source`, `provider`, `origin`.

Le payload peut aussi être directement un tableau JSON de tendances.

## Format de sortie CLI

En cas de succès :

```json
{"ok": true, "received": 10, "raw_inserted": 10, "topics_inserted": 5}
```

En cas d'erreur :

```json
{"ok": false, "error": "message d'erreur"}
```

## Scoring

Le score final est borné entre `0` et `100` et combine :

- présence de mots liés au mystère, au crime, aux affaires non classées ou à l'inexpliqué ;
- longueur du titre, avec un bonus pour les titres suffisamment descriptifs ;
- présence de chiffres dans le titre ;
- petit bonus pour les titres formulés comme une question.

## Idempotence

L'agent évite les doublons :

- dans `trends_raw`, une tendance est considérée déjà présente si `source`, `raw_title` et `url` correspondent ;
- dans `topics_hot`, un sujet est considéré déjà présent si `topic` et `niche` correspondent.

Relancer le même webhook ne duplique donc pas les tendances ni les sujets chauds déjà insérés.

## Commandes de test

Depuis la racine du dépôt :

```bash
python scripts/agents/trendspotter_test.py
```

Tester l'idempotence en lançant deux fois la même commande :

```bash
python scripts/agents/trendspotter_test.py
python scripts/agents/trendspotter_test.py
```

Tester avec un fichier JSON :

```bash
python scripts/agents/trendspotter.py payload.json
```

Tester avec `stdin` :

```bash
cat payload.json | python scripts/agents/trendspotter.py
```

Utiliser une base temporaire ou alternative :

```bash
SQLITE_DB_PATH=./tmp/trendspotter_test.db python scripts/agents/trendspotter_test.py
```
