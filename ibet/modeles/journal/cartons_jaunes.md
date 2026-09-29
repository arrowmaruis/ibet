# Modèle des cartons jaunes — journal des versions

Fichier : `ibet/modeles/cartons.py`. Clé : `cartons_jaunes`.

---

## 3.0.0 — en service depuis le 2026-09-27

**Changement** : le profil de l'arbitre désigné ne part plus d'un a priori
neutre (« arbitre moyen ») mais de son **historique** sur worldfootball.net :
jaunes donnés, rapportés à la moyenne de chaque compétition, sur les saisons
**antérieures** à celle du match. Les feuilles de match affinent ensuite, comme
en 2.0.0. Nouvelle source de données, donc version majeure.
**Pourquoi** : en 2.0.0, les feuilles ne connaissent en médiane que 9 matchs
par arbitre — trop peu pour distinguer un arbitre sévère d'un coulant. La base
worldfootball, relevée par une session précédente puis laissée sans usage, en
connaît le double.
**Mesure avant adoption** : `python -m ibet mesurer-cartons arbitre=0.5
variance_arbitre=1 joueurs=0 entraineur=0`, variantes « historique », comparées
match par match à la 2.0.0. Réglage par défaut **fixé avant la mesure** (lissage
20, poids 0, puissance 0,5) :

| Matchs rejoués | Gain par match vs 2.0.0 | t |
|---|---|---|
| 60 % anciens (1 573) | **+0,0141** | +4,5 |
| 40 % récents (1 049) | +0,0018 | +0,5 |
| saison 2025-26, arbitre dans l'historique (1 035) | **+0,0228** | +4,3 |
| saison 2026-27, arbitre dans l'historique (311) | +0,0015 | +0,2 |

Le réglage le mieux classé sur les anciens matchs (lissage 10, poids 0,5,
puissance 1 : +0,0196) **perdait** sur les récents (−0,0080, t = −0,8) : il
apprenait les anciens matchs plutôt que les arbitres. Il n'est pas retenu.

### Ce que fait la version
- **Données** : `python -m ibet arbitres` relève les tableaux d'arbitres de
  worldfootball.net — 25 compétitions (coupes d'Europe, deux ou trois divisions
  d'Angleterre, d'Espagne, d'Italie, d'Allemagne, de France, des Pays-Bas ;
  Portugal, Turquie, Écosse, Autriche, Suisse, Grèce, Danemark, Pologne,
  Russie), saisons 2023-24 à 2025-26. Une ligne par arbitre, compétition et
  **saison**, dans la table `arbitres_saisons` de `ibet.db` : 2 242 lignes,
  678 arbitres, dont 368 vus au moins 20 fois. La source ne publie pas encore
  la saison 2026-27 (404) : les arbitres de la saison en cours viennent des
  feuilles de match Flashscore.
- **Rapprochement des noms** (`arbitres.historique_de`) : « Manzano J. / Esp »
  → « Jesús Gil Manzano / Spain ». Nom de famille contenu dans le nom complet,
  même initiale, **même pays** (un « Pinheiro J. » brésilien n'hérite pas du
  Portugais João Pinheiro) ; entre deux homonymes, le premier nom de famille
  départage (« Munuera J. » = José Munuera Montero, pas Juan Martínez
  Munuera) ; un cas encore ambigu est écarté. 305 arbitres des feuilles
  retrouvés, 61 % des matchs archivés ; aucun arbitre source attribué à deux
  noms Flashscore.
- **A priori** (`Discipline.arbitre`) : rapport historique
  `(jaunes + 20) / (attendus + 20)`, `attendus` = matchs × moyenne de jaunes de
  la compétition et de la saison. Il remplace le 1 de la 2.0.0 comme centre de
  l'a priori ; son poids reste `lissage_arbitre` = 40 cartons attendus. La
  saison du match est exclue, même en partie.
- **Sans historique** (arbitre non retrouvé, compétition non couverte),
  le calcul est exactement celui de la 2.0.0.
- La fiche porte la trace : `apports.arbitre.historique` = matchs connus et
  rapport de départ.

### Points forts
- Gain net quand les feuilles connaissent mal l'arbitre : +0,023 par match
  (t = 4,3) sur la saison 2025-26.
- Jamais de perte mesurée avec le réglage retenu.
- Couvre des arbitres absents des feuilles (Championship, Serie B,
  2. Bundesliga…).

### Points faibles
- **Sur la saison en cours, aucun gain mesurable** (+0,0015, t = 0,2, 311
  matchs) : les feuilles y connaissent déjà ces arbitres par la saison
  2025-26, que l'historique couvre aussi.
- 39 % des matchs archivés restent sans historique : arbitres hors d'Europe,
  noms trop différents (« Madley R. » vs « Bobby Madley »), pays à code inconnu.
