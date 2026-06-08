# n8n — Phase 1.3 Docker Base

Ce document décrit la base Docker minimale pour lancer n8n sur le futur VPS Hetzner CAX41 sous Ubuntu 22.04 ARM64.

## Rôle de n8n

n8n servira d'orchestrateur d'automatisations pour le pipeline DUALMIND. À cette phase, il fournit uniquement une interface web locale au VPS et un stockage persistant pour préparer les futurs workflows.

Aucun workflow n8n, reverse proxy, domaine, certificat SSL ou script d'installation global n'est créé dans cette phase.

## Prérequis

- VPS Ubuntu 22.04 ARM64.
- Docker et le plugin Docker Compose installés.
- Un fichier `.env` local créé depuis `.env.example`.

Exemple de préparation locale sur le VPS :

```bash
cp .env.example .env
```

Avant le premier lancement, adapter les valeurs n8n dans `.env`, notamment les identifiants d'accès et la clé de chiffrement.

## Lancer n8n

Depuis la racine du repo :

```bash
docker compose up -d
```

n8n sera accessible sur :

```text
http://localhost:5678
```

Si le port est ouvert sur le VPS, l'interface peut aussi être accessible via l'adresse IP publique du serveur sur le port `5678`.

## Voir les logs

```bash
docker compose logs -f n8n
```

## Arrêter n8n

```bash
docker compose down
```

Cette commande arrête et supprime le conteneur, mais conserve le volume Docker persistant `n8n_data`.

## Stockage des données

Les données internes de n8n sont stockées dans le volume Docker nommé :

```text
n8n_data
```

Ce volume est monté dans le conteneur sur :

```text
/home/node/.n8n
```

Il conserve les données n8n entre deux redémarrages ou recréations du conteneur.

La base SQLite applicative du projet DUALMIND reste séparée et doit utiliser le chemin :

```text
./data/dualmind.db
```

## Variables `.env` à préparer plus tard

Les variables suivantes sont prévues pour la configuration n8n et devront être définies avec des valeurs robustes avant une exposition réelle du service :

- `APP_TIMEZONE=America/Guadeloupe`
- `N8N_HOST=localhost` pour cette phase sans domaine.
- `N8N_PORT=5678`
- `N8N_PROTOCOL=http`
- `WEBHOOK_URL=http://localhost:5678/` tant qu'il n'y a pas de domaine.
- `N8N_BASIC_AUTH_ACTIVE=true`
- `N8N_BASIC_AUTH_USER=<utilisateur-local>`
- `N8N_BASIC_AUTH_PASSWORD=<mot-de-passe-fort-local>`
- `N8N_ENCRYPTION_KEY=<cle-longue-aleatoire>`

Ne jamais committer le fichier `.env` ni de vraie clé API.

## Limites de cette phase

- Pas de reverse proxy.
- Pas de nom de domaine.
- Pas de SSL/TLS.
- Pas de workflow n8n.
- Pas de script d'installation global.
- Pas de configuration de sauvegarde automatisée.
- Pas de durcissement réseau avancé.

Cette configuration est une base de démarrage simple, lisible et compatible avec Ubuntu 22.04 ARM64.

## Sécurité minimale

- Garder `.env` hors Git.
- Remplacer toutes les valeurs `change_me` avant usage réel.
- Utiliser un mot de passe fort pour `N8N_BASIC_AUTH_PASSWORD`.
- Utiliser une clé longue et aléatoire pour `N8N_ENCRYPTION_KEY`.
- Ne pas stocker de vraies clés API dans `.env.example`, la documentation ou les commits.
- Ne pas exposer le port `5678` publiquement sans reverse proxy, HTTPS et règles firewall adaptées.
- Restreindre l'accès au VPS avec SSH sécurisé et pare-feu système.
- Sauvegarder régulièrement le volume Docker `n8n_data` avant toute mise à jour importante.
