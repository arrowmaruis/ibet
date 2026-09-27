# Moteur commun — journal des versions

Le socle partagé par tous les modèles : estimation du nombre attendu (Maher),
lois de comptage, sélection des propositions, réglages (`Params`). Fichiers :
`estimation.py`, `lois.py`, `offres.py`, `reglages.py`.

Toute modification ici change **toutes** les grandeurs : elle incrémente
`VERSION_MOTEUR` (`ibet/modeles/__init__.py`) et se mesure avec
`python -m ibet etude --moteur`.

---

## 1.1.0 — en service depuis le 2026-09-27

**Changement** : les propositions suivent ce que proposent les bookmakers.
Aucune probabilité ne change ; ce qui change, c'est **quelles** propositions
la fiche montre.
**Pourquoi** : la fiche proposait des évidences que personne ne cote (« plus
de 0.5 but » à 93 %, payé 1.01), et des lignes fixes sans rapport avec le
match (« plus de 7.5 corners » pour un match à douze corners attendus).

### Ce que fait la version
- **Lignes du match** (`base.lignes_du_match`) : la ligne principale est celle
  dont la probabilité de dépassement est la plus proche de 50 %, comme le
  bookmaker place la sienne. L'échelle garde `ecart_*` lignes de part et
  d'autre, dans la gamme où les bookmakers en ouvrent (`gamme_*` de chaque
  modèle) :

  | Grandeur | Total | ± lignes | Par équipe | ± lignes |
  |---|---|---|---|---|
  | Buts | 0.5 – 6.5 | 2 | 0.5 – 3.5 | 1 |
  | Corners | 6.5 – 14.5 | 3 | 1.5 – 8.5 | 2 |
  | Tirs cadrés | 4.5 – 12.5 | 2 | 1.5 – 7.5 | 2 |
  | Cartons jaunes | 1.5 – 7.5 | 2 | 0.5 – 4.5 | 1 |

  Les échelles, les propositions et les fourchettes utilisent ces lignes ; le
  xG (jamais parié) garde ses seuils fixes.
- **Dix bookmakers de référence** (`ibet/modeles/bookmakers.py`) : 1xBet,
  Pinnacle, bet365, Bwin, William Hill, Betfair, Betway, Betclic, Winamax,
  Unibet. Chacun porte sa marge 1X2 **mesurée** : football-data.co.uk
  (2024-25 et 2025-26, jusqu'à 4 728 matchs) ou the-odds-api.com (Ligue 1,
  18 matchs) pour les opérateurs absents de la première. De 1.6 % (1xBet) à
  13.9 % (Unibet FR). Cote payée : `1 / (p·(1 + marge))`.
- **Cote minimale** (`offres.COTE_MIN` = 1.15) : une proposition n'est retenue,
  ni affichée au tableau des marchés, que si **trois** des dix bookmakers au
  moins la paient 1.15 ou plus. Plafond de fait : 82 % sur les buts et l'issue,
  80 % sur les marchés spéciaux (il était de 95 %).
- **Marchés spéciaux** (corners, tirs cadrés, cartons) : marge majorée de
  3 points (`MAJORATION_SPECIAUX`). C'est une **hypothèse** : aucune source
  gratuite ne publie ces cotes.
- **Dans la fiche** : chaque proposition porte sa cote juste (1 / p), la
  fourchette des cotes sur les dix bookmakers et chez combien elle reste
  jouable ; un tableau montre, bookmaker par bookmaker, combien de
  propositions de la fiche il paie au-dessus de 1.15.

### Points faibles
- **Cotes estimées, pas relevées** : la marge d'un opérateur est une moyenne ;
  sur un match donné, sa cote peut s'en écarter. Les marges de Betfair,
  Betway, Betclic, Winamax et Unibet reposent sur 18 matchs seulement.
- **Gammes de lignes communes aux dix** : les lignes réellement ouvertes par
  chaque opérateur ne sont publiées par aucune source gratuite.
- Les lignes affichées dépendent du match : deux fiches ne montrent plus les
  mêmes seuils, ce qui rend les échelles moins comparables d'un match à
  l'autre.

---

## 1.0.0 — retirée le 2026-09-27

**Changement** : aucun changement de calcul. C'est le moteur de `ibet/prevision/predict.py` tel
qu'il était avant le découpage en un modèle par événement, versionné pour la
première fois. Le découpage a été vérifié sur 1 500 matchs synthétiques, avec
et sans contexte et avec des réglages variés : les fiches sont identiques au
dernier chiffre.

