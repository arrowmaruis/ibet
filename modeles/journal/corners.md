# Modèle des corners — journal des versions

Fichier : `modeles/corners.py`. Clé : `corners`.

---

## 1.0.0 — en service depuis le 2026-09-27

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
