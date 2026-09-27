# Système d'information — état, risques, et route vers l'indépendance

Relevé du **26 septembre 2026**. Ce document décrit **où vivent les données**, ce
qui est reconstituable et ce qui ne l'est pas, puis ce qu'il faut ajouter pour que
le système cesse de dépendre de ce que les sources publient aujourd'hui.

Le README décrit le modèle et les critères ; celui-ci ne parle que du stockage.

---

## 1. Où sont les données, aujourd'hui

Trois dépôts, de natures très différentes — et c'est la distinction qui compte :

| Dépôt | Volume | Nature | Reconstituable ? |
|---|---|---|---|
| `donnees/ibet.db` (SQLite) | **8,8 Mo** | ce qui fait foi | **non** pour l'essentiel |
| `donnees/forces.json` | 500 Ko | notes attaque / défense et moyennes de buts par compétition | oui, `python -m ibet forces` |
| `donnees/cache/` | 8 298 fichiers, 22,8 Mo utiles (**48 Mo occupés**) | copie de travail des sources | oui, en retéléchargeant |

### `donnees/ibet.db` — les cinq tables

| Table | Lignes | Ce qu'elle porte |
|---|---|---|
| `predictions` | **90** | la fiche complète telle qu'émise (`payload`, 15,6 Ko en moyenne), plus les colonnes de tri. 57 portent un contexte (les 14 critères) |
| `offres` | **1 764** | une ligne par proposition, pour que le bilan soit une requête. 762 tranchées |
| `cotes` | **278** | un relevé horodaté par appel, jamais écrasé — c'est ce qui rend le mouvement de ligne lisible |
| `coupons` | 2 | sélections enregistrées, avec leurs filtres |
| `resultats` | **20 663** | l'archive locale des matchs terminés : score pour les 20 663, statistiques détaillées pour **4 535** seulement (3,9 Mo) |

Le schéma est créé par `store.SCHEMA`, en `CREATE TABLE IF NOT EXISTS`.

### `donnees/cache/` — ce qui n'est qu'une copie

| Type de clé | Fichiers | Poids | Ce que c'est |
|---|---|---|---|
| `stats` | 6 560 | 6,1 Mo | statistiques d'un match terminé |
| `hist` | 344 | **9,8 Mo** | historique complet des deux équipes d'un match |
| `flashscore` | 24 | 5,1 Mo | une journée entière de matchs |
| `compos` | 516 | 0,9 Mo | compositions |
| `classement` | 63 | 0,5 Mo | classements |
| `info`, `meteo`, `ville`, `cotes`, `absents`, `agregat` | 791 | 0,2 Mo | le reste |

La plus ancienne entrée a **19 jours**. Les 48 Mo occupés pour 22,8 Mo utiles sont
le prix de 8 298 fichiers de quelques kilo-octets sur un système de fichiers qui
alloue par blocs : ce n'est pas un problème de place, c'est un problème de nombre
d'inodes — et c'est ce qui rend `--clear-cache` long.

---

## 2. Les cinq problèmes, par ordre d'urgence

### 2.1 — Aucune sauvegarde, aucun versionnement *(critique)*

Le dossier **n'est pas un dépôt git**, et aucun code du projet ne fait de
sauvegarde : ni `VACUUM INTO`, ni copie, ni export. Or `ibet.db` contient :

- **90 engagements datés** qui, par construction, ne peuvent pas être reproduits —
  une fiche est une prévision émise à une date, avec un réglage ; la recalculer
  aujourd'hui donnerait autre chose ;
- **20 663 résultats** dont la source ne publie plus que ±7 jours. Ce qui est
  tombé hors de cette fenêtre **n'est plus téléchargeable**. `ibet/collecte/resultats.py`
  existe justement parce que onze fiches — 264 propositions — avaient déjà été
  perdues ainsi.

Une suppression accidentelle, une corruption de fichier ou un disque qui lâche
efface tout le passif de mesure du projet. C'est le seul point de ce document qui
mérite d'être traité avant tout le reste, et c'est aussi le moins coûteux :

