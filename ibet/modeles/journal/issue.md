# Modèle de l'issue — journal des versions

Fichier : `ibet/modeles/issue.py`. Clé : `issue`.

Pas d'estimation propre : l'issue se lit sur la loi jointe des deux scores. Le
modèle reçoit du modèle des buts ses nombres attendus **définitifs** (contexte
appliqué) et leurs dispersions. Ses résultats sont publiés dans la fiche des
buts (`resultat`, `p_les_deux_marquent`, `scores_probables`), mais `ibet/evaluation/etude.py`
lui attribue bien ses propositions.

Conséquence à garder en tête : **une nouvelle version des buts change aussi
l'issue**. Pour attribuer une amélioration de l'issue, comparer les deux
versions avec `python -m ibet etude --modele issue` et `--modele buts`.

---

## 1.1.0 — en service depuis le 2026-09-27

**Changement** : aucun changement de code. Le modèle reçoit les nombres de buts
attendus de la version **2.0.0 des buts** (notes au maximum de vraisemblance,
total resserré vers la moyenne de la compétition). Quand les cotes 1X2 sont
connues à l'émission, le 1X2 publié est mélangé à 90 % à celui du bookmaker
(voir [buts.md](buts.md)) ; ses probabilités changent donc, et la règle du journal impose un nouveau numéro.

**Pourquoi** : c'est l'issue qui souffrait le plus de notes tassées. Sur les
67 fiches tranchées avec cotes, le favori désigné n'avait raison que 39 fois,
moins que « toujours le domicile » (40), là où le marché en trouvait 50.

**Mesure avant adoption** (walk-forward, voir [buts.md](buts.md)) :

| Mesure | 1.0.0 | 1.1.0 | Marché |
|---|---|---|---|
| Brier 1X2, 4 996 matchs de 2026 | 0.6014 | **0.5924** (t = −4.8) | — |
| Brier 1X2, 67 fiches avec cotes (mêmes données) | 0.5332 | **0.4891** | 0.4581 |
| Favori juste, 67 fiches | 45 | **49** | 50 |
| Brier 1X2, **1 784 matchs de 2026**, contre Bet365 | 0.6146 *(modèle seul)* | 0.5958 *(combiné 10 %)* | **0.5954** *(Bet365)* |

### Ce que fait la version
Identique à la 1.0.0 : matrice des scores (binomiale négative, `rho = 0`), 1X2,
les deux marquent, scores exacts, mêmes propositions.

### Points forts
- **Calibration conservée** : favori annoncé 0.4-0.5 → observé 0.42 ; 0.6-0.7 →
  0.63 ; 0.7-0.8 → 0.73 (banc d'essai, 4 996 matchs).
- **Plus de propositions d'issue** attendues au-dessus du seuil de 60 % : les
  écarts nets (Algérie – Zambie, Nigéria – Madagascar, Mozambique – Sénégal)
  ressortent désormais à 73-78 %, là où la 1.0.0 restait sous 55 %.

### Points faibles
- **Très grands favoris légèrement trop sûrs** : annoncé 0.84, observé 0.79
  (167 matchs) ; annoncé 0.95, observé 0.89 (73 matchs).
- **Nul** : bien calibré en moyenne (24.0 % annoncé et observé), mais jamais au
  centre d'une proposition. Dixon et Coles (`rho = −0.05`) ne change rien de
  mesurable (Brier ± 0.0005).

### Pistes pour la suite
- Re-mesurer la tranche 80-95 % sur les fiches 1.1.0 ; si l'excès persiste,
  resserrer l'écart des seuls très grands favoris.
- Poids du modèle dans l'issue combinée (10 %) : voir [buts.md](buts.md).

### Mesures en service
Pas encore de fiche tranchée en 1.1.0.

---

## 1.0.0 — retirée le 2026-09-27

**Changement** : première version versionnée. Calcul identique à celui d'avant
le découpage en un modèle par événement.

### Ce que fait la version
- **Matrice des scores** : produit des deux lois de buts (binomiale négative,
  dispersions effectives), facteur de Dixon et Coles (`rho`, 0 en service),
  normalisée.
- **1X2** : somme des cases selon le signe de l'écart.
- **Les deux marquent** : masse hors de la première ligne et de la première
  colonne.
- **Scores exacts** : les 3 cases les plus probables.
- **Propositions** : victoire domicile / extérieur, nul, trois doubles chances,
  les deux marquent / une équipe au moins ne marque pas.

### Points forts
- **Cohérence** : issue, scores exacts et seuils de buts sortent d'une seule
  matrice, et ne peuvent pas se contredire.
- **Double chance à peu près tenue** : annoncé 74.9 %, observé 70.3 %
  (37 propositions, 24 matchs), écart dans l'erreur type (± 6.9).

### Points faibles
- **Peu de propositions retenues** : l'issue dépasse rarement le seuil de 60 %,
  si bien que 39 propositions seulement ont été tranchées en 44 matchs. La mesure
  reste faible.
- **Aucun terme de confrontation directe** : les confrontations ne jouent que
  par le critère 5 du contexte, pas dans la matrice.
- **Nul** : sans Dixon et Coles (`rho = 0`), les scores serrés sont traités
  comme indépendants. La littérature montre que ce choix sous-estime le 1-1 et
  surestime le 1-0 / 0-1. Non encore mesuré sur ce projet.

### Pistes pour la suite
- **Calibration du nul** : mesurer annoncé contre observé pour « Match nul » sur
  le banc d'essai (toutes probabilités, pas seulement les propositions
  retenues).
- **Dixon et Coles** : le réglage de `rho` agit ici en premier. Le mesurer en
  Brier et en log-loss de l'issue, pas seulement sur les seuils.
- **Mélange au marché** : `marche.melanger` existe déjà ; mesurer un poids
  optimal par backtest avant d'en faire une version.

### Mesures en service
Pas encore de fiche tranchée en 1.0.0. Référence de départ (fiches antérieures) :

| Famille | Matchs | Props | Annoncé | Observé | Écart | ± | Brier |
|---|---|---|---|---|---|---|---|
| Toutes | 24 | 39 | 74.5 % | 71.8 % | +2.7 | 6.9 | 0.2041 |
| double chance | 24 | 37 | 74.9 % | 70.3 % | +4.6 | 6.9 | 0.2090 |
| les deux marquent | 2 | 2 | 66.2 % | 100 % | — | — | 0.1150 |
