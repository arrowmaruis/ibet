# Modèle des tirs cadrés — journal des versions

Fichier : `ibet/modeles/tirs_cadres.py`. Clé : `tirs_cadres`.

---

## 1.0.1 — en service depuis le 2026-09-27

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
