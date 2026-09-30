# Modèle des buts — journal des versions

Fichier : `ibet/modeles/buts.py`. Clé : `buts`.

Le modèle le plus riche : il reçoit le xG et les notes attaque / défense
globales, porte le facteur de Dixon et Coles, alimente le modèle de l'issue et
ouvre les marchés sur le couple des scores (écart, parité, cage inviolée,
combinés).

---

## 2.2.0 — en service depuis le 2026-09-30

**Changement** : dispersion **1,173 → 1,0** (loi de Poisson). Rien d'autre ne
change.
**Pourquoi** : sur les fiches émises (buts 2.x, 76 matchs), le modèle était
juste sur le niveau (total prévu 2,84, réel 2,86 ; pente 1,02) mais **trop
prudent sur « Équipe : plus de X buts »** : annoncé 77,3 %, réalisé **92,5 %**
(−15,2 ± 3,8 points, 53 propositions). À ce niveau de probabilité, c'est « l'équipe
marque au moins un but » : la loi prévoyait trop d'équipes à zéro. La dispersion
1,173 avait été mesurée sur le modèle de forme, avant les notes au maximum de
vraisemblance et le recalage du total sur le marché.
**Mesure avant adoption** : archive football-data.co.uk, nombres attendus
corrigés par les cotes 1X2 et plus / moins 2,5, dispersion choisie sur les
saisons jusqu'à 2022-23 (25 896 matchs), jugée sur 2023-24 et après (7 407) :

| Test (7 407 matchs) | 2.1.0 (1,173) | **2.2.0 (1,0)** |
|---|---|---|
| Log-vraisemblance par match | — | **+0,023** (t = +14,9) |
| Équipes à zéro but : prévu / observé | 30,0 % / 25,6 % | **27,3 %** / 25,6 % |
| Équipe « plus de » : annoncé → réalisé | 70,8 → 76,1 % | **71,4 → 73,8 %** |
| Équipe « moins de » | 73,7 → 73,1 % | 73,6 → 72,9 % |
| Total « plus de » | 71,9 → 76,0 % | **73,2 → 74,9 %** |
| Total « moins de » | 74,1 → 74,0 % | 74,1 → 73,2 % |

### Ce que fait la version
Celle de la 2.1.0, sous une loi de Poisson.

