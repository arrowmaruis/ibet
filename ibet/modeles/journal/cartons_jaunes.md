# Modèle des cartons jaunes — journal des versions

Fichier : `ibet/modeles/cartons.py`. Clé : `cartons_jaunes`.

---

## 2.0.0 — en service depuis le 2026-09-27

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
