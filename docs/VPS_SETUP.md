# Installation de base du VPS DUALMIND

Ce document décrit le script `setup_dualmind_vps.sh`, prévu pour préparer la base système du futur VPS Hetzner CAX41 sous Ubuntu 22.04 ARM64 pour le pipeline Archive Noire.

## Rôle du script

`setup_dualmind_vps.sh` installe ou vérifie les dépendances système minimales nécessaires au pipeline, prépare les dossiers locaux de travail, crée un environnement Python `.venv/`, puis installe les dépendances Python du projet si `requirements.txt` existe.

Le script est volontairement limité à la préparation de base du serveur. Il ne configure pas encore le domaine, le SSL, un reverse proxy, un firewall avancé, ni les workflows n8n.

## Quand l'utiliser

Utiliser ce script après la création du VPS et après avoir copié ou synchronisé le dépôt sur la machine.

Il est conçu pour être relançable autant que possible sans casser l'installation existante :

- les paquets apt sont réinstallés/vérifiés de façon idempotente ;
- les dossiers sont créés avec `mkdir -p` ;
- l'environnement `.venv/` est recréé/vérifié par `python3 -m venv` ;
- le dépôt Docker et sa clé GPG ne sont ajoutés que s'ils sont absents.

## Prérequis

- VPS Linux, cible recommandée : Hetzner CAX41 ARM64 ;
- Ubuntu 22.04 ;
- accès shell avec un utilisateur pouvant utiliser `sudo`, ou session `root` ;
- connexion réseau active pour `apt`, Docker et `pip` ;
- dépôt `DUALMIND_VPS_PIPELINE` déjà présent sur le VPS.

Le script vérifie qu'il est lancé sur Linux et affiche un avertissement si la distribution, la version Ubuntu ou l'architecture ne correspondent pas à la cible prévue.

## Comment le lancer

Depuis la racine du dépôt sur le VPS :

```bash
chmod +x setup_dualmind_vps.sh
./setup_dualmind_vps.sh
```

Le script utilise `sudo` automatiquement si l'utilisateur courant n'est pas `root`.

## Dossiers créés

Le script crée les dossiers suivants s'ils n'existent pas déjà :

- `data/` : données locales du pipeline ;
- `output/` : fichiers produits par les traitements ;
- `logs/` : journaux applicatifs ;
- `tmp/` : fichiers temporaires ;
- `secrets/` : emplacement local réservé aux fichiers sensibles présents uniquement sur le VPS.

Le script ne supprime aucun fichier existant.

## Dépendances installées

Le script lance `apt-get update`, puis installe ou vérifie :

- `curl` ;
- `git` ;
- `unzip` ;
- `ca-certificates` ;
- `gnupg` ;
- `lsb-release` ;
- `build-essential` ;
- `python3` ;
- `python3-pip` ;
- `python3-venv` ;
- `sqlite3` ;
- `ffmpeg` ;
- Docker Engine ;
- Docker CLI ;
- `containerd` ;
- plugin Docker Buildx ;
- plugin Docker Compose.

Il crée aussi l'environnement Python local `.venv/` et installe les dépendances listées dans `requirements.txt` si ce fichier est présent.

## Versions affichées

À la fin de l'installation, le script affiche les versions de :

```bash
python3 --version
pip --version
ffmpeg -version
sqlite3 --version
docker --version
docker compose version
```

Dans le script, la commande `pip --version` est exécutée via le pip de `.venv/` pour confirmer l'environnement Python local.

## Commandes après installation

Après l'exécution du script, les commandes recommandées sont :

```bash
cp .env.example .env
nano .env
python scripts/init_sqlite.py
docker compose up -d
docker compose logs -f n8n
```

Adapter ces commandes si certains fichiers ou scripts ne sont pas encore présents dans la phase courante du projet.

## Limites de cette phase

Cette phase ne fait volontairement pas les actions suivantes :

- aucune vraie clé API n'est créée ou ajoutée ;
- aucun secret n'est généré ;
- aucune API externe n'est appelée par le pipeline ;
- aucun workflow n8n n'est créé ;
- aucun dépôt externe n'est cloné automatiquement ;
- aucun domaine n'est configuré ;
- aucun certificat SSL n'est installé ;
- aucun reverse proxy n'est configuré ;
- aucun firewall avancé n'est configuré.

## Règles de sécurité

- Ne jamais mettre de vraies clés API dans GitHub.
- Ne jamais commiter le fichier `.env` réel.
- Remplir `.env` uniquement sur le VPS.
- Garder les secrets locaux dans des emplacements exclus du versionnement.
- Vérifier le contenu de `secrets/` avant toute archive, synchronisation ou sauvegarde externe.
- Utiliser `.env.example` uniquement comme modèle sans valeur sensible réelle.