### Points faibles
- Les « plus » restent un peu prudents (−1,7 à −2,4 points sur l'archive).
- L'issue en dépend (même matrice des scores) : issue 1.3.0.

### Mesures en service
Pas encore de fiche tranchée en 2.2.0.

---

## 2.1.0 — retirée le 2026-09-30

**Changement** : quand la cote plus / moins 2,5 buts est relevée avant le match
(the-odds-api, consensus des opérateurs, marge de chacun retirée), le **total
attendu est recalé** pour que P(plus de 2,5) vaille 30 % celle du modèle et
70 % celle des bookmakers (`POIDS_MODELE_TOTAL`). L'écart entre les deux
équipes reste celui du modèle ; toutes les lignes de buts suivent.
**Pourquoi** : sur les totaux, le modèle est proche des bookmakers mais
derrière (Brier +2,5 : 0.2434 contre 0.2401 pour Bet365).
**Mesure avant adoption** : 1 784 matchs de 2026 appariés aux cotes plus /
moins 2,5 de Bet365 (football-data.co.uk), modèle rejoué en walk-forward :

| Ligne | Modèle seul | Probabilité +2,5 mélangée | **Total recalé, 30 %** | t |
|---|---|---|---|---|
| +1.5 | 0.1695 | 0.1695 | **0.1671** | −3.4 |
| +2.5 | 0.2434 | 0.2401 | **0.2400** | −3.0 |
| +3.5 | 0.2136 | 0.2136 | **0.2112** | −2.4 |
| +4.5 | 0.1287 | 0.1287 | 0.1281 | −1.1 |

Mélanger la seule probabilité +2,5 n'améliore que cette ligne ; recaler le
total améliore aussi 1,5 et 3,5, pour lesquelles aucune cote n'est relevée.

### Ce que fait la version
- Tout ce que fait la 2.0.0.
- `caler` : si `apports["totaux_marche"]` (P(+2,5) du consensus) est connu,
  recherche par dichotomie du total dont P(+2,5) vaut la cible, écart fixé ;
  trace `marche.total_marche` (P du modèle, P du marché, total avant / après).
- Relevé : `api_client.probabilite_plus_de_buts`, appelé par
  `context.collecter` **seulement pour un match à venir** et hors rétrospectif.
  Compétitions couvertes : celles de `CLES_AGREGATEUR` ; sans clé
  `ODDS_API_KEY`, rien ne change.

### Points faibles
- Couverture limitée aux compétitions de l'agrégateur (une quarantaine), et
  quota mensuel : un appel par compétition et par jour (deux crédits).
- Mesure faite contre Bet365 ; le consensus the-odds-api mélange d'autres
  opérateurs européens. À re-mesurer sur les fiches 2.1.0.

### Mesures en service
Pas encore de fiche tranchée en 2.1.0.

---

## 2.0.0 — retirée le 2026-09-28

**Changement** : les notes attaque / défense (`ibet/prevision/forces.py`) sont estimées au
**maximum de vraisemblance** sur tout le corpus, et non plus apprises match
après match par descente de gradient. Le total attendu est ensuite **resserré
de moitié** vers la moyenne de buts de la compétition.

**Pourquoi** : sur les 67 fiches tranchées avec cotes, le favori désigné
n'avait raison que 39 fois (marché : 50), Brier 1X2 0.5615 contre 0.4581 au
marché. Le modèle n'était pas mal calibré — il départageait mal les équipes :
avec un pas de 0.04 et une équipe médiane à 4 matchs dans le corpus, les notes
restaient tassées près de la moyenne (PSG – Slovan Bratislava : 55 % pour
Paris, marché 95 %). Côté totaux, la pente du total réel sur le total prévu
n'était que de 0.52 : les totaux prévus étaient deux fois trop étalés, d'où le
défaut « totaux trop sûrs » de la 1.0.0.

**Mesure avant adoption** (walk-forward, chaque match prévu avec les seules
données d'avant lui) :

| Mesure | 1.0.0 | 2.0.0 | t |
|---|---|---|---|
| Brier 1X2, 4 996 matchs de 2026 | 0.6014 | **0.5924** | −4.8 |
| Brier 1X2, 67 fiches émises (mêmes données) | 0.5332 | **0.4891** | — |
| Favori juste, 67 fiches (marché : 50) | 45 | **49** | — |
| Brier des seuils de total 1.5 / 2.5 / 3.5, 4 899 matchs | 0.2141 | **0.2087** | −7.8 |
| Props de total à 60-95 % : annoncé / observé | 73.6 / 71.2 | **72.2 / 72.7** | — |

Issue combinée aux cotes des bookmakers. Trois mesures successives, de plus
en plus larges ; seule la dernière a fixé la méthode.

1. **67 fiches émises avec cotes** : recaler l'écart des nombres attendus sur
   une issue à 80 % marché semblait battre le marché (0.4509 contre 0.4572).
2. **3 731 matchs de 2026, cotes moyennes de clôture de BetExplorer** : le gain
   disparaît ; toute part du modèle coûte (20 % : +0.0025, t = +3.0).
3. **1 784 matchs de 2026 appariés aux cotes de Bet365** (football-data.co.uk,
   appariement par date, noms et score final ; Pinnacle absent de ces fichiers
   depuis 2025-26) :

| Variante | Brier 1X2 | vs Bet365 | t | Favori juste |
|---|---|---|---|---|
| **Bet365 seul (avant-match)** | **0.5954** | — | — | **50.8 %** |
| Modèle seul | 0.6146 | +0.0192 | +5.4 | 48.5 % |
| **Mélange, 10 % modèle** *(retenu)* | 0.5958 | +0.0004 | +1.2 | 50.6 % |
| Mélange, 20 % modèle | 0.5965 | +0.0012 | +1.7 | 50.5 % |
| Écart recalé, 20 % modèle | 0.5982 | +0.0028 | +2.6 | 50.5 % |
| Écart recalé, 0 % modèle | 0.5972 | +0.0019 | +2.2 | 50.6 % |

Mêmes conclusions contre la clôture de Bet365 (1 817 matchs) et la moyenne des
bookmakers (1 863). Le poids du modèle choisi mois par mois sur les seuls mois
précédents vaut 0 de mars à septembre. Le mélange à 10 % garde une combinaison
sans perte mesurable. Recaler l'écart fait moins bien à tout poids : le nul de
la matrice est moins juste que celui du bookmaker.

Sur le **plus / moins de 2.5 buts**, le modèle est proche du bookmaker :
Bet365 0.2401, modèle 0.2434 (+0.0033, t = +2.1), mélange 10-20 % 0.2400
(−0.0001, non significatif). Contre Pinnacle (102 matchs), le modèle fait
0.2390 contre 0.2417, non significatif. Les totaux ne sont pas mélangés : leurs
cotes ne sont relevées qu'après l'émission, et le gain mesuré est nul.

De bout en bout sur les 84 fiches émises que les notes couvrent (fiches
réellement émises contre version 2.0.0 rejouée) : Brier 1X2 0.5880 → 0.5397,
favori juste 44 → 53.

### Ce que fait la version
- **Notes** : `forces.construire`, Maher / Dixon-Coles au maximum de
  vraisemblance, résolution coordonnée par coordonnée (12 passes). Poids
  `exp(−0.002 · âge en jours)` (demi-vie ≈ 1 an), a priori d'un match fictif à
  la moyenne par équipe, bornes ± 2 en logarithme (sécurité, inactives en
  pratique). Une équipe n'est « établie » qu'à 8 matchs **porteurs de la
  cible** : un match sans xG n'établit plus les notes de xG.
- **Total** : `forces.lambdas_attendus` resserre le total (xG si établis,
  sinon buts) vers la moyenne de la compétition,
  `total = réf + 0.5 · (total − réf)` ; `réf` = moyenne de buts de la
  compétition (nom normalisé) tirée vers la moyenne globale par 10 matchs
  fictifs, enregistrée dans `forces.json`.
- **Écart** : celui des notes sur les buts, inchangé.
- **Issue combinée aux cotes** (quand les cotes 1X2 sont connues à
  l'émission) : le 1X2 publié, et ses propositions (victoire, nul, double
  chance), est le mélange 10 % modèle / 90 % bookmaker, marge de Shin retirée
  (`POIDS_MODELE_ISSUE`). Les nombres de buts attendus ne changent pas, donc
  seuils, scores exacts, « les deux marquent » et combinés restent ceux du
  modèle. La trace `marche` (issue du modèle seul, du bookmaker) est dans la
  fiche ; le critère 13 reçoit l'issue du modèle seul.
- Le reste (dispersion 1.173, `rho = 0`, seuils, marchés propres) est celui de
  la 1.0.0.

### Points forts
- **Discrimination** : le gain vient d'équipes mieux départagées, pas de
  probabilités artificiellement tranchées — la calibration reste bonne (favori
  annoncé 64 %, observé 63 %).
- **Totaux** : le défaut principal de la 1.0.0 est corrigé sur le banc
  d'essai (écart annoncé − observé ramené de +2.4 à −0.5 point sur les
  propositions de total à 60-95 %).

### Points faibles
- **Couverture** : sous 8 matchs dans le corpus, une équipe n'a pas de notes et
  la fiche retombe sur le modèle de forme, nettement moins bon (6 fiches sur
  90). Le corpus ne grossit qu'avec les historiques consultés.
- **Encore derrière le marché** : 0.4891 contre 0.4581 sur les fiches avec
  cotes. Le marché connaît compositions, blessures et motivation.
- **Totaux sur les fiches** : sur les 84 fiches rejouées, l'erreur absolue sur
  le total passe de 1.312 à 1.342. Échantillon trop petit pour contredire les
  4 899 matchs du banc d'essai, mais à surveiller.
- **Loi des totaux** : Poisson (φ = 1) reproduit mieux la fréquence des 0-0
  que la dispersion 1.173 (7.5 % observés ; 8.3 % sous Poisson, 9.9 % sous
  1.173). Non changé dans cette version.

### Pistes pour la suite
- **Dispersion** : re-mesurer 1.173 contre 1.0 maintenant que les nombres
  attendus sont resserrés (log-vraisemblance du score exact : 2.9597 contre
  2.9525 à φ = 1 sur le banc d'essai).
- **Poids du modèle dans l'issue** (`POIDS_MODELE_ISSUE = 0.1`) : la mesure
  hors échantillon le donne à 0 ; 10 % est gardé pour la combinaison. À
  re-mesurer contre les cotes relevées à l'émission, et non à la clôture.
- **Cohérence de la fiche** : quand les cotes existent, le 1X2 publié ne se lit
  plus exactement sur la matrice des scores (la victoire à 70 % peut côtoyer des
  scores exacts calculés sur une victoire à 55 %). C'est le prix d'une issue
  plus juste ; les deux restent dans la fiche.
- **Fiches sans cotes** : le mélange n'agit pas ; leur issue reste celle du
  modèle seul, nettement moins bonne. Relever les cotes plus souvent à
  l'émission est le levier le plus direct.
- **Valeur des paris** : une issue à 80 % marché ne peut plus signaler de
  « valeur » contre ce même marché ; le critère 13 garde l'issue du modèle seul
  pour cela.

### Mesures en service
Pas encore de fiche tranchée en 2.0.0.

---

## 1.0.0 — retirée le 2026-09-27

**Changement** : première version versionnée. Calcul identique à celui d'avant
le découpage en un modèle par événement.

### Ce que fait la version
- **Nombre attendu** : moteur Maher (voir [moteur.md](moteur.md)), lu au score.
- **xG (critère 11, avec contexte seulement)** : mélange
  `λ = (1 − w) · λ_buts + w · λ_xG`, avec `w` le poids du critère (plancher),
  relevé quand l'échantillon est court si `xg_echantillon > 0` (0 en service).
- **Notes attaque / défense globales** (`forces.lambdas_attendus`) : quand elles
  existent, elles **remplacent** le modèle (`POIDS_MODELE = 0`), total et écart
  compris. Le modèle de forme reste le repli (environ 14 % des fiches) et le
  porteur des corrections de contexte.
- **Loi** : binomiale négative, dispersion **1.173**. Corrélation entre les deux
  équipes : 0 (mesurée −0.008 ± 0.010, nulle). Recalage : 1.0 (biais mesuré
  −0.02).
- **Dixon et Coles** : branché, `rho = 0` en service.
- **Seuils** : par équipe 0.5 à 3.5 ; au total 0.5 à 5.5 ; mis en avant 2.5.
- **Marchés propres** : écart de 2 ou 3 buts, pair / impair, cage inviolée,
  combinés « les deux marquent et plus de 1.5 / 2.5 », « victoire et plus de
  1.5 / 2.5 ».

### Points forts
- **Estimation bien étalée** : pente du réel sur le prévu 0.94 à k = 10, la
  meilleure des quatre grandeurs.
- **Notes globales** : erreur absolue sur le total 1.437 → 1.396 (t = −5.9,
  5 360 matchs). Retirer le modèle de forme au profit des notes est confirmé par
  deux mesures indépendantes (walk-forward : 100 % modèle coûte +0.0280 de
  Brier, t = +11.7 ; fiches tranchées : 0.5949 contre 0.6350).
- **Lignes par équipe** : annoncé 89.5 %, observé 93.0 % (71 propositions,
  44 matchs). Plutôt prudentes.

### Points faibles
- **Totaux trop sûrs** : annoncé 90.1 %, observé 81.8 %, écart **+8.3 points
  ± 4.0**, significatif (77 propositions, 44 matchs). C'est le plus gros défaut
  mesuré du modèle.
- **Fourchettes** : 5 propositions seulement, 2 réussies sur 5 pour 67 %
  annoncés. Trop peu pour conclure, à surveiller.
- **Repli de forme peu discriminant** : sans notes globales, le modèle
  n'atteignait 50 % de certitude que 7 fois sur 26 fiches, là où le marché va
  jusqu'à 90 %.
- **Dixon et Coles inexploité** : `rho = 0`, faute de gain mesurable sur
  l'échantillon disponible. Les petits scores (0-0, 1-1) sont donc traités
  comme indépendants.

### Pistes pour la suite
- **Totaux** : re-mesurer sur les fiches 1.0.0. Si l'écart persiste, tester une
  dispersion du total plus large que celle des équipes, ou un recalage des
  seules probabilités de total, en backtest apparié.
- **Dixon et Coles** : `--regler rho=-0.05,-0.1` sur le corpus actuel, bien plus
  large que celui du premier réglage.
- **Mélange xG selon l'échantillon** : `--regler xg_echantillon=3,6`.
  Hypothèse : améliorer à la fois l'issue et les propositions.

### Mesures en service
Pas encore de fiche tranchée en 1.0.0. Référence de départ (fiches antérieures) :

| Famille | Matchs | Props | Annoncé | Observé | Écart | ± | Brier |
|---|---|---|---|---|---|---|---|
| Toutes | 44 | 153 | 89.1 % | 85.6 % | +3.5 | 2.8 | 0.1170 |
| équipe | 44 | 71 | 89.5 % | 93.0 % | −3.4 | 3.1 | 0.0645 |
| total | 44 | 77 | 90.1 % | 81.8 % | **+8.3** | 4.0 | 0.1528 |
| fourchette | 5 | 5 | 67.4 % | 40.0 % | +27.4 | 24.2 | 0.3095 |
