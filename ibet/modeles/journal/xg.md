# Modèle des buts attendus (xG) — journal des versions

Fichier : `ibet/modeles/xg.py`. Clé : `xg`. **Auxiliaire** : estimé, jamais parié.

Ce modèle ne produit aucune proposition. Il sert deux autres modèles :
- le **contexte** (critère 11), qui confronte le nombre de buts attendu au xG ;
- le **modèle des buts**, qui mélange son estimation à celle du xG.

Il ne s'étudie donc pas avec `ibet/evaluation/etude.py` : son effet se lit dans les versions des
buts et de l'issue, et se mesure en backtest avec et sans contexte.

---

## 1.0.0 — en service depuis le 2026-09-27

**Changement** : première version versionnée. Calcul identique à celui d'avant
le découpage en un modèle par événement.

### Ce que fait la version
- **Nombre attendu** : moteur Maher (voir [moteur.md](moteur.md)), lu dans les
  statistiques du match (`xg`), avec la même référence de compétition et la même
  régularisation que les buts.
- Calculé **seulement avec contexte**.

### Points forts
- **Moins bruité que les buts** : la variance du xG par match est environ trois
  fois plus faible que celle des buts.

### Points faibles
- **Biais constant** : le xG ne voit pas la finition. Il mesure la même
  production offensive que les buts, mais ce n'est pas la même grandeur.
- **Mélange à poids fixe** : à 0.50 et 0.75, il améliorait les propositions mais
  dégradait l'issue.

### Pistes pour la suite
- **Poids selon l'échantillon** (`xg_echantillon`) : le mécanisme est en place,
  réglé à 0. Hypothèse : le xG doit peser lourd sur 3 matchs et peu sur 30. À
  valider par `--regler xg_echantillon=...`.
