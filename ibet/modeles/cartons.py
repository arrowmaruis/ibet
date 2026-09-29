"""Modele des cartons jaunes."""

from __future__ import annotations

import copy
import math
from typing import Any

from .base import ModeleParEquipe
from .estimation import forces_des_equipes

CARDS_LINE = 4.5

# Rapport variance / moyenne : 0.847. Seule grandeur SOUS-dispersee -- il s'en
# donne un nombre remarquablement regulier --, d'ou une loi binomiale : un
# nombre limite de situations ou l'arbitre peut en donner un.
DISPERSION = 0.847

# Correlation des residus entre les deux equipes : +0.083 +/- 0.017 (t = +4.9,
# n = 3 976). Les cartons vont ensemble : un match tendu en donne aux deux. Une
# correlation positive elargit la loi du total.
CORRELATION = 0.083

# Biais observe sur 100 matchs : +0.09. Pas de recalage.
CALIBRATION = 1.0

# --- 2.0.0 : arbitre, joueurs, entraineur (voir `discipline.py`) -------------
#
# Chaque apport est un facteur multiplicatif sur le nombre attendu de chaque
# cote, eleve a son poids : a 0 il ne fait rien, a 1 il s'applique en entier.
# Les poids sortent de `mesure_cartons.py` (reglage sur les 60 % de matchs les
# plus anciens, jugement sur les 40 % les plus recents) ; le detail est dans
# `journal/cartons_jaunes.md`.
#
# Mesure (2 622 matchs rejoues, 1 049 de test) : seul l'arbitre gagne.
#   arbitre, lissage 40, poids 0.5     log-vraisemblance +0.0095 (t = +2.0)
#   + variance de l'arbitre, poids 1    +0.0112 (t = +2.3), Brier du total
#                                       0.2026 -> 0.2010
#   joueurs (onze aligne / habituel)   +0.0024 au mieux (t = +1.3) : 0
#   entraineur (nouvel arrivant)       +0.0013 (t = +0.5) : 0
# Les deux derniers sont CALCULES et AFFICHES dans la fiche, sans rien
# deplacer -- meme regle que les criteres de contexte a poids nul.
#
# --- 3.0.0 : historique de l'arbitre (worldfootball.net) ---------------------
#
# Le profil de l'arbitre ne part plus d'un a priori neutre (rapport 1) mais de
# ce qu'il a montre les saisons PRECEDENTES dans 25 competitions europeennes
# (`stockage/arbitres.py`, `Discipline.arbitre(..., historique=...)`). Les
# feuilles affinent ensuite. Aucun poids ci-dessous n'a change : le reglage par
# defaut (lissage 20, poids 0, puissance 0.5) a ete fixe AVANT la mesure, et le
# meilleur reglage des anciens matchs (puissance 1) perdait sur les recents.
#
# Mesure, historique contre 2.0.0, match par match :
#   60 % anciens (1 573)          +0.0141 (t = +4.5)
#   40 % recents (1 049)          +0.0018 (t = +0.5)
#   saison 2025-26, arbitre connu +0.0228 (t = +4.3, n = 1 035)
#   saison 2026-27, arbitre connu +0.0015 (t = +0.2, n = 311)
POIDS_ARBITRE = 0.5
POIDS_JOUEURS = 0.0
POIDS_ENTRAINEUR = 0.0
# Ce que l'incertitude sur l'arbitre ajoute a la correlation entre les deux
# cotes. Un arbitre deplace les deux equipes dans le meme sens : s'il est
# inconnu, ou vu trois fois, cette part du total reste aleatoire, et la loi du
# total doit s'elargir d'autant.
POIDS_VARIANCE_ARBITRE = 1.0

# Garde-fou : aucun apport ne multiplie un nombre attendu par plus de 1.6 ni
# moins de 1/1.6. Un profil extreme sur peu de matchs ne doit pas faire la
# prevision a lui seul.
BORNE = 1.6


def _borne(facteur: float) -> float:
    return max(1.0 / BORNE, min(BORNE, facteur))


