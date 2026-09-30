# Modèle des tirs cadrés — journal des versions

Fichier : `ibet/modeles/tirs_cadres.py`. Clé : `tirs_cadres`.

---

## 2.2.0 — en service depuis le 2026-09-30

**Changement** : dispersion (rapport variance / moyenne) **1,396 → 1,10**. Rien
d'autre ne change.
**Pourquoi** : sur l'archive, le modèle 2.1.0 était trop **prudent dans les deux
sens** : « plus de » annoncés 71,6 %, réalisés 75,0 % ; « moins de » 71,0 → 72,2 %
(7 406 matchs de test, `python -m ibet mesurer-sens`). Les duels l'étaient aussi
sur les fiches (annoncés 69-71 %, réalisés 74-87 %). La cause : la dispersion
1,396 avait été mesurée sur le modèle de forme seul ; les nombres attendus
corrigés par les cotes du marché (2.x) expliquent une part de la variance
qu'elle attribuait au hasard, et la loi restait trop large.
**Mesure avant adoption** : grille 1,0 à 1,615, choisie sur les saisons jusqu'à
2022-23 (22 988 matchs, log-vraisemblance des comptes par équipe), jugée sur
2023-24 et après (7 406 matchs) :

| Test (7 406 matchs) | 2.1.0 (1,396) | **2.2.0 (1,10)** |
|---|---|---|
| Log-vraisemblance par match | — | **+0,027** (t = +11,1) |
| « Plus de » : annoncé → réalisé | 71,6 → 75,0 % | **72,2 → 72,2 %** |
| « Moins de » : annoncé → réalisé | 71,0 → 72,2 % | **71,9 → 72,2 %** |
| Duels : annoncé → réalisé | 70,9 → 72,7 % | 71,3 → 70,6 % |

### Ce que fait la version
Celle de la 2.1.0 (correction par les cotes 1X2 et plus / moins 2,5 buts), avec
une loi binomiale négative de dispersion 1,10 au lieu de 1,396.

### Points forts
- Les deux sens sont justes au dixième de point sur 7 406 matchs.
- Plus grand gain de log-vraisemblance mesuré sur une dispersion dans le projet.

### Points faibles
- La mesure porte sur le modèle rejoué de l'archive (football-data.co.uk), qui
  reproduit la correction du marché sans être le modèle en service à l'identique.
- Les corners ont le même symptôme en plus faible (1,615 → 1,396 : +0,0047,
  t = 2,9) mais la calibration des « plus » s'y dégrade : non changés.

### Pistes pour la suite
- Vérifier sur les fiches 2.2.0 tranchées (`python -m ibet etude`, page Calibration).

### Mesures en service
Pas encore de fiche tranchée en 2.2.0.

---

## 2.1.0 — retirée le 2026-09-30

**Changement** : quand la cote plus / moins 2,5 buts est relevée avant le match
(voir buts 2.1.0), la correction du marché passe au **jeu complet** de
coefficients : 4e variable p(+2,5) − 0,5 (`COEFS_MARCHE_TOTAL`). Sans cette
cote, repli sur le jeu 1X2 seul de la 2.0.0.
**Pourquoi** : c'est la piste notée en 2.0.0 ; un match que le marché voit
ouvert produit plus de tirs cadrés des deux côtés.
**Mesure avant adoption** : même mesure que la 2.0.0 (`mesure_historique.py`,
7 406 matchs de test) : log-vraisemblance +0.0999 avec la cote 2,5, contre
+0.0827 pour le 1X2 seul.

### Ce que fait la version
- Domicile (0.4546, 0.6421, 0.4787, +0.6146) ; extérieur (0.4248, 0.7180,
  0.4997, +0.4599), sur (constante, log λ, écart p_dom − p_ext, p(+2,5) − 0,5).

### Points faibles
- **Source de la cote différente de celle du réglage.** Les coefficients ont
  été ajustés avec la cote MOYENNE d'OUVERTURE de football-data.co.uk (Avg>2.5,
  marge retirée par normalisation de la paire moyenne). En service, p(+2,5)
  vient du consensus the-odds-api : marge retirée opérateur par opérateur, puis
  moyenne, relevée à l'émission (plus proche de la clôture que de
  l'ouverture). L'écart attendu est faible, mais la version n'est pas mesurée
  sur sa propre source : à vérifier sur les fiches 2.1.0.
- Trace `apports.marche.p_plus_25` dans la fiche quand le jeu complet a servi.

### Mesures en service
Pas encore de fiche tranchée en 2.1.0.

---

## 2.0.0 — retirée le 2026-09-28

**Changement** : même correction que les corners 3.0.0 : les **cotes 1X2 du
marché** corrigent le nombre attendu de chaque équipe (`ibet/modeles/apports.py`),
coefficients mesurés sur l'historique long (football-data.co.uk). Le facteur
des styles reste affiché à poids 0.
**Pourquoi** : le favori cadre plus de tirs que son historique ne le dit, et
dix matchs d'historique ne suffisent pas à fixer le niveau d'une équipe.
**Mesure avant adoption** : `python -m ibet mesurer-historique --grandeur
tirs_cadres`, réglage 2012-13 à 2022-23 (22 988 matchs), verdict 2023-24 à
2026-27 (7 406 matchs) :

