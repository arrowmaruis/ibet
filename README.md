# iBET — prévision statistique des matchs de football

iBET estime, pour chaque match, la probabilité de dizaines d'événements : issue,
nombre de buts, corners, tirs cadrés, cartons jaunes, seuil par seuil et équipe
par équipe. Chaque probabilité publiée est ensuite **confrontée au résultat réel** :
un modèle qui n'est pas mesuré n'est qu'une opinion mise en forme.

Trois règles gouvernent tout le projet :

1. **Des méthodes publiées, pas de formule maison** : Maher (1982), Dixon & Coles
   (1997), estimateurs à rétrécissement (Efron & Morris 1975).
2. **Aucun réglage sans mesure** : chaque paramètre est choisi sur des matchs
   anciens et jugé sur des matchs plus récents, qu'aucun réglage n'a vus.
3. **Un modèle par événement, versionné** : chaque version porte ses mesures,
   ses points forts et ses points faibles dans
   [`ibet/modeles/journal/`](ibet/modeles/journal/).

---

## Les chiffres clés

Sur les **166 fiches émises puis tranchées** entre le 6 et le 30 septembre 2026,
soit **2 895 propositions** :

| | Annoncé | Réalisé | Écart | Brier |
|---|---|---|---|---|
| **Toutes les propositions** | 78,4 % | **79,8 %** | −1,3 ± 0,9 | 0,156 |