class ModeleCartonsJaunes(ModeleParEquipe):
    cle = "cartons_jaunes"
    version = "3.0.0"
    libelle = "Cartons jaunes"
    champ = "cartons_jaunes"
    seuil = CARDS_LINE
    seuils_equipe = (0.5, 1.5, 2.5, 3.5)
    seuils_total = (2.5, 3.5, 4.5, 5.5)
    # Lignes des bookmakers : total de 1.5 a 7.5, par equipe de 0.5 a 4.5.
    gamme_total = (1.5, 7.5)
    gamme_equipe = (0.5, 4.5)
    ecart_total = 2
    ecart_equipe = 1
    prefere = "les deux"
    dispersion = DISPERSION
    correlation = CORRELATION
    calibration = CALIBRATION

    poids_arbitre = POIDS_ARBITRE
    poids_joueurs = POIDS_JOUEURS
    poids_entraineur = POIDS_ENTRAINEUR
    poids_variance_arbitre = POIDS_VARIANCE_ARBITRE

    def ajuster(
        self, lam: tuple[float, float], apports: dict[str, Any] | None
    ) -> tuple[float, float, dict[str, Any] | None]:
        """Arbitre, onze aligne et entraineur, appliques au nombre attendu.

        `apports` est la discipline du match (`context.discipline_du_match`) :

            {"arbitre": {"rapport", "variance", "matchs", ...},
             "domicile": {"joueurs": {"facteur", ...}, "entraineur": {"facteur", ...}},
             "exterieur": {...}}

        Sans apport, le modele est exactement le 1.0.0.
        """
        if not apports:
            return lam[0], lam[1], None
        arbitre = apports.get("arbitre") or {}
        f_arbitre = _borne(float(arbitre.get("rapport") or 1.0) ** self.poids_arbitre)
        resultat = []
        trace: dict[str, Any] = {"arbitre": dict(arbitre, facteur=round(f_arbitre, 3))}
        for rang, cote in enumerate(("domicile", "exterieur")):
            bloc = apports.get(cote) or {}
            f_j = float((bloc.get("joueurs") or {}).get("facteur") or 1.0) ** self.poids_joueurs
            f_e = float((bloc.get("entraineur") or {}).get("facteur") or 1.0) ** self.poids_entraineur
            facteur = _borne(f_arbitre * f_j * f_e)
            resultat.append(lam[rang] * facteur)
            trace[cote] = {
                "joueurs": round(f_j, 3), "entraineur": round(f_e, 3),
                "facteur_total": round(facteur, 3),
                "detail_joueurs": bloc.get("joueurs") or {},
                "detail_entraineur": bloc.get("entraineur") or {},
            }
        trace["lambda_avant_discipline"] = (round(lam[0], 2), round(lam[1], 2))
        return resultat[0], resultat[1], trace

    def correlation_du_match(
        self, lam_home: float, lam_away: float, apports: dict[str, Any] | None
    ) -> float:
        """Correlation des deux cotes, elargie de ce qu'on ignore de l'arbitre.

        Un facteur d'arbitre de variance v, commun aux deux cotes, ajoute
        v.lam_dom.lam_ext a la covariance. Rapporte a la variance de chaque cote
        (phi.lam), cela fait v.racine(lam_dom.lam_ext)/phi de correlation.
        """
        arbitre = (apports or {}).get("arbitre")
        if not arbitre or self.poids_variance_arbitre <= 0:
            return self.correlation
        v = float(arbitre.get("variance") or 0.0)
        ajout = self.poids_variance_arbitre * v * math.sqrt(
            max(lam_home, 0.0) * max(lam_away, 0.0)
        ) / self.dispersion
        return min(0.6, self.correlation + ajout)

    def prevoir(self, estimation, teams, baseline, params, correction=None,
                with_candidates=False, apports=None):
        if not apports:
            return super().prevoir(estimation, teams, baseline, params, correction,
                                   with_candidates, apports)
        # La correlation depend du match (de ce qu'on sait de l'arbitre) : on
        # prevoit avec une copie qui la porte, sans toucher au modele partage.
        lam_home, lam_away, _ = self.ajuster(estimation["lambda"], apports)
        modele = copy.copy(self)
        modele.correlation = self.correlation_du_match(lam_home, lam_away, apports)
        entry = ModeleParEquipe.prevoir(modele, estimation, teams, baseline, params,
                                        correction, with_candidates, apports)
        entry["correlation"] = round(modele.correlation, 3)
        # Le socle range la trace d'`ajuster` a la place des forces ; ici les
        # deux comptent : les forces disent d'ou vient le nombre attendu, la
        # discipline ce qui l'a deplace.
        entry["discipline"] = entry.pop("forces")
        entry["forces"] = forces_des_equipes(baseline, teams, self.champ, params)
        return entry