| Variante (test) | Écart log-vraisemblance | t | Brier total | Brier équipe | Pente |
|---|---|---|---|---|---|
| référence (≈ moteur) | — | — | 0.1965 | 0.2000 | 1.05 |
| **marché, 1X2 seul (retenu)** | **+0.0827** | **+17.0** | **0.1959** | **0.1899** | **1.16** |
| marché, 1X2 + plus / moins 2,5 buts | +0.0999 | +18.4 | 0.1933 | 0.1887 | 0.92 |
| historique profond + marché | +0.1009 | +17.2 | 0.1927 | 0.1885 | 0.91 |

### Ce que fait la version
- `log λ' = a + b·log λ + c·(p_équipe − p_adversaire)` ; domicile a = 0.181,
  b = 0.819, c = 0.498 ; extérieur a = 0.254, b = 0.846, c = 0.472. Bornée à
  [λ/1.6 ; 1.6·λ]. Sans cotes, rien ne change.

### Points faibles
- **La cote plus / moins 2,5 buts vaudrait +0.017 de plus**, mais le projet ne
  relève que le 1X2 avant les matchs. La pente de 1.16 montre que le 1X2 seul
  sur-corrige légèrement.

### Pistes pour la suite
- Relever la cote plus / moins 2,5 buts (the-odds-api, marché `totals`) et
  passer aux coefficients complets : domicile (0.455, 0.642, 0.479, +0.615 sur
  p(+2.5) − 0.5), extérieur (0.425, 0.718, 0.500, +0.460).

### Mesures en service
Pas encore de fiche tranchée en 2.0.0.

| Période | Matchs | Props | Annoncé | Observé | Écart | Brier |
|---|---|---|---|---|---|---|

---

## 1.0.1 — retirée le 2026-09-27

**Changement** : la fiche affiche le facteur des **styles des joueurs alignés**
(clé `styles`), sans rien déplacer : poids **0**. Aucune probabilité ne change.
**Pourquoi** : même idée que les corners 2.0.0 — un tireur absent, un autre qui
arrive —, sur les tirs cadrés par 90 minutes de chaque titulaire.
**Mesure avant adoption** : `python -m ibet mesurer-styles --grandeur
tirs_cadres`, 928 matchs de test : onze aligné, poids 0.25, +0.0037 de
log-vraisemblance (t = +1.2) ; à poids 1, −0.036 (t = −2.8). Dans le bruit à
poids faible, nuisible à poids plein : le facteur reste à zéro, comme les
joueurs des cartons dans un cas semblable (t = +1.3).

### Pistes pour la suite
- Re-mesurer quand l'archive des stats joueur aura doublé ; le signe est bon.

---

## 1.0.0 — retirée le 2026-09-27

**Changement** : première version versionnée. Calcul identique à celui d'avant
le découpage en un modèle par événement.

### Ce que fait la version
- **Nombre attendu** : moteur Maher (voir [moteur.md](moteur.md)), lu dans les
  statistiques du match (`tirs_cadres`).
- **Loi** : binomiale négative, dispersion **1.396**.
- **Corrélation** entre les deux équipes : 0 (mesurée +0.010 ± 0.017, nulle).
- **Recalage** : 1.0. Biais mesuré de −0.40 sur 100 matchs, mais sans direction
  nette selon les tranches, donc non recalé.
- **Seuils** : par équipe 2.5 à 5.5 ; au total 5.5 à 9.5 ; mis en avant 7.5
  (côté « plus »).
- **Marché propre** : duel.

### Points forts
- **Le mieux calibré des quatre** sur les fiches émises : annoncé 81.9 %,
  observé 82.3 % (192 propositions, 44 matchs).
- **Lignes par équipe** : annoncé 83.3 %, observé 82.8 %.

### Points faibles
- **Duel nettement sous-annoncé** : annoncé 68.4 %, observé 86.4 %, écart
  **−18.0 points ± 7.8**, significatif (22 propositions, 18 matchs). Le modèle
  est trop prudent sur « plus de tirs cadrés pour X ».
- **Pente de 0.62** à k = 10 : l'estimation reste trop étalée, moins que pour
  les corners.

### Pistes pour la suite
- **Duel** : la grille du duel emploie la dispersion de base et ignore
  l'incertitude d'estimation. Hypothèse : elle écrase les écarts entre équipes.
  À tester en construisant le duel à partir des nombres attendus sans
  sur-dispersion, et en comparant en backtest apparié.

### Mesures en service
Pas encore de fiche tranchée en 1.0.0. Référence de départ (fiches antérieures) :

| Famille | Matchs | Props | Annoncé | Observé | Écart | ± | Brier |
|---|---|---|---|---|---|---|---|
| Toutes | 44 | 192 | 81.9 % | 82.3 % | −0.4 | 3.8 | 0.1455 |
| équipe | 44 | 99 | 83.3 % | 82.8 % | +0.4 | 3.9 | 0.1398 |
| total | 43 | 71 | 84.3 % | 80.3 % | +4.0 | 6.3 | 0.1547 |
| duel | 18 | 22 | 68.4 % | 86.4 % | **−18.0** | 7.8 | 0.1422 |
