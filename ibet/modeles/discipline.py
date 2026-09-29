"""Profils de discipline : arbitres, joueurs, entraineurs.

Le modele de Maher sait ce qu'une EQUIPE prend de cartons en moyenne. Trois
choses lui echappent, et elles se lisent toutes dans les feuilles de match
archivees (`store.feuilles`, remplies par `api_client.feuille_de_match`) :

  - **l'arbitre** : c'est lui qui sort les cartons. Il deplace les DEUX equipes
    dans le meme sens, ce qui explique a la fois la correlation positive des
    cartons (+0.083) et des totaux trop surs d'eux ;
  - **les joueurs alignes** : une equipe privee de ses deux milieux
    recuperateurs n'est plus la meme equipe devant l'arbitre ;
  - **l'entraineur** : l'historique d'une equipe melange les matchs de
    l'entraineur actuel et ceux de son predecesseur. Le systeme de jeu, la
    consigne de presser ou de laisser venir, changent avec lui.

Chaque profil est un RAPPORT observe / attendu, lisse vers 1 par un effectif
fictif (meme idee que `SHRINKAGE` dans le moteur) : un arbitre vu trois fois ne
dit presque rien, un arbitre vu quarante fois dit beaucoup. L'attendu d'un
match est ce que les deux equipes auraient du prendre compte tenu de leurs
propres habitudes -- sans quoi l'arbitre des derbys, a qui l'on confie les
matchs chauds, passerait pour severe.

Tout est calcule a partir des feuilles ANTERIEURES a la date demandee : l'index
est alimente dans l'ordre chronologique, et une question posee a une date ne
lit que ce qui la precede. C'est ce qui rend la mesure honnete.

Aucune requete ici : le module ne lit que ce qu'on lui donne.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date as _date
from typing import Any, Iterable, NamedTuple

# Demi-vie de l'anciennete, en jours. Un arbitre change peu ; un joueur change
# de role, de club, de maturite. Une saison pour les uns, une demi pour l'autre.
DEMI_VIE_ARBITRE = 365.0
DEMI_VIE_JOUEUR = 240.0
DEMI_VIE_EQUIPE = 180.0

# Effectifs fictifs de lissage, en unites d'attendu (nombre de cartons
# attendus). Mesures par `mesure_cartons.py` sur les 60 % de matchs les plus
# anciens : a 12, l'arbitre DEGRADAIT la prevision (-0.019) -- cinq matchs ne
# disent presque rien d'un arbitre, et un lissage faible les croyait. A 40
# (une dizaine de matchs fictifs au niveau moyen), il l'ameliore.
LISSAGE_ARBITRE = 40.0
LISSAGE_JOUEUR = 15.0
LISSAGE_EQUIPE = 8.0

# Historique des arbitres (worldfootball.net, `stockage/arbitres.py`) : il
# remplace l'a priori neutre (rapport 1) par le rapport que l'arbitre a montre
# les saisons precedentes, lui-meme lisse de `LISSAGE_HISTORIQUE` cartons
# attendus fictifs. `POIDS_HISTORIQUE` dit de combien cet historique renforce
# l'a priori : 0 = il en deplace le centre sans en changer le poids (qui reste
# `lissage_arbitre`), 1 = chaque carton attendu de l'historique compte comme
# un carton attendu des feuilles. Valeurs mesurees par `mesure_cartons.py`.
LISSAGE_HISTORIQUE = 20.0
POIDS_HISTORIQUE = 0.0

# Minutes jouees en moyenne par un titulaire, faute de mieux quand on ne sait
# pas encore qui sortira.
MINUTES_TITULAIRE = 80.0


class Reglage(NamedTuple):
    lissage_arbitre: float = LISSAGE_ARBITRE
    lissage_joueur: float = LISSAGE_JOUEUR
    lissage_equipe: float = LISSAGE_EQUIPE
    lissage_historique: float = LISSAGE_HISTORIQUE
    poids_historique: float = POIDS_HISTORIQUE


def saison_de(date: str) -> str:
    """Saison europeenne d'une date AAAA-MM-JJ : du 1er juillet au 30 juin."""
    try:
        annee, mois = int(date[:4]), int(date[5:7])
    except (ValueError, IndexError):
        return ""
    debut = annee if mois >= 7 else annee - 1
    return "%d-%d" % (debut, debut + 1)


