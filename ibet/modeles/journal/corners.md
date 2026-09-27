# Modèle des corners — journal des versions

Fichier : `ibet/modeles/corners.py`. Clé : `corners`.

---

## 3.0.0 — en service depuis le 2026-09-27

**Changement** : le nombre attendu de chaque équipe est corrigé par les **cotes
1X2 du marché** d'avant-match, avant le facteur des styles (inchangé depuis la
2.0.0). La correction est une régression de Poisson mesurée sur une nouvelle
source : l'**historique long** de football-data.co.uk (sept championnats depuis
2012-13, table `historique`, `python -m ibet historique`).
**Pourquoi** : deux défauts notés depuis la 1.0.0. L'estimation est **trop
étalée** : dix matchs d'historique surestiment les écarts entre équipes. Et le
modèle ne sait pas qui va **dominer** le match, alors que le favori obtient les
corners de sa domination. Le marché sait les deux.
**Mesure avant adoption** : `python -m ibet mesurer-historique`, rejeu
chronologique de 30 394 matchs avec corners et cotes. Coefficients ajustés sur
2012-13 à 2022-23 (22 988 matchs), verdict sur 2023-24 à 2026-27 (7 406 matchs),
contre un estimateur calqué sur le moteur (dix derniers matchs, régularisation
22) :

| Variante (test) | Écart log-vraisemblance | t | Brier total | Brier équipe | Pente réel / prévu |
|---|---|---|---|---|---|
| référence (≈ moteur) | — | — | 0.2224 | 0.2008 | 0.78 |
| historique profond (profil sur deux saisons) | +0.0283 | +5.3 | 0.2239 | 0.1966 | 0.53 |
| **marché, 1X2 seul (retenu)** | **+0.0688** | **+16.0** | **0.2219** | **0.1930** | **1.08** |
| marché, 1X2 + plus / moins 2,5 buts | +0.0697 | +16.2 | 0.2217 | 0.1929 | 1.06 |
| historique profond + marché | +0.0765 | +16.6 | 0.2209 | 0.1925 | 1.07 |

Le gain vaut dans les sept championnats (de +0.040 en Liga à +0.10 aux
Pays-Bas). C'est dix fois celui des styles des joueurs (+0.0074).

### Ce que fait la version
- **Correction du marché** (`ibet/modeles/apports.py`), probabilités 1X2
  moyennes, marge retirée proportionnellement :
  `log λ' = a + b·log λ + c·(p_équipe − p_adversaire)`.
  Domicile : a = 0.619, b = 0.588, c = 0.437. Extérieur : a = 0.540,
  b = 0.670, c = 0.395. Bornée à [λ/1.6 ; 1.6·λ].
- b ≈ 0.6 ramène les écarts de l'historique vers la moyenne, là où le
  moteur les exagérait. Avec c ≈ 0.44, un favori à 70 % contre 12 % obtient
  environ 29 % de corners de plus qu'à cotes égales.
- **Styles des onze** : inchangés (poids 0.25, onze aligné seulement),
  appliqués après le marché.
- **Sans cotes** (match rejoué, source muette), rien ne change : le modèle
  vaut alors la 2.0.0.
- Trace dans la fiche : clé `apports` (`marche`, `styles`).

### Points forts
- Plus gros gain mesuré sur les corners depuis le versionnage, stable d'un
  championnat à l'autre, sur 7 406 matchs qu'aucun réglage n'a vus.
- Corrige l'étalement : pente 0.78 → 1.08 (la cible est 1).

### Points faibles
- **Estimateur de référence approché** : les coefficients ont été ajustés sur
  un Maher reconstruit (dix matchs, régularisation 22), pas sur le λ exact du
  moteur (référence de compétition Flashscore, contexte). Même échelle, mais à
  vérifier sur les fiches tranchées.
- **Cotes d'ouverture** : la source donne des cotes relevées en début de
  semaine, le projet celles du jour. Les secondes en savent plus (compositions,
  blessures) ; l'effet ne peut qu'être au moins aussi bon, mais n'est pas mesuré.
- **Sept championnats** : ailleurs, les mêmes coefficients s'appliquent sans
  avoir été mesurés.

### Pistes pour la suite
- **Historique profond** : +0.007 de plus par-dessus le marché ; demande une
  table de correspondance des noms d'équipes entre football-data.co.uk et
  Flashscore (« Man United » / « Manchester Utd »).