Quand iBET annonce 78 %, l'événement se produit 80 fois sur 100. Le modèle est
**calibré**, et plutôt prudent que présomptueux. Le détail par grandeur et par
tranche est dans [Performances](#performances).

---

## L'algorithme

```
  historique des deux équipes
            │
   1. Force des équipes ─────────── attaque / défense, maximum de vraisemblance
            │
   2. Nombre attendu par événement ─ buts, corners, tirs cadrés, cartons (λ)
            │
   3. Apports propres à l'événement ─ cotes du marché, styles des joueurs,
            │                          arbitre…
   4. Contexte ───────────────────── quatorze critères, bornés et affichés
            │
   5. Loi de comptage ────────────── Poisson, binomiale négative ou binomiale
            │
   6. Propositions ───────────────── lignes du match, familles de paris, 60-95 %
```

### 1. Force des équipes

C'est le modèle attaque / défense de **Maher (1982)**, dans la forme de **Dixon &
Coles (1997)** :

```
λ_dom = exp(μ + h + attaque[dom] − défense[ext])
λ_ext = exp(μ     + attaque[ext] − défense[dom])
```

Toutes les notes sont estimées **en même temps, au maximum de vraisemblance**,
sur l'ensemble des matchs connus. Chaque passe résout exactement une note, les
autres étant fixées :

```
exp(attaque[i]) = (buts marqués + a_priori) / (buts attendus + a_priori)
```

- **Oubli** : chaque match pèse `exp(−0,002 × âge en jours)`. Un match vieux d'un
  an pèse deux fois moins qu'un match d'hier.
- **A priori** : chaque équipe part d'un match fictif joué exactement à la
  moyenne. Une équipe vue deux fois ne peut donc pas recevoir une note extrême.
- **Échelle commune** : les notes sont comparables d'une compétition à l'autre,
  si bien qu'un 1,8 but par match en troisième division ne vaut pas un 1,8 but en
  première.
- **Seuil** : une équipe n'est « établie » qu'à partir de 8 matchs. En dessous, la
  fiche retombe sur le modèle de forme (étape 2).

**Total resserré.** Les notes étalent trop les totaux : la pente du total réel
sur le total prévu n'était que de 0,52. Le total attendu est donc resserré de
moitié vers la moyenne de la compétition, sans toucher à l'écart entre les deux
équipes :

```
total = moyenne_compétition + 0,5 × (total − moyenne_compétition)
```

### 2. Nombre attendu par événement

Corners, tirs cadrés et cartons passent par le même schéma de Maher, avec leur
propre référence de compétition :

```
Attaque_i   = (événements produits par i et par match)  / moyenne de la compétition
Défense_j   = (événements concédés par j et par match)  / moyenne de la compétition
λ_dom       = Attaque_dom × Défense_ext × moyenne à domicile
```

Deux corrections rendent l'estimation robuste sur peu de matchs :

- **Niveau des adversaires.** Chaque match est rapporté à la force de
  l'adversaire rencontré : trois buts contre la meilleure défense ne valent pas
  trois buts contre la pire.
- **Régularisation** (*empirical Bayes*). Sur cinq matchs, un rapport de 1,5
  tient autant du bruit que du signal, et le modèle en multiplie deux. Les forces
  sont donc ramenées vers 1 :

  ```
  force_ajustée = (n × force_observée + k) / (n + k),   k = 22 (mesuré)
  ```

  Pour Hoffenheim - Dortmund sur cinq matchs, la victoire extérieure passe de
  84 % sans régularisation à 46 % avec. Aucun marché ne descendait sous 55-60 %
  sur ce type d'affiche.

### 3. Un modèle par événement

Chaque grandeur a son modèle, sa version et ses constantes, toutes mesurées :

| Modèle | Version | Loi | Dispersion | Corrélation entre les deux équipes |
|---|---|---|---|---|
| Buts | 2.1.0 | binomiale négative | 1,173 | 0 |
| Issue (1X2, double chance, les deux marquent, scores exacts) | 1.2.0 | matrice jointe des buts | — | — |
| Corners | 3.0.0 | binomiale négative | 1,615 | −0,149 |
| Tirs cadrés | 2.2.0 | binomiale négative | 1,10 | 0 |
| Cartons jaunes | 3.1.0 | **binomiale** | 0,847 | +0,083 |
| xG (auxiliaire, jamais parié) | 1.0.0 | Poisson | 1,0 | 0 |

La **dispersion** est le rapport variance / moyenne, mesuré sur des milliers de
matchs. À 1, la loi de Poisson est exacte. Au-dessus, on passe à une binomiale
négative de même moyenne. En dessous, à une binomiale : c'est le cas des cartons,
distribués avec une régularité remarquable.

La **corrélation** ne change pas les lois par équipe, seulement celle du total :

```
Var(total) = φ·λ_dom + φ·λ_ext + 2·ρ·√(φ·λ_dom · φ·λ_ext)
```

Les corners sont en partie à somme nulle : l'équipe qui domine en obtient au
détriment de l'autre, d'où ρ < 0 et un total plus resserré. Les cartons vont
ensemble : un match tendu en donne aux deux, d'où ρ > 0 et un total plus large.

Ce que chaque modèle ajoute au nombre attendu :

- **Buts 2.1.0.** Quand la cote plus / moins 2,5 buts est connue avant le match,
  le total attendu est recalé pour que P(plus de 2,5) vaille 30 % celle du modèle
  et 70 % celle du marché. Toutes les lignes de buts suivent, et l'écart entre les
  équipes reste celui du modèle.
- **Issue 1.2.0.** Toutes les probabilités (issue, seuils, scores exacts) sont
  sommées sur **une seule matrice jointe** des scores, ce qui les rend
  cohérentes entre elles. Quand les cotes 1X2 sont connues, le 1X2 publié est un
  mélange à **10 % modèle et 90 % marché**, marge retirée par la méthode de Shin :
  sur l'issue, le marché est plus juste que le modèle (voir
  [Performances](#face-au-marché)).
- **Corners 3.0.0.**
  - *Correction par le marché.* Une régression de Poisson corrige le nombre
    attendu par les probabilités 1X2 du marché, car le favori obtient les
    corners de sa domination :

    ```
    log λ' = a + b·log λ + c·(p_équipe − p_adversaire)
    domicile : a = 0,619   b = 0,588   c = 0,437
    extérieur : a = 0,540   b = 0,670   c = 0,395
    ```

  - *Styles des joueurs.* Un facteur « onze aligné / onze habituel », calculé
    joueur par joueur (taux par 90 minutes, lissé vers le taux du poste), ajuste
    le nombre attendu au poids 0,25.
- **Tirs cadrés 2.2.0.** Même correction par le marché, en utilisant le 1X2 et
  le plus / moins 2,5 buts. La dispersion est ramenée de 1,396 à 1,10, puisque les
  cotes expliquent une part de la variance attribuée jusque-là au hasard.
- **Cartons jaunes 3.1.0.**
  - *Profil de l'arbitre.* Les cartons qu'il donne sont rapportés à ceux que ses
    équipes auraient dû prendre. Sans cette référence, l'arbitre des derbys
    passerait pour sévère.
  - *Point de départ du profil.* Il part de l'historique de l'arbitre sur les
    saisons précédentes, et non de la moyenne. Il est lissé par 40 cartons
    attendus fictifs et appliqué aux deux équipes à la puissance 0,5.
  - *Arbitre inconnu ou mal connu.* Ce qu'on ignore de lui est un facteur commun
    aux deux équipes : sa variance élargit la loi du total.
  - *Recalage.* Les nombres attendus sont multipliés par 0,97.
  - *Joueurs et entraîneur.* L'onze aligné face à l'onze habituel, et le profil
    d'un entraîneur récemment arrivé, sont calculés et affichés à poids nul :
    la mesure ne les a pas validés.
- **xG.** Une seconde estimation du nombre de buts, moins bruitée (variance
  environ trois fois plus faible), mélangée à 25 % au modèle des buts.

### 4. Contexte : quatorze critères

Le modèle ne connaît que des comptages passés. Tout ce qui les entoure est
rassemblé en quatorze critères, relevés à l'émission et enregistrés avec la
fiche. Trois principes :

1. **Chaque critère est indépendant et faillible.** Un critère absent vaut
   neutre, jamais zéro, et n'empêche ni les autres ni la prévision.
2. **Aucune correction n'est appliquée sans être affichée.** La fiche garde le
   multiplicateur exact de chaque critère et le nombre attendu d'avant le contexte.
3. **Les corrections sont bornées.** Le produit est plafonné par grandeur (×1,12
   sur les buts et les tirs, ×1,15 sur les corners, ×1,25 sur les cartons) :
   quatorze facteurs à 3 % dans le même sens feraient ×1,5.

| # | Critère | Poids en service |
|---|---|---|
| 1 | Style de jeu : le *choc* des deux styles (possession, bloc bas, fautes) | **1** |
| 2 | Forme récente contre forme d'ensemble | 0 |
| 3 | Système et effectif : absences pondérées par poste | 0 |
| 4 | Adversaires de style comparable (repondération de l'historique) | 0 |
| 5 | Confrontations directes | 0 |
| 6 | Enjeu de la compétition | 0 |
| 7 | Domicile / extérieur | *dans le modèle* |
| 8 | Fatigue et calendrier : repos, densité | **1** |
| 9 | Motivation : tension mesurée | 0 |
| 10 | Météo à l'heure du coup d'envoi | 0 |
| 11 | xG, seconde estimation des buts | **0,25** |
| 12 | Arbitre (critère d'affichage : l'arbitre est désormais dans le modèle des cartons) | 0 |
| 13 | Cotes du marché : écart au consensus, valeur espérée | *affiché seulement* |
| 14 | Taille de l'échantillon : score de confiance par grandeur | *affiché seulement* |

Un critère à zéro est calculé et affiché, sans rien déplacer. C'est le sort des
critères que la mesure n'a pas soutenus, ou qui n'ont pas encore pu être mesurés.

### 5. Des probabilités aux propositions

- **Lignes du match.** La ligne principale est celle dont la probabilité de
  dépassement est la plus proche de 50 %, comme le ferait un bookmaker. L'échelle
  garde quelques lignes de part et d'autre.
- **Familles de paris** : total, par équipe, fourchette, duel, issue, double
  chance, les deux marquent, écart, parité, cage inviolée, combiné. Toutes sont
  calculées sur la même loi, donc cohérentes : pair + impair = 1, un combiné ne
  dépasse jamais la moins probable de ses conditions, etc.
- **Sélection.** Sont retenues les propositions entre **60 et 95 %**, au plus
  **deux par famille**, pour ne pas présenter « moins de 3,5 », « moins de 4,5 » et
  « moins de 5,5 » comme trois choix distincts. Une proposition n'est retenue que si
  **trois opérateurs au moins** la paieraient 1,15 ou plus, compte tenu de leur
  marge mesurée : une évidence payée 1,01 n'a rien à faire sur une fiche.
- **Tranchable.** Chaque libellé se juge après coup à partir du seul score ou de
  la seule statistique du match. C'est vérifié par les tests.

### 6. Comment le modèle est mesuré

- **Coupure temporelle (*walk-forward*).** Chaque match rejoué n'est prévu
  qu'avec les données d'avant son coup d'envoi.
- **Réglage et verdict séparés.** Les réglages sont choisis sur les matchs les
  plus anciens (60 %) et jugés **une seule fois** sur les plus récents (40 %). Un
  gain qui n'existe que là où on l'a réglé n'en est pas un.
- **Comparaisons appariées.** Deux variantes sont comparées sur les mêmes matchs,
  avec une erreur type groupée par match : une fiche porte une douzaine de
  propositions tranchées par le même résultat.
- **Mesures.** Score de Brier, log-vraisemblance, et calibration (annoncé contre
  réalisé, par tranche et par sens).
- **Fiches figées.** Une fiche émise est datée et ne se réécrit pas. Les versions
  de tous les modèles y sont inscrites, pour que chaque résultat soit attribué à
  la version qui l'a produit.

---

## Performances

### Sur les fiches réellement émises

166 fiches tranchées, 2 895 propositions, du 6 au 30 septembre 2026. La plupart
ont été émises avant les versions actuelles des modèles : ces chiffres jugent le
système tel qu'il a tourné, pas uniquement la dernière version.

| Grandeur | Propositions | Fiches | Annoncé | Réalisé | Écart | Brier |
|---|---|---|---|---|---|---|
| Buts | 923 | 166 | 80,6 % | 84,0 % | −3,3 ± 1,3 | 0,129 |
| Cartons jaunes | 592 | 111 | 78,3 % | 80,2 % | −1,9 ± 1,8 | 0,153 |
| Corners | 690 | 127 | 77,4 % | 76,4 % | +1,0 ± 2,1 | 0,177 |
| Tirs cadrés | 690 | 127 | 76,7 % | 77,1 % | −0,4 ± 2,0 | 0,174 |
| **Toutes** | **2 895** | **166** | **78,4 %** | **79,8 %** | **−1,3 ± 0,9** | **0,156** |

Par tranche de probabilité annoncée :

| Tranche | Propositions | Annoncé | Réalisé |
|---|---|---|---|
| 60-70 % | 506 | 66,0 % | 70,9 % |
| 70-80 % | 1 197 | 75,3 % | 75,6 % |
| 80-90 % | 841 | 84,6 % | 85,4 % |
| 90-100 % | 351 | 92,3 % | 93,2 % |

Au-dessus de 70 %, l'annoncé et le réalisé coïncident à moins d'un point. Entre
60 et 70 %, le modèle est **trop prudent** de 5 points : ce qu'il annonce à 66 %
arrive 71 fois sur 100.

### Sur les bancs d'essai

Toutes ces mesures sont en *walk-forward*, sur des matchs rejoués.

**Force des équipes** (Brier du 1X2, plus bas = mieux) :

| Modèle | Matchs | Brier |
|---|---|---|
| Uniforme (⅓ - ⅓ - ⅓) | 5 360 | 0,6667 |
| Forme récente seule | 5 360 | 0,6196 |
| Elo | 5 360 | 0,6047 |
| **Attaque / défense** | 5 360 | **0,5916** (t = −11,7 contre la forme) |
| Notes apprises en ligne → **maximum de vraisemblance** | 4 996 | 0,6014 → **0,5924** (t = −4,8) |

#### Face au marché

Sur **1 784 matchs de 2026** appariés aux cotes d'avant-match d'un grand
opérateur :

| | Brier 1X2 | Favori juste |
|---|---|---|
| Modèle seul | 0,6146 | 48,5 % |
| **Marché seul** | **0,5954** | **50,8 %** |
| Mélange à 10 % modèle *(publié)* | 0,5958 | 50,6 % |

**Sur l'issue, le modèle seul ne bat pas le marché**, qui connaît les
compositions, les blessures et la motivation. C'est pourquoi le 1X2 publié est à
90 % celui du marché. Sur le plus / moins de 2,5 buts, en revanche, le modèle est
proche du marché (0,2434 contre 0,2401), et le recalage partiel du total améliore
toutes les lignes :

| Ligne de buts | Modèle seul | **Total recalé** | t |
|---|---|---|---|
| plus de 1,5 | 0,1695 | **0,1671** | −3,4 |
| plus de 2,5 | 0,2434 | **0,2400** | −3,0 |
| plus de 3,5 | 0,2136 | **0,2112** | −2,4 |

**Totaux de buts** (4 899 matchs), resserrement vers la moyenne : Brier des
seuils 0,2141 → **0,2087** (t = −7,8). Propositions de total à 60-95 % : annoncé
73,6 % / réalisé 71,2 % avant, **72,2 % / 72,7 %** après.

**Corners** :

| Variante | Réglage / verdict | Log-vraisemblance | t | Pente réel / prévu (cible 1) |
|---|---|---|---|---|
| Référence | — | — | — | 0,78 |
| Styles des joueurs alignés (2.0.0) | 1 390 / 928 matchs | +0,0074 | +2,7 | — |
| **Correction par le marché (3.0.0)** | 22 988 / 7 406 matchs | **+0,0688** | **+16,0** | **1,08** |

Le gain du marché vaut dans les sept championnats mesurés, de +0,040 à +0,10.

**Tirs cadrés 2.2.0** (verdict sur 7 406 matchs), dispersion 1,396 → 1,10 :
log-vraisemblance **+0,027** (t = +11,1).

| | Avant | **Après** |
|---|---|---|
| « Plus de » : annoncé → réalisé | 71,6 → 75,0 % | **72,2 → 72,2 %** |
| « Moins de » : annoncé → réalisé | 71,0 → 72,2 % | **71,9 → 72,2 %** |

**Cartons jaunes** (réglage sur 1 573 matchs, verdict sur les 1 049 plus récents,
chaque version contre la précédente) :

| Version | Apport | Log-vraisemblance | t |
|---|---|---|---|
| 2.0.0 | arbitre + variance de l'arbitre | +0,0112 | +2,3 |
| 3.0.0 | historique de l'arbitre sur les saisons précédentes | +0,0018 | +0,5 |
| 3.1.0 | recalage ×0,97 | +0,0087 | +2,0 |

L'historique de l'arbitre ne gagne presque rien sur les matchs les plus récents,
où l'arbitre est déjà connu par la saison précédente. Il gagne nettement quand
l'arbitre est mal connu : +0,0228 (t = +4,3) sur 1 035 matchs de la saison
2025-26.

Avec la 3.1.0, les « plus de X cartons » passent de +4,3 à **+2,5 points**
d'excès de confiance, et les « moins de » de −2,2 à **−1,0**.

**Critères de contexte** (80 matchs de huit grands championnats, 12 898
propositions, log-loss, signe négatif = mieux) :

| Réglage | Propositions | t | Retenu |
|---|---|---|---|
| **Style + xG + fatigue** | **−0,00279** | **−8,7** | **oui** |
| Style seul | −0,00200 | −7,2 | oui |
| xG à 0,25 | −0,00064 | −4,0 | oui |
| Fatigue | −0,00017 | −3,8 | oui |
| Météo | +0,00039 | +6,1 | non |
| Confrontations directes | +0,00121 | +4,1 | non |
| Similarité des adversaires | −0,00007 | −0,5 | non |
| Forme récente | +0,00007 | +1,7 | non |

Quatre critères parfaitement défendables sur le papier **dégradent** la
prévision. C'est la raison d'être de la règle « aucun réglage sans mesure ».

### Limites connues

- **Le marché reste meilleur sur l'issue.** Le modèle n'apporte rien de mesurable
  au 1X2 d'un grand opérateur.
- **Historiques courts.** Sous 8 matchs, une équipe n'a pas de notes. Les
  sélections nationales et les équipes de jeunes, dont les amicaux sont écartés,
  sont les moins bien servies.
- **Cartons.** Les « plus de X cartons au total » promettent encore un peu trop
  (+3,5 points au test).
- **Compositions et arbitre** ne sont connus qu'environ une heure avant le coup
  d'envoi. Une fiche émise la veille ne les a pas : le planificateur la réémet
  30 à 90 minutes avant le match.
- **Bancs d'essai approchés.** Plusieurs mesures portent sur une reconstruction
  du modèle rejoué, pas sur le moteur en service à l'identique. Les fiches
  tranchées de chaque version viennent les confirmer ou les infirmer.

---

## Lancer

```bash
python -m ibet tests      # les tests (sans réseau)
python -m ibet emettre    # émet et enregistre les prévisions des matchs à venir
python -m ibet verifier   # tranche les fiches dont le match est terminé
python -m ibet etude      # compare ce que chaque version de chaque modèle a donné
python -m ibet serveur    # API et interface web (port 8000)
```

`python -m ibet --help` liste toutes les commandes.

## Références

- M. J. Maher (1982). *Modelling association football scores*. Statistica
  Neerlandica 36(3).
- M. J. Dixon & S. G. Coles (1997). *Modelling association football scores and
  inefficiencies in the football betting market*. Applied Statistics 46(2).
- B. Efron & C. Morris (1975). *Data analysis using Stein's estimator and its
  generalizations*. JASA 70.
- H. S. Shin (1993). *Measuring the incidence of insider trading in a market for
  state-contingent claims*. The Economic Journal 103.
- G. W. Brier (1950). *Verification of forecasts expressed in terms of
  probability*. Monthly Weather Review 78.