```bash
git init && git add -A && git commit -m "etat au 26 septembre"
python -c "import sqlite3,datetime; sqlite3.connect('donnees/ibet.db').execute(
  \"VACUUM INTO 'donnees/sauvegardes/ibet-%s.db'\" % datetime.date.today())"
```

`VACUUM INTO` plutôt qu'une copie de fichier : il produit une base **cohérente**
même si une écriture est en cours, ce qu'un `cp` ne garantit pas.

### 2.2 — Aucun mécanisme de migration *(bloquant pour la suite)*

`CREATE TABLE IF NOT EXISTS` crée une table absente et **ne touche jamais** une
table existante. Conséquence directe : ajouter une colonne à `resultats` ou à
`predictions` dans `store.SCHEMA` **n'a aucun effet** sur la base actuelle, et
sans erreur. Le code lira alors une colonne qui n'existe pas.

Il n'y a ni `PRAGMA user_version`, ni `ALTER TABLE`, ni table de version. C'est
exactement ce qu'il faut poser avant la moindre évolution du schéma :

```python
# store.py
SCHEMA_VERSION = 1   # a incrementer a chaque migration

MIGRATIONS = {
    2: ["ALTER TABLE resultats ADD COLUMN competition TEXT NOT NULL DEFAULT ''"],
    3: [...],
}

def migrer() -> int:
    """Amene la base au schema courant. Idempotent."""
    with connect() as cx:
        actuelle = cx.execute("PRAGMA user_version").fetchone()[0]
        for version in sorted(v for v in MIGRATIONS if v > actuelle):
            for instruction in MIGRATIONS[version]:
                cx.execute(instruction)
            cx.execute("PRAGMA user_version = %d" % version)
```

Le point délicat : la base **existante** est au schéma 1 sans le savoir
(`user_version` vaut 0 par défaut). La première migration doit donc reconnaître
une base déjà peuplée et se contenter de poser le numéro, sans rejouer une
création.

### 2.3 — L'archive est écrite mais n'est pas une source

`store.archiver_journee()` est appelé à chaque récupération d'une journée
(`ibet/interfaces/cli.py`, `ibet/prevision/forecast.py`, `ibet/interfaces/serveur.py`) et `ibet/evaluation/verify.py` lit l'archive pour trancher
une fiche que la source ne publie plus. **Mais aucune prévision n'est calculée
depuis l'archive** : `api_client.get_form()` et `league_baseline()` vont au réseau,
et retombent sur `.cache/` — jamais sur `resultats`.

Autrement dit, les 20 663 matchs archivés servent à *vérifier*, pas à *prévoir*.
Tant que c'est le cas, une panne de la source ou un changement de son flux arrête
le système, alors que la matière du modèle est déjà sur le disque.

C'est le cœur de l'indépendance, et c'est le chantier 3.2 ci-dessous.

### 2.4 — Le cache est un second dépôt de vérité, traité comme jetable

`store.reprendre_le_cache()` existe parce que des résultats n'avaient jamais
existé ailleurs que dans `.cache/`. Les 20 663 lignes de `resultats` portent
toutes `releve_le = 2026-09-24` : elles ont été récupérées **en une passe**, très
probablement par cette fonction.

Or `main.py --clear-cache` appelle `cache.clear()`, qui supprime les 8 298
fichiers sans condition. Tant que tout ce qui compte n'a pas été versé dans la
base, cette commande peut détruire des données non reconstituables — et rien dans
son intitulé ne le laisse deviner.

Deux corrections, indépendantes : verser avant d'effacer, et le dire.

### 2.5 — `forces.json` vit hors de la base

Les notes d'attaque et de défense sur échelle commune — l'état du modèle en
ligne, mis à jour après chaque match — sont dans un fichier JSON de 500 Ko, à
côté d'une base qui existe précisément pour ça.

Trois conséquences : deux dépôts à sauvegarder au lieu d'un, aucune écriture
atomique (un arrêt en pleine écriture laisse un JSON tronqué et donc illisible),
et **aucun historique** — on ne peut pas savoir ce que le modèle pensait d'une
équipe la semaine dernière, alors que c'est exactement ce qu'il faudrait pour
juger une fiche après coup.