- Re-ajuster les coefficients sur le λ du moteur lui-même, avec le backtest
  apparié, quand assez de fiches émises porteront des cotes.

### Mesures en service
Pas encore de fiche tranchée en 3.0.0.

| Période | Matchs | Props | Annoncé | Observé | Écart | Brier |
|---|---|---|---|---|---|---|

---

## 2.0.0 — retirée le 2026-09-27

**Changement** : le modèle reçoit les **styles des joueurs alignés**, tirés
d'une nouvelle source : les statistiques par joueur et par match de Flashscore
(table `stats_joueurs`). Un facteur « onze aligné / onzes de référence »
déplace le nombre attendu de chaque équipe, au poids **0.25**, et **seulement
quand la composition est publiée**.
**Pourquoi** : un corner naît d'un geste individuel — centre dévié, tir contré,
débordement. Le modèle d'équipe ne sait pas qui joue : un latéral qui centre
dix fois par match ne produit pas les corners du défenseur central qui le
remplace.
**Mesure avant adoption** : `python -m ibet mesurer-styles`, rejeu
chronologique de 2 318 matchs de sept championnats (Angleterre, Espagne,
Italie, Allemagne, France, Portugal, Pays-Bas ; 2025-26 et début 2026-27),
chaque profil ne voyant que les matchs antérieurs. Poids choisi sur les 60 %
les plus anciens (1 390 matchs), verdict sur les 40 % les plus récents
(928 matchs) :

| Variante (test) | Poids | Écart log-vraisemblance | t | Brier total | Brier équipe |
|---|---|---|---|---|---|
| référence (Maher réduit aux corners) | — | — | — | 0.2244 | 0.1991 |
| **onze aligné, taux par 90 (retenu)** | **0.25** | **+0.0074** | **+2.7** | **0.2236** | **0.1986** |
| onze aligné, part du volume d'équipe | 0.25 | +0.0021 | +0.8 | 0.2239 | 0.1991 |
| onze habituel (3 derniers matchs) | 0.25 | −0.0005 | −0.2 | 0.2246 | 0.1990 |
| onze aligné, taux, poids 1 | 1 | +0.0050 | +0.5 | 0.2235 | 0.1989 |
| onze habituel, poids 1 | 1 | −0.0203 | −2.2 | 0.2270 | 0.2002 |

Comme pour les cartons, la référence est un Maher reconstruit sur l'archive,
pas le moteur complet : le gain mesuré est celui d'un facteur **ajouté** à un
attendu d'équipes.

### Ce que fait la version
- **Données** : `api_client.stats_joueurs` lit, pour un match terminé, une
  trentaine de grandeurs par joueur (minutes, tirs, tirs contrés, centres,
  dribbles, touches dans la surface, dégagements, contres…) et son poste.
  Relevé : `python -m ibet rattraper-joueurs` (reprenable, incrémental ;
  2 750 matchs, 85 000 lignes joueur au 27 septembre 2026).
- **Profil d'un joueur** (`ibet/modeles/styles.py`) : taux par 90 minutes,
  pondérés par l'ancienneté (demi-vie 240 jours), lissés vers le taux de son
  poste par 450 minutes fictives.
- **Indice corners** : ce qu'un geste pèse en corners, d'après la régression
  des corners d'une équipe sur les gestes de ses joueurs dans le même match
  (5 490 équipes-matchs, R² = 0.53) : centres 1, tirs contrés 1, touches dans
  la surface 0.35, dribbles réussis 0.3, tirs 0.2. Un corner pour environ
  sept centres.
- **Facteur** : indice de l'onze aligné (80 minutes chacun) divisé par la
  moyenne, pondérée comme l'historique (demi-vie 180 jours), des onzes des
  vingt derniers matchs — les mêmes profils des deux côtés, seul l'effectif
  change. Élevé à la puissance **0.25**, borné à [1/1.3 ; 1.3]. Facteur 1 sans
  cinq onzes de référence, ou hors des championnats relevés.
- **Onze habituel** : calculé et affiché dans la fiche (clé `styles`), jamais
  appliqué.
- Sans contexte, `ajuster` ne fait rien : le modèle est alors le 1.0.0.