def historique_anterieur(
    historique: Iterable[dict[str, Any]] | None, date: str
) -> tuple[float, float, float]:
    """(jaunes, attendus, matchs) des saisons STRICTEMENT anterieures a `date`.

    La saison du match est exclue meme en partie : ses tableaux cumulent des
    matchs posterieurs a la date, et les lire reviendrait a prevoir avec le
    resultat sous les yeux.
    """
    saison = saison_de(date)
    jaunes = attendus = matchs = 0.0
    for ligne in historique or ():
        if saison and (ligne.get("saison") or "") >= saison:
            continue
        jaunes += float(ligne.get("jaunes") or 0.0)
        attendus += float(ligne.get("attendus") or 0.0)
        matchs += float(ligne.get("matchs") or 0.0)
    return jaunes, attendus, matchs


def _jours(a: str, b: str) -> float:
    """Jours entre deux dates AAAA-MM-JJ (b apres a). 0 si l'une manque."""
    try:
        return float((_date.fromisoformat(b[:10]) - _date.fromisoformat(a[:10])).days)
    except ValueError:
        return 0.0


def _poids(anciennete: float, demi_vie: float) -> float:
    return 0.5 ** (max(0.0, anciennete) / demi_vie)


def groupe_de_poste(place: int | None, systeme: str) -> str:
    """G, D, M ou A a partir de la place dans le dispositif ("1-4-2-3-1").

    La place 1 est le gardien ; les suivantes remplissent les lignes dans
    l'ordre du systeme. La premiere ligne apres le gardien est la defense, la
    derniere l'attaque, tout ce qui est entre est le milieu.
    """
    if not place:
        return "?"
    try:
        lignes = [int(x) for x in (systeme or "").split("-") if x.strip()]
    except ValueError:
        lignes = []
    if not lignes or sum(lignes) != 11:
        lignes = [1, 4, 4, 2]
    cumul = 0
    for rang, taille in enumerate(lignes):
        cumul += taille
        if place <= cumul:
            if rang == 0:
                return "G"
            if rang == 1:
                return "D"
            if rang == len(lignes) - 1:
                return "A"
            return "M"
    return "?"


def cle_arbitre(nom: str, pays: str = "") -> str:
    """Nom et pays : "Martinez J." arbitre dans plusieurs championnats qui ne
    sont pas le meme homme. Le pays est absent de certaines fiches ; le nom
    seul reste alors la cle, faute de mieux."""
    nom = (nom or "").strip()
    pays = (pays or "").strip()
    return "%s|%s" % (nom, pays) if nom and pays else nom


def jaunes_du_cote(feuille_row: dict[str, Any], cote: str) -> int | None:
    """Cartons jaunes d'un cote : la statistique officielle d'abord, c'est elle
    que le modele prevoit ; a defaut, le compte des evenements."""
    officiel = ((feuille_row.get("stats") or {}).get(cote) or {}).get("cartons_jaunes")
    if officiel is not None:
        return int(officiel)
    cartons = (feuille_row.get("feuille") or {}).get("cartons")
    if cartons is None:
        return None
    return sum(1 for c in cartons if c.get("cote") == cote and c.get("type") == "jaune")


class _Serie:
    """Observations datees (observe, attendu) d'une entite."""

    __slots__ = ("points",)

    def __init__(self) -> None:
        self.points: list[tuple[str, float, float]] = []

    def ajouter(self, date: str, observe: float, attendu: float) -> None:
        self.points.append((date, observe, attendu))

    def sommes(self, a_la_date: str, demi_vie: float) -> tuple[float, float, int]:
        o = e = 0.0
        n = 0
        for d, obs, att in self.points:
            if d >= a_la_date:
                continue
            w = _poids(_jours(d, a_la_date), demi_vie)
            o += w * obs
            e += w * att
            n += 1
        return o, e, n

    def rapport(self, a_la_date: str, demi_vie: float, lissage: float) -> tuple[float, int, float]:
        """(rapport lisse, nombre de matchs, attendu cumule pondere)."""
        o, e, n = self.sommes(a_la_date, demi_vie)
        return (o + lissage) / (e + lissage), n, e


