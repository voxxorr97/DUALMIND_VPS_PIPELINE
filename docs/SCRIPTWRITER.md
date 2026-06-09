# Agent Scriptwriter — DualMind v2.2

`Scriptwriter` est le deuxième agent Python du pipeline DualMind v2.2. Il transforme les sujets chauds déjà sélectionnés par `Trendspotter` en scripts courts pour la niche francophone **Affaires Mystérieuses Non Classées**.

## Rôle

L'agent :

1. lit les lignes de `topics_hot` dont le statut est `pending` ;
2. limite chaque exécution à **3 scripts maximum** pour contrôler les coûts API ;
3. génère un script vidéo de 60 secondes avec Claude (`claude-sonnet-4-6`) ;
4. insère chaque script dans `scripts_generated` avec le statut `ready` ;
5. insère le prompt utilisé dans `prompts_history` ;
6. met à jour le sujet traité dans `topics_hot` avec le statut `scripted` ;
7. journalise l'exécution dans `logs/scriptwriter.log`.

## Format du script généré

Le format demandé à Claude est strictement découpé en quatre segments :

```text
[HOOK] (0-5s) : phrase choc qui accroche immédiatement
[DÉVELOPPEMENT] (5-45s) : 3 faits troublants, style journalistique, rythme rapide
[RÉVÉLATION] (45-55s) : twist ou élément inexpliqué
[CTA] (55-60s) : question ouverte pour engager les commentaires
```

Le rendu cible une vidéo courte de 60 secondes pour YouTube Shorts et TikTok, en français, avec un ton mystérieux, factuel et sans superlatifs inutiles.

## Prompt template Claude

Le prompt intégré à `scripts/agents/scriptwriter.py` est :

```text
Tu es un expert en contenu mystère francophone pour YouTube Shorts et TikTok.
Génère un script de 60 secondes sur ce sujet : {topic_title}
Format strict :
[HOOK] (0-5s) : phrase choc qui accroche immédiatement
[DÉVELOPPEMENT] (5-45s) : 3 faits troublants, style journalistique, rythme rapide
[RÉVÉLATION] (45-55s) : twist ou élément inexpliqué
[CTA] (55-60s) : question ouverte pour engager les commentaires
Langue : français, ton mystérieux et factuel, pas de superlatifs inutiles.
```

## Variables d'environnement

L'agent charge `.env` depuis la racine du dépôt et respecte aussi les variables déjà exportées par le shell, systemd, Docker ou n8n.

Variables utiles :

```bash
CLAUDE_API_KEY=replace_with_claude_api_key
SQLITE_DB_PATH=./data/dualmind.db
LOG_LEVEL=INFO
```

- `CLAUDE_API_KEY` est obligatoire pour un run réel avec Claude.
- `SQLITE_DB_PATH` est optionnelle ; par défaut, la base est `data/dualmind.db`.
- `LOG_LEVEL` est optionnelle ; par défaut, le niveau est `INFO`.

## Idempotence et gestion d'erreurs

- L'agent ne lit que les sujets `pending`, donc un sujet déjà `scripted` n'est pas re-scripé.
- Si un script `ready` existe déjà pour un sujet encore `pending`, le sujet est simplement marqué `scripted` et aucun nouvel appel Claude n'est effectué.
- En cas d'erreur API ou de réponse vide, l'agent réessaie une fois.
- Après deux échecs, l'erreur est écrite dans `logs/scriptwriter.log`, le sujet est ignoré, et l'agent continue avec le sujet suivant.

## Commandes de test

Depuis la racine du dépôt, lancer le test standalone sans clé API réelle :

```bash
python scripts/agents/scriptwriter_test.py
```

Le test :

1. crée une base SQLite temporaire dans le dossier système temporaire ;
2. insère 2 faux topics avec le statut `pending` ;
3. exécute l'agent avec un mock de l'API Claude ;
4. vérifie que `scripts_generated` contient 2 scripts `ready` ;
5. vérifie que `prompts_history` contient 2 prompts ;
6. vérifie que les topics sont passés à `scripted` ;
7. affiche les scripts et prompts générés.

## Commandes de run réel

Préparer `.env` :

```bash
cp .env.example .env
# puis renseigner CLAUDE_API_KEY dans .env
```

Exécuter l'agent avec la limite par défaut de 3 scripts :

```bash
python scripts/agents/scriptwriter.py
```

Limiter volontairement à 1 ou 2 scripts :

```bash
python scripts/agents/scriptwriter.py --limit 1
python scripts/agents/scriptwriter.py --limit 2
```

Utiliser une base SQLite alternative :

```bash
SQLITE_DB_PATH=./tmp/scriptwriter_dev.db python scripts/agents/scriptwriter.py
```
