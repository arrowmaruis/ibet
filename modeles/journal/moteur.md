# Moteur commun — journal des versions

Le socle partagé par tous les modèles : estimation du nombre attendu (Maher),
lois de comptage, sélection des propositions, réglages (`Params`). Fichiers :
`estimation.py`, `lois.py`, `offres.py`, `reglages.py`.

Toute modification ici change **toutes** les grandeurs : elle incrémente
`VERSION_MOTEUR` (`modeles/__init__.py`) et se mesure avec
`python etude.py --moteur`.

---

## 1.0.0 — en service depuis le 2026-09-27

**Changement** : aucun changement de calcul. C'est le moteur de `predict.py` tel
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