- 11 pages absentes chez la source (Conference League 2023-24 ; Suisse, Grèce,
  Danemark, Pologne, Russie avant 2025-26).

### Pistes pour la suite
- Re-mesurer sur la saison 2026-27 quand elle comptera un millier de matchs.
- Relever la saison 2026-27 dès que la source la publie (`python -m ibet arbitres`).
- Pénalties par arbitre (dans la base, inexploités) pour le modèle des buts.

### Mesures en service
Pas encore de fiche tranchée en 3.0.0.

---

## 2.0.0 — retirée le 2026-09-27

**Changement** : le modèle reçoit la **discipline du match** — l'arbitre désigné,
l'onze aligné, l'entraîneur — tirée d'une nouvelle source : les feuilles de
match archivées (table `feuilles`). Seul l'arbitre déplace les probabilités ;
joueurs et entraîneur sont calculés et affichés à poids nul.
**Pourquoi** : totaux trop sûrs en 1.0.0 (annoncé 80,2 %, observé 70,0 %) alors
que les lignes par équipe étaient justes — la signature d'un facteur commun aux
deux équipes que le modèle ignore. L'arbitre en est le premier candidat.
**Mesure avant adoption** : `python -m ibet mesurer-cartons`, rejeu chronologique de
2 622 matchs (chaque profil ne voit que les matchs antérieurs). Réglages choisis
sur les 60 % les plus anciens (1 573 matchs), verdict unique sur les 40 % les
plus récents (1 049 matchs) :

| Variante (test) | Log-vraisemblance | Écart | t | Brier total | Brier équipe |
|---|---|---|---|---|---|
| référence (≈ 1.0.0) | −5.3666 | — | — | 0.2026 | 0.1669 |
| arbitre | −5.3571 | +0.0095 | +2.0 | 0.2015 | 0.1664 |
| **arbitre + variance (retenu)** | **−5.3554** | **+0.0112** | **+2.3** | **0.2010** | **0.1664** |

Calibration des seuils du total annoncés à 70 % ou plus : écart de 4,0 points
(82,0 % annoncé, 78,0 % observé) ramené à 3,1 (81,8 % / 78,7 %).

La référence de cette mesure est un Maher réduit aux cartons, reconstruit sur
l'archive, et non le moteur complet : les gains mesurés sont ceux d'un facteur
**ajouté** à un attendu d'équipes, pas un backtest de la fiche entière.

### Ce que fait la version
- **Données** : `api_client.feuille_de_match` lit, pour un match terminé,
  l'arbitre (et son pays), les entraîneurs, le système, les minutes de chaque
  joueur et chaque carton avec son auteur. Rattrapage :
  `python -m ibet rattraper-feuilles` (4 154 feuilles, 3 215 avec arbitre, de 2012
  à septembre 2026, 680 arbitres dont 109 vus au moins dix fois).
- **Arbitre** (`ibet/modeles/discipline.py`) : rapport cartons observés / cartons
  attendus d'après les équipes qu'il a eues — sans quoi l'arbitre des derbys
  passerait pour sévère —, pondéré par l'ancienneté (demi-vie 365 jours),
  lissé par **40** cartons attendus fictifs, appliqué aux deux côtés à la
  puissance **0.5**, borné à [1/1.6 ; 1.6]. Clé : nom **et** pays.
- **Variance de l'arbitre** : ce qu'on ignore de lui (tout, s'il n'est pas
  désigné) est un facteur commun aux deux côtés ; il s'ajoute à la corrélation
  du total : `+ v·√(λdom·λext)/φ`, poids **1**. Arbitre inconnu : corrélation
  ≈ 0,14 au lieu de 0,083 ; arbitre vu vingt fois : ≈ 0,10.
- **Joueurs** (poids **0**) : taux de jaunes par 90 minutes de chaque titulaire,
  lissé vers le taux de son poste (gardien 0,06 ; défenseur 0,20 ; milieu 0,20 ;
  attaquant 0,17 ; poste inconnu 0,18), onze aligné rapporté à l'onze habituel des cinq derniers
  matchs.
- **Entraîneur** (poids **0**) : pendant ses huit premiers matchs, le profil de
  ses équipes précédentes remplace à proportion celui de l'équipe. N'agit que si
  un prédécesseur a été vu dans l'archive.
- Le **critère 12** du contexte reste à poids nul : le remonter compterait
  l'arbitre deux fois.
- Sans contexte, `ajuster` ne fait rien : le modèle est exactement le 1.0.0.

### Points forts
- Premier apport de l'arbitre à gagner sur des matchs jamais vus (t = +2,3),
  et il gagne sur les deux familles de seuils (total et équipe).
- Utile même quand l'arbitre est inconnu : l'élargissement du total agit sur
  les deux tiers des fiches émises jusqu'ici (38 sur 57 sans arbitre désigné).

