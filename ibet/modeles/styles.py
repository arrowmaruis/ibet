"""Styles de jeu des joueurs : profils, etiquettes, et ce qu'un onze change.

Le modele de Maher sait ce qu'une EQUIPE obtient de corners ou de tirs cadres
en moyenne. Il ne sait pas QUI joue. Or un corner nait presque toujours d'un
geste individuel -- un centre devie, un tir contre, un dribble dans le couloir
qui finit en sortie de but -- et ces gestes ont des auteurs : un lateral qui
deborde et centre dix fois par match ne fabrique pas le meme nombre de corners
que le defenseur central qui le remplace.

Ce module lit les statistiques par joueur archivees (`store.stats_joueurs`,
remplies par `rattrapage_joueurs.py`) et en tire, pour chaque joueur :

  - des TAUX PAR 90 MINUTES (tirs, centres, dribbles, touches dans la surface,
    degagements...), ponderes par l'anciennete et lisses vers le taux de son
    poste : un joueur vu 120 minutes ne dit presque rien ;
  - sa PART du volume de son equipe quand il est sur le terrain : 30 % des
    centres de l'equipe, c'est un trait du joueur qui voyage mieux d'un club a
    l'autre qu'un taux brut, gonfle par une equipe qui a toujours le ballon ;
  - des ETIQUETTES de style (« centreur », « tireur », « dribbleur »...), quand
    un taux est dans le haut de son poste.

Et pour un match, un FACTEUR par equipe : ce que l'onze aligne produit, divise
par ce que produisaient les onzes de la periode que l'historique de l'equipe
reflete deja. C'est le seul ecart que le modele d'equipe ignore -- l'absence du
centreur, l'arrivee d'un tireur l'ete dernier.

Tout est calcule a partir des matchs ANTERIEURS a la date demandee.
Aucune requete ici : le module ne lit que ce qu'on lui donne.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as _date
from typing import Any, Iterable, NamedTuple

# Demi-vie de l'anciennete, en jours : un joueur change de role et de club.
DEMI_VIE_JOUEUR = 240.0

# Minutes fictives au taux du poste : 450, cinq matchs complets. En dessous,
# le taux du joueur pese moins que celui de son poste.
LISSAGE_MINUTES = 450.0

# Minutes d'un titulaire, faute de savoir qui sortira.
MINUTES_TITULAIRE = 80.0

# Minutes minimales pour recevoir une etiquette de style, et percentile du
# poste a partir duquel elle est donnee.
MINUTES_ETIQUETTE = 450.0
PERCENTILE_ETIQUETTE = 0.80

# Grandeurs suivies par joueur (cles de `api_client.STATS_JOUEUR`).
GRANDEURS = (
    "tirs", "tirs_cadres", "tirs_contres", "tirs_surface", "tirs_de_loin", "tirs_tete",
    "xg", "buts",
    "centres", "centres_reussis", "dribbles", "dribbles_reussis",
    "conduites_progressives", "touches_surface", "entrees_surface",
    "touches", "passes", "passes_dernier_tiers", "passes_longues",
    "passes_en_profondeur", "passes_cles", "xa", "grosses_occasions_creees",
    "degagements", "contres", "interceptions", "tacles", "duels_aeriens_gagnes",
    "fautes", "fautes_subies", "jaunes", "hors_jeux",
)

# Etiquettes : (libelle, grandeur par 90 minutes, postes concernes).
ETIQUETTES = (
    ("tireur", "tirs", "DMA"),
    ("tireur de loin", "tirs_de_loin", "DMA"),
    ("centreur", "centres", "DMA"),
    ("dribbleur", "dribbles", "DMA"),
    ("porteur de balle", "conduites_progressives", "DMA"),
    ("present dans la surface", "touches_surface", "DMA"),
    ("createur", "passes_cles", "DMA"),
    ("jeu long", "passes_longues", "GDM"),
    ("jeu aerien", "duels_aeriens_gagnes", "DMA"),
    ("degageur", "degagements", "DM"),
    ("contreur", "contres", "DM"),
    ("recuperateur", "interceptions", "DM"),
    ("provocateur de fautes", "fautes_subies", "DMA"),
)

# Indices de volume : ce qu'un onze produit, en combinaison de gestes. Les
# poids des corners sont ceux d'une regression des corners d'une equipe sur
# ses gestes du match (`mesure_styles.py --poids`) ; a defaut, un geste = 1.
INDICES = {
    # Regression sur 2 010 equipes-matchs (R2 = 0.53) : un corner pour ~7
    # centres ou ~7 tirs contres ; touches dans la surface, dribbles et tirs
    # pesent trois a cinq fois moins. Coefficients rapportes a celui du centre.
    "corners": {"centres": 1.0, "tirs_contres": 1.0, "touches_surface": 0.35,
                "dribbles_reussis": 0.3, "tirs": 0.2},
    "tirs_cadres": {"tirs_cadres": 1.0},
    "tirs": {"tirs": 1.0},
}


class Reglage(NamedTuple):
    demi_vie: float = DEMI_VIE_JOUEUR
    lissage: float = LISSAGE_MINUTES
    # "taux" : taux par 90 du joueur ; "part" : sa part du volume de l'equipe.
    mode: str = "taux"


def _jours(a: str, b: str) -> float:
    try:
        return float((_date.fromisoformat(b[:10]) - _date.fromisoformat(a[:10])).days)
    except ValueError:
        return 0.0


def _poids(anciennete: float, demi_vie: float) -> float:
    return 0.5 ** (max(0.0, anciennete) / demi_vie)


def _indice(stats: dict[str, float], poids: dict[str, float]) -> float:
    return sum(c * float(stats.get(g) or 0.0) for g, c in poids.items())


class Styles:
    """Index chronologique des statistiques par joueur.

    `ajouter` doit etre appele dans l'ordre des dates. Les questions prennent
    une date et ne lisent que ce qui la precede strictement.
    """

    def __init__(self, reglage: Reglage = Reglage()) -> None:
        self.reglage = reglage
        # Joueur -> [(date, minutes, stats, poste, equipe, stats d'equipe en sa presence)]
        self._joueur: dict[str, list[tuple[str, float, dict, str, str, dict]]] = defaultdict(list)
        self._nom: dict[str, str] = {}
        self._poste: dict[str, str] = {}
        self._equipe: dict[str, str] = {}
        # Poste -> [minutes, {grandeur: somme}] : l'a priori.
        self._groupe: dict[str, list[Any]] = defaultdict(lambda: [0.0, defaultdict(float)])
        # Equipe -> [(date, titulaires [(id, minutes)])]
        self._onzes: dict[str, list[tuple[str, list[tuple[str, float]]]]] = defaultdict(list)
        # Profils deja calcules, par (joueur, date) : un facteur en demande
        # plusieurs centaines a la meme date. Vide a chaque ajout.
        self._memo: dict[tuple[str, str], dict[str, Any]] = {}

    # --- Alimentation -------------------------------------------------------

    def ajouter(self, match: dict[str, Any]) -> None:
        date = match.get("date") or ""
        if not date:
            return
        self._memo.clear()
        for cote in ("domicile", "exterieur"):
            equipe = match.get(cote) or ""
            joueurs = (match.get("joueurs") or {}).get(cote) or []
            if not joueurs:
                continue
            # Volume de l'equipe sur le match, pour les parts. Rapporte aux
            # minutes : un joueur present 45 minutes n'a vu que la moitie des
            # centres de son equipe.
            total = defaultdict(float)
            for j in joueurs:
                for g in GRANDEURS:
                    total[g] += float((j.get("stats") or {}).get(g) or 0.0)
            titulaires = []
            for j in joueurs:
                minutes = float(j.get("minutes") or (j.get("stats") or {}).get("minutes") or 0.0)
                if minutes <= 0 or not j.get("id"):
                    continue
                poste = j.get("poste") or "?"
                stats = j.get("stats") or {}
                presence = {g: total[g] * min(1.0, minutes / 90.0) for g in GRANDEURS}
                self._joueur[j["id"]].append((date, minutes, stats, poste, equipe, presence))
                self._nom[j["id"]] = j.get("nom") or self._nom.get(j["id"], "")
                self._poste[j["id"]] = poste
                self._equipe[j["id"]] = equipe
                groupe = self._groupe[poste]
                groupe[0] += minutes
                for g in GRANDEURS:
                    groupe[1][g] += float(stats.get(g) or 0.0)
                if j.get("titulaire"):
                    titulaires.append((j["id"], minutes))
            if len(titulaires) >= 10:
                self._onzes[equipe].append((date, titulaires))

    def alimenter(self, matchs: Iterable[dict[str, Any]]) -> "Styles":
        for m in sorted(matchs, key=lambda r: r.get("date") or ""):
            self.ajouter(m)
        return self

    # --- Questions ----------------------------------------------------------

    def taux_du_poste(self, poste: str) -> dict[str, float]:
        minutes, sommes = self._groupe.get(poste) or self._groupe.get("?") or (0.0, {})
        if not minutes:
            return {g: 0.0 for g in GRANDEURS}
        return {g: sommes.get(g, 0.0) * 90.0 / minutes for g in GRANDEURS}

    def profil(self, joueur_id: str, date: str) -> dict[str, Any]:
        """Taux par 90 lisses, parts du volume de l'equipe, minutes connues."""
        cle = (joueur_id, date)
        if cle in self._memo:
            return self._memo[cle]
        poste = self._poste.get(joueur_id, "?")
        a_priori = self.taux_du_poste(poste)
        k = self.reglage.lissage
        m = 0.0
        s = defaultdict(float)
        presence = defaultdict(float)
        matchs = 0
        for d, minutes, stats, _p, _e, pres in self._joueur.get(joueur_id) or ():
            if d >= date:
                continue
            w = _poids(_jours(d, date), self.reglage.demi_vie)
            m += w * minutes
            matchs += 1
            for g in GRANDEURS:
                s[g] += w * float(stats.get(g) or 0.0)
                presence[g] += w * pres.get(g, 0.0)
        taux = {g: (s[g] + k * a_priori[g] / 90.0) * 90.0 / (m + k) for g in GRANDEURS}
        # Part lissee vers 1/11 : un joueur mal connu pese un onzieme.
        part = {g: (s[g] + 1.0 / 11.0 * 5.0) / (presence[g] + 5.0) for g in GRANDEURS}
        profil = {"id": joueur_id, "nom": self._nom.get(joueur_id, ""), "poste": poste,
                  "equipe": self._equipe.get(joueur_id, ""), "minutes": m, "matchs": matchs,
                  "par_90": taux, "part": part}
        self._memo[cle] = profil
        return profil

    def volume(self, titulaires: list[str], date: str, indice: str) -> float:
        """Ce qu'un onze produit sur l'indice, a MINUTES_TITULAIRE chacun."""
        poids = INDICES[indice]
        total = 0.0
        for jid in titulaires:
            p = self.profil(jid, date)
            valeurs = p["part"] if self.reglage.mode == "part" else p["par_90"]
            total += _indice(valeurs, poids) * MINUTES_TITULAIRE / 90.0
        return total

    def onze_habituel(self, equipe: str, date: str, profondeur: int = 3) -> list[str]:
        """Les onze joueurs les plus titularises sur les derniers matchs."""
        passes = [o for o in self._onzes.get(equipe) or () if o[0] < date][-profondeur:]
        compte: dict[str, int] = defaultdict(int)
        for _, t in passes:
            for jid, _m in t:
                compte[jid] += 1
        return [j for j, _ in sorted(compte.items(), key=lambda x: -x[1])[:11]]

    def facteur(
        self, equipe: str, titulaires: list[str] | None, date: str, indice: str,
        demi_vie_equipe: float = 180.0, fenetre: int = 20,
    ) -> dict[str, Any]:
        """Onze du jour contre onzes que l'historique de l'equipe reflete.

        Le numerateur est l'onze aligne s'il est connu, sinon l'onze habituel
        des trois derniers matchs. Le denominateur est la moyenne, ponderee
        comme le moteur pondere l'historique, des onzes des `fenetre` derniers
        matchs. Les deux sont calcules avec les MEMES profils, a la meme date :
        seul l'effectif change.

        Rend un facteur 1 sans au moins cinq onzes de reference.
        """
        passes = [o for o in self._onzes.get(equipe) or () if o[0] < date][-fenetre:]
        if len(passes) < 5:
            return {"facteur": 1.0, "onzes_de_reference": len(passes)}
        source = "aligne"
        onze = [j for j in (titulaires or []) if j]
        if len(onze) < 10:
            onze = self.onze_habituel(equipe, date)
            source = "habituel"
        if len(onze) < 10:
            return {"facteur": 1.0, "onzes_de_reference": len(passes)}
        du_jour = self.volume(onze, date, indice)
        num = den = 0.0
        for d, t in passes:
            w = _poids(_jours(d, date), demi_vie_equipe)
            num += w * self.volume([j for j, _ in t], date, indice)
            den += w
        reference = num / den if den else 0.0
        if reference <= 0:
            return {"facteur": 1.0, "onzes_de_reference": len(passes)}
        return {"facteur": du_jour / reference, "source": source,
                "volume_onze": round(du_jour, 2), "volume_reference": round(reference, 2),
                "onzes_de_reference": len(passes)}

    # --- Catalogue ----------------------------------------------------------

    def seuils_etiquettes(self, date: str) -> dict[tuple[str, str], float]:
        """Percentile PERCENTILE_ETIQUETTE de chaque grandeur, par poste, parmi
        les joueurs assez vus."""
        valeurs: dict[tuple[str, str], list[float]] = defaultdict(list)
        for jid in self._joueur:
            p = self.profil(jid, date)
            if p["minutes"] < MINUTES_ETIQUETTE:
                continue
            for _lib, g, postes in ETIQUETTES:
                if p["poste"] in postes:
                    valeurs[(p["poste"], g)].append(p["par_90"][g])
        seuils = {}
        for cle, v in valeurs.items():
            v.sort()
            if len(v) >= 10:
                seuils[cle] = v[int(PERCENTILE_ETIQUETTE * (len(v) - 1))]
        return seuils

    def etiquettes(self, profil: dict[str, Any], seuils: dict[tuple[str, str], float]) -> list[str]:
        if profil["minutes"] < MINUTES_ETIQUETTE:
            return []
        rendu = []
        for lib, g, postes in ETIQUETTES:
            seuil = seuils.get((profil["poste"], g))
            if profil["poste"] in postes and seuil is not None and profil["par_90"][g] >= seuil > 0:
                rendu.append(lib)
        return rendu

    def effectif(self, equipe: str, date: str) -> list[dict[str, Any]]:
        """Joueurs vus avec l'equipe (dernier club connu), des plus utilises
        aux moins utilises."""
        ids = [j for j, e in self._equipe.items() if e == equipe]
        profils = [self.profil(j, date) for j in ids]
        return sorted(profils, key=lambda p: -p["minutes"])

    def equipes(self) -> list[str]:
        return sorted(set(self._equipe.values()))


