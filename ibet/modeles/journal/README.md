# Journal des versions des modèles

Chaque modèle d'événement a une **version**, inscrite dans chaque fiche émise.
Ce dossier en tient l'histoire : ce que chaque version fait, ce qu'elle réussit,
ce qu'elle rate, et ce qu'on a appris en la mesurant.

| Fichier | Modèle | Version en service |
|---|---|---|
| [moteur.md](moteur.md) | Socle commun : estimation Maher, lois, sélection des offres | 1.0.0 |
| [buts.md](buts.md) | Buts | 2.0.0 |
| [issue.md](issue.md) | Issue du match : 1X2, double chance, les deux marquent, scores exacts | 1.1.0 |
| [corners.md](corners.md) | Corners | 1.0.0 |
| [tirs_cadres.md](tirs_cadres.md) | Tirs cadrés | 1.0.0 |
| [cartons_jaunes.md](cartons_jaunes.md) | Cartons jaunes : arbitre, joueurs, entraîneur | 2.0.0 |
| [xg.md](xg.md) | Buts attendus (auxiliaire, jamais parié) | 1.0.0 |

La version en service se lit dans le code (`version = "…"` dans la classe du
modèle, `VERSION_MOTEUR` dans `ibet/modeles/__init__.py`) et avec `python -m ibet etude`.

## Numérotation : `MAJEURE.MINEURE.CORRECTIF`

| Incrément | Quand | Exemple |
|---|---|---|
| **MAJEURE** | La méthode change : autre loi, autre estimateur, nouvelle source de données | passer de la binomiale négative à un modèle bivarié pour les corners |
| **MINEURE** | Même méthode, un paramètre re-mesuré ou un marché ajouté | dispersion des corners re-mesurée de 1.615 à 1.55 |
| **CORRECTIF** | Correction d'un défaut, sans changement voulu des probabilités | un libellé mal formé, un cas limite qui plantait |

**Une modification qui change une seule probabilité impose un nouveau numéro.**
Sinon, deux fiches aux chiffres différents porteraient la même version, et
l'étude les mélangerait.

Une modification du **moteur commun** (`estimation.py`, `lois.py`, `offres.py`,
`reglages.py`) incrémente `VERSION_MOTEUR`, pas les versions des modèles. Elle
touche toutes les grandeurs à la fois, et `python -m ibet etude --moteur` la lit
comme telle.

## Publier une nouvelle version

1. **Mesurer avant de changer** : `python -m ibet --backtest` sur la version en
   service, puis sur la version candidate, avec les **mêmes matchs**. C'est la
   seule comparaison appariée ; l'étude des fiches émises vient ensuite confirmer
   sur des matchs que personne n'avait vus.
2. Modifier le modèle, incrémenter `version` dans sa classe.
3. Ajouter une section en tête du journal du modèle, en suivant le modèle
   ci-dessous : ce qui change, pourquoi, la mesure qui l'a justifié, les points
   forts et faibles attendus.
4. Marquer la version précédente « retirée » avec sa date.
5. `python -m ibet tests` : un test vérifie que chaque version en service a sa
   section dans le journal.
6. Après une trentaine de matchs tranchés, `python -m ibet etude --modele <cle>` :
   reporter les chiffres observés dans la section « Mesures en service ».

## Gabarit d'une section de version

```markdown
## 1.1.0 — en service depuis le AAAA-MM-JJ

**Changement** : ce qui diffère de la version précédente, en une ou deux phrases.
**Pourquoi** : le défaut mesuré qui l'a motivé (chiffres, échantillon).
**Mesure avant adoption** : backtest apparié, ancienne contre nouvelle (Brier, log-loss, t).

### Ce que fait la version
Méthode et paramètres, avec leur valeur.

### Points forts
- …, mesuré sur … (chiffres).

### Points faibles
- …, mesuré sur … (chiffres).

### Pistes pour la suite
- hypothèse à tester, et comment la tester.

### Mesures en service
| Période | Matchs | Props | Annoncé | Observé | Écart | Brier |
|---|---|---|---|---|---|---|
```

## Lire une étude sans se tromper

- **Compter les matchs, pas les propositions.** Une fiche porte une douzaine de
  propositions tranchées par le même résultat ; l'erreur type de `ibet/evaluation/etude.py` est
  groupée par match pour cette raison.
- **Sous une trentaine de matchs**, l'écart entre deux versions est presque
  toujours dans le bruit.
- **Deux versions n'ont pas vu les mêmes matchs.** Une version en service
  pendant une trêve internationale ne se compare pas directement à une version
  en service en plein championnat : c'est le rôle du backtest apparié.
- **« antérieure »** désigne les fiches émises avant le versionnage (septembre
  2026). Ce n'est pas la version 1.0.0 : plusieurs réglages ont changé pendant
  cette période. Elles servent de point de départ, pas de version à battre.
