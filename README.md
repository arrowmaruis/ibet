# Match Fetcher — matchs de football par date

Récupère la liste des matchs de football pour une date donnée, via **API REST officielles**
(JSON structuré, clé d'authentification, quotas documentés). Export CSV / JSON, filtre par
championnat, cache local pour économiser le quota.

---

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
copy .env.example .env          # Windows  (cp sur macOS/Linux)
```

### Antivirus / proxy qui inspecte le HTTPS

Si vous obtenez `SSL: CERTIFICATE_VERIFY_FAILED`, c'est qu'un antivirus (Avast, AVG,
Kaspersky, ESET…) ou un proxy d'entreprise re-signe le trafic HTTPS avec sa propre autorité,
que Python ne connaît pas. Correctif :

```bash
python -m ibet certificats
```

Le script fusionne les certificats publics avec l'autorité locale détectée et affiche la ligne
`CA_BUNDLE=…` à coller dans `.env`.

---

## Choisir un provider

Quatre sources interchangeables, toutes gratuites. Le provider se règle par la variable
`PROVIDER` du `.env` ou par `--provider`.

| Provider | Clé requise | Quota gratuit | Couverture | Pour quoi faire |
|---|---|---|---|---|
| `flashscore` | non | — | **mondiale**, ~200 compétitions/jour, mais **± 7 jours** autour d'aujourd'hui | **défaut : résultats, scores en direct et statistiques, sans inscription** |
| `thesportsdb` | non (clé de test `123`) | — | grandes compétitions européennes en **mode journée** ; sinon échantillon de 3 matchs | dates éloignées sans inscription |
| `football-data` | oui, gratuite | 10 req/min | 12 grandes compétitions (Ligue 1, PL, Liga, Serie A, Bundesliga, LDC…) | usage réel sur saison complète |
| `api-football` | oui, gratuite | 100 req/jour | mondiale, 1000+ compétitions | couverture maximale, dates quelconques |

### Flashscore (défaut)

Source par défaut : c'est la seule qui donne, **sans aucune clé**, la totalité des matchs du
jour avec leurs scores, leur statut en direct et leurs statistiques.

```bash
python -m ibet                                       # tous les matchs du jour
python -m ibet --date 2026-09-04 --league "Ligue 2"  # résultats d'hier
python -m ibet --league "Premier League" --stats     # avec statistiques
```

Les noms de compétitions sont ceux de flashscore.fr, en français (`Ligue 1`, `Ligue des
champions`, `Premier League`…), et le filtre `--league` reste une recherche par sous-chaîne.
Comme la couverture est mondiale, `--league "Ligue 1"` ramène aussi les Ligue 1 tunisienne et
algérienne : l'affichage les sépare en indiquant le pays dans chaque en-tête.

Deux limites à connaître :

- **Fenêtre de ± 7 jours** autour d'aujourd'hui. Au-delà, le programme le signale et renvoie
  vers `football-data` ou `api-football`, qui acceptent une date quelconque. Les **résultats**,
  eux, ne dépendent plus de cette fenêtre : ils sont archivés dans `ibet.db` dès qu'un match
  terminé passe sous les yeux du programme (voir [Archive des résultats](#archive-des-résultats)).
- **L'arbitre n'est pas fourni** par ce flux (les sept statistiques de match, si).

> ⚠️ Contrairement aux trois autres, Flashscore n'est pas une API publiée : on lit le flux que
> le site utilise pour sa propre page d'accueil. Il peut changer sans préavis, et les CGU du
> site interdisent la réutilisation commerciale des données. À réserver à un usage personnel,
> avec un volume de requêtes raisonnable — le cache local y contribue.
>
> Si le flux revient vide, c'est que le jeton `x-fsign` a changé : relevez sa valeur dans
> l'onglet Réseau du navigateur sur https://www.flashscore.fr/ et renseignez
> `FLASHSCORE_FSIGN` dans `.env`.

> ⚠️ L'endpoint « matchs du jour » de TheSportsDB est plafonné à **3 matchs** sur la clé
> publique. Le **mode journée** (ci-dessous) contourne cette limite pour les six grandes
> compétitions européennes. Pour tout le reste du football mondial, prenez une clé gratuite
> chez `football-data` ou `api-football`.

### Mode journée (grandes compétitions européennes)

Dès que `--league` désigne une compétition connue, le client bascule automatiquement sur
l'endpoint des journées de championnat, **non plafonné** — une journée complète, avec scores
et statuts :

```bash
python -m ibet --list-leagues                              # compétitions couvertes
python -m ibet --league "Premier League" --round 4         # journée 4 entière
python -m ibet --league "Ligue 1"  --date 2026-09-12       # journée détectée automatiquement
python -m ibet --league ldc --round 6                      # alias acceptés
```

Compétitions couvertes : Premier League, Ligue 1, La Liga, Serie A, Bundesliga, Ligue des
champions — avec alias (`epl`, `l1`, `liga`, `ldc`, `c1`, `france`, `italie`…).

Sans `--round`, la journée couvrant la date demandée est trouvée par **recherche dichotomique**
sur les numéros de journée (~6 requêtes, ensuite mises en cache). Une estimation par semaines
calendaires serait plus simple mais fausse dès qu'une compétition n'a pas de cadence
hebdomadaire — en C1, les tours préliminaires s'étalent sur des mois et se chevauchent.

Si la date demandée ne contient aucun match, le programme affiche les dates réellement jouées
les plus proches plutôt qu'un simple « aucun résultat ».

### Obtenir une clé gratuite

- **football-data.org** → https://www.football-data.org/client/register
  Inscription par e-mail, clé envoyée immédiatement. Renseignez `FOOTBALL_DATA_KEY` dans `.env`.
- **API-Football** → https://dashboard.api-football.com/register
  Compte gratuit, onglet *API Keys*. Renseignez `API_FOOTBALL_KEY` dans `.env`.

---

## Utilisation

```bash
python -m ibet                                            # matchs d'aujourd'hui
python -m ibet --date 2026-09-10
python -m ibet --date 2026-09-10 --league "Ligue 1"
python -m ibet --date 2026-09-10 --export csv
python -m ibet --provider football-data --date 2026-09-10 --export both
python -m ibet --date 2026-09-10 --tz America/New_York
python -m ibet --clear-cache
```

### Options

| Option | Description |
|---|---|
| `--date`, `-d` | Date `YYYY-MM-DD` (défaut : aujourd'hui) |
| `--league`, `-l` | Filtre sur le nom du championnat (sous-chaîne, insensible à la casse) |
| `--team`, `-t` | Filtre sur une équipe, à domicile ou à l'extérieur (sous-chaîne) |
| `--all-teams` | Avec `--team`, conserve les équipes féminines, jeunes et réserves |
| `--form [N]` | Forme des deux équipes sur leurs N derniers matchs (défaut 10, max 30) |
| `--predict` | Prévision buts / corners / tirs cadrés / cartons jaunes (Poisson). Implique `--form` |
| `--top [N]` | Classe les prévisions des matchs à venir, les plus tranchées d'abord (défaut 10). Implique `--predict` |
| `--backtest` | Rejoue les matchs terminés de la sélection et mesure la qualité du modèle |
| `--open` | Ouvre la page Flashscore des matchs sélectionnés (5 onglets maximum) |
| `--provider`, `-p` | `thesportsdb` \| `football-data` \| `api-football` |
| `--export`, `-e` | `csv` \| `json` \| `both` → écrit dans `donnees/exports/` |
| `--output`, `-o` | Chemin de sortie explicite (incompatible avec `--export both`) |
| `--round`, `-r` | Affiche la journée N complète (nécessite `--league`) |
| `--list-leagues` | Liste les compétitions du mode journée |
| `--stats` | Ajoute les statistiques des matchs terminés (1 requête par match) |
| `--max-stats` | Plafond de matchs enrichis par `--stats` (défaut : 20) |
| `--tz` | Fuseau horaire IANA d'affichage (défaut : `Europe/Paris`) |
| `--no-cache` | Force l'appel API en ignorant le cache |
| `--quiet`, `-q` | N'affiche pas le tableau console |
| `--clear-cache` | Vide le cache local puis quitte |

### Équipe première, féminines, jeunes et réserves

Chercher un club renvoie **son équipe première**. `--team Monchengladbach` ne ramène plus
« B. Monchengladbach II », et `--team Genoa` ne ramène ni « Genoa F » ni « Genoa -20 ». Les
déclinaisons sont reconnues au suffixe — ` F`, ` II`, ` B`, ` 2`, ` -17`, ` -20`, `U19` — et
au nom de la compétition (« Bundesliga - Femmes », « Campeonato Nacional U19 »). Ces motifs
ont été relevés sur un jour complet du flux, soit 3 518 équipes, sans faux positif.

Elles restent accessibles de deux façons :

```bash
python -m ibet --team "Monchengladbach II"          # la recherche les vise
python -m ibet --team Monchengladbach --all-teams   # tout conserver
```

Rien n'est écarté en silence : le programme indique toujours combien de matchs il a mis de
côté, et si la recherche ne correspond **qu'à** des déclinaisons, il le dit au lieu d'annoncer
« aucune équipe ne correspond ».

### Statistiques d'un match

`--team` restreint la sélection à une équipe ; combiné à `--stats`, il produit la fiche d'un
seul match :

```bash
python -m ibet --date 2026-09-04 --team Boulogne --stats
```

```
  Boulogne 1 - 1 Dijon
  --------------------
  Ligue 2 (France) - 2026-09-04 a 20:00
  Arbitre : non fourni par cette source

                   Boulogne                        Dijon
  Tirs cadres          3         ####|########     6
  Tirs (total)        15      #######|#####        12
  Corners             10     ########|####         5
  Fautes              10        #####|#######      15
  Cartons jaunes       2       ######|######       2
  Cartons rouges       0             |             0
  Possession         47%       ######|######       53%
```

Les deux moitiés de barre divergent depuis l'axe central, proportionnellement au partage
entre les équipes : on compare deux longueurs opposées plus vite que deux colonnes de
chiffres. Une barre vide des deux côtés signifie un total nul (ici, aucun carton rouge) et
non une donnée manquante — les statistiques réellement absentes sont listées sous le tableau.

Quelques repères :

- Les statistiques n'existent **qu'après le coup de sifflet final**. Sur un match à venir ou
  en cours, `--stats` le signale au lieu d'afficher un tableau vide.
- Une requête par match, d'où le plafond `--max-stats` (20 par défaut) si votre sélection est
  large. Avec `--team`, la question ne se pose pas.
- Le résultat est mis en cache 30 jours : un match terminé ne change plus. `--no-cache` force
  un nouvel appel.
- En CSV et JSON, `--stats` ajoute une colonne par statistique et par équipe
  (`corners_dom`, `corners_ext`, …).

#### Quand un match n'a pas de statistiques

Flashscore ne collecte pas de statistiques pour toutes les divisions. Un match de Bundesliga
en a ; le même jour, un match de Regionalliga West (D4 allemande) n'en a aucune, et **la page
du site n'en montre pas davantage** — la donnée n'existe pas, ce n'est pas le programme qui
la manque. Le message le dit et fournit l'adresse pour vérifier :

```
B. Monchengladbach II - Dortmund II : Flashscore ne collecte pas de statistiques
pour cette competition. La page du match n'en montre pas davantage :
https://www.flashscore.fr/match/dM7yWTRa/
```

L'adresse de chaque match figure dans la colonne `url` des exports, et `--open` ouvre
directement les pages concernées :

```bash
python -m ibet --team Monchengladbach --open
```

Avant de conclure à une absence de données, vérifiez le statut : les statistiques n'existent
qu'une fois le match **terminé**. Un match encore « En cours » est ignoré par `--stats`.

### Forme sur les N derniers matchs

`--form` donne, pour un match, l'historique récent des **deux** équipes :

```bash
python -m ibet --team Monchengladbach --form         # 10 derniers matchs
python -m ibet --team Monchengladbach --form 6       # 6 derniers
python -m ibet --team Monchengladbach --form 6 --stats   # + moyennes détaillées
```

```
  B. Monchengladbach  -  6 derniers matchs
    5 V  0 N  1 D    buts 20-6  (3.3 marque, 1.0 encaisse par match)
    serie : D V V V V V   (le plus recent a gauche)
    dont 4 amical(aux), marques (A) : peu representatifs

    2026-08-29 ext. RB Leipzig               0-3  D Bundesliga
    2026-08-23 ext. Schott Mainz             5-0  V Coupe d'Allemagne - DFB Pokal
    2026-08-15 dom. Aston Villa              2-1  V (A) Amical Club
    2026-08-12 ext. SSVg Velbert             8-0  V (A) Amical Club
    ...

    Moyennes par match (sur 4 match(s) avec statistiques) :
      Tirs cadres        8.5
      Tirs (total)       22.5
      Corners            7.0
      Possession         65.5%
```

Trois points importants :

- **Les amicaux sont marqués `(A)` et comptés à part.** Un 8-0 de pré-saison contre une équipe
  amateur gonfle le bilan sans rien dire de la forme réelle. Ici, 4 des 6 matchs sont des
  amicaux : le « 5 V 0 N 1 D » ne veut pas dire grand-chose, et l'affichage le signale au lieu
  de le laisser croire.
- **Les buts sont vus depuis l'équipe suivie**, à domicile comme à l'extérieur : `0-3 D` contre
  Leipzig signifie 0 marqué, 3 encaissés.
- **Le coût diffère selon les options.** `--form` seul ne coûte **qu'une requête** par match
  (le flux porte déjà l'historique des deux équipes). Avec `--stats`, il faut une requête par
  match d'historique, soit ~12 pour `--form 6` : comptez 25 s au premier appel, puis 1 s une
  fois en cache. `--form` est donc plafonné à 3 matchs sélectionnés — utilisez-le avec `--team`.

Les moyennes ne portent que sur les matchs qui ont des statistiques ; le nombre est indiqué
entre parenthèses, et vaut parfois 0 en division inférieure.

### Prévision statistique

```bash
python -m ibet --date 2026-09-06 --league "Ligue 1" --team "Marseille" --predict
```

```
  Marseille - Paris FC
  ====================
  Base : 5 et 5 matchs officiels (10 amical(aux) ecarte(s))

  Attendu             Marseille     Paris FC    Total
  ---------------------------------------------------
  Buts                     1.25         1.29     2.55
  Corners                  5.32         4.42     9.74
  Tirs cadres              4.58         4.18     8.76
  Cartons jaunes           1.42         2.14     3.56

  Issue                1 (dom.)     2 (ext.)  N (nul)
  probabilite             35.7%        37.5%    26.7%
  Scores les plus probables : 1-1 12.7%   0-1 10.1%   1-0  9.8%

  Buts                   +0.5    +1.5    +2.5    +3.5
    Marseille           71.5%   35.7%   13.2%    3.9%
    Paris FC            72.5%   37.0%   14.1%    4.2%
  total du match         +0.5    +1.5    +2.5    +3.5    +4.5    +5.5
    les deux equipes    92.2%   72.2%   46.8%   25.2%   11.5%    4.5%

  Corners                +2.5    +3.5    +4.5    +5.5    +6.5
    Marseille           86.0%   73.2%   58.1%   43.1%   30.0%
    Paris FC            77.1%   60.6%   44.0%   29.6%   18.7%
  total du match         +7.5    +8.5    +9.5   +10.5   +11.5
    les deux equipes    71.4%   60.6%   49.5%   38.8%   29.4%

  ... (memes echelles pour les tirs cadres et les cartons)

  Confrontations directes : 8 rencontres (1972 a 2026)
    2026-01-31  Ligue 1                    2 - 2
    2025-08-23  Ligue 1                    5 - 2
    (scores vus depuis Marseille ; non utilisees dans le calcul)

  Grandeur         Proposition                                    Reussite
  ------------------------------------------------------------------------
  Buts             Plus de 0.5 buts au total                         92.2%
                   Moins de 4.5 buts au total                        88.5%
                   Marseille : moins de 2.5 buts                     86.8%
  Corners          Marseille : plus de 2.5 corners                   86.0%
                   Paris FC : moins de 6.5 corners                   81.3%
  Tirs cadres      Plus de 5.5 tirs cadres au total                  86.9%
  Cartons jaunes   Marseille : moins de 3.5 cartons jaunes           94.4%

  Buts   Maher sur 93 matchs de Ligue 1, corrige du niveau des adversaires
         (5 et 4 matchs), forces regularisees (k=22), demi-vie 0 j
         (competition seulement) (sur 5 et 5 matchs)
```

Quatre grandeurs sont prévues — **buts, corners, tirs cadrés, cartons jaunes** —, chacune
déclinée seuil par seuil plutôt que sur une seule ligne : deux matchs annoncés à 26 % de
« plus de 2,5 buts » peuvent être très différents un seuil plus bas.

#### Les options de paris, par match

Chaque fiche produit **dix familles de paris**, toutes calculées sur la même loi que l'issue —
donc cohérentes entre elles :

| Famille | Exemple | Grandeurs |
|---|---|---|
| `total` | Plus de 7,5 corners au total | les quatre |
| `equipe` | Marseille : moins de 2,5 buts | les quatre |
| `fourchette` | Entre 1 et 3 buts au total | les quatre |
| `duel` | Plus de corners pour Marseille | corners, tirs, cartons |
| `issue` | Victoire Marseille | buts |
| `double chance` | Marseille ou nul · Pas de match nul | buts |
| `les deux marquent` | Les deux équipes marquent | buts |
| `écart` | Marseille gagne par 2 buts ou plus | buts |
| `parité` | Nombre total de buts pair | buts |
| `cage inviolée` | Marseille n'encaisse aucun but | buts |
| `combiné` | Victoire Marseille et plus de 1,5 buts | buts |

Elles sont présentées en **deux tableaux, qui ne se recouvrent pas** :

- **Les propositions retenues** — les plus sûres (60 à 95 %), avec **au plus deux par
  famille**. Sans ce plafond, les six retenues étaient six seuils voisins de la même échelle :
  « moins de 3,5 », « moins de 4,5 », « moins de 5,5 » ne sont pas trois choix, c'est le même
  pari trois fois. Le plafond se relâche par paliers si trop peu de familles tiennent dans la
  fourchette. La borne haute écarte les évidences : une proposition à 99,5 % est vraie par
  construction et aucun marché ne l'offre.
- **Le tableau des marchés** — tout le reste, **y compris sous 50 %**. « Victoire par deux
  buts d'écart » ne dépasse jamais 50 %, et n'en est pas moins un pari qu'on peut vouloir
  prendre : le masquer reviendrait à décider à la place du lecteur quels marchés existent. Les
  familles `total` et `equipe` en sont absentes — les échelles les montrent déjà, seuil par
  seuil et plus finement.

Deux garanties, vérifiées par les tests :

1. **Chaque libellé produit est tranchable après coup** par `verify.check_offer`, à partir du
   seul score (ou de la seule statistique) du match. Une proposition qu'on ne saurait pas juger
   trois jours plus tard n'a rien à faire sur une fiche.
2. **Les familles sont cohérentes entre elles** : `pair + impair = 1`, un écart de 3 buts est
   plus rare qu'un écart de 2, lui-même plus rare que la victoire simple, un combiné ne dépasse
   jamais la moins probable de ses deux conditions, et une fourchette vaut exactement la
   différence des deux seuils qui la bornent.

> Le bilan de `ibet/evaluation/verify.py` ne porte que sur **les propositions retenues**, pas sur le tableau
> des marchés : y verser des paris à 50 % rendrait le taux de réussite illisible.

##### Le côté du marché mis en avant

Chaque grandeur porte un `marche_privilegie`, rendu par `GET
/api/predictions/{id}/options` :

| Grandeur | Côté mis en avant |
|---|---|
| Buts | les deux |
| **Corners** | **plus de X** |
| **Tirs cadrés** | **plus de X** |
| Cartons jaunes | les deux |

Une précision qui évite un malentendu, parce qu'elle change ce qu'on peut en
attendre : « plus de 9,5 corners » et « moins de 9,5 corners » sont les **deux
faces de la même probabilité** — l'une vaut *p*, l'autre *1 − p*. Le modèle ne
peut donc pas être plus fiable sur l'une que sur l'autre, et la mesure de
calibration du projet ne montre aucun écart entre les deux sens. `prefere` ne
change **aucun calcul** : c'est un choix de **présentation**, qui dit quelle face
afficher en premier. Les deux restent dans la fiche — écarter l'autre reviendrait
à décider à la place du lecteur.

#### Le modèle

Aucune formule maison. Deux méthodes publiées, appliquées telles quelles :

1. **Nombre attendu (`lambda`)** — modèle attaque / défense de **Maher (1982)**, *Modelling
   association football scores*, Statistica Neerlandica 36(3) :

   ```
   Attaque_i   = (événements produits par i et par match) / moyenne de la compétition
   Défense_j   = (événements concédés par j et par match) / moyenne de la compétition
   lambda_dom  = Attaque_dom × Défense_ext × (moyenne à domicile)
   lambda_ext  = Attaque_ext × Défense_dom × (moyenne à l'extérieur)
   ```

   Deux équipes exactement moyennes redonnent la moyenne de la compétition — c'est la
   propriété attendue, et elle est vérifiée par les tests. **Les quatre grandeurs passent par
   ce même modèle** : corners, tirs cadrés et cartons ont leur propre référence de
   compétition, bâtie comme celle des buts (voir plus bas).

2. **Correction du niveau des adversaires** — trois buts contre la meilleure défense du
   championnat ne valent pas trois buts contre la pire. La moyenne brute traite les deux de la
   même façon et récompense un calendrier facile. Chaque match est donc rapporté à la force de
   l'adversaire rencontré :

   ```
   produit corrigé = produit / défense(adversaire)
   concédé corrigé = concédé / attaque(adversaire)
   ```

   C'est la correction de calendrier implicite dans l'estimation simultanée de Maher (chez
   lui, toutes les forces sont estimées ensemble). Elle est appliquée ici en une passe, à
   partir des forces mesurées sur la compétition entière — **18 équipes notées en Bundesliga,
   20 en Premier League, 18 en Ligue 1**, soit la totalité de chaque championnat. Un
   adversaire d'une autre compétition (coupe) n'a pas de force connue : ce match reste brut, et
   l'affichage indique combien de matchs ont réellement été corrigés.

3. **Régularisation des forces** — Maher estime attaque et défense sur une saison complète.
   Sur 4 ou 5 matchs, un ratio de 1,5 tient autant du bruit que du signal, et le modèle en
   **multiplie deux** : les écarts se composent. Les forces sont donc ramenées vers 1
   proportionnellement à la taille de l'échantillon (estimateur à rétrécissement, *empirical
   Bayes* — Efron & Morris 1975, JASA 70) :

   ```
   force_ajustée = (n × force_observée + k) / (n + k)      avec k = 22
   ```

   `k` s'interprète comme un nombre de matchs fictifs joués au niveau moyen : à `n = k`,
   l'observation et la moyenne pèsent autant. `n` est l'**effectif efficace** — la somme des
   poids des matchs, égale à leur nombre tant que la pondération par ancienneté est
   désactivée. La valeur `k = 22` est **mesurée** (voir « Réglage des paramètres » et
   « L'étalement, et pourquoi k valait 10 »). Elle a longtemps valu 10.

   L'effet est important. Hoffenheim - Dortmund, sur 5 matchs :

   | | sans régularisation | avec |
   |---|---|---|
   | Buts attendus | 0,84 - 3,33 | 1,60 - 1,93 |
   | Victoire extérieure | **84,2 %** | **45,8 %** |

   84 % sur cinq matchs n'était défendable par rien ; aucun marché ne descend sous 55-60 %
   sur ce type d'affiche.

4. **Distribution** — loi de **Poisson** de paramètre `lambda`, choix classique pour un
   comptage d'événements sur une durée fixe, retenu par Maher puis par **Dixon & Coles
   (1997)**, *Applied Statistics* 46(2). Toutes les probabilités de la fiche — issue, seuils,
   scores exacts — sont sommées sur **une seule matrice jointe**, ce qui garantit qu'elles
   restent cohérentes entre elles. La matrice est tronquée à six écarts-types au-delà de
   `lambda`, et non à un nombre fixe d'événements : à `lambda = 6` par équipe (des corners),
   une borne fixe à 12 laissait tomber 2 pour mille de la masse.

5. **Réglages** — trois paramètres, groupés dans `predict.Params` et **enregistrés dans chaque
   fiche** : `rho` (dépendance des petits scores, Dixon & Coles), `half_life` (demi-vie de la
   pondération par ancienneté), `shrinkage` (le `k` ci-dessus). Valeurs retenues :
   `Params(rho=0.0, half_life=0.0, shrinkage=22.0)`. Les deux premières sont donc neutres :
   les mécanismes existent et sont testés, la mesure ne les soutient pas (voir plus bas).

#### La force des équipes sur une échelle commune

Le modèle normalise chaque équipe par la moyenne de **sa** compétition. C'est juste à
l'intérieur d'un championnat, et faux dès qu'on en sort : deux équipes qui marquent 1,8 but
par match reçoivent la même note, que ce soit en Premier League ou en troisième division. La
mesure l'a établi — sur les fiches émises, le modèle n'atteignait 50 % de certitude que 7 fois
sur 26, là où le marché va jusqu'à 90 %.

`ibet/prevision/forces.py` construit l'échelle qui manquait sur **16 855 matchs et 2 551 équipes déjà en cache**
(2003→2026), sans une requête de plus :

```bash
python -m ibet forces     # reconstruit forces.json (25 s)
```

> ⚠️ **À relancer quand le cache grossit.** Un fichier de notes ne se périme pas
> bruyamment : il continue de répondre, avec des valeurs plausibles, simplement
> calculées sur moins de matchs qu'il n'en existe. `forces.avertissement()` le
> signale désormais avant chaque émission et chaque banc d'essai — voir
> [Ce qu'un fichier de notes périmé coûte](#ce-quun-fichier-de-notes-périmé-coûte).

##### Notes attaque / défense, estimées par maximum de vraisemblance

C'est le modèle de **Maher (1982)** et **Dixon & Coles (1997)** — attaque et défense séparées
pour chaque équipe :

```
lambda_dom = exp(mu + h + attaque[dom] − defense[ext])
lambda_ext = exp(mu     + attaque[ext] − defense[dom])
```

Toutes les notes sont estimées **d'un coup**, au maximum de vraisemblance, sur tout le corpus
(`forces.construire`). Chaque passe résout exactement chaque note, les autres fixées :

```
exp(attaque[i]) = (buts marqués + a_priori) / (buts attendus + a_priori)
```

- **`OUBLI = 0.002`** par jour : un match d'il y a un an pèse deux fois moins qu'un match
  d'hier ;
- **`A_PRIORI = 1`** : chaque équipe part d'un match fictif joué exactement à la moyenne, ce qui
  empêche une équipe vue deux fois de recevoir une note extrême.

**Pourquoi pas l'apprentissage en ligne.** La première version apprenait les notes par descente
de gradient, match après match, avec un pas de 0,04. Un tel pas ne laisse pas aux notes le temps
de s'écarter de la moyenne quand l'équipe médiane du corpus n'a que **4 matchs** : même les
grands clubs restaient tassés. PSG – Slovan Bratislava sortait à 55 % pour Paris (marché : 95 %),
Mozambique – Sénégal à 39 % pour le Sénégal (marché : 71 %). Le modèle n'était **pas** mal
calibré — un favori annoncé à 64 % gagnait 63 % du temps — il départageait mal les équipes.

| Walk-forward, données d'avant chaque match | en ligne | **vraisemblance** | marché |
|---|---|---|---|
| 4 996 matchs de 2026 — Brier 1X2 | 0,6014 | **0,5924** *(t = −4,8)* | — |
| 67 fiches émises avec cotes — Brier 1X2 | 0,5332 | **0,4891** | 0,4581 |
| 67 fiches émises — favori juste | 45 | **49** | 50 |

Réglages balayés (Brier 1X2, 4 996 matchs) : oubli 0 → 0,5932, 0,002 → **0,5924**,
0,005 → 0,5942 ; a priori 1 → **0,5924**, 3 → 0,5937. Essayés sans gain : avantage du terrain
par compétition (t = +0,3), cibles mélangées buts / xG (t = −0,9), dispersion ou Dixon-Coles
sur le 1X2 (± 0,0005), probabilités plus tranchées (température : toujours pire).

##### Le total resserré vers la moyenne de la compétition

Les notes étalent trop les **totaux** : la pente du total réel sur le total prévu n'est que de
**0,52** (4 899 matchs de 2026). Un match annoncé à 3,8 buts en donne 3,3 ; un match annoncé à
1,8 en donne 2,3. D'où les « moins de 2,5 » annoncés à 69 % et réussis à 60 %, et les « plus de
3,5 » annoncés à 69 % et réussis à 48 %.

`forces.lambdas_attendus` resserre donc le total **de moitié** vers la moyenne de buts de la
compétition (`RESSERREMENT_TOTAL = 0.5`, moyenne tirée vers la moyenne globale par 10 matchs
fictifs, enregistrée dans `forces.json`). L'écart entre les deux équipes est gardé tel quel.

| | avant | **après** |
|---|---|---|
| Brier des seuils 1,5 / 2,5 / 3,5 | 0,2141 | **0,2087** *(t = −7,8)* |
| Propositions de total à 60-95 % : annoncé / observé | 73,6 % / 71,2 % | **72,2 % / 72,7 %** |

Le même resserrement appliqué à l'écart n'apporte rien de mesurable (t = −1,1) : il n'est pas
appliqué.

##### L'issue combinée aux cotes des bookmakers

Quand les cotes 1X2 sont connues à l'émission, le 1X2 publié — et ses propositions victoire, nul,
double chance — est le mélange **10 % modèle, 90 % bookmaker** (marge de Shin retirée,
`POIDS_MODELE_ISSUE` dans `ibet/modeles/buts.py`). Les nombres de buts attendus ne changent pas.

Mesuré sur **1 784 matchs de 2026** appariés aux cotes de Bet365 (football-data.co.uk), le modèle
rejoué avec les seules données d'avant chaque match :

| | Brier 1X2 | Favori juste |
|---|---|---|
| modèle seul | 0,6146 | 48,5 % |
| **Bet365 seul** | **0,5954** | **50,8 %** |
| combinaison, 10 % modèle *(retenue)* | 0,5958 *(+0,0004, non significatif)* | 50,6 % |

Sur le 1X2, le modèle n'apporte rien au bookmaker : le poids réglé mois par mois hors échantillon
vaut 0. Les 10 % gardent une combinaison sans perte mesurable. Une première mesure sur 67 fiches
semblait donner l'avantage à la combinaison : c'était du bruit, que 3 731 matchs BetExplorer
puis 1 784 matchs Bet365 ont effacé. Sur le plus / moins de 2,5 buts, en revanche, le modèle est
à 0,003 de Bet365.

Le critère 13 (valeur des paris) continue de comparer le **modèle seul** au bookmaker.

**Validation historique** — l'ancienne version en ligne, 5 360 matchs :

| | Brier | vs forme seule |
|---|---|---|
| forme récente seule *(l'ancien modèle)* | 0,6196 | — |
| Elo, écart seul | 0,6047 | t = −4,8 |
| **attaque / défense** | **0,5916** | **t = −11,7** |
| uniforme | 0,6667 | t = +15,0 |

##### Ce qu'un fichier de notes périmé coûte

Le corpus a grossi de **12 710 à 16 855 matchs** — les 4 145 de plus viennent du
rattrapage décrit plus haut, qui est allé chercher une trentaine d'historiques d'équipes pour
retrouver des résultats sortis de la fenêtre de la source. `forces.json`, lui, n'a pas été
reconstruit. Rien ne l'a signalé.

Les conséquences n'étaient pas subtiles. Dans l'ancien corpus, **aucune sélection nationale**
n'atteignait le seuil de huit matchs : elles étaient toutes absentes des notes, et
`POIDS_MODELE = 0` n'a d'effet que là où les notes existent. Toutes les fiches de sélection
retombaient donc sur le modèle de forme — celui que ce tableau donne perdant.

Ça se voyait dans les fiches, sans qu'on sache l'expliquer : sur les 28 prévisions émises pour
les qualifications CAN et la Ligue des Nations, le total de buts attendu ne s'écartait de la
moyenne de sa compétition que de **7 %** (écart-type rapporté à la moyenne : 7,6 % pour la CAN,
7,2 % pour l'UEFA). Pays-Bas - Allemagne et Autriche - Israël recevaient pratiquement la même
annonce. Le modèle ne discriminait pas, parce qu'il n'avait rien pour le faire.

Une fois le fichier reconstruit, les mêmes équipes sont notées et **elles se séparent** :
Pays-Bas attaque +0,666, Comores −0,369 — presque une unité d'écart en logarithme, soit un
rapport de 2,8 sur les buts attendus.

**La mesure, sur 910 matchs de sélection en walk-forward**, notes contre modèle de forme :

| | Modèle | Notes | Écart apparié | t |
|---|---|---|---|---|
| Erreur absolue sur l'**écart** | 1,390 | **1,293** | −0,097 | **−5,5** |
| Erreur absolue sur le **total** | 1,361 | **1,326** | −0,035 | **−2,8** |
| log-loss | 1,8606 | 1,8526 | −0,008 | −1,1 |

Le gain est le plus net là où les fiches étaient le plus plates : l'écart entre les deux
équipes. Le résultat mesuré sur les clubs se transporte donc bien aux sélections — ce qui
n'allait pas de soi, une sélection jouant six fois par an, contre des adversaires d'un autre
continent, avec un effectif qui change.

**Le garde-fou.** `forces.fraicheur()` compare le nombre de matchs dont les notes sont issues à
ce que le cache contient. Au-delà de 10 % de retard, `forces.avertissement()` rend une phrase
que `ibet/prevision/forecast.py` et `main.py --backtest` écrivent sur la sortie d'erreur **avant** de
travailler — avant, parce qu'une fiche produite sur des notes périmées ne se rattrape pas :
elle est enregistrée telle quelle et n'est jamais recalculée.

Le seuil de 10 % n'est pas un seuil de justesse : des notes un peu anciennes restent bonnes. Il
marque le point où l'écart devient assez grand pour changer **qui** est noté, et c'est en
franchissant ce cap que les sélections sont passées de « aucune note » à « toutes notées ».

Et, contrairement à Elo, elles améliorent aussi le **total** : erreur absolue 1,396 contre
1,437, **t = −5,9**. Elo ne sait que départager deux équipes ; l'attaque/défense dit aussi
combien de buts attendre.

##### Les xG pour le volume, les buts pour l'écart

Sur les 1 201 matchs où les deux jeux de notes sont établis :

| Notes construites sur | Brier | erreur sur le total |
|---|---|---|
| les buts | **0,5969** | 1,388 |
| les xG | 0,6033 | **1,344** *(t = −3,9)* |
| **total xG + écart buts** *(retenu)* | 0,5975 | **1,343** |

Les xG lissent la réussite devant le but : ils disent mieux **combien d'occasions** une équipe
se procure, moins bien **laquelle des deux convertit**. Prendre le meilleur des deux sur chaque
moitié donne le total des xG sans rien perdre sur l'issue.

##### Le poids laissé au modèle : zéro, et deux mesures le disent

| Mesure | Échantillon | Poids optimal du modèle |
|---|---|---|
| contre une reconstitution du signal de forme | 5 360 matchs | **0 %** *(100 % coûte +0,0280 de Brier, t = +11,7)* |
| contre le **vrai** modèle, contexte compris | 21 fiches tranchées | **0 %** *(0,5949 contre 0,6350, t = −1,67)* |

Les deux courbes sont **monotones jusqu'à zéro**. La seconde a peu de matchs, la première
beaucoup de puissance ; elles concordent. Le modèle n'est donc plus employé pour les **buts**
quand les notes existent. Il reste :

- le repli quand elles manquent (5 fiches sur 26 : équipe trop peu vue, ou deux groupes qui ne
  se sont jamais rencontrés) ;
- le seul estimateur des **corners, tirs cadrés et cartons**, que les notes ne couvrent pas ;
- le porteur des **corrections de contexte**, qui s'appliquent par-dessus, inchangées.

Sur les 26 fiches déjà tranchées, le Brier passe de **0,6742 à 0,6384** (t = −1,79) — le modèle
passe ainsi, pour la première fois, sous le score d'un tirage uniforme (0,6667).

##### Deux garde-fous, parce qu'une note peut ne rien vouloir dire

- Sous 8 matchs, les notes d'une équipe ne sont pas établies et `lambdas_attendus` rend `None`
  — jamais « moyenne ». Ne pas connaître une équipe et la savoir moyenne sont deux états
  différents.
- Les mises à jour sont à somme nulle entre les deux équipes : la moyenne d'un groupe fermé ne
  bouge jamais, et la meilleure équipe d'un championnat faible monte autant que celle d'un
  championnat fort. Le corpus compte **16 groupes**, dont un de 1 673 équipes (78 %) relié par
  les coupes d'Europe. Shymkent y figurait à 1833 points Elo, au-dessus du Bayern, parce qu'il
  domine un îlot qui n'a jamais rencontré le reste — sa note ne disait pas qu'il était fort,
  seulement qu'il était le meilleur de son îlot. Deux équipes de groupes différents ne sont
  **pas comparables**, et la comparaison est refusée.

> Un classement **Elo** avait d'abord été construit ici. Il gardait une valeur propre
> (t = −13,4 contre l'uniforme) mais il est **dominé sur les deux tableaux** : Brier 0,6047
> contre 0,5916, et surtout il ne sait rien dire du total de buts. Il a donc été **retiré**
> plutôt que laissé en place à ne plus servir.

#### Dispersion et corrélation, mesurées sur le corpus

La famille « total » était mal calibrée (−8,8 points) alors que la famille « equipe » ne
l'était pas (−1,2 point). Deux causes possibles, et elles n'appellent pas le même correctif :

```
Var(total) = Var(dom) + Var(ext) + 2 Cov(dom, ext)
```

Mesuré équipe par équipe, chacune rapportée à sa propre moyenne (ce qui retire
l'hétérogénéité entre équipes), sur des milliers de matchs :

| Grandeur | dispersion `φ` | corrélation `ρ` | matchs |
|---|---|---|---|
| Buts | 1,173 | −0,008 *(t = −0,8, nul)* | 12 710 |
| Corners | **1,615** | **−0,149** *(t = −9,0)* | 4 097 |
| Tirs cadrés | 1,396 | +0,010 *(t = +0,6, nul)* | 4 095 |
| Cartons jaunes | **0,847** | **+0,083** *(t = +4,9)* | 3 976 |

Trois enseignements :

- **Les corners sont partiellement à somme nulle** : une équipe qui domine en prend au
  détriment de l'autre. La corrélation négative *resserre* la loi du total sous ce que
  l'indépendance prédit — 15,25 contre 12,97 attendu, pour 12,61 observé.
- **Les cartons vont ensemble** : un match tendu en donne aux deux. Et ils sont la seule
  grandeur **sous-dispersée** — il s'en donne un nombre remarquablement régulier. Une
  binomiale négative ne sait pas se resserrer ; sous 1, `count_pmf` passe donc à une
  **binomiale**, qui donne exactement la dispersion voulue.
- **La corrélation ne change que les totaux.** Les lois par équipe sont inchangées, ce qui
  explique que la famille « equipe » allait déjà bien. `dispersion_du_total` absorbe la
  corrélation en calant les deux premiers moments du total.

> ⚠️ Ces valeurs corrigent les **lois**, pas le **biais de moyenne**. Le seuil « plus de 7,5
> corners » restait annoncé à 80,6 % pour 57,1 % observé après correction : cet échec-là vient
> de ce que le modèle sur-annonce le total de corners (10,23 prévu pour 9,54 réel), pas de la
> forme de la loi. Le facteur `CALIBRATION["corners"] = 1.07` pousse d'ailleurs dans le
> mauvais sens et reste à réexaminer sur un échantillon suffisant.

#### D'où viennent les moyennes de la compétition

Flashscore ne publie pas de classement exploitable par ces flux : tous les points d'entrée
testés renvoient vide, un autre championnat, ou 404. La référence est donc **recalculée depuis
les résultats réels** : le programme prend les autres matchs de la même compétition le même
jour, lit l'historique des équipes concernées, déduplique, et en tire les moyennes.

En pratique cela donne des échantillons solides — 138 matchs pour la Bundesliga
(1,91 but à domicile, 1,40 à l'extérieur, soit 3,30 par match), 98 pour la Ligue 1 — et
l'avantage du terrain en ressort tout seul. L'échantillon couvre toutes les équipes de la
compétition, pas seulement les deux du match : une équipe n'est donc pas normalisée par
elle-même.

**Corners, tirs cadrés et cartons ont la même référence**, bâtie sur les mêmes matchs. Elle
coûte une requête par match de la compétition (plafonnée à 120), puis rien pendant trente
jours : un match terminé ne change plus. Sous 15 matchs effectivement couverts elle n'est pas
rendue, et la grandeur retombe sur la moyenne production / concession — ce qui arrive dans les
divisions que Flashscore ne détaille pas.

#### Dispersion des comptages

La loi de Poisson impose variance = moyenne. Si une grandeur varie davantage, Poisson est trop
étroite et les probabilités de seuil sont trop tranchées aux extrêmes. Les corners sont dans ce
cas ; ils passent donc par une **binomiale négative** de même moyenne et de variance
`moyenne × dispersion` — qui redonne exactement Poisson à dispersion 1, ce que les tests
vérifient.

Deux mesures, sur des échantillons et des méthodes différentes :

| Grandeur | dispersion **brute** (variance / moyenne, toutes équipes confondues) | dispersion **conditionnelle** (chaque équipe rapportée à sa propre moyenne) | retenue |
|---|---|---|---|
| Buts | 1,34 | **1,12** | 1,00 (Poisson) |
| Corners | 2,13 | — | **1,37** |
| Tirs cadrés | 1,75 | — | 1,00 (Poisson) |
| Cartons jaunes | 0,85 | — | 1,00 (Poisson) |

Les deux colonnes ne mesurent pas la même chose, et c'est la seconde qui compte. Par la loi de
la variance totale, si `X | lambda` suit bien Poisson :

```
Var(X) = E[lambda] + Var(lambda)
```

Le second terme est l'hétérogénéité **entre équipes** : mélanger une attaque forte et une
attaque faible gonfle le rapport même quand Poisson est exactement vrai pour chacune. Or c'est
l'hypothèse conditionnelle que le modèle fait, pas la marginale. Sur les buts, la mesure brute
donne 1,34 et la conditionnelle 1,12 — et une partie de ce qui reste vient de ce que le
`lambda` de chaque équipe est lui-même estimé sur neuf matchs. **La dispersion brute est donc
une borne supérieure**, pas la valeur à utiliser.

Ce qu'on peut en conclure sans se tromper : l'**ordre** est net et cohérent d'une mesure à
l'autre — corners ≫ tirs cadrés > buts > cartons —, et les corners se détachent assez pour que
les corriger vaille mieux que de ne rien faire. Les cartons, mesurés **sous** 1, n'ont rien à
élargir. Les tirs cadrés sont dans une zone grise : faute d'une mesure conditionnelle sur eux,
ils restent sur Poisson plutôt que d'être élargis d'une valeur devinée.

#### Limites, à lire avant d'interpréter

- **Les scores sont supposés indépendants.** Le facteur τ de Dixon & Coles, qui corrige les
  quatre petits scores (0-0, 1-0, 0-1, 1-1), est implémenté et réglable par `Params.rho`, mais
  **laissé à zéro** : mesuré sur 149 matchs, il ne change rien (quatrième décimale).
- **Seuls les corners sont corrigés de leur surdispersion** (voir ci-dessous). Les tirs cadrés
  le sont probablement aussi, dans une moindre mesure, et ne le sont pas.
- **L'échantillon est petit** : quelques matchs officiels par équipe, les amicaux étant
  écartés du calcul. La régularisation en tient compte, mais l'incertitude reste large et
  n'est pas affichée sous forme d'intervalle.
- **Les deux équipes sont toujours évaluées sur le même périmètre** : soit les deux sur les
  matchs de la compétition (si chacune en a au moins 4), soit les deux sur tous leurs matchs
  officiels. Jamais l'une sur le championnat et l'autre sur un mélange coupe + championnat.
  Le périmètre est décidé **grandeur par grandeur**, sur les matchs qui la portent : une équipe
  peut avoir dix matchs de championnat et trois fiches statistiques.
- **Certains tournois de pré-saison ne sont pas marqués « amical »** par la source (Emirates
  Cup, par exemple) et comptent donc comme officiels.
- **Aucune pondération par l'ancienneté** en vigueur (le mécanisme existe, voir plus bas), ni
  prise en compte des absents, du calendrier ou de la motivation.
- **Aucun terme de confrontation directe.** Les rencontres passées entre les deux équipes sont
  affichées, jamais utilisées dans le calcul.
- **Une prévision ne vaut que pour un match à venir.** Sur un match déjà joué, le calcul reste
  correct mais ce n'est plus une prévision : l'affichage le signale et rappelle le score réel,
  pour qu'une rétrodiction réussie ne passe pas pour une performance du modèle.

Ces probabilités décrivent **le modèle, pas la réalité**. Ce n'est pas un outil de pari.

### Évaluer le modèle (`--backtest`)

Un modèle jamais confronté aux résultats n'est qu'une opinion mise en forme. `--backtest`
rejoue les matchs **terminés** de la sélection et compare ses probabilités à ce qui s'est
réellement produit :

```bash
python -m ibet --date 2026-09-04 --backtest --quiet
```

Deux précautions rendent la mesure honnête :

- **Coupure temporelle.** Le flux rend les derniers matchs d'une équipe *à ce jour*. Rejouer
  un match d'il y a cinq jours sans couper l'historique reviendrait à lui donner connaissance
  de ce qui s'est passé après. La forme et la référence de championnat sont donc recalculées
  avec une coupure au coup d'envoi du match évalué.
- **Deux modèles de référence à battre** : `uniforme` (1/3 partout) et `fréquences de la
  compétition` (les taux 1/N/2 du championnat, sans rien savoir des équipes). Si le modèle ne
  bat pas le second, toute la machinerie attaque/défense n'apporte rien.

Quatre mesures, toutes standard :

| Mesure | Ce qu'elle dit | Lecture |
|---|---|---|
| **Brier** (Brier 1950) | écart quadratique entre probabilité annoncée et issue | plus bas est meilleur |
| **Log-loss** | −log(p) accordé à l'issue survenue | punit durement une certitude erronée |
| **Calibration** | ce qui est annoncé à 70 % arrive-t-il 7 fois sur 10 ? | annoncé ≈ observé |
| **Biais par grandeur** | le modèle annonce-t-il trop de corners, de buts ? | proche de 0 |

Les deux dernières répondent à des questions que le Brier ne pose pas. Un modèle peut classer
correctement et mentir sur ses pourcentages ; et le Brier ne juge que l'issue, donc les
colonnes « corners », « tirs cadrés » et « cartons » d'une fiche n'avaient jusqu'ici **aucune
garantie derrière elles**. Le biais par grandeur n'apparaît que si les statistiques réelles
sont disponibles, c'est-à-dire avec `--stats`.

#### Résultat mesuré, et ce qu'il autorise à dire

Sur **149 matchs terminés** répartis sur 6 jours et une trentaine de compétitions, comparaison
appariée (les deux modèles voient exactement les mêmes rencontres, avec le même historique) :

| | Brier | Log-loss | Réussite | Erreur sur le total de buts |
|---|---|---|---|---|
| modèle | **0,6291** | **1,0421** | 45,6 % | **1,588** |
| état antérieur du code | 0,6339 | 1,0494 | 48,3 % | 1,601 |
| fréquences de la compétition | 0,6490 | 1,0689 | 41,6 % | — |

Écarts appariés, signe négatif = mieux :

| | Brier | Log-loss |
|---|---|---|
| modèle − état antérieur | −0,0048 ± 0,0047 (t = −1,0) | −0,0072 ± 0,0069 (t = −1,1) |
| modèle − fréquences | −0,0198 ± 0,0172 (t = −1,2) | −0,0267 ± 0,0233 (t = −1,2) |
| état antérieur − fréquences | −0,0151 ± 0,0212 (t = −0,7) | −0,0195 ± 0,0294 (t = −0,7) |

Le modèle arrive devant sur les probabilités, et son avance sur les fréquences de la
compétition a grandi (t passe de −0,7 à −1,2). **Mais rien n'est établi** : il faudrait
|t| ≥ 2 pour conclure, et on est à 1,2. La bonne formulation est « le modèle va
systématiquement dans le bon sens, sur un échantillon trop petit pour que ce soit démontré ».

Le taux de réussite, lui, **baisse** (48,3 % → 45,6 %). Ce n'est pas contradictoire : il ne
regarde que l'issue la plus probable et ignore complètement la calibration. Un modèle qui
annonce 40/35/25 au lieu de 45/30/25 peut se tromper plus souvent de favori tout en donnant
de meilleures probabilités. C'est le Brier et le log-loss qui tranchent, pas lui.

La fenêtre de ±7 jours de la source est ici la contrainte de fond : elle plafonne l'échantillon
disponible, et début septembre, la trêve internationale la réduit encore.

#### Réglage des paramètres

`backtest.tune()` compare des réglages **sur les mêmes matchs**, avec la même coupure
temporelle, et classe par log-vraisemblance. Sur les 149 matchs ci-dessus, à partir de la
configuration retenue (`k = 10`, sans pondération, ρ = 0) :

| Réglage | Log-loss | Écart apparié |
|---|---|---|
| k = 14 | 1,0396 | −0,0025 ± 0,0040 (t = −0,6) |
| k = 20 | 1,0397 | −0,0024 ± 0,0078 (t = −0,3) |
| ρ = −0,08 | 1,0413 | −0,0008 ± 0,0032 (t = −0,3) |
| ρ = −0,03 | 1,0416 | −0,0005 ± 0,0012 (t = −0,4) |
| **retenu (k = 10, ρ = 0, sans pondération)** | **1,0421** | — |
| demi-vie 60 j | 1,0437 | +0,0016 ± 0,0110 |
| demi-vie 45 j | 1,0459 | +0,0037 ± 0,0127 |
| demi-vie 30 j | 1,0491 | +0,0069 ± 0,0148 |
| demi-vie 21 j | 1,0513 | +0,0092 ± 0,0162 |
| demi-vie 14 j | 1,0532 | +0,0111 ± 0,0174 |
| k = 6 | 1,0519 | +0,0097 ± 0,0067 (t = +1,5) |

Trois lectures :

- **Le rétrécissement sert, et `k = 6` est trop faible** (+0,0097, t = 1,5). Sur ces 149
  matchs la courbe paraît plate au-delà de 10 : `k = 14` et `k = 20` sont indiscernables de
  `k = 10`. ⚠️ **Cette lecture était fausse, faute d'échantillon.** Les deux valeurs faisaient
  déjà *mieux*, et 149 matchs jugés sur la seule issue ne pouvaient pas le voir. Mesuré sur
  tout le corpus et sur le **seuil** — ce qu'une fiche vend vraiment —, `k = 22` gagne
  nettement. Voir la section suivante ; c'est la valeur en service.
- **La pondération par ancienneté ne sert pas ici.** Les cinq demi-vies essayées font toutes
  moins bien que son absence, et l'écart croît quand la demi-vie raccourcit — ce n'est pas du
  bruit sans direction, c'est une pente. Elle est donc **désactivée** (`half_life = 0`), le
  mécanisme restant en place et testé pour être repris sur un échantillon plus large.
- **Le facteur τ de Dixon-Coles ne change rien** : quatrième décimale, dans les deux sens.

#### L'étalement, et pourquoi `k` valait 10

Le classement ci-dessus juge un réglage sur **l'issue d'un match**. Une fiche ne vend pas
l'issue : elle vend des **seuils** — « plus de 9,5 corners », « moins de 4,5 cartons ». Un
réglage peut être neutre sur le premier et décisif sur les seconds, et c'est exactement ce
qui s'est produit.

La mesure qui le montre est la régression du réel sur le prévu, `réel = a + b × prévu` :

| Grandeur | n | pente `b` à `k = 10` |
|---|---|---|
| Buts | 6 316 | 0,94 |
| Corners | 1 750 | **0,43** |
| Tirs cadrés | 1 748 | 0,62 |
| Cartons jaunes | 1 651 | 0,66 |

Une pente de 0,43 veut dire : **quand le modèle annonce un corner de plus, la réalité n'en
fait que 0,43**. Le modèle *sur-étale* — il écarte ses λ de la moyenne deux fois trop. Et un
λ trop écarté pousse la probabilité d'un seuil trop loin vers la certitude.

C'est la forme exacte du défaut que les fiches montraient depuis le début — « annoncé 80 %,
observé 57 % » — et qu'on avait cherché du côté de la **calibration**, avec un recalage des
corners à 1,07 qu'il a fallu retirer. Le défaut n'était pas dans le niveau annoncé, il était
dans l'**étalement**.

Le résultat, en walk-forward sur le corpus entier :

| Grandeur | n | Brier `k = 10` → `k = 22` | t |
|---|---|---|---|
| Buts | 6 316 | 0,2472 → 0,2447 | −4,9 |
| Corners | 1 750 | 0,2530 → 0,2505 | −2,9 |
| Tirs cadrés | 1 748 | 0,2476 → 0,2460 | −1,6 |
| Cartons jaunes | 1 651 | 0,2470 → 0,2433 | −3,8 |

**Le contrôle qui compte** : un réglage qui rapproche simplement les prévisions de la moyenne
de la compétition gagne mécaniquement sur une ligne placée près de cette moyenne, et perd sur
les extrêmes. On a donc rejoué **toutes** les lignes proposables :

```
  grandeur        lignes essayees          ameliorees   degradees
  buts            1.5 / 2.5 / 3.5               3           0
  corners         7.5 / 8.5 / 9.5 / 10.5 / 11.5 5           0
  tirs cadres     6.5 / 7.5 / 8.5 / 9.5         4           0
  cartons         2.5 / 3.5 / 4.5               3           0
```

**16 sur 16**, en score de Brier *et* en log-loss. Le log-loss punit très durement une
certitude erronée : qu'il suive écarte l'hypothèse d'un gain acheté en s'autorisant des
annonces plus sûres.

Pourquoi 22 et non un optimum par grandeur (26, 18, 22, 22) ? Parce que les écarts entre 18
et 26 sont dans le bruit pour chacune prise séparément. Quatre constantes réglées chacune sur
son propre échantillon s'ajusteraient au passé bien plus qu'une seule que quatre mesures
indépendantes désignent.

**Ce que cet épisode enseigne sur la méthode.** La bonne valeur était visible dès le premier
classement — `k = 14` et `k = 20` y faisaient déjà mieux. Deux choses l'ont masquée : un
échantillon de 149 matchs, et surtout le fait de juger sur l'issue une grandeur qui sert aux
seuils. Un réglage doit être mesuré **sur l'usage auquel il sert**.

#### Ce qui a été essayé et écarté

Deux corrections défendables sur le papier ont été implémentées, mesurées, puis retirées.
Elles sont documentées ici pour qu'on ne les réinvente pas sans mesurer.

| Correction | Idée | Effet sur le log-loss |
|---|---|---|
| **Correction du lieu** | ramener chaque match d'historique à un lieu neutre avant d'en faire la moyenne, l'avantage du terrain étant déjà dans le multiplicateur | +0,0027 ± 0,0032 (t = +0,8) |
| **Retrait du match courant** | recalculer la force de l'adversaire sans le match qu'elle sert à corriger (auto-inclusion) | +0,0015 ± 0,0017 (t = +0,9) |
| **les deux ensemble** | | +0,0041 ± 0,0035 (t = +1,2) |

Deux **paramètres supplémentaires** ont ensuite été ajoutés puis réglés de la
même façon, sur 109 matchs de douze championnats. Tous deux sont neutres à zéro,
c'est-à-dire que la première ligne du tableau est le modèle actuel :

| Réglage | Log-loss | Écart apparié |
|---|---|---|
| **retenu (`home_edge = 0`, `lambda_shrink = 0`)** | **1,0203** | — |
| `home_edge = 0,25` | 1,0209 | +0,0006 ± 0,0036 (t = +0,2) |
| `home_edge = 0,50` | 1,0226 | +0,0024 ± 0,0074 (t = +0,3) |
| `home_edge = 1,00` | 1,0299 | +0,0096 ± 0,0152 (t = +0,6) |
| `lambda_shrink = 2` | 1,0322 | +0,0119 ± 0,0054 (t = **+2,2**) |
| `lambda_shrink = 5` | 1,0436 | +0,0233 ± 0,0093 (t = **+2,5**) |
| `lambda_shrink = 10` | 1,0541 | +0,0338 ± 0,0124 (t = **+2,7**) |

- **`home_edge`** — avantage du terrain propre à chaque équipe plutôt que celui,
  commun, de la compétition. L'idée tient : tous les stades ne pèsent pas pareil.
  La mesure ne la soutient pas, et la dégradation croît avec la valeur du
  paramètre : ce n'est pas du bruit sans direction, c'est une pente. Une équipe
  n'a que quelques matchs de chaque côté, et le rapport domicile/extérieur qu'on
  en tire est trop bruité pour ce qu'il prétend corriger.
- **`lambda_shrink`** — rétrécir le nombre attendu lui-même vers la moyenne du
  championnat, en plus du rétrécissement déjà appliqué aux forces. **Le seul
  réglage de toute la campagne à dégrader de façon significative** (t > 2 dès la
  plus petite valeur, et l'effet croît). L'explication est simple après coup :
  la régularisation des forces fait déjà ce travail, et l'appliquer une seconde
  fois au produit efface précisément ce que le modèle a extrait.

Les deux mécanismes **restent en place, désactivés**, comme la pondération par
ancienneté : le code est écrit et testé, et `backtest.tune` pourra les reprendre
si un échantillon plus large devient accessible.

Le raisonnement derrière chacune tient. Une équipe qui vient d'enchaîner les déplacements est
bien sous-évaluée par une moyenne brute ; et diviser les buts d'une équipe par la défense d'un
adversaire qui inclut déjà ces mêmes buts ramène bien la correction vers zéro. Mais **la
mesure ne les soutient pas**, et à `k = 6` les deux ensemble dégradaient franchement
(+0,0111 ± 0,0054, t = +2,0 — le seul résultat de toute cette campagne à sortir du bruit, et
il est dans le mauvais sens).

Explication plausible pour le retrait du match courant : il suppose que le match figure bien
au bilan de l'adversaire, ce que la référence — bâtie sur les historiques d'une dizaine de
matchs du jour, dédupliqués — ne garantit pas. Et sur `n − 1` matchs avec `n` petit, il
amplifie le bruit là où la correction devrait être la plus prudente.

Conclusion générale de la campagne : **sur cet échantillon, presque rien ne se distingue du
hasard.** Le seul réglage qui ressorte est le rétrécissement, et c'est celui qui a été gardé.

### Les quatorze critères de décision

Le modèle de Maher ne connaît que des comptages passés. Tout ce qui les entoure —
qui arbitre, sous quelle pluie, après combien de jours de repos, avec quel enjeu —
se lit ailleurs. `ibet/prevision/context.py` rassemble ce contexte en **quatorze critères**,
relevés à l'émission et enregistrés avec la fiche.

```bash
python -m ibet --date 2026-09-13 --team Coventry --predict     # critères affichés
python -m ibet --date 2026-09-13 --team Coventry --predict --sans-contexte
python -m ibet emettre --majeures --max 8                          # fiches avec critères
curl http://localhost:8000/api/criteres                        # le catalogue
```

#### Afficher la composition probable

Trois façons, selon d'où on regarde :

```bash
# 1. En console — sous les critères, avec le reste de la prévision
python -m ibet --date 2026-09-13 --team "Manchester Utd" --predict

# 2. Par l'API, sur une fiche déjà émise
curl http://localhost:8000/api/predictions/0YOA44w3/composition

# 3. En Python, sans passer par une fiche
python -c "import api_client as a; m=[x for x in a.get_matches('2026-09-13') \
  if x['match_id']=='0YOA44w3'][0]; f=a.get_form(m, count=8); \
  print(a.recent_lineups(f['domicile']['matchs']))"
```

```
  Composition probable
  --------------------

  Manchester Utd  -  1-4-2-3-1  (deduite de 5 match(s))
    gardien
        1  Lammens S.               5 titularisation(s)
    defense
        2  Dalot D.                 3 titularisation(s)
        5  Maguire H.               5 titularisation(s)
        6  Martinez Li.             4 titularisation(s)
       23  Shaw L.                  5 titularisation(s)
    milieu
       18  Tielemans Y.             3 titularisation(s)
       37  Mainoo K.                4 titularisation(s)
       19  Mbeumo B.                5 titularisation(s)
    attaque
        8  Fernandes B.             5 titularisation(s)
        9  Rashford M.              2 titularisation(s)
       10  Cunha M.                 4 titularisation(s)
    ecartes : Diallo A. (blesse)
```

Le champ `annoncee` distingue les deux cas : `true` quand la source a publié la
vraie composition (une heure avant le coup d'envoi — elle fait foi), `false`
quand elle est déduite. Un joueur *incertain* reste dans le onze, signalé : il
joue une fois sur deux, et l'écarter serait aussi faux que l'ignorer.

| # | Critère | Source | Agit sur |
|---|---|---|---|
| 1 | **Style de jeu** — profil tactique (possession, bloc bas, contre-attaque) et ce que le *choc* des deux styles ajoute | possession, tirs, fautes, passes longues | corners, cartons, buts |
| 2 | **Forme récente et rang** — écart entre les 5 derniers matchs et l'ensemble | historique + classement | buts |
| 3 | **Système et effectif** — blessures et suspensions, pondérées par poste | sportsgambler + compositions Flashscore | buts |
| 4 | **Adversaires de style comparable** — chaque match d'historique pondéré par la ressemblance de l'adversaire | forces de la compétition | *la moyenne elle-même* |
| 5 | **Confrontations directes** — buts, corners et cartons des dernières rencontres | historique + fiches des rencontres | buts |
| 6 | **Enjeu de la compétition** — championnat ou match couperet, situation au classement | nom de la phase + classement | buts, cartons |
| 7 | **Domicile / extérieur** | historique + moyennes de la compétition | *déjà dans le modèle* |
| 8 | **Fatigue et calendrier** — repos, densité, prochaine échéance | dates des matchs + prochaine journée | buts |
| 9 | **Motivation** — tension mesurée : cartons des confrontations passées, écart de rang, revanche | confrontations + classement | cartons |
| 10 | **Météo** — pluie, vent, température à l'heure du coup d'envoi | Open-Meteo (gratuit, sans clé) | buts, corners, cartons |
| 11 | **Statistiques avancées (xG)** — deuxième estimation du nombre de buts | xG de chaque match | buts |
| 12 | **Discipline de l'arbitre** — cartons par match de l'arbitre désigné | désignation + fiches arbitrées | cartons |
| 13 | **Cotes du marché** — écart au consensus, **valeur espérée** aux meilleures cotes, mouvement de ligne | betexplorer (≈30 opérateurs) | *rien* |
| 14 | **Taille de l'échantillon** — score de confiance par grandeur | tout ce qui précède | *rien* |

#### Trois principes, tous conséquences de la même exigence

1. **Chaque critère est indépendant et faillible.** Une compétition sans
   classement, un arbitre pas encore désigné, une ville introuvable : chacun se
   déclare `disponible: false` sans empêcher les treize autres ni la prévision.
   Un critère absent vaut **neutre**, jamais zéro. L'affichage distingue trois
   états — `ok`, `~` (partiel), `--` (indisponible) —, parce que « pas encore
   publié » et « mesuré, sans effet » mènent au même multiplicateur et ne disent
   pas la même chose.
2. **Aucune correction n'est appliquée sans être affichée.** Chaque critère porte
   son `effet` : le multiplicateur exact, et de quel côté. La fiche conserve
   aussi `lambda_avant_contexte`. Une correction qu'on ne pourrait pas retrouver
   après coup serait indéfendable.
3. **Les corrections sont bornées.** Quatorze facteurs chacun à 3 % dans le même
   sens font 1,5, et aucun des quatorze ne le voulait. Le **produit** est donc
   plafonné par grandeur (`context.PLAFOND` : 1,12 sur les buts, 1,25 sur les
   cartons — plus large là où la moyenne est la plus basse).

#### Où chaque critère agit

Les trois emplacements ne sont pas interchangeables, et c'est ce qui impose à
`predict.build` de fonctionner en deux phases :

- **Avant la moyenne** — le critère 4 repondère chaque match d'historique selon
  la ressemblance entre l'adversaire de ce jour-là et celui d'aujourd'hui. C'est
  la même mécanique que la pondération par ancienneté de Dixon et Coles,
  appliquée à une autre dimension : là où elle demande « ce match est-il
  récent ? », celle-ci demande « ce match ressemble-t-il à celui qui vient ? ».
  Corriger après coup reviendrait à rattraper une moyenne au lieu de la calculer.
- **Sur le nombre attendu** — le critère 11 *mélange* deux estimations de la même
  quantité (`λ = (1 − p)·buts + p·xG`), les autres le *multiplient*.
- **Après** — les critères 13 et 14 ne déplacent rien : le premier compare aux
  cotes, le second qualifie ce qui est annoncé.

#### Ce qui n'a pas de source, et ce qui est dit à la place

- **Les cotes (critère 13)** ne sont pas dans le flux Flashscore : son point
  d'entrée `df_od_` répond vide, et sur les quatre éditions essayées (fr, com,
  co.uk, livescore.in). Elles viennent donc de **BetExplorer**, du même groupe,
  qui compare une trentaine d'opérateurs — et, ce qui rend la chose exploitable,
  **emploie les mêmes identifiants de match**. Une cote se rattache donc au match
  sans aucun rapprochement par nom d'équipe. Une requête par journée suffit pour
  tous les matchs du jour.
  Chaque relevé est **horodaté et jamais écrasé** : un mouvement de ligne ne se
  lit pas dans une cote, seulement dans deux cotes prises à deux moments. On peut
  aussi en pousser un à la main (`POST /api/predictions/{id}/cotes`), qui l'emporte.
  Le critère ne corrige rien, et c'est délibéré : mélanger les probabilités du
  modèle à celles du marché améliorerait mécaniquement toute mesure de
  calibration — le marché est bien calibré — mais reviendrait à **recopier le
  marché en croyant le prévoir**. Trois choses en sont tirées à la place :
  l'**écart** au consensus (cotes moyennes), la **valeur espérée** de chaque pari
  aux **meilleures** cotes offertes (`p × cote − 1`, la seule lecture qui dise si
  un pari rapporte), et le **mouvement de ligne**.
  Avec un garde-fou : au-delà de **25 % d'espérance**, la fiche signale que
  l'écart est *démesuré*. Vingt-cinq pour cent de rendement attendu sur l'issue
  d'un match de football n'existe pas — le marché intègre l'effectif, la nouvelle
  du matin et l'argent de gens qui perdent à leurs frais. Un tel écart mesure ce
  qui manque **au modèle**, typiquement un échantillon de trois journées. Sans
  cette réserve, un « +129 % d'espérance » affiché tel quel se lirait comme un
  conseil.
- **La composition probable (critère 3)** n'est publiée nulle part gratuitement.
  Elle est donc **déduite** de ce que les sources publient vraiment : la
  composition **réelle** des derniers matchs. Un joueur qui a commencé les cinq
  derniers commencera vraisemblablement le sixième. Le flux des compositions porte
  aussi la **place** de chaque joueur dans le dispositif (1 = gardien, puis la
  ligne défensive…), ce qui permet de reconstituer un onze **placé** et non une
  simple liste : pour chacune des onze places, le joueur qui l'a le plus souvent
  occupée et qui est disponible.
  La déduction n'est jamais présentée comme une annonce : chaque joueur porte son
  **nombre de titularisations** sur la période, les absents écartés sont listés à
  part, et un onze bâti sur cinq matchs concordants se distingue d'un onze bâti
  sur deux titularisations isolées. Dès que la vraie composition est publiée —
  une heure avant le coup d'envoi — c'est elle qui est rendue, marquée `annoncee`.
- **Les absences (critère 3)** ne sont dans Flashscore qu'avec la composition,
  environ une heure avant le coup d'envoi — trop tard pour une prévision émise la
  veille. Elles viennent donc de **sportsgambler**, qui les publie en continu pour
  29 compétitions, avec le motif (blessé, incertain, suspendu) et le poste.
  Elles sont **pondérées par poste** : une absence en attaque retire des buts à son
  équipe, une absence en défense en donne à l'adversaire, un milieu compte moitié
  pour chaque. Un joueur douteux compte pour moitié — le compter plein
  surestimerait l'affaiblissement, l'ignorer le sous-estimerait.
  Cette source-ci **ne partage pas les identifiants de match** : il faut rapprocher
  les équipes par leur nom, ce qui est exactement ce qui rend fragiles les
  assemblages de sources (« Manchester Utd » ici, « Manchester United » là). Le
  rapprochement est donc **strict et vérifié unique** — en cas de doute, on ne
  rapproche pas et le critère se déclare partiel. Un critère indisponible est sans
  conséquence ; un critère qui donnerait à Manchester City les blessés de
  Manchester United fausserait la prévision sans que rien ne le signale.
- **L'arbitre (critère 12)** n'est le plus souvent désigné que le jour du match.
  Son profil est reconstitué dans les matchs des deux équipes — un échantillon
  petit et **biaisé**, d'où le seuil de quatre rencontres avant tout effet.
- **Les derbys (critère 9)** ne sont *pas* une liste écrite à la main : elles
  vieillissent mal et n'existent que pour les championnats connus. La rivalité est
  mesurée là où elle laisse une trace — les cartons que ces deux équipes se sont
  déjà donnés, comparés à la moyenne de leur compétition.

#### Mesurer le contexte, sans se mentir

```bash
python -m ibet --date 2026-09-05 --league "Premier League" --backtest --contexte --quiet
```

Deux sources **ne peuvent pas** être ramenées à la date d'un match passé, et les
lire donnerait une mesure flatteuse et fausse — exactement ce que la coupure
temporelle du banc d'essai existe pour empêcher :

- **le classement**, qui est celui d'aujourd'hui et contient donc le résultat
  qu'on cherche à prévoir ;
- **l'arbitre**, publié après coup alors qu'il est presque toujours inconnu avant.

`context.collecter(retrospectif=True)` les coupe, et les critères qui en dépendent
se déclarent indisponibles — comme ils le feraient sur un match réel sans elles.
Reste une réserve à garder en tête : la **météo** d'un match passé est la météo
observée, alors qu'avant le match c'est une prévision. L'écart est petit à
quelques jours, mais il va dans le sens flatteur.

#### Ce que les quatorze critères ont donné

**80 matchs de huit grands championnats** — Premier League, LaLiga, Serie A,
Ligue 1, Bundesliga, Championship, Eredivisie, Liga Portugal —, historique coupé
au coup d'envoi de chaque match, classement et arbitre coupés. Chaque réglage voit
**exactement les mêmes rencontres**, et deux mesures sont rendues parce qu'elles
ne regardent pas la même chose :

- **l'issue** (log-loss 1X2, 80 observations) — elle ne voit que les buts ;
- **les propositions** (log-loss binaire, 12 898 observations) — elle voit les
  quatre grandeurs, donc les critères qui portent sur les corners et les cartons,
  invisibles à la première. C'est aussi celle qui a la puissance : ses barres
  d'erreur sont dix fois plus serrées.

Signe négatif = mieux. Le réglage de référence est l'absence de contexte.

| Réglage | Issue (t) | Propositions (t) | Retenu |
|---|---|---|---|
| **style = 1** | −0,0004 (−0,8) | **−0,00200 (−7,2)** | **oui** |
| **xG = 0,25** | −0,0009 (−0,2) | **−0,00064 (−4,0)** | **oui** |
| **fatigue = 1** | −0,0003 (−0,4) | **−0,00017 (−3,8)** | **oui** |
| xG = 0,50 | +0,0004 (+0,0) | −0,00094 (−3,0) | non |
| xG = 0,75 | +0,0041 (+0,3) | −0,00093 (−1,9) | non |
| enjeu = 1 | 0,0000 | 0,00000 | *non mesuré* |
| similarité = 0,5 | +0,0027 (+1,5) | −0,00007 (−0,5) | non |
| similarité = 1 | +0,0060 (+1,5) | +0,00012 (+0,4) | non |
| forme = 1 | +0,0005 (+0,4) | +0,00007 (+1,7) | non |
| météo = 1 | +0,0003 (+0,6) | **+0,00039 (+6,1)** | non |
| confrontations = 0,5 | −0,0026 (−1,0) | **+0,00121 (+4,1)** | non |
| confrontations = 1 | −0,0014 (−0,4) | **+0,00206 (+5,3)** | non |

**Un contrôle avant tout le reste** : « sans contexte » et « tous les critères à
zéro » donnent le **même log-loss au dix-millième** (1,0417 et 0,5659). La
garantie de neutralité tient au chiffre près — sans elle, aucune de ces
comparaisons ne voudrait rien dire, puisqu'on ne saurait pas si l'écart vient du
critère ou du fait d'avoir branché la machinerie.

Puis la combinaison des trois retenus, mesurée à son tour :

| Réglage | Issue | Propositions |
|---|---|---|
| **les trois retenus** | **1,0402** — −0,0015 (−0,3) | **0,5631** — **−0,00279 ± 0,00032 (t = −8,7)** |
| retenus sans xG | 1,0409 — −0,0007 (−0,9) | 0,5638 — −0,00216 (−7,7) |
| retenus sans fatigue | 1,0404 — −0,0013 (−0,2) | 0,5633 — −0,00263 (−8,3) |
| retenus sans style | 1,0405 — −0,0011 (−0,2) | 0,5651 — −0,00080 (−4,8) |
| sans contexte | 1,0417 | 0,5659 |

**Les trois ensemble font mieux que chacun d'eux** (−0,00279 contre −0,00200 pour
le meilleur seul), et chacun porte encore sa part une fois les deux autres
présents : retirer le style coûte 0,00199, retirer le xG 0,00063, retirer la
fatigue 0,00016 — exactement leurs contributions isolées. Les trois corrections
ne se recouvrent donc pas, ce qui n'allait pas de soi puisque toutes trois
touchent les mêmes grandeurs. À t = −8,7, c'est le seul résultat de tout le projet qui
sorte franchement du bruit — les campagnes précédentes sur les réglages du modèle
plafonnaient à |t| ≈ 2.

##### Ce que la mesure a écarté, et pourquoi c'est le plus instructif

Quatre critères parfaitement défendables sur le papier **dégradent la prévision** :

- **La météo** (t = +6,1). Le raisonnement tient — la pluie gêne le jeu court —
  mais les seuils retenus ne retrouvent pas cet effet dans les comptages.
- **Les confrontations directes** (t = +4,1, puis +5,3 à poids plein — une
  pente). Elles *améliorent* l'issue (t = −1,0), et c'est un piège classique :
  la mesure qui les flatte porte sur 80 observations, celle qui les condamne sur
  12 898. C'est la seconde qui tranche.
- **La similarité des adversaires** (+0,0027 puis +0,0060 sur l'issue — encore une
  pente). Même sort que `lambda_shrink` en son temps.
- **La forme récente** contre la forme d'ensemble, légèrement, sur les deux mesures.

Les quatre mécanismes **restent en place, à zéro** : le code est écrit et testé,
et une mesure sur un échantillon plus large pourra les reprendre. C'est la même
règle que pour `half_life`, `home_edge` et `lambda_shrink`.

##### Quatre critères qu'on ne peut pas mesurer ainsi

`effectif`, `arbitre`, `motivation` et `enjeu` restent à zéro **faute de mesure**,
pas faute de pertinence :

- **effectif, arbitre** — leurs sources ne publient que l'**état courant** (la
  liste des blessés d'aujourd'hui) ou publient **après coup** (l'arbitre). Les
  lire sur un match passé donnerait au modèle ce que personne n'avait avant le
  coup d'envoi : la mesure serait flatteuse et fausse.
- **enjeu** — l'échantillon ne contient **aucun match couperet**. Son écart
  mesuré est exactement nul : ce n'est pas un résultat, c'est une absence de test.
- **motivation** — sa composante « écart de rang » dépend du classement, coupé
  pour la même raison.

Vu que quatre critères plausibles viennent de dégrader la prévision, les activer
sans mesure serait parier. Ils le deviendront **prospectivement** : chaque fiche
émise enregistre désormais son contexte tel qu'il était **au moment de
l'émission** — donc la liste des absents et l'arbitre de ce jour-là. Quelques
dizaines de fiches vérifiées suffiront à les départager.

En attendant, `POIDS_CONTEXTE` dans le `.env` permet de les essayer sans toucher
au code :

```bash
POIDS_CONTEXTE=effectif=0.5,arbitre=1
```

Le réglage employé est écrit dans chaque fiche : une prévision produite avec des
poids modifiés reste identifiable comme telle.

### Exemple de sortie

`python -m ibet --league "Premier League" --round 4` :

```
  10 match(s) - English Premier League - journee 4 (2026-2027)
  ============================================================

  [ samedi 12/09/2026 ]

  English Premier League (England)
  --------------------------------
  16:00  Bournemouth                -       Brentford                   A venir
  16:00  Liverpool                  -       Fulham                      A venir
  16:00  Chelsea                    -       Hull City                   A venir
  18:30  Tottenham Hotspur          -       Everton                     A venir
  21:00  Sunderland                 -       Arsenal                     A venir

  [ dimanche 13/09/2026 ]

  English Premier League (England)
  --------------------------------
  17:30  Manchester United          -       Manchester City             A venir
```

Sur une journée jouée, les scores et statuts remontent :

```
  [ samedi 22/08/2026 ]

  Italian Serie A (Italy)
  -----------------------
  18:30  Udinese                    1 - 1   Como                        Termine
  18:30  Inter Milan                4 - 1   Monza                       Termine
  20:45  Genoa                      0 - 2   Napoli                      Termine
```

---

## Statistiques de match (`--stats`)

Pour les matchs **terminés** : tirs cadrés, tirs totaux, corners, fautes, cartons jaunes et
rouges, possession, et nom de l'arbitre.

```bash
python -m ibet --league "serie a" --round 1 --date 2026-08-22 --stats
python -m ibet --league "Ligue 1" --date 2026-09-12 --stats --max-stats 5 --export csv
```

```
  Udinese 1 - 1 Como
  ------------------
  Arbitre            Daniele Chiffi, Italy
  Tirs cadres             3   |   6
  Tirs (total)           16   |   20
  Corners                 5   |   8
  Fautes                 14   |   11
  Cartons jaunes          2   |   3
  Cartons rouges          -   |   1
  Possession            45%   |   55%
```

### Ce que le contexte va chercher en plus

Six sources, au-delà de celles que le modèle utilisait déjà. Toutes sont lues par
`api_client` et rendent `{}` plutôt que de lever : aucune n'est indispensable.
Les deux dernières ne sont pas Flashscore, et la même réserve s'applique à elles —
ce ne sont pas des API publiées, elles peuvent changer sans préavis, et l'usage
doit rester personnel.

| Donnée | Flux | Disponibilité |
|---|---|---|
| Stade, ville, capacité, **arbitre**, affluence | `df_sui_` | l'arbitre seulement le jour du match, parfois après |
| **Classement** : rang, points, derniers résultats, **prochain match** | `df_to_` | vide pour une coupe à élimination directe |
| **Compositions** : système, titulaires, absents | `df_li_` | environ une heure avant le coup d'envoi |
| **Météo** à l'heure du coup d'envoi | Open-Meteo | 14 jours au maximum, ville du stade requise |
| **Cotes 1X2** (moyennes et meilleures) | betexplorer.com | une requête par journée, mêmes identifiants de match |
| **Blessures et suspensions** (motif, poste) | sportsgambler.com | 29 compétitions, rapprochement par nom d'équipe |

Les statistiques de match rapportent aussi, depuis la même requête et donc sans
coût supplémentaire, treize grandeurs de plus (`api_client.STAT_EXTRA`) : **xG**,
xGOT, grosses occasions, tirs dans la surface, touches dans la surface adverse,
passes réussies, passes dans le dernier tiers, passes longues, centres, duels,
tacles, hors-jeu, arrêts. Elles n'apparaissent pas dans la fiche d'un match —
treize lignes de plus que personne ne lit une par une — mais elles sont ce qui
permet d'établir un profil de jeu et de faire passer les **buts attendus par le
même modèle que les buts**.

### Ce que chaque source peut fournir

| Donnée | TheSportsDB (sans clé) | API-Football (clé gratuite) |
|---|:--:|:--:|
| Tirs cadrés / tirs totaux | ✅ | ✅ |
| Corners | ❌ | ✅ |
| Fautes | ❌ | ✅ |
| Cartons jaunes / rouges | ❌ | ✅ |
| Possession | ❌ | ✅ |
| Arbitre | ❌ | ✅ |

La clé publique TheSportsDB plafonne `lookupeventstats.php` à 5 lignes, toutes liées aux tirs,
et son endpoint de détail ne comporte aucun champ arbitre. **Les corners, fautes, cartons et
l'arbitre exigent donc une clé API-Football** (gratuite, 100 requêtes/jour).

Bonne nouvelle : chaque match TheSportsDB porte l'identifiant API-Football du même match
(`idAPIfootball`). Le programme s'en sert comme pont — vous continuez à chercher les matchs
sans clé, et les statistiques complètes arrivent dès que `API_FOOTBALL_KEY` est renseignée.
Sans clé, il affiche ce qu'il a et indique explicitement ce qui manque.

**Quota** : une requête par match. `--max-stats N` plafonne l'enrichissement (défaut : 20).
Les statistiques d'un match terminé ne changeant plus, elles sont mises en cache 30 jours.

---

## Format des données

Chaque match est normalisé de la même façon quel que soit le provider :

| Champ | Description |
|---|---|
| `date` / `heure` | coup d'envoi **dans le fuseau d'affichage** |
| `kickoff_utc` | horodatage brut en UTC |
| `championnat` / `pays` | compétition |
| `journee` | numéro de journée (mode journée uniquement) |
| `domicile` / `exterieur` | équipes |
| `statut` | `A venir` \| `En cours` \| `Termine` \| `Reporte` \| `Inconnu` |
| `score_domicile` / `score_exterieur` | `null` si non disponible |
| `provider` / `match_id` | traçabilité de la source |

> Le champ `date` peut différer du `--date` demandé : les API indexent les matchs par date
> **UTC**, alors que l'affichage est converti dans votre fuseau. Un match à 23h00 UTC le 5
> apparaît donc à 01h00 le 6 en heure de Paris.

---

## Cache

Chaque réponse est mise en cache dans `donnees/cache/` sous une clé `provider|date|fuseau`, avec une
durée de vie de `CACHE_TTL` secondes (1 h par défaut). Utile pour ne pas brûler les
100 requêtes/jour d'API-Football pendant le développement.

- `--no-cache` force un appel réseau
- `--clear-cache` vide le dossier
- `CACHE_TTL=0` dans `.env` désactive complètement le cache

---

## Structure

Le code est dans le paquet `ibet/`, les données dans `donnees/`, la documentation
dans `docs/`. À la racine, il ne reste que la configuration.

```
iBET/
├── ibet/                       # le code, un dossier par étape
│   ├── __main__.py             #   point d'entrée : python -m ibet <commande>
│   ├── chemins.py              #   seul module qui sait où sont les données
│   ├── sources/                # d'où viennent les données
│   │   ├── api_client.py       #   flux des matchs, stats, forme, feuilles de match
│   │   ├── cache.py            #   cache disque des réponses (donnees/cache/)
│   │   └── certificats.py      #   bundle de certificats (antivirus / proxy HTTPS)
│   ├── stockage/               # où elles sont gardées
│   │   ├── store.py            #   base SQLite des prévisions (donnees/ibet.db)
│   │   └── arbitres.py         #   base des arbitres (donnees/arbitres.db)
│   ├── collecte/               # ce qui remplit le stockage après coup
│   │   ├── resultats.py        #   résultats sortis de la fenêtre de la source
│   │   ├── feuilles.py         #   feuilles de match (arbitres, cartons, minutes)
│   │   └── joueurs.py          #   statistiques par joueur
│   ├── modeles/                # un modèle dédié par événement
│   │   ├── buts.py, issue.py, corners.py, tirs_cadres.py, cartons.py, xg.py
│   │   ├── discipline.py       #   arbitres, joueurs, entraîneurs (cartons)
│   │   ├── styles.py           #   styles de jeu des joueurs (corners, tirs)
│   │   ├── base.py             #   contrat commun d'un modèle d'événement
│   │   ├── estimation.py, lois.py, offres.py, reglages.py   # moteur commun
│   │   └── journal/            #   une fiche par modèle : versions, forces, faiblesses
│   ├── prevision/              # ce qui fait travailler les modèles
│   │   ├── predict.py          #   chef d'orchestre de la prévision
│   │   ├── context.py          #   les 14 critères de décision
│   │   ├── forces.py           #   notes attaque / défense (donnees/forces.json)
│   │   ├── forecast.py         #   émission et enregistrement des fiches
│   │   └── marche.py           #   valeur face au marché, sélection, rendement
│   ├── evaluation/             # ce qui juge les prévisions
│   │   ├── verify.py           #   confrontation aux résultats réels
│   │   ├── backtest.py         #   banc d'essai sur des matchs déjà joués
│   │   ├── etude.py            #   comparaison des versions de chaque modèle
│   │   ├── criteres.py         #   mesure des critères laissés à poids zéro
│   │   └── mesure_cartons.py, mesure_styles.py
│   └── interfaces/             # ce que l'on appelle
│       ├── cli.py              #   ligne de commande principale
│       ├── serveur.py          #   API HTTP pour le front (FastAPI)
│       ├── exporter.py         #   export CSV / JSON, rendu console
│       └── catalogue.py        #   styles de jeu par équipe
├── tests/test_stats.py         # tests (sans réseau) : python -m ibet tests
├── donnees/                    # données locales, hors git (voir ibet/chemins.py)
│   ├── ibet.db, arbitres.db, forces.json, predictions_ouvertes.json
│   └── cache/, exports/, sauvegardes/, certificats/
├── docs/
│   ├── donnees.md              # système d'information : stockage, risques, migrations
│   └── progression.md          # suivi de progression
├── .env / .env.example         # clés et réglages (.env jamais commité)
├── pyproject.toml              # description du projet, réglage d'isort
├── requirements.txt
└── README.md
```

Les dépendances vont de haut en bas de `ibet/` : `sources` ne connaît rien
d'autre, `modeles` ne lit ni le réseau ni la base, `interfaces` peut tout
appeler. Un nouveau fichier se range selon cette question : *à quelle étape
sert-il ?*

### Commandes

Toutes passent par `python -m ibet`, lancé depuis la racine du projet
(`python -m ibet aide` pour la liste) :

| Commande | Rôle |
|---|---|
| `python -m ibet [options]` | matchs, stats, forme, prévision, backtest, valeur (ex-`main.py`) |
| `python -m ibet serveur` | API pour le front, port 8000, rechargement auto |
| `python -m ibet emettre` | émet et enregistre les prévisions des matchs à venir |
| `python -m ibet verifier` | tranche les prévisions dont le match est fini |
| `python -m ibet etude` | compare les versions des modèles |
| `python -m ibet forces` | reconstruit les notes attaque / défense |
| `python -m ibet rattraper` / `rattraper-feuilles` / `rattraper-joueurs` | collecte après coup |
| `python -m ibet catalogue` | styles de jeu des joueurs |
| `python -m ibet mesurer-cartons` / `mesurer-styles` | mesures des apports |
| `python -m ibet base` | crée les tables, importe l'historique |
| `python -m ibet certificats` | bundle de certificats |
| `python -m ibet tests` | tests |

Ce que chaque chose **stocke**, ce qui est reconstituable et ce qui ne l'est pas,
et les tables qu'il reste à poser pour que le modèle sache prévoir sans réseau :
voir [docs/donnees.md](docs/donnees.md). Deux points y sont signalés comme urgents — le
dossier n'est pas versionné et n'a aucune sauvegarde, alors que `ibet.db` porte
20 663 résultats que la source ne republie plus ; et `CREATE TABLE IF NOT EXISTS`
ne migre rien, donc toute évolution du schéma est aujourd'hui silencieusement
sans effet sur la base existante.

L'interface web est un projet **à part**, dans un dossier voisin :

```
Projets/
├── iBET/          # ce projet : récupération, modèle, CLI, API
└── ibet-web/      # interface web (Vue 3 + Vite)
```

## Interface web

`../ibet-web/` est un projet Vue 3 indépendant, avec son propre dépôt de
dépendances et son propre build : il ne récupère rien lui-même, il lit l'API
HTTP locale servie par `ibet/interfaces/serveur.py`, qui expose les mêmes matchs normalisés que
le CLI, depuis le même cache.

```bash
# depuis ce projet
python -m ibet serveur        # 1. l'API

# depuis le dossier voisin
cd ../ibet-web && npm install && npm run dev   # 2. le front (http://localhost:5173)
```

| Route | Renvoie |
|---|---|
| `GET /api/matchs?date=&league=&team=&provider=&tz=&refresh=` | `{ date, provider, fuseau, total, matchs[] }` |
| `GET /api/predictions` | `{ total, bilan, a_verifier, predictions[] }` — depuis la base, sans toucher à la source |
| `GET /api/predictions/{match_id}` | la prévision complète, telle qu'elle a été émise |
| `GET /api/predictions/{match_id}/options` | **les options de paris**, regroupées par marché : issue, puis chaque grandeur déclinée par équipe et au total, avec le côté mis en avant |
| `GET /api/predictions/{match_id}/contexte` | **les 14 critères** tels qu'ils ont été relevés à l'émission |
| `GET /api/predictions/{match_id}/composition` | **la composition probable** des deux équipes, placée par ligne, avec le nombre de titularisations de chacun et les absents écartés |
| `GET /api/criteres` | le **catalogue** des 14 critères : libellé, source, grandeurs touchées, poids en vigueur. Statique, aucun appel à la source |
| `GET /api/predictions/{match_id}/cotes` | relevés de cotes, mouvement de ligne et écart au modèle |
| `POST /api/predictions/{match_id}/cotes` | enregistre un relevé à la main (`{"domicile": 2.10, "nul": 3.40, "exterieur": 3.20}`). N'écrase jamais le précédent ; l'émission en enregistre déjà un automatiquement |
| `POST /api/predictions/verifier` | tranche les prévisions dont le match est fini. **Seul appel qui interroge la source** : un résultat réel n'est dans aucune base tant qu'il n'a pas été relevé |

Les paramètres sont ceux du CLI et passent par le même code. `refresh=1` ignore
le cache disque et redemande la source ; sans lui, la réponse peut avoir jusqu'à
`CACHE_TTL` secondes (1 h par défaut), ce qui se voit sur un match en cours.

L'API traduit ses pannes en statuts explicites — `429` quota épuisé, `503` clé
absente, `502` source injoignable — avec le message lisible dans `detail`.

Voir `../ibet-web/README.md` pour l'architecture du front et son design system.

## Émettre des prévisions

`ibet/prevision/forecast.py` prend les matchs **à venir**, applique le modèle et écrit une
fiche en base — datée, avec le réglage employé et les propositions engagées.

```bash
python -m ibet emettre --majeures --max 8                    # les matchs du jour qui comptent
python -m ibet emettre --date 2026-09-08 --league "Ligue des Champions - Phase" --max 6
python -m ibet emettre --jours 3 --league Championship --pays Angleterre
```

| Option | Effet |
|---|---|
| `--date` / `--jours N` | jour de départ et nombre de journées couvertes |
| `--majeures` | ne retient que les grandes compétitions européennes (12 couvertes, liste dans `ibet/prevision/forecast.py`) |
| `--league` / `--pays` | filtres par sous-chaîne. **Le pays est souvent indispensable** : « Championship » ramène aussi la Motsepe Championship sud-africaine, « Ligue 1 » la tunisienne et l'algérienne |
| `--max N` | plafond de prévisions émises (défaut : 10) |
| `--refaire` | réémet un match déjà en base (remplace sa fiche) |
| `--variantes` | conserve les équipes féminines, de jeunes et réserves, écartées par défaut |

Sans sélection, le plafond serait consommé par les premiers matchs de l'horaire
— une journée compte plus de quatre cents matchs à venir sur deux cents
compétitions, dont beaucoup de championnats régionaux. `--majeures` règle le cas
courant ; `--league` / `--pays` le reste.

Une prévision coûte une vingtaine de requêtes : historique détaillé des deux
équipes, plus la référence de leur championnat. D'où le plafond — la source
n'est pas une API publiée et ses CGU demandent un volume raisonnable. Le cache
absorbe les répétitions : deux matchs d'une même compétition partagent toute la
référence.

`ibet/prevision/forecast.py` met en **fiche** ce que `ibet/prevision/predict.py` calcule. La distinction n'est
pas cosmétique : le résultat du modèle change d'un jour à l'autre (l'historique
s'allonge, la référence bouge), alors que la fiche est datée et figée. C'est ce
qui permet de la confronter au résultat réel sans qu'elle ait pu être réécrite
entre-temps.

## Vérifier les résultats

Une prévision attend la fin de son match. Tant qu'il n'est pas joué il n'y a rien
à trancher ; une fois joué, la laisser en attente reviendrait à s'épargner le
verdict. `ibet/evaluation/verify.py` va chercher le score et les statistiques réelles, puis
tranche chaque proposition — **sans jamais retoucher les probabilités
annoncées** : seul le champ `verifie` est renseigné.

```bash
python -m ibet verifier            # les fiches de la base dont le match est fini
python -m ibet verifier --toutes   # y compris celles dont le match n'a pas commencé
python -m ibet verifier --json     # le fichier historique predictions_ouvertes.json
```

Seules les fiches **mûres** sont examinées : coup d'envoi + 135 minutes (90 de
jeu, la mi-temps et les arrêts). Interroger la source pour un match de
après-demain ne peut rien apprendre. Les matchs d'une même journée sont
récupérés en une requête, pas une par fiche.

L'écran des prévisions le fait tout seul : au chargement, s'il reste des fiches
mûres, la vérification part en arrière-plan et la liste se met à jour quand les
résultats arrivent. Le bouton **Vérifier les résultats** la relance à la demande,
et le compteur à côté indique combien de fiches attendent leur verdict.

## Ce que valent les propositions

Le banc d'essai (`--backtest`) juge trois choses, pas une : l'**issue** (Brier,
log-loss, face à deux références), les **nombres attendus** de chaque grandeur
(biais, erreur absolue), et depuis peu les **propositions elles-mêmes** — la
seule ligne qu'un lecteur voit et sur laquelle il décide.

Chaque proposition est tranchée par le même code que la vérification après coup
(`verify.check_offer`) : ce qui est mesuré est exactement ce qui sera compté sur
les fiches émises. Les propositions sont ventilées par famille (total, équipe,
issue, double chance, les deux marquent) parce qu'elles ne passent pas par le
même calcul : un biais sur les totaux n'implique rien sur les issues.

**Mesure sur 109 matchs de douze championnats** (5 journées, historique coupé au
coup d'envoi de chaque match) :

| Propositions | Nombre | Annoncé | Observé | Écart |
|---|---|---|---|---|
| retenues (celles des fiches) | 327 | 91,5 % | 90,8 % | +0,7 pt |
| toutes | 1 962 | 75,7 % | 75,4 % | +0,3 pt |
| total | 654 | 79,0 % | 78,4 % | +0,6 pt |
| équipe | 872 | 78,3 % | 78,4 % | −0,2 pt |
| double chance | 292 | 69,4 % | 68,8 % | +0,5 pt |
| les deux marquent | 109 | 58,2 % | 57,8 % | +0,4 pt |

Aucun écart ne dépasse deux erreurs types : **les pourcentages affichés tiennent
leurs promesses**, et il n'y a rien à recalibrer. Sur l'issue, le modèle bat ses
références (Brier 0,614 contre 0,658 pour les fréquences du championnat, 53,2 %
de réussite contre 40,4 %).

Deux précautions rendent ce tableau lisible :

- Chaque proposition est énumérée **avec son complémentaire** (« plus de 2.5 » et
  « moins de 2.5 »). Sur l'ensemble, annoncé et observé valent donc 50 % par
  construction, quelle que soit la qualité du modèle. Le résumé ne porte que sur
  la face où le modèle penche (probabilité ≥ 50 %).
- Une mesure sur une seule journée avait montré un écart de 8 points autour de
  75 %. Sur 109 matchs il retombe à 2,9 points, soit 1,4 erreur type : c'était du
  bruit. C'est la raison d'être de l'erreur type affichée partout ici.

### Ce que les fiches réellement émises ont donné

Le banc d'essai rejoue des matchs ; les fiches émises, elles, ont été écrites
**avant** le coup d'envoi et vérifiées après. C'est l'épreuve la plus honnête,
et elle est indépendante de tout ce qui précède.

Au 7 septembre 2026 — 12 fiches vérifiées, 144 propositions tranchées :

| Grandeur | Propositions | Annoncé | Réalisé | Écart |
|---|---|---|---|---|
| Buts | 36 | 87,6 % | 80,6 % | +7,0 pt |
| Corners | 36 | 81,0 % | 66,7 % | **+14,3 pt** |
| Tirs cadrés | 36 | 84,8 % | 91,7 % | −6,8 pt |
| Cartons jaunes | 36 | 84,4 % | 77,8 % | +6,7 pt |
| **ensemble** | **144** | **84,5 %** | **79,2 %** | **+5,3 pt** (erreur type 3,4) |

Pris seul, cet écart vaut 1,6 erreur type : il ne prouve rien. Mais il va dans
le même sens que les deux mesures de banc d'essai (+3,1 pt sur 53 matchs, +2,1
sur 29), et **trois échantillons indépendants qui penchent du même côté pèsent
plus que chacun d'eux**. Les corners, déjà les plus corrigés du modèle
(dispersion 1,37, recalage ×1,07), restent de loin les pires : la correction en
place ne suffit pas.

C'est ce constat, et non une intuition, qui a motivé le paramètre
`estimation_dispersion`.

## Base des prévisions

Une prévision coûte cher à produire : une requête d'historique par équipe, une
dizaine d'autres pour reconstituer la référence du championnat, et le calcul
par-dessus. La relire ne doit rien coûter — et surtout pas retourner interroger
la source. Une fois émise, elle est donc écrite dans `ibet.db` (SQLite, un
fichier, aucune installation) et n'est plus jamais recalculée.

```bash
python -m ibet base     # crée les tables et importe predictions_ouvertes.json
```

La commande est idempotente : la relancer après un passage de `ibet/evaluation/verify.py`
resynchronise les résultats réels et le sort de chaque proposition.

| Table | Contenu |
|---|---|
| `predictions` | la prévision complète telle qu'émise (`payload`), plus les colonnes qui servent à lister sans ouvrir le JSON |
| `offres` | une ligne par proposition, pour que le bilan soit une requête et non un parcours de fichiers |
| `cotes` | un relevé par appel, **horodaté et jamais écrasé**. C'est ce qui distingue le critère 13 des treize autres : un mouvement de ligne n'existe que dans la différence entre deux relevés |
| `resultats` | le score de chaque match terminé vu par le programme, qu'il porte une fiche ou non (voir ci-dessous) |

Le `payload` est conservé **tel quel**, sans réécriture : une prévision est un
engagement pris à une date, avec un réglage donné. La relire modifiée lui ôterait
toute valeur — c'est le principe qui fait déjà que `ibet/evaluation/verify.py` tranche sans
retoucher les probabilités annoncées.

Le taux de réussite n'est calculé que sur les propositions **tranchées** :
rapporter les réussites au total ferait passer une prévision en attente pour un
échec.

### Archive des résultats

La source ne publie une journée que **sept jours** autour d'aujourd'hui. Passé
ce délai, une fiche non vérifiée ne se tranche plus : elle ne compte ni en
réussite ni en échec, et disparaît de toute mesure. Le projet a perdu **onze
fiches — 264 propositions** de cette façon, faute d'avoir lancé `ibet/evaluation/verify.py`
pendant deux semaines.

La parade n'est pas de vérifier plus souvent — ça reste une discipline, et une
discipline finit toujours par céder. C'est de **ne plus dépendre du moment où
on le fait** : dès qu'un match terminé passe sous les yeux du programme, son
score entre dans la table `resultats`. `ibet/evaluation/verify.py` lit ensuite l'archive, et
n'interroge la source que si l'archive ne sait pas.

Les points de capture sont ceux qui récupéraient déjà une journée, donc la
parade ne coûte **aucune requête supplémentaire** :

| Appelant | Quand |
|---|---|
| `ibet/interfaces/cli.py` | à chaque journée affichée en ligne de commande |
| `ibet/interfaces/serveur.py` | à chaque journée servie au front — le point le plus fréquent |
| `ibet/prevision/forecast.py` | à l'émission des prévisions |
| `ibet/evaluation/verify.py` | à la vérification, plus les fiches statistiques au passage |

L'archive est volontairement **indépendante des fiches** : on garde le résultat
même d'un match sur lequel aucune prévision n'a été émise. Ça ne coûte rien, et
une prévision peut être émise plus tard sur un match déjà joué — le backtest en
vit.

Un garde-fou compte plus que le reste : **un match en cours n'est jamais
archivé**. Il porte un score, et ce score n'est pas un résultat. Trancher une
fiche sur un 0-0 de la vingtième minute serait une erreur silencieuse, pire que
le trou qu'on cherche à combler.

```bash
python -m ibet base     # crée les tables, importe l'historique, reprend le cache
```

La reprise verse dans l'archive tout ce que le cache des requêtes contient
déjà : **19 000 matchs terminés**, dont 4 500 avec leurs statistiques, sans une
seule requête réseau. L'historique d'équipe mis en cache remonte des mois, bien
au-delà de la fenêtre de sept jours.

### Rattraper ce qui est déjà sorti de la fenêtre

L'archive empêche la perte de se reproduire, mais elle ne peut rien pour ce qui
n'a jamais été téléchargé. `ibet/collecte/resultats.py` est la voie de retour, et elle tient
à une particularité du flux : **l'historique d'une équipe remonte des mois**.
Un match du 13 septembre n'est plus dans la journée du 13 septembre, mais il
reste dans la forme récente de ses deux équipes.

Une subtilité rend la chose moins directe : le flux **exclut de la forme le
match qu'on consulte**. Demander l'historique du match perdu ne le rend donc
pas — il faut passer par un match *voisin* de l'une de ses équipes, que ce même
historique vient justement de nommer. D'où les deux passes.

```bash
python -m ibet rattraper              # toutes les fiches en attente sans résultat
python -m ibet rattraper --limite 5   # s'arrêter après cinq fiches
```

Sur les 18 fiches que la source ne rendait plus, le rattrapage a retrouvé les
18, en **28 appels** — deux par fiche, une seconde d'écart. L'échantillon
tranché est passé de 26 fiches (528 propositions) à **44 fiches (762
propositions)**.

## Le plafond, et ce qu'il implique

Une mesure préalable cadre tout ce qui suit. Prenons un **oracle parfait** : il
connaît le vrai λ de chaque match, exactement. Données infinies, modèle parfait,
aucune erreur d'estimation. Voici ce qu'il obtient sur une ligne d'équilibre :

| Marché | Taux de réussite plafond | Brier plancher |
|---|---|---|
| corners, +9.5 total | 61,2 % | 0,231 |
| buts, +2.5 total | 60,5 % | 0,233 |
| cartons, +3.5 total | 64,0 % | 0,221 |

61 %, et c'est un maximum absolu : aucune quantité de données, aucun modèle ne
passe au-dessus. Le reste est le match lui-même.

Le budget d'erreur sur le total de corners le dit autrement :

```
ecart-type du resultat reel                     : 3.61 corners   <- irreductible
erreur d'estimation, echantillon actuel (n = 4) : 1.80
erreur d'estimation avec trois fois plus (n=12) : 1.04

erreur totale n = 4  : 4.03
erreur totale n = 12 : 3.75     soit -7 %
```

Le hasard du match domine l'erreur d'estimation d'un facteur deux. **Tripler les
données ne fait gagner que 7 %**, et l'écart au parfait n'est que de 10 %. Ce
n'est pas là qu'un facteur d'échelle est disponible.

Trois conséquences, qui expliquent les outils décrits plus bas :

- **Mieux estimer λ** est presque épuisé (−10 % au mieux).
- **Mieux calibrer** l'est déjà : 75,7 % observé pour 75,4 % annoncé, aucun écart
  au-delà de deux erreurs types.
- **Battre le marché** est le seul axe que rien ne borne — parce qu'il ne mesure
  pas la précision mais la *valeur*. Un modèle calibré qui joue tous les matchs
  perd la marge de l'opérateur à chaque coup ; le même, joué seulement là où il
  diverge du marché *et* a raison, est une autre affaire.

D'où le principe de tout ce qui suit : **on n'ajoute pas de précision, on ajoute
de quoi décider**. Chaque mécanisme ci-dessous est neutre par défaut et ne bouge
qu'après mesure.

## Régler un paramètre (`--regler`)

`Params` porte les réglages du modèle. Jusqu'ici aucun n'était atteignable depuis
la ligne de commande : `run_backtest` appelait `predict.build()` sans transmettre
`params`, et `backtest.tune` ne servait qu'à comparer avec et sans contexte.

```bash
python -m ibet --date 2026-09-06 --pays Angleterre --stats \
               --backtest --contexte --regler xg_echantillon=0,3,6,12
```

Les valeurs sont comparées **sur exactement les mêmes rencontres**, avec le même
historique et la même coupure — sans quoi on comparerait des moyennes calculées
sur des matchs différents. La première sert de référence à l'écart apparié : y
mettre celle en vigueur. Les clés sont les champs de `Params` (`rho`,
`half_life`, `shrinkage`, `home_edge`, `lambda_shrink`, `estimation_dispersion`,
`xg_echantillon`).

Le classement est rendu tel quel, avec l'erreur type de chaque score. **Au-delà
de |t| = 2 seulement**, l'écart cesse de s'expliquer par le hasard de
l'échantillon : en deçà, le tableau désigne un premier sans justifier aucun
changement.

### Deux pièges de sélection, et comment les éviter

Ils faussent une mesure sans jamais se signaler, et le second a effectivement
produit un banc d'essai vide de sens avant d'être trouvé.

**`--league` est une sous-chaîne sur une couverture mondiale.** `"Premier
League"` ramène l'Ukraine, le Rwanda, la Russie, Malte, le Lesotho, le
Kazakhstan, Hong Kong, le Ghana et le Canada autant que l'Angleterre. D'où
`--pays`, la même option que `ibet/prevision/forecast.py` :

```bash
python -m ibet --date 2026-09-06 --league "Premier League" --pays Angleterre
```

`--round`, qui résout le nom canonique vers l'*English Premier League*, ne peut
pas servir ici : il exige le provider `thesportsdb`, alors que la prévision exige
`flashscore`, seul à publier l'historique des équipes.

**`--max-stats` coupe l'alphabet, pas un échantillon.** Le flux est trié par
pays. Sur une journée mondiale de 1 334 matchs, `--max-stats 40` s'arrête à
l'Allemagne — et les compétitions africaines et amateurs qui trient en premier ne
publient aucune statistique. Un banc d'essai lancé sans filtre mesure donc ce qui
trie en premier en croyant mesurer les grands championnats. L'avertissement dit
désormais **où** la coupe s'arrête, mais la parade est de choisir la sélection au
lieu de la subir.

## Le mélange xG selon l'échantillon (`xg_echantillon`)

Le critère 11 mélange deux estimations de la même quantité avec un poids **fixe**
(0,25) : `lambda = 0.75 × buts + 0.25 × xG`. Or les deux ne se dégradent pas au
même rythme.

Les buts sont un comptage très faible — environ 1,35 par équipe et par match.
Leur moyenne sur *n* matchs a donc une variance d'ordre 1,35 / *n*. Les buts
attendus mesurent la même production offensive avec une variance par match
nettement moindre, mais ils ne sont pas la même grandeur : la finition existe, et
un xG ne la voit pas. Le xG porte un **biais constant** là où les buts portent
une **variance en 1/n**. Le poids optimal équilibre les deux, et il **dépend
de n**. Un poids fixe est la moyenne de deux régimes qui n'ont rien à voir.

```
melange(n) = poids + (1 - poids) × XG_ECHANTILLON / (n + XG_ECHANTILLON)
```

À `XG_ECHANTILLON = 0` — le défaut — la fonction rend le poids du critère et le
modèle est **exactement** celui d'avant, ce que les tests vérifient pour toute
combinaison de (poids, n).

Ce réglage répond à un constat déjà au dossier : à 0,50 et 0,75, le mélange fixe
*améliore* les propositions mais *dégrade* l'issue. Les deux mesures ne portent
pas sur les mêmes échantillons — les propositions sont dominées par les grandeurs
estimées sur peu de matchs, l'issue vient des buts qui en ont davantage. Un poids
qui suit l'échantillon peut donc, en principe, gagner sur les deux. **C'est une
hypothèse**, et le défaut reste zéro tant que `--regler` ne l'a pas confirmée.

## Calibration selon le déséquilibre de l'affiche

Le tableau des propositions juge la calibration **en moyenne**. Une moyenne juste
peut recouvrir deux erreurs égales et opposées.

Les quatre grandeurs passent par la même structure — attaque × défense, chaque
force agissant séparément — établie pour les buts seulement. La raison de s'en
méfier ailleurs est la même dans les trois cas : la production ne dépend pas que
des deux équipes, elle dépend aussi de l'**état du match**. Cet état n'est pas
observable au coup d'envoi, mais il n'est pas imprévisible : il découle du
déséquilibre attendu, que le modèle calcule déjà.

L'intérêt de ventiler les quatre ensemble est qu'elles ne prédisent **pas la même
chose** :

| Grandeur | Signature attendue |
|---|---|
| **Buts** | rien nulle part — c'est le **témoin**. Si cette ligne s'allume, ce n'est pas un effet de jeu qui a été trouvé, c'est la mesure qui est fausse |
| **Corners** | l'équipe menée pousse, celle qui mène gère : le total est à peu près conservé, l'effet se voit sur le **partage** |
| **Tirs cadrés** | même mécanisme, vraisemblablement plus faible — un tir part de partout, un corner suppose le jeu installé |
| **Cartons jaunes** | signature **inverse** : un match serré se hache, un match plié s'apaise. C'est le **Total** qui bouge, pas le partage |

C'est leur concordance qui vaut preuve, pas un écart isolé.

```
  Grandeur        Ecart lam  Matchs      Favori    Outsider       Total   Asymetrie
  Buts            0.11-0.20     133     +2.9 pt     -1.9 pt     -2.4 pt     +4.8 pt
  Corners         0.11-0.20     133   +9.4 pt *   -5.6 pt *     -0.6 pt  +15.0 pt *
  Cartons jaunes  0.21-0.30     134     -0.1 pt     +1.8 pt   +9.6 pt *     -2.0 pt
```

La grandeur mesurée pour le partage est `asymetrie = ecart(favori) −
ecart(outsider)` : nulle si la structure multiplicative suffit, croissante avec
le déséquilibre s'il lui manque un terme d'interaction. Les bornes des tranches
sont tirées des données par terciles — le déséquilibre attendu des corners est
bien plus resserré que celui des buts, et figer des seuils produirait des
tranches vides.

Les erreurs types sont **groupées par match**, et non par proposition. Le détail
n'est pas cosmétique : il a produit un faux positif avant d'être vu. Un même
match fournit une dizaine de propositions — « plus de 0.5 », « plus de 1.5 »,
« plus de 2.5 »… — toutes tirées du même λ et du même résultat, qui se réalisent
ou échouent ensemble. L'erreur type d'une proportion de Bernoulli suppose des
tirages indépendants ; appliquée au nombre de propositions, elle annonçait une
précision que l'échantillon n'avait pas, et dix-sept matchs faisaient ressortir
le témoin — les buts — à 18 points d'écart avec une étoile. La variance est donc
estimée **entre matchs** (estimateur groupé, Liang et Zeger 1986), et la colonne
`Matchs` du tableau dit ce que la mesure vaut. Le seuil d'affichage compte lui
aussi des matchs.

Le détecteur est vérifié dans les deux sens par les tests : il retrouve un effet
planté et distingue les deux signatures ; sur des cellules sans effet, son taux
de faux positifs reste voisin des 5 % attendus et son asymétrie moyenne est
nulle. **C'est un test, pas une correction** : il ne change aucune prévision, il
dit s'il y a quelque chose à corriger, et sur quelle grandeur.

Une réserve propre aux cartons : leur premier responsable n'est ni l'une ni
l'autre équipe, c'est l'arbitre. Un effet trouvé là peut n'être qu'un effet
d'arbitre mal réparti entre les tranches, et devra être confirmé à arbitre
comparable.

## La grille des scores

Le banc d'essai jugeait l'issue (Brier, log-loss) et l'erreur sur le total de
buts. La **grille des scores** — la ligne la plus lue d'une fiche, et la plus
facile à juger de mémoire — n'était évaluée nulle part. « Le modèle est mauvais
sur les scores » n'était donc ni vérifiable ni réfutable.

```
  Grille des scores
  Score exact en tete        5.9% des matchs (annonce 11.4%)
  Reel parmi les trois      35.3%
  Log-loss du score reel    2.978 +/- 0.196
  Face aux moyennes de la competition : +0.062 +/- 0.084 (t = +0.7)
```

Trois lectures, et une seule juge vraiment :

- **le taux de tête** se lit *à côté* de ce qui était annoncé, jamais seul. Un
  modèle qui met le bon score en tête 10 % du temps en annonçant 10 % est
  honnête ; un qui en annonce 17 % ment. C'est cette comparaison qui manque à
  l'impression « il est nul sur les scores » ;
- **la couverture du top 3** ;
- **le log-loss du score réel**, quel que soit son rang : un modèle qui met 11 %
  sur le 2-1 qui arrive fait mieux qu'un qui y met 6 %, même si aucun des deux ne
  l'avait en tête.

Le plafond est bas et il faut le dire, sans quoi la mesure invite à « améliorer »
ce qui est déjà au bout : un oracle connaissant le **vrai** λ ne place le bon
score en tête qu'environ **12,5 %** du temps, et ses trois premiers ne couvrent
que **33 %**. Le score exact n'est pas prévisible au-delà, par personne.

### L'incohérence que la mesure a révélée

Les échelles passaient par `effective_dispersion`, qui tient compte de ce que λ
est **estimé** sur trois à cinq matchs — la loi prédictive vraie est alors plus
large qu'une Poisson de paramètre l'estimation. La grille des scores, elle,
restait strictement poissonienne. La fiche affichait donc côte à côte des seuils
qui reconnaissaient l'incertitude d'estimation et des scores qui la niaient, et
un modèle trop sûr de lui annonce 11,4 % là où il en réalise 5,9 %.

`score_matrix` accepte désormais les dispersions, et `outcome_probabilities`,
`both_teams_score` et `most_likely_scores` les transmettent. À
`estimation_dispersion = 0` — le défaut — tout est identique au bit près.

```
  estimation_dispersion=0.0 -> phi 1.000, P(1-1) 11.89%, P(3-2) 2.86%
  estimation_dispersion=1.0 -> phi 1.250, P(1-1) 10.23%, P(3-2) 2.33%
```

### Ce que la mesure a répondu : non

Le raisonnement était défendable — un λ estimé sur quatre matchs justifie une
grille plus large, et un modèle trop sûr de lui annonce 11,4 % là où il en
réalise 5,9 %. Mesuré sur 17 matchs, il est faux.

```
  Valeur        Matchs  Log-loss    Brier   Score   Ecart appariee (issue)
  2                 17    1.1153   0.6754   3.0809   -0.004 +/- 0.0091 (t = -0.5)
  1                 17    1.1181   0.6776   3.0281   -0.001 +/- 0.0058 (t = -0.2)
  0.5               17    1.1190   0.6783   3.0025   -0.000 +/- 0.0033 (t = -0.1)
  0                 17    1.1194   0.6789   2.9783   reference

  Le meme classement, juge sur la GRILLE DES SCORES :
  2            +0.103 +/- 0.0587 (t = +1.7)
  1            +0.050 +/- 0.0331 (t = +1.5)
  0.5          +0.024 +/- 0.0178 (t = +1.4)
```

Élargir la grille **dégrade** la prévision de score, et l'écart croît avec le
réglage : +0,024, +0,050, +0,103. Aucun t n'atteint 2 individuellement, mais ce
n'est pas du bruit sans direction — c'est une pente, exactement la forme qui
avait fait écarter `half_life`. Le réglage reste donc à **zéro**, cette fois pour
une raison mesurée et non par abstention.

Le tableau illustre aussi pourquoi les deux classements existent : sur l'issue,
`estimation_dispersion = 2` arrive **premier** ; sur les scores, **dernier**. Les
juger sur une seule métrique aurait donné une réponse, et la mauvaise.

### Un réglage se juge sur l'usage visé

`--regler` classait par log-loss de l'**issue**. Or `estimation_dispersion` et
`rho` agissent d'abord sur la grille des scores et ne touchent l'issue
qu'indirectement : les classer sur elle seule revenait à les juger à côté. Le
tableau porte maintenant les deux classements, et ils peuvent se contredire —
c'est celui qui correspond à l'usage visé qui tranche.

Une note au passage, parce qu'elle a failli devenir une fausse piste : on
pourrait croire que `rho` (Dixon & Coles) a été écarté sur la mauvaise métrique,
puisqu'il corrige les quatre petits scores. C'est faux, et mesurable — de
`rho = 0` à `rho = −0.10`, le déplacement maximal vaut **2,38 points sur une
issue** contre **1,19 sur un score exact**, parce que τ transfère les 1-0 et 0-1
vers les 0-0 et 1-1, donc vers le nul. Le juger sur l'issue était correct.

## Ce que les fiches émises ont appris (`--bilan`)

```bash
python -m ibet --bilan      # lit la base, sans réseau ni date
python -m ibet --valeur     # où se servir du modèle, aux cotes du moment
```

Les mêmes mesures sont exposées à l'interface web : `GET /api/bilan` (lecture
seule, sans réseau) et `GET /api/valeur` (lit les cotes déjà enregistrées ;
c'est `--valeur` en CLI qui les rafraîchit).

Deux mesures que le banc d'essai ne peut pas produire. Il rejoue des matchs
passés : il ne connaît ni les cotes affichées ce jour-là, ni l'arbitre désigné.
Elles ne peuvent venir que de l'usage réel, fiche après fiche.

### Où se servir du modèle (`--valeur`)

`--bilan` mesure ce qui est passé ; `--valeur` dit où parier maintenant. Il
relève les cotes des fiches en attente — ce qui fait exister le mouvement de
ligne, une cote seule ne disant rien — et classe les paris dont l'espérance est
positive.

Deux marques, et elles comptent plus que le classement :

- **`!` écart démesuré** — au-delà de 25 % d'espérance, ce n'est presque jamais
  une occasion. Le marché intègre l'effectif, la nouvelle du matin et l'argent
  de gens qui ont tort à leurs frais ; un modèle qui lui donne 40 points d'écart
  se trompe plus souvent qu'il ne trouve.
- **`?` prévision de repli** — la fiche porte `methode : moyenne production /
  concession`, c'est-à-dire qu'aucune référence de compétition n'a pu être
  établie. C'est le cas de **toute la Coupe d'Europe**, et c'est là que le modèle
  est le plus faible : il normalise chaque équipe dans **son** championnat puis
  multiplie, sans aucune notion de force relative entre compétitions. Comparer la
  moyenne de buts d'un club grec à celle d'un club autrichien revient à supposer
  les deux championnats équivalents.

Sur un relevé réel de 14 fiches, **10 des 12 paris trouvés étaient démesurés**,
et 5 venaient de prévisions de repli. Ce n'est pas une mine d'or, c'est un
diagnostic : le garde-fou a fait exactement son travail. Les deux lignes qui
restaient — un match de Championship correctement référencé, et un nul — sont
l'ordre de grandeur attendu. Une sélection vide ou très courte est le cas
**normal** : le marché est bien calibré, et un écart suffisant pour couvrir la
marge de l'opérateur est rare par construction.

### Ce que les propositions émises ont donné, par option

`--bilan` ouvre sur cette mesure, et c'est la plus honnête du projet : chaque
ligne a été écrite **avant** le coup d'envoi et vérifiée après. Le banc d'essai,
lui, rejoue des matchs et juge des propositions qu'aucune fiche n'a affichées.

Sur **288 propositions tranchées et 24 matchs** :

```
  Par grandeur              Propos.  Matchs   Annonce   Observe            Ecart
  Buts                           72      24     89.6%     83.3%  +6.3 pt +/- 4.9
  Corners                        72      24     83.7%     75.0%  +8.7 pt +/- 6.7
  Tirs cadres                    72      24     85.3%     87.5%  -2.2 pt +/- 4.4
  Cartons jaunes                 72      24     86.4%     84.7%  +1.6 pt +/- 5.2

  Par famille
  equipe                        190      24     86.2%     84.7%  +1.4 pt +/- 3.1
  total                          94      24     86.6%     79.8%  +6.9 pt +/- 4.9

  ENSEMBLE  annonce 86.3%, observe 82.6%, ecart +3.6 pt +/- 3.4
```

Les erreurs types sont **groupées par match**. Une fiche porte une douzaine de
propositions tirées du même λ et tranchées par le même résultat : si le match
finit 4-0, elles se réalisent ou échouent ensemble. Les compter comme
indépendantes divise l'erreur type par la racine d'un effectif que l'échantillon
n'a pas — ici par presque deux (±3,4 au lieu de ±2,2), assez pour faire passer un
écart ordinaire pour un résultat. Sous cinq matchs, aucune erreur type n'est
rendue : la variance entre deux groupes ne mesure rien.

**Lecture.** Aucun écart n'atteint deux erreurs types, donc rien n'est établi.
Mais le signe est le même presque partout — buts, corners, cartons, totaux, trois
tranches sur quatre penchent vers « promet plus qu'il ne tient ». Aucun écart
isolé n'est concluant ; leur concordance l'est davantage, et c'est la forme d'un
biais réel plutôt que du bruit. Deux observations valent d'être suivies : les
**lignes par équipe sont bien calibrées quand les totaux dérapent** (+1,4 contre
+6,9), ce qui est l'*inverse* de ce que prédit l'hypothèse de l'état du match ;
et les **corners restent la grandeur la plus fautive**, comme partout ailleurs
dans ce dossier.

**Le constat qui compte n'est pas la calibration.** La probabilité annoncée
moyenne est de **86,3 %**. Une telle proposition n'est rentable qu'au-delà d'une
cote de **1,159**, et au taux réellement observé (82,6 %) qu'au-delà de **1,210**
— alors qu'un opérateur à 5 % de marge la cote autour de **1,104**, sous les
deux. Un portefeuille concentré à ce niveau de probabilité perd de l'argent
**même parfaitement calibré**, et l'écart de 3,6 points coûte 4 points de
rendement supplémentaires.

Ce n'est donc pas la calibration qu'il faut corriger — elle est dans le bruit —
c'est le **choix** des propositions. `OFFER_MIN = 0.60` protège la lisibilité de
la fiche : en deçà, annoncer une proposition revient à annoncer un tirage à pile
ou face. Mais c'est exactement ce seuil qui exclut les paris rentables : une
proposition à 40 % cotée 3,20 vaut mieux qu'une à 90 % cotée 1,08, et elle ne
sera jamais affichée. La fiche informe, elle ne parie pas — et `--valeur`, qui
regarde `p × cote − 1` plutôt que `p`, est là pour l'autre usage. Ce n'est pas un
défaut à réparer, c'est un arbitrage à connaître.

#### Ce que l'échantillon élargi a répondu

L'archive des résultats a fait passer l'échantillon de 24 fiches à **44**, et de
288 propositions tranchées à **762** — en rattrapant des fiches qu'on croyait
définitivement perdues, donc sans le moindre biais de sélection : ce sont les
fiches qu'on n'avait *pas pu* vérifier, pas celles qu'on a choisi de garder.

```
  Par grandeur              Propos.  Fiches   Annonce   Observe            Ecart
  Buts                          192      44     86.1%     82.8%  +4.1 pt +/- 3.2
  Corners                       192      44     80.1%     78.6%  +3.4 pt +/- 4.2
  Tirs cadres                   192      44     81.9%     82.3%  -0.8 pt +/- 3.4
  Cartons jaunes                186      43     83.2%     81.2%  +1.9 pt +/- 3.4

  ENSEMBLE                      762      44     82.8%     81.2%  +2.1 pt +/- 2.0
```

Par tranche de probabilité annoncée, avec l'intervalle de Wilson à 95 % sur
l'observé :

| Tranche | n | Annoncé | Observé | IC 95 % |
|---|---|---|---|---|
| 60-70 % | 74 | 65,4 % | 64,9 % | [53,5 ; 74,8] |
| 70-80 % | 170 | 75,8 % | 75,3 % | [68,3 ; 81,2] |
| 80-90 % | 357 | 85,5 % | 83,2 % | [79,0 ; 86,7] |
| 90-100 % | 161 | 92,4 % | 90,7 % | [85,2 ; 94,3] |

**Trois corrections à ce qui précède.**

L'écart d'ensemble tombe de +3,6 à **+2,1 points**, soit une erreur type. Le
biais soupçonné plus haut — « promet plus qu'il ne tient » — ne se confirme pas
en triplant l'échantillon : c'est la signature d'un effet de petit nombre, pas
d'un défaut du modèle.

Les **corners ne sont plus la grandeur la plus fautive**. Leur écart passe de
+8,7 à +3,4 points, et l'annoncé tombe dans l'intervalle observé. C'est la
troisième fois que ce soupçon s'évapore à la mesure — après le recalage à 1,07
qu'il a fallu retirer, et après le seuil « plus de 7,5 corners » annoncé à
80,6 % pour 57,1 % observés sur 21 propositions, qui donne aujourd'hui 79,7 %
pour **72,4 %** sur 29. Ne rien régler sur ces vingt-et-une propositions était
le bon choix.

**Aucune tranche n'est hors de son intervalle.** Le modèle est calibré. Le vrai
défaut est ailleurs, et il se lit au score de Brier :

```
  Brier du modele                  0.1467
  Brier d'un taux fixe a 81.2 %    0.1524
  gain                            -0.0058   (score de competence : 3.7 %)
```

Annoncer **81,2 % à toutes les propositions sans rien calculer** donne presque
le même score que le modèle. Ce n'est pas un problème de calibration mais de
**pouvoir discriminant** : le modèle sait dire *combien*, il ne sait pas encore
dire *lesquelles* se réaliseront. Et c'est en partie mécanique — `OFFER_MIN`
et `OFFER_CEILING` enferment toutes les propositions entre 60 % et 95 %, ce qui
laisse peu de place pour écarter les cas les uns des autres.

C'est là, et non sur la calibration, que porte le prochain gain.

### Le rendement face au marché

Le critère 13 calcule déjà la valeur d'un pari — `p × cote − 1`, l'espérance de
gain par euro engagé. Ce qui manquait est la question d'après : **cette valeur
annoncée s'est-elle traduite en rendement ?** Tant qu'elle n'est pas mesurée,
jouer sur la valeur est une croyance.

Le bilan porte sur les **trois issues de chaque fiche**, et non sur les
propositions qu'elle a retenues. La distinction a failli rendre la mesure
impossible sans que rien ne l'indique : `select_offers` retient ce qui dépasse
60 %, et une victoire sèche y parvient rarement sur une affiche équilibrée — ce
sont les totaux larges et les doubles chances qui montent si haut. Une fiche
typique met donc en avant « moins de 4.5 buts au total » et « Blackburn ou nul »,
sans une seule issue, alors que la base ne relève que des cotes 1X2. Les deux
ensembles ne se rencontraient jamais. Les probabilités d'issue, elles, sont dans
**chaque** fiche — le bloc `issue` de la grandeur Buts — qu'une proposition
d'issue ait été retenue ou non : les valoriser toutes les trois donne trois paris
par match au lieu de zéro.

`marche.bilan()` les ventile par tranche de valeur et rapporte le rendement d'une
mise plate. La lecture attendue si l'écart au marché
vaut quelque chose : le rendement **monte** avec la tranche, et la première ligne
— les paris que le modèle juge perdants — rend moins que les autres. S'il est
plat, le modèle diverge du marché sans le battre, et la sélection ne sert à rien :
c'est un résultat, pas un échec.

L'erreur type n'est pas décorative. Le gain d'un pari à cote 3 vaut +2 ou −1 :
sur deux cents paris, +8 % ne se distingue pas de zéro. Elle est **groupée par
match**, comme celle du tableau conditionnel : plusieurs paris d'une même
rencontre — « Victoire A », « Match nul », « Victoire B » — sont mutuellement
exclusifs, un seul peut gagner, et les compter comme des tirages indépendants
annoncerait une précision que l'échantillon n'a pas. C'est le match qui est tiré
au sort, pas le pari, et le tableau affiche les deux comptes.

Deux garde-fous :

- Seules les cotes **relevées avant le coup d'envoi** comptent. Un relevé sans
  coup d'envoi connu est refusé, pas accepté : perdre quelques paris mesurables
  vaut mieux qu'en mesurer un avec une cote impossible à obtenir à temps.
- Un écart démesuré au marché n'est presque jamais une occasion, c'est le signe
  que le modèle est mal informé. `selection()` les rend à part plutôt que de les
  cacher.

**Limite énoncée à chaque appel** : la base ne relève que les cotes 1X2. La
valeur n'est calculable que sur les propositions d'issue ; les totaux, les lignes
par équipe et les doubles chances — l'essentiel de ce qu'une fiche propose —
restent hors de portée tant qu'aucune source ne les couvre.

### Les critères laissés à poids zéro

Quatre critères sont calculés, affichés, et ne déplacent rien : l'effectif (3),
l'enjeu (6), la motivation (9), l'arbitre (12). Ce n'est pas un jugement sur leur
pertinence, c'est l'absence de mesure — et la campagne a montré que quatre
critères parfaitement défendables sur le papier **dégradaient** la prévision une
fois mesurés.

Ils ne sont pas mesurables *a posteriori* : leurs sources ne publient que l'état
courant, ou publient après coup. Les rejouer sur des matchs passés leur donnerait
ce que personne n'avait avant le coup d'envoi.

Ils sont mesurables **prospectivement**. Un poids nul n'annule pas le critère :
`effet` reste vide mais `valeur` est calculée et enregistrée dans la fiche. Le
signal brut est donc au dossier. `criteres.bilan()` régresse le résidu du modèle
sur ce signal :

```
residu = reel / prevu        residu = 1 + pente × (signal − reference) + bruit
```

- **pente nulle** : le critère ne dit rien que le modèle ignore. Le poids zéro
  devient une décision mesurée plutôt qu'une abstention.
- **pente positive et signalée** : il voit ce que le modèle rate. C'est la seule
  justification acceptable pour lui donner un poids.
- **pente négative** : il voit à l'envers, et il faut comprendre pourquoi.

Cinq signaux pour quatre critères : le critère 3 en porte deux qui n'ont rien à
voir — les absences, et le **changement de dispositif**.

Ce dernier mérite une note, parce que le critère 3 refuse explicitement de
corriger sur le système, et qu'il a raison à moitié. Le style est déjà lu dans
les comptages (critère 1, poids 1, le plus utile du lot) et y distingue déjà le
bloc bas de la contre-attaque — mieux qu'une étiquette, un « 5-3-2 » sur le
papier pouvant être un bloc haut à trois centraux. Le système **habituel** est
donc redondant. Un **changement** ne l'est pas : une équipe qui joue 4-3-3 toute
la saison et aligne un 5-4-1 fait quelque chose que ses statistiques n'ont jamais
vu, et l'information n'existe qu'une heure avant le coup d'envoi.

Conséquence pratique : ce signal exige d'émettre **près du coup d'envoi**. Une
fiche écrite la veille ne connaît que l'habitude, et comparer l'habitude à
elle-même donne un signal constant que la régression rejette à juste titre.
Émettre tôt laisse du temps pour parier, émettre tard donne l'information ; les
deux ne peuvent pas être vrais en même temps.

**Aucun poids n'est modifié par ce module.** Il produit le chiffre à partir
duquel un poids pourrait être changé, à la main, dans `context.Poids`.

## Codes de sortie

| Code | Signification |
|---|---|
| `0` | succès (y compris « aucun match ce jour-là ») |
| `1` | erreur fonctionnelle : clé absente/refusée, quota, date invalide, réseau |
| `2` | erreur d'usage de la CLI |
| `130` | interruption clavier |