class Discipline:
    """Index chronologique des feuilles de match.

    `ajouter` doit etre appele dans l'ordre des dates. Les questions
    (`arbitre`, `joueurs`, `equipe`, `entraineur`) prennent une date et ne
    lisent que ce qui la precede strictement.
    """

    def __init__(self, reglage: Reglage = Reglage()) -> None:
        self.reglage = reglage
        # Competition : cartons par equipe et par match, a domicile et dehors.
        self._competition: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
        self._global = [0.0, 0.0, 0.0]  # jaunes domicile, jaunes exterieur, matchs
        # Equipe : ce qu'elle prend (attaque) et ce que ses adversaires prennent
        # contre elle (defense), rapportes a la competition.
        self._equipe_prend: dict[str, _Serie] = defaultdict(_Serie)
        self._equipe_fait_prendre: dict[str, _Serie] = defaultdict(_Serie)
        self._arbitre: dict[str, _Serie] = defaultdict(_Serie)
        self._entraineur: dict[str, _Serie] = defaultdict(_Serie)
        # Entraineur en poste de chaque equipe, avec la date de son premier match.
        self._poste: dict[str, dict[str, str]] = {}
        # Joueur : (date, minutes, jaunes, groupe de poste).
        self._joueur: dict[str, list[tuple[str, float, int, str]]] = defaultdict(list)
        # Taux par 90 minutes de chaque groupe de poste : l'a priori d'un joueur
        # qu'on connait mal.
        self._groupe = defaultdict(lambda: [0.0, 0.0])  # jaunes, minutes
        # Onzes de depart passes de chaque equipe : (date, titulaires, systeme).
        # C'est l'onze HABITUEL, celui que l'historique de l'equipe reflete deja.
        self._onzes: dict[str, list[tuple[str, list[dict[str, Any]], str]]] = defaultdict(list)

    # --- Alimentation -------------------------------------------------------

    def moyennes(self, competition: str) -> tuple[float, float]:
        """Jaunes par match a domicile et a l'exterieur dans la competition."""
        jd, je, n = self._competition.get(competition) or (0.0, 0.0, 0.0)
        gd, ge, gn = self._global
        # La competition est lissee vers l'ensemble : vingt matchs d'un
        # championnat ne suffisent pas a en fixer la moyenne.
        k = 20.0
        base_d = gd / gn if gn else 2.0
        base_e = ge / gn if gn else 2.2
        return ((jd + k * base_d) / (n + k), (je + k * base_e) / (n + k))

    def ajouter(self, row: dict[str, Any]) -> None:
        date = row.get("date") or ""
        if not date:
            return
        jd = jaunes_du_cote(row, "domicile")
        je = jaunes_du_cote(row, "exterieur")
        if jd is None or je is None:
            return
        competition = row.get("competition") or ""
        dom, ext = row.get("domicile") or "", row.get("exterieur") or ""
        feuille = row.get("feuille") or {}

        # L'attendu est calcule AVANT d'ajouter le match : c'est ce qu'on aurait
        # prevu la veille, sans l'arbitre.
        lam_d, lam_e = self.attendu_equipes(competition, dom, ext, date)
        md, me = self.moyennes(competition)

        self._equipe_prend[dom].ajouter(date, jd, md)
        self._equipe_prend[ext].ajouter(date, je, me)
        self._equipe_fait_prendre[dom].ajouter(date, je, me)
        self._equipe_fait_prendre[ext].ajouter(date, jd, md)

        arbitre = cle_arbitre(row.get("arbitre") or feuille.get("arbitre") or "",
                              feuille.get("arbitre_pays") or "")
        if arbitre:
            self._arbitre[arbitre].ajouter(date, jd + je, lam_d + lam_e)

        for cote, equipe, obs, moy in (("domicile", dom, jd, md), ("exterieur", ext, je, me)):
            bloc = feuille.get(cote) or {}
            coach = (bloc.get("entraineur") or {}).get("id") or ""
            if coach:
                # Rapporte a la competition, comme une equipe : c'est le profil
                # de SES equipes, quel que soit le club.
                self._entraineur[coach].ajouter(date, obs, moy)
                poste = self._poste.get(equipe)
                if not poste or poste["id"] != coach:
                    # `precedent` vide : c'est le premier entraineur que
                    # l'archive voit a ce club, et rien ne dit qu'il vient
                    # d'arriver -- l'archive commence peut-etre au milieu de
                    # son mandat.
                    self._poste[equipe] = {"id": coach, "depuis": date,
                                           "nom": (bloc.get("entraineur") or {}).get("nom", ""),
                                           "precedent": poste["id"] if poste else ""}
            jaunes_joueur: dict[str, int] = defaultdict(int)
            for c in feuille.get("cartons") or []:
                if c.get("cote") == cote and c.get("type") in ("jaune", "deuxieme_jaune"):
                    jaunes_joueur[c.get("id") or ""] += 1
            systeme = bloc.get("systeme") or ""
            titulaires = [j for j in bloc.get("joueurs") or [] if j.get("titulaire") and j.get("id")]
            if len(titulaires) >= 10:
                self._onzes[equipe].append((date, titulaires, systeme))
            for j in bloc.get("joueurs") or []:
                minutes = float(j.get("minutes") or 0)
                if minutes <= 0 or not j.get("id"):
                    continue
                groupe = groupe_de_poste(j.get("place"), systeme)
                y = jaunes_joueur.get(j["id"], 0)
                self._joueur[j["id"]].append((date, minutes, y, groupe))
                self._groupe[groupe][0] += y
                self._groupe[groupe][1] += minutes

        self._competition[competition][0] += jd
        self._competition[competition][1] += je
        self._competition[competition][2] += 1
        self._global[0] += jd
        self._global[1] += je
        self._global[2] += 1

    def alimenter(self, rows: Iterable[dict[str, Any]]) -> "Discipline":
        for row in sorted(rows, key=lambda r: r.get("date") or ""):
            self.ajouter(row)
        return self

    # --- Questions ----------------------------------------------------------

    def equipe(self, nom: str, date: str) -> tuple[float, float, int]:
        """(rapport pris, rapport fait prendre, matchs) d'une equipe."""
        k = self.reglage.lissage_equipe
        prend, n, _ = self._equipe_prend[nom].rapport(date, DEMI_VIE_EQUIPE, k) if nom in self._equipe_prend else (1.0, 0, 0.0)
        fait, _, _ = self._equipe_fait_prendre[nom].rapport(date, DEMI_VIE_EQUIPE, k) if nom in self._equipe_fait_prendre else (1.0, 0, 0.0)
        return prend, fait, n

    def attendu_equipes(
        self, competition: str, dom: str, ext: str, date: str
    ) -> tuple[float, float]:
        """Jaunes attendus de chaque cote, d'apres les seules equipes.

        Modele de Maher reduit a l'essentiel -- moyenne de la competition, ce
        que l'equipe prend, ce que l'adversaire fait prendre. Il sert
        d'attendu a l'arbitre et a l'entraineur, et de reference a la mesure.
        """
        md, me = self.moyennes(competition)
        pd, fd, _ = self.equipe(dom, date)
        pe, fe, _ = self.equipe(ext, date)
        return md * pd * fe, me * pe * fd

    def arbitre(
        self, nom: str, date: str, historique: Iterable[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Rapport lisse de l'arbitre, et l'incertitude qui reste dessus.

        `variance` est la variance a posteriori du rapport sous un a priori
        Gamma d'effectif `lissage` : elle sert a elargir la loi du total quand
        l'arbitre est mal connu (ou inconnu : rapport 1, variance 1/lissage).

        `historique` (lignes par saison de `stockage.arbitres.historique_de`)
        remplace le centre de l'a priori : au lieu de supposer l'arbitre moyen,
        on part de ce qu'il a montre les saisons precedentes, et les feuilles
        affinent. Sans historique, le calcul est exactement celui de la 2.0.0.
        """
        r = self.reglage
        k = r.lissage_arbitre
        a_priori, poids_a_priori = 1.0, k
        trace_historique = None
        jh, eh, mh = historique_anterieur(historique, date)
        if eh > 0:
            a_priori = (jh + r.lissage_historique) / (eh + r.lissage_historique)
            poids_a_priori = k + r.poids_historique * eh
            trace_historique = {"matchs": mh, "rapport": round(a_priori, 3)}

        serie = self._arbitre.get((nom or "").strip())
        o, e, n = serie.sommes(date, DEMI_VIE_ARBITRE) if serie else (0.0, 0.0, 0)
        forme = o + poids_a_priori * a_priori
        taux = e + poids_a_priori
        rendu = {"arbitre": nom or "", "rapport": forme / taux, "matchs": n,
                 "variance": forme / taux ** 2, "attendu_cumule": round(e, 1)}
        if trace_historique:
            rendu["historique"] = trace_historique
        return rendu

    def taux_joueur(self, joueur_id: str, groupe: str, date: str) -> tuple[float, float]:
        """(jaunes par 90 minutes lisses, minutes connues) d'un joueur."""
        g_j, g_m = self._groupe.get(groupe) or self._groupe.get("?") or (0.0, 0.0)
        tot_j = sum(v[0] for v in self._groupe.values())
        tot_m = sum(v[1] for v in self._groupe.values())
        a_priori = (g_j + 5.0 * (tot_j / tot_m if tot_m else 0.02)) / (g_m + 5.0) if g_m else (
            tot_j / tot_m if tot_m else 0.02)
        a_priori *= 90.0
        y = m = 0.0
        for d, minutes, jaunes, _g in self._joueur.get(joueur_id) or ():
            if d >= date:
                continue
            w = _poids(_jours(d, date), DEMI_VIE_JOUEUR)
            y += w * jaunes
            m += w * minutes
        k = self.reglage.lissage_joueur
        # Lissage en cartons : k cartons "fictifs" au taux du poste.
        return (y + k) / (m / 90.0 + k / a_priori), m

    def somme_joueurs(
        self, joueurs: list[dict[str, Any]], systeme: str, date: str
    ) -> dict[str, Any]:
        """Jaunes attendus d'un onze, joueur par joueur, a MINUTES_TITULAIRE."""
        total = 0.0
        detail = []
        for j in joueurs:
            groupe = groupe_de_poste(j.get("place"), systeme)
            taux, minutes = self.taux_joueur(j.get("id") or "", groupe, date)
            part = taux * MINUTES_TITULAIRE / 90.0
            total += part
            detail.append({"joueur": j.get("joueur", ""), "poste": groupe,
                           "jaunes_par_90": round(taux, 3), "minutes_connues": int(minutes)})
        return {"attendu": total, "joueurs": detail}

    def facteur_joueurs(
        self, equipe: str, titulaires: list[dict[str, Any]], systeme: str, date: str,
        habituel: int = 5,
    ) -> dict[str, Any]:
        """Onze aligne contre onze habituel : ce que la composition change.

        L'historique de l'equipe dit deja ce que prend son onze HABITUEL. Seul
        l'ecart compte : deux recuperateurs absents, un defenseur averti a
        chaque match qui fait son retour. Le rapport des deux sommes de taux,
        calculees le meme jour avec les memes taux, isole cet ecart.

        Rend un facteur 1 quand l'onze n'est pas connu (moins de dix titulaires
        identifies) ou que l'equipe n'a pas trois onzes passes de reference.
        """
        passes = [o for o in self._onzes.get(equipe) or () if o[0] < date][-habituel:]
        if len([j for j in titulaires if j.get("id")]) < 10 or len(passes) < 3:
            return {"facteur": 1.0, "onzes_de_reference": len(passes)}
        aligne = self.somme_joueurs(titulaires, systeme, date)
        reference = sum(
            self.somme_joueurs(t, sy, date)["attendu"] for _, t, sy in passes
        ) / len(passes)
        if reference <= 0:
            return {"facteur": 1.0, "onzes_de_reference": len(passes)}
        return {
            "facteur": aligne["attendu"] / reference,
            "attendu_onze": round(aligne["attendu"], 3),
            "attendu_habituel": round(reference, 3),
            "onzes_de_reference": len(passes),
            "joueurs": aligne["joueurs"],
        }

    def entraineur(self, equipe: str, coach_id: str, date: str) -> dict[str, Any]:
        """Rapport lisse de l'entraineur sur toute sa carriere connue, et
        depuis quand il dirige l'equipe."""
        k = self.reglage.lissage_equipe
        serie = self._entraineur.get(coach_id or "")
        rapport, n = (serie.rapport(date, DEMI_VIE_EQUIPE * 2, k)[:2]
                      if serie else (1.0, 0))
        poste = self._poste.get(equipe) or {}
        return {"id": coach_id, "rapport": rapport, "matchs": n,
                "en_poste_depuis": poste.get("depuis", "") if poste.get("id") == coach_id else ""}

    def entraineur_en_poste(self, equipe: str) -> dict[str, str]:
        return dict(self._poste.get(equipe) or {})

    def matchs_depuis(self, equipe: str, depuis: str, date: str) -> int:
        """Matchs de l'equipe joues entre `depuis` (inclus) et `date` (exclu)."""
        serie = self._equipe_prend.get(equipe)
        if not serie or not depuis:
            return 0
        return sum(1 for d, _, _ in serie.points if depuis <= d < date)

    def facteur_entraineur(
        self, equipe: str, coach_id: str, date: str, installation: int
    ) -> dict[str, Any]:
        """Correction d'un entraineur recemment arrive.

        L'historique de l'equipe est surtout celui de son predecesseur tant que
        le nouveau n'a pas dirige `installation` matchs. Pendant ce temps, on
        remplace l'equipe par l'entraineur, a proportion decroissante :

            facteur = (profil de l'entraineur / profil de l'equipe) ** (1 - m / installation)

        Pour un entraineur en poste de longue date, le facteur vaut 1 : son
        systeme est deja dans l'historique de l'equipe.
        """
        if not coach_id:
            return {"facteur": 1.0, "matchs_en_poste": None}
        poste = self._poste.get(equipe) or {}
        if not poste:
            return {"facteur": 1.0, "matchs_en_poste": None}
        if poste.get("id") == coach_id:
            if not poste.get("precedent"):
                # Aucun changement observe : l'historique est le sien.
                return {"facteur": 1.0, "matchs_en_poste": None}
            m = self.matchs_depuis(equipe, poste.get("depuis", ""), date)
        else:
            m = 0  # il n'a encore dirige aucun match archive ici
        if m >= installation:
            return {"facteur": 1.0, "matchs_en_poste": m}
        profil = self.entraineur(equipe, coach_id, date)
        if profil["matchs"] == 0:
            return {"facteur": 1.0, "matchs_en_poste": m}
        prend, _, _ = self.equipe(equipe, date)
        rapport = profil["rapport"] / prend if prend > 0 else 1.0
        return {"facteur": rapport ** (1.0 - m / installation), "matchs_en_poste": m,
                "profil_entraineur": round(profil["rapport"], 3),
                "matchs_entraineur": profil["matchs"], "profil_equipe": round(prend, 3)}


def variance_log(rapport_variance: float, rapport: float) -> float:
    """Variance relative d'un facteur multiplicatif (delta method)."""
    return rapport_variance / max(rapport, 1e-9) ** 2 if rapport > 0 else 0.0


def ecart_type_relatif(variance: float) -> float:
    return math.sqrt(max(0.0, variance))