# Garde-fou : un onze ne multiplie pas un nombre attendu par plus de 1.3 ni
# moins de 1/1.3. Un profil extreme sur peu de minutes ne doit pas faire la
# prevision a lui seul.
BORNE = 1.3


def appliquer(
    lam: tuple[float, float], apports: dict[str, Any] | None, poids: float
) -> tuple[float, float, dict[str, Any] | None]:
    """Nombres attendus multiplies par le facteur de chaque onze, a la
    puissance `poids`, bornes. A poids nul, les nombres ressortent intacts --
    mais la trace est rendue : le facteur reste lisible dans la fiche."""
    if not apports:
        return lam[0], lam[1], None
    resultat = []
    trace: dict[str, Any] = {"poids": poids}
    for rang, cote in enumerate(("domicile", "exterieur")):
        bloc = apports.get(cote) or {}
        brut = float(bloc.get("facteur") or 1.0)
        applique = max(1.0 / BORNE, min(BORNE, brut ** poids)) if poids else 1.0
        resultat.append(lam[rang] * applique)
        trace[cote] = dict(bloc, facteur_applique=round(applique, 3))
    trace["lambda_avant_styles"] = (round(lam[0], 2), round(lam[1], 2))
    return resultat[0], resultat[1], trace


class AvecStyles:
    """Melange pour un `ModeleParEquipe` qui recoit les styles des onze.

    `ajuster` applique le facteur ; `prevoir` range sa trace sous « styles » et
    garde les forces d'equipe sous « forces » -- les deux comptent : les forces
    disent d'ou vient le nombre attendu, les styles ce qui l'a deplace.
    """

    poids_styles = 0.0

    def ajuster(self, lam, apports):
        return appliquer(lam, apports, self.poids_styles)

    def prevoir(self, estimation, teams, baseline, params, correction=None,
                with_candidates=False, apports=None):
        entry = super().prevoir(estimation, teams, baseline, params, correction,
                                with_candidates, apports)
        if apports:
            from .estimation import forces_des_equipes

            entry["styles"] = entry.pop("forces")
            entry["forces"] = forces_des_equipes(baseline, teams, self.champ, params)
        return entry