---

## 3. Ce qu'il faut ajouter

Les chantiers sont ordonnés : chacun suppose le précédent.

### 3.1 — Le socle *(quelques heures)*

1. `git init` + une sauvegarde `VACUUM INTO` datée, déclenchée à chaque émission.
2. `PRAGMA user_version` et la fonction `migrer()` ci-dessus, appelée par
   `store.init()`.
3. `cache.clear()` reverse dans la base ce qui n'y est pas encore, ou refuse.

### 3.2 — Les tables qui manquent pour prévoir hors ligne

Aujourd'hui, tout ce qui n'est pas une fiche vit soit dans un blob JSON, soit
dans le cache. Quatre tables rendraient le modèle interrogeable sans réseau — et
**requêtable**, ce qu'un blob n'est pas : « tous les corners de l'équipe X » est
aujourd'hui un parcours de 20 663 JSON.

| Table | Clé | Ce qu'elle porte | Remplace |
|---|---|---|---|
| `equipes` | `equipe_id` | nom canonique, **alias** par source, pays, compétition | les noms en dur, et le rapprochement fragile par nom avec sportsgambler |
| `competitions` | `competition_id` | nom, pays, phase, niveau | `normalize_competition()` appelé partout |
| `matchs` | `match_id` | date, compétition, deux équipes, lieu, statut, score | la partie « journée » de `.cache/flashscore` |
| `statistiques` | `match_id` + `equipe_id` | une ligne par équipe et par match, **une colonne par grandeur** (buts, corners, tirs, cartons, xG, possession…) | le blob `resultats.stats`, et 6 560 fichiers `.cache/stats` |

La table `equipes` mérite un mot : c'est elle qui réglerait définitivement le
problème des noms. « Manchester Utd » chez Flashscore, « Manchester United » chez
sportsgambler — le rapprochement se fait aujourd'hui par heuristique à chaque
appel, avec le risque documenté d'un homonyme. Une table d'alias le fait une fois,
et le corrige à la main quand il se trompe.

Une fois ces tables en place, `api_client.get_form()` et `league_baseline()`
peuvent **lire la base d'abord** et n'aller au réseau que pour ce qui manque.
C'est le renversement qui rend le système indépendant : la source devient une
façon d'alimenter l'archive, plus la condition de fonctionner.

### 3.3 — L'historique de ce que le modèle pensait

- `forces` : les notes d'attaque/défense **horodatées**, une ligne par équipe et
  par mise à jour, au lieu d'un JSON écrasé. C'est ce qui permet de répondre
  à « le modèle avait-il vu venir cette série ? ».
- `criteres_mesures` : ce que `ibet/evaluation/criteres.py` mesure prospectivement, pour que la
  mesure s'accumule au lieu d'être recalculée à chaque passage.

### 3.4 — Ce qu'il ne faut probablement *pas* faire

- **Changer de moteur de base.** 8,8 Mo et 22 000 lignes ne justifient pas
  PostgreSQL. SQLite tient des millions de lignes, et le jour où un serveur
  devient utile sera le jour où plusieurs machines écrivent — pas avant.
- **Normaliser les fiches.** Le `payload` doit rester un blob figé : c'est un
  engagement daté, et le découper en colonnes inviterait à le réécrire. La règle
  actuelle est la bonne.

---

## 4. Ce que je n'ai pas vérifié

Par honnêteté sur la portée de ce relevé : j'ai lu le schéma, mesuré les volumes
et tracé les appels d'archivage, mais je n'ai pas relu en détail `ibet/prevision/marche.py`,
`ibet/evaluation/criteres.py` ni `ibet/prevision/forces.py`, qui ont été écrits après mon dernier passage. S'ils
persistent quelque chose que ce document ignore, il est incomplet sur ce point.

Les 20 663 résultats portent tous la même date de relevé (24 septembre), ce qui
indique une récupération en une passe. Je n'ai pas retrouvé quelle commande l'a
produite — `reprendre_le_cache()` est l'hypothèse la plus probable, pas une
certitude.