### Ce que fait la version
- **Estimation** : modèle attaque / défense de Maher (1982), normalisé par la
  moyenne de la compétition, chaque match ramené à un adversaire moyen
  (correction de calendrier). En l'absence de référence de compétition, repli
  sur `(produit + concédé par l'adversaire) / 2`.
- **Périmètre** : les deux équipes restreintes à la compétition du match si
  chacune y a au moins 4 matchs, sinon aucune des deux.
- **Régularisation** des forces vers 1 : `k = SHRINKAGE = 22` matchs fictifs.
- **Lois** : Poisson, binomiale négative (sur-dispersion) ou binomiale
  (sous-dispersion) selon la `dispersion` du modèle. Pour le total : dispersion
  corrigée de la corrélation entre les deux équipes.
- **Sélection** : propositions entre 60 % et 95 %, 6 au plus, 2 par famille
  avant de relâcher le plafond.
- **Réglages neutres** (mécanismes en place, désactivés faute de gain mesuré) :
  `half_life = 0`, `rho = 0`, `home_edge = 0`, `lambda_shrink = 0`,
  `estimation_dispersion = 0`, `xg_echantillon = 0`.

### Points forts
- **Régularisation k = 22** : mesurée en walk-forward sur le seuil de chaque
  grandeur, elle améliore les 16 lignes proposables, en Brier et en log-loss.
  Buts : 0.2472 → 0.2447 (t = −4.9, n = 6 316) ; cartons : 0.2470 → 0.2433
  (t = −3.8) ; corners : 0.2530 → 0.2505 (t = −2.9).
- **Lignes par équipe bien calibrées** : sur les fiches émises, la famille
  « équipe » annonce 85.9 % et en réalise 85.9 % (397 propositions, 44 matchs).
- **Une seule loi par grandeur** : seuil mis en avant, échelles et propositions
  sortent du même calcul et ne peuvent plus se contredire.

### Points faibles
- **Les totaux promettent trop** : famille « total », annoncé 83.5 %, observé
  77.3 %, écart +6.2 points ± 2.7 (260 propositions, 44 matchs, significatif).
  Les lignes par équipe, calculées avec les mêmes nombres attendus, sont justes.
  Le défaut est donc dans la **loi du total**, pas dans l'estimation.
- **Les duels sont sous-annoncés** : famille « duel », annoncé 67.1 %, observé
  77.0 % (61 propositions, 20 matchs). La grille du duel emploie la dispersion
  de base, sans la corrélation ni l'incertitude d'estimation.
- **Réglages choisis sur un échantillon qui recoupe celui de l'évaluation** :
  les valeurs de `Params` sont comparées par log-vraisemblance, pas estimées par
  maximum de vraisemblance sur plusieurs saisons.
- **Pas de pondération par ancienneté** (`half_life = 0`) : sur 149 matchs, les
  demi-vies de 14 à 60 jours faisaient toutes moins bien. Mesure ancienne, sur
  un petit échantillon.

### Pistes pour la suite
- **Totaux** : re-mesurer l'écart sur les fiches 1.0.0. Les fiches antérieures
  couvrent une période où les réglages ont changé, et une partie d'entre elles a
  pu être émise avant la correction de corrélation. Si l'écart persiste, essayer une loi bivariée plutôt qu'un calage sur deux moments.
- **Duel** : construire la grille avec les dispersions effectives et la
  corrélation, puis la comparer à la version actuelle en backtest apparié.
- **Demi-vie** : la reprendre avec `--regler half_life=...` sur le corpus
  entier, maintenant plus large que 149 matchs.

### Mesures en service
Pas encore de fiche tranchée en 1.0.0. Référence de départ (fiches antérieures,
avant le versionnage, jusqu'au 2026-09-27) :

| Famille | Matchs | Props | Annoncé | Observé | Écart | ± |
|---|---|---|---|---|---|---|
| Ensemble | 44 | 762 | 82.8 % | 81.2 % | +1.6 | 1.7 |
| équipe | 44 | 397 | 85.9 % | 85.9 % | −0.0 | 1.8 |
| total | 44 | 260 | 83.5 % | 77.3 % | **+6.2** | 2.7 |
| duel | 20 | 61 | 67.1 % | 77.0 % | −10.0 | 5.7 |