### Points forts
- Gain réel sur des matchs jamais vus (t = +2.7), sur les deux familles de
  seuils (total et équipe).
- Catalogue lisible des styles : `python -m ibet catalogue --equipe <nom>`.

### Points faibles
- **Gain modeste** : +0.0074 par match. L'onze aligné ne s'écarte de l'onze
  de référence que de 6 % en moyenne (12 % au 90e centile).
- **Composition publiée une heure avant le match** : une fiche émise la veille
  n'en profite pas.
- **Sept championnats seulement**, depuis août 2025 : ailleurs, facteur 1.
- **Taux dépendants du club** : un joueur transféré d'une équipe dominante
  arrive avec des taux gonflés. Le mode « part du volume d'équipe », censé
  corriger cela, a moins gagné (+0.0021).

### Pistes pour la suite
- **Volet défensif** : dégagements et contres de l'adversaire, qui concèdent
  des corners.
- **Remplaçants** : 80 minutes par titulaire, faute de mieux.
- Re-mesurer à la fin de la saison 2026-27, quand l'archive aura doublé.

### Mesures en service
Pas encore de fiche tranchée en 2.0.0.

| Période | Matchs | Props | Annoncé | Observé | Écart | Brier |
|---|---|---|---|---|---|---|

---

## 1.0.0 — retirée le 2026-09-27

**Changement** : première version versionnée. Calcul identique à celui d'avant
le découpage en un modèle par événement.

### Ce que fait la version
- **Nombre attendu** : moteur Maher (voir [moteur.md](moteur.md)), lu dans les
  statistiques du match (`corners`), normalisé par la moyenne de corners de la
  compétition quand elle est connue.
- **Loi** : binomiale négative, dispersion **1.615**, la plus forte des quatre
  grandeurs.
- **Corrélation** entre les deux équipes : **−0.149** ± 0.017 (t = −9.0,
  n = 4 097). Les corners sont partiellement à somme nulle, ce qui resserre la
  loi du total.
- **Recalage** : 1.0. L'ancien 1.07 a été retiré : il portait la prévision à
  10.23 pour 9.54 observés sur 26 fiches.
- **Seuils** : par équipe 2.5 à 6.5 ; au total 7.5 à 11.5 ; mis en avant 9.5
  (côté « plus »).
- **Marché propre** : duel (« plus de corners pour X », « autant ou plus »).

### Points forts
- **Lignes par équipe** : annoncé 84.1 %, observé 82.0 % (111 propositions,
  44 matchs), écart dans l'erreur type.
- **Biais de volume faible** : en walk-forward sur 1 322 matchs, le modèle
  sous-annonce de 0.24 corner sur 9.6 (2.5 %).

### Points faibles
- **Estimation trop étalée** : pente du réel sur le prévu **0.43** à k = 10 (la
  plus faible des quatre). Quand le modèle annonce un corner de plus, la réalité
  n'en fait que 0.43. k = 22 réduit le défaut sans l'effacer.
- **Données plus rares** : les corners exigent les statistiques détaillées. Il
  y a environ 1 750 matchs mesurés, contre 6 300 pour les buts.
- **Duel sous-annoncé** : annoncé 66.1 %, observé 73.7 % (19 propositions),
  échantillon trop court pour conclure.

### Pistes pour la suite
- **Étalement** : régularisation propre aux corners (k plus grand), ou
  `lambda_shrink > 0`. À tester en backtest apparié sur la ligne 9.5.
- **Total** : vérifier que la correction de corrélation suffit (écart +3.0 sur
  les fiches antérieures, non significatif).

### Mesures en service
Pas encore de fiche tranchée en 1.0.0. Référence de départ (fiches antérieures) :

| Famille | Matchs | Props | Annoncé | Observé | Écart | ± | Brier |
|---|---|---|---|---|---|---|---|
| Toutes | 44 | 192 | 80.1 % | 78.6 % | +1.5 | 3.7 | 0.1671 |
| équipe | 44 | 111 | 84.1 % | 82.0 % | +2.2 | 4.0 | 0.1503 |
| total | 37 | 62 | 77.2 % | 74.2 % | +3.0 | 5.9 | 0.1884 |
| duel | 15 | 19 | 66.1 % | 73.7 % | −7.6 | 11.5 | 0.1960 |