### Points faibles
- **Gain modeste.** +0,011 de log-vraisemblance par match : réel, pas
  spectaculaire. Le total reste trop sûr de 3 points.
- **Archive peu profonde** : 4 154 matchs sur 78 compétitions. Deux arbitres
  seulement ont vingt matchs ou plus ; à lissage 12, le facteur arbitre
  **dégradait** la prévision (−0,019), preuve que le bruit domine sous une
  dizaine de matchs.
- **Joueurs et entraîneur n'ont rien montré** : l'historique d'une équipe
  contient déjà son onze habituel et son entraîneur ; l'écart n'apparaît que
  sur quelques matchs, noyé à cette taille d'échantillon.
- **Poste souvent inconnu** : le flux ne donne pas la place du joueur dans
  toutes les compétitions ; un cinquième du temps de jeu archivé retombe sur
  le taux moyen, ce qui affaiblit le volet joueurs.
- Les compositions ne sont publiées qu'une heure avant le match : une fiche
  émise la veille n'a de toute façon que l'onze habituel.

### Pistes pour la suite
- **Approfondir l'archive des arbitres** : rattraper des saisons complètes
  des grands championnats (historiques d'équipes, puis `ibet/collecte/feuilles.py`).
  C'est le levier principal : le gain de l'arbitre croît avec le nombre de ses
  matchs connus (+0,014 sur les arbitres vus au moins trois fois).
- **Re-mesurer dispersion et corrélation du total** sur les feuilles : l'écart
  de 3 points qui reste ne vient plus de l'arbitre.
- **Joueurs** : re-mesurer à 10 000 matchs, et tester les suspensions
  (joueur à quatre jaunes, absent au match suivant) plutôt que l'onze entier.
- **Entraîneur** : mesurer sur une saison entière d'intersaison, quand l'archive
  aura assez de changements (425 matchs concernés aujourd'hui).

### Mesures en service
Pas encore de fiche tranchée en 2.0.0.

| Période | Matchs | Props | Annoncé | Observé | Écart | Brier |
|---|---|---|---|---|---|---|

---

## 1.0.0 — retirée le 2026-09-27

**Changement** : première version versionnée. Calcul identique à celui d'avant
le découpage en un modèle par événement.

### Ce que fait la version
- **Nombre attendu** : moteur Maher (voir [moteur.md](moteur.md)), lu dans les
  statistiques du match (`cartons_jaunes`). Le contexte peut le corriger
  (arbitre, critère 12 ; tension, critère 9).
- **Loi** : **binomiale**, dispersion **0.847**. C'est la seule grandeur
  sous-dispersée : il se donne un nombre de cartons remarquablement régulier.
- **Corrélation** entre les deux équipes : **+0.083** ± 0.017 (t = +4.9,
  n = 3 976). Un match tendu en donne aux deux, ce qui élargit la loi du total.
- **Recalage** : 1.0 (biais mesuré +0.09).
- **Seuils** : par équipe 0.5 à 3.5 ; au total 2.5 à 5.5 ; mis en avant 4.5.
- **Marché propre** : duel.

### Points forts
- **Lignes par équipe très bien calibrées** : annoncé 87.4 %, observé 87.9 %
  (116 propositions, 43 matchs), Brier 0.100.
- **Régularisation** : k = 22 améliore le seuil de 0.2470 à 0.2433 (t = −3.8).

### Points faibles
- **Totaux trop sûrs** : annoncé 80.2 %, observé 70.0 %, écart **+10.2 points
  ± 6.0** (50 propositions, 31 matchs). Ce n'est pas encore significatif, mais
  c'est l'écart le plus large des totaux.
- **Dépendance à l'arbitre** : le nombre de cartons tient beaucoup à l'arbitre,
  que seul le contexte apporte. Sans contexte, le modèle ne le voit pas.

### Pistes pour la suite
- **Totaux** : la corrélation +0.083 élargit le total. Vérifier sur les fiches
  1.0.0 si elle suffit, sinon re-mesurer la corrélation par compétition (le
  style d'arbitrage varie d'un championnat à l'autre).
- **Arbitre** : mesurer le gain du critère 12 sur les seuls cartons, avec
  `ibet/evaluation/criteres.py`.

### Mesures en service
Pas encore de fiche tranchée en 1.0.0. Référence de départ (fiches antérieures) :

| Famille | Matchs | Props | Annoncé | Observé | Écart | ± | Brier |
|---|---|---|---|---|---|---|---|
| Toutes | 43 | 186 | 83.2 % | 81.2 % | +2.1 | 3.3 | 0.1392 |
| équipe | 43 | 116 | 87.4 % | 87.9 % | −0.5 | 3.1 | 0.1001 |
| total | 31 | 50 | 80.2 % | 70.0 % | **+10.2** | 6.0 | 0.2066 |
| duel | 19 | 20 | 66.6 % | 70.0 % | −3.4 | 9.5 | 0.1971 |
