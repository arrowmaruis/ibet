"""Mesure ce que l'historique long et les cotes du marche apportent aux corners.

Rejoue, dans l'ordre chronologique, les matchs de l'historique long
(`python -m ibet historique`, football-data.co.uk). Pour chacun, on ne lit que
les matchs anterieurs ; on prevoit, on note, puis on ajoute le match.

Reference : un estimateur calque sur le moteur en service -- les DIX derniers
matchs de chaque equipe, rapportes a la moyenne du championnat a domicile et a
l'exterieur, regularises vers 1 par 22 matchs fictifs (`SHRINKAGE`), sans
ponderation par l'anciennete. Variantes :

    profond   la regularisation tire vers le profil de l'equipe sur ses deux
              dernieres saisons (demi-vie 365 jours), plutot que vers la
              moyenne du championnat ;
    marche    le nombre attendu est corrige par les cotes d'avant-match :
              log lambda = a + b.log(reference) + c.(p_dom - p_ext) + d.p_plus25,
              coefficients ajustes (regression de Poisson) sur le reglage ;
    les deux  la meme regression, sur l'estimateur « profond ».

Reglage : saisons jusqu'a 2022-23. Verdict : 2023-24 et apres, jamais vues au
reglage. Note : log-vraisemblance du couple et du total sous la loi du modele
en service, Brier des seuils, erreur type groupee par match.

Usage :
    python -m ibet mesurer-historique                        # corners
    python -m ibet mesurer-historique --grandeur tirs_cadres
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict, deque
from datetime import date as _date
from typing import Any

from ibet.collecte.historique import historique
from ibet.modeles import corners as m_corners
from ibet.modeles import tirs_cadres as m_tirs
from ibet.modeles.lois import count_pmf, dispersion_du_total, team_over_probability, total_over_probability
from ibet.stockage import store

FORME = 10            # matchs lus par le moteur
SHRINKAGE = 22.0      # matchs fictifs au niveau moyen (moteur)
DEMI_VIE_PROFIL = 365.0
LISSAGE_PROFIL = 10.0
MIN_MATCHS = 3
DEBUT_TEST = "2023-07-01"

GRANDEURS = {
    "corners": ("corners_dom", "corners_ext", m_corners.DISPERSION, m_corners.CORRELATION,
                m_corners.ModeleCorners.seuils_equipe, m_corners.ModeleCorners.seuils_total),
    "tirs_cadres": ("cadres_dom", "cadres_ext", m_tirs.DISPERSION, m_tirs.CORRELATION,
                    m_tirs.ModeleTirsCadres.seuils_equipe, m_tirs.ModeleTirsCadres.seuils_total),
}


def _jours(a: str, b: str) -> float:
    return float((_date.fromisoformat(b) - _date.fromisoformat(a)).days)


def probabilites(m: dict[str, Any]) -> tuple[float, float, float] | None:
    """(p_dom, p_ext, p_plus25) sans marge, cotes d'ouverture. None si absentes."""
    cotes = [m.get("cote_dom"), m.get("cote_nul"), m.get("cote_ext")]
    if not all(cotes):
        return None
    inv = [1.0 / c for c in cotes]
    s = sum(inv)
    p25 = 0.5
    if m.get("cote_plus25") and m.get("cote_moins25"):
        a, b = 1.0 / m["cote_plus25"], 1.0 / m["cote_moins25"]
        p25 = a / (a + b)
    return inv[0] / s, inv[2] / s, p25


class Estimateur:
    """Forme recente et profil long de chaque equipe, par championnat."""

    def __init__(self) -> None:
        self.ligue = defaultdict(lambda: [0.0, 0.0, 0.0])  # dom, ext, matchs
        # Equipe -> dernieres observations (date, pour/moy, contre/moy)
        self.recents: dict[str, deque] = defaultdict(lambda: deque(maxlen=FORME))
        self.long: dict[str, list[tuple[str, float, float]]] = defaultdict(list)

    def moyennes(self, ch: str) -> tuple[float, float]:
        d, e, n = self.ligue[ch]
        return (d / n, e / n) if n >= 20 else (0.0, 0.0)

    def _profil(self, cle: str, date: str) -> tuple[float, float]:
        """(attaque, defense) longues, lissees vers 1."""
        sa = sd = w_tot = 0.0
        for d, a, df in self.long.get(cle) or ():
            age = _jours(d, date)
            if age > 2 * 365:
                continue
            w = 0.5 ** (age / DEMI_VIE_PROFIL)
            sa += w * a
            sd += w * df
            w_tot += w
        k = LISSAGE_PROFIL
        return (sa + k) / (w_tot + k), (sd + k) / (w_tot + k)

    def forces(self, cle: str, date: str, profond: bool) -> tuple[float, float, int]:
        obs = list(self.recents.get(cle) or ())
        n = len(obs)
        prior = self._profil(cle, date) if profond else (1.0, 1.0)
        if not n:
            return prior[0], prior[1], 0
        a = sum(o[1] for o in obs) / n
        d = sum(o[2] for o in obs) / n
        return ((n * a + SHRINKAGE * prior[0]) / (n + SHRINKAGE),
                (n * d + SHRINKAGE * prior[1]) / (n + SHRINKAGE), n)

    def attendu(self, m: dict, profond: bool) -> tuple[float, float, int] | None:
        ch = m["championnat"]
        md, me = self.moyennes(ch)
        if not md:
            return None
        ad, dd, nd = self.forces(ch + "|" + m["domicile"], m["date"], profond)
        ae, de, ne = self.forces(ch + "|" + m["exterieur"], m["date"], profond)
        return md * ad * de, me * ae * dd, min(nd, ne)

    def ajouter(self, m: dict, yd: float, ye: float) -> None:
        ch = m["championnat"]
        md, me = self.moyennes(ch)
        if md:
            for eq, pour, contre, mp, mc in ((m["domicile"], yd, ye, md, me),
                                            (m["exterieur"], ye, yd, me, md)):
                cle = ch + "|" + eq
                self.recents[cle].append((m["date"], pour / mp, contre / mc))
                self.long[cle].append((m["date"], pour / mp, contre / mc))
        l = self.ligue[ch]
        l[0] += yd
        l[1] += ye
        l[2] += 1


def rejouer(grandeur: str) -> list[dict[str, Any]]:
    cd, ce = GRANDEURS[grandeur][:2]
    est = Estimateur()
    lignes = []
    for m in historique():
        yd, ye = m.get(cd), m.get(ce)
        if yd is None or ye is None:
            continue
        ref = est.attendu(m, False)
        prof = est.attendu(m, True)
        p = probabilites(m)
        if ref and prof and p and ref[2] >= MIN_MATCHS:
            lignes.append({"date": m["date"], "championnat": m["championnat"],
                           "yd": yd, "ye": ye, "ref": ref[:2], "prof": prof[:2], "p": p})
        est.ajouter(m, float(yd), float(ye))
    return lignes


# --- Regression de Poisson (IRLS), sans dependance ----------------------------

def _resoudre(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c and abs(m[c][c]) > 1e-12:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def poisson(x: list[list[float]], y: list[float], iterations: int = 25) -> list[float]:
    k = len(x[0])
    beta = [math.log(max(sum(y) / len(y), 1e-6))] + [0.0] * (k - 1)
    for _ in range(iterations):
        xtwx = [[0.0] * k for _ in range(k)]
        xtwz = [0.0] * k
        for r, t in zip(x, y):
            eta = sum(b * v for b, v in zip(beta, r))
            mu = math.exp(eta)
            z = eta + (t - mu) / mu
            for i in range(k):
                xtwz[i] += mu * r[i] * z
                for j in range(k):
                    xtwx[i][j] += mu * r[i] * r[j]
        nouveau = _resoudre(xtwx, xtwz)
        if max(abs(a - b) for a, b in zip(nouveau, beta)) < 1e-8:
            return nouveau
        beta = nouveau
    return beta


def _variables(l: dict, cote: str, base: str, avec25: bool = True) -> list[float]:
    """Pour le cote domicile : ecart de probabilites tel quel ; pour
    l'exterieur, son oppose -- c'est la domination de l'equipe consideree."""
    lam = l[base][0 if cote == "d" else 1]
    pd, pe, p25 = l["p"]
    ecart = (pd - pe) if cote == "d" else (pe - pd)
    x = [1.0, math.log(max(lam, 1e-6)), ecart]
    return x + [p25 - 0.5] if avec25 else x


def ajuster_marche(lignes: list[dict], base: str, avec25: bool = True) -> dict[str, list[float]]:
    coefs = {}
    for cote, y in (("d", "yd"), ("e", "ye")):
        coefs[cote] = poisson([_variables(l, cote, base, avec25) for l in lignes],
                              [float(l[y]) for l in lignes])
    return coefs


def _predire(l: dict, coefs: dict[str, list[float]], base: str) -> tuple[float, float]:
    avec25 = len(coefs["d"]) == 4
    return tuple(math.exp(sum(b * v for b, v in zip(coefs[c], _variables(l, c, base, avec25))))
                 for c in ("d", "e"))


def _noter(ld: float, le: float, yd: int, ye: int, grandeur: str) -> dict[str, float]:
    phi, corr, s_eq, s_tot = GRANDEURS[grandeur][2:]
    phi_t = dispersion_du_total(ld, le, phi, corr)
    t = yd + ye
    total = [(total_over_probability(ld, le, s, phi_t), t > s) for s in s_tot]
    equipe = [(team_over_probability(l, s, phi), y > s) for l, y in ((ld, yd), (le, ye)) for s in s_eq]
    return {
        "ll": math.log(max(count_pmf(yd, ld, phi) * count_pmf(ye, le, phi), 1e-12))
        + math.log(max(count_pmf(t, ld + le, phi_t), 1e-12)),
        "brier_total": sum((p - o) ** 2 for p, o in total) / len(total),
        "brier_equipe": sum((p - o) ** 2 for p, o in equipe) / len(equipe),
        "pente": (ld + le, t),
    }


def _pente(paires: list[tuple[float, float]]) -> float:
    """Pente du reel sur le prevu : 1 = etalement juste, < 1 = trop etale."""
    mx = sum(p for p, _ in paires) / len(paires)
    my = sum(o for _, o in paires) / len(paires)
    cov = sum((p - mx) * (o - my) for p, o in paires)
    var = sum((p - mx) ** 2 for p, _ in paires)
    return cov / var if var else 0.0


def rapport(lignes: list[dict], variantes: dict[str, Any], grandeur: str, titre: str) -> None:
    print("\n== %s : %d matchs" % (titre, len(lignes)))
    print("%-22s %9s %9s %7s %10s %10s %7s" % (
        "variante", "ll", "d ll", "t", "brier tot", "brier eq", "pente"))
    notes = {nom: [_noter(*f(l), l["yd"], l["ye"], grandeur) for l in lignes]
             for nom, f in variantes.items()}
    ref = notes["reference"]
    for nom, v in notes.items():
        diffs = [a["ll"] - r["ll"] for a, r in zip(v, ref)]
        d = sum(diffs) / len(diffs)
        e = math.sqrt(sum((x - d) ** 2 for x in diffs) / (len(diffs) - 1) / len(diffs))
        print("%-22s %9.4f %+9.4f %+7.1f %10.4f %10.4f %7.2f" % (
            nom, sum(a["ll"] for a in v) / len(v), d, d / e if e > 1e-12 else 0.0,
            sum(a["brier_total"] for a in v) / len(v),
            sum(a["brier_equipe"] for a in v) / len(v),
            _pente([a["pente"] for a in v])))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grandeur", default="corners", choices=list(GRANDEURS))
    parser.add_argument("--detail", action="store_true", help="test championnat par championnat")
    args = parser.parse_args(argv)
    detail = args.detail
    store.init()
    lignes = rejouer(args.grandeur)
    if len(lignes) < 1000:
        print("Trop peu de matchs (%d). Lancer d'abord : python -m ibet historique" % len(lignes))
        return 1
    reglage = [l for l in lignes if l["date"] < DEBUT_TEST]
    test = [l for l in lignes if l["date"] >= DEBUT_TEST]

    c_ref = ajuster_marche(reglage, "ref")
    c_prof = ajuster_marche(reglage, "prof")
    c_1x2 = ajuster_marche(reglage, "ref", avec25=False)
    for nom, c in (("marche", c_ref), ("marche 1X2 seul", c_1x2), ("profond + marche", c_prof)):
        for cote in ("d", "e"):
            print("%-17s %s : %s" % (nom, "domicile " if cote == "d" else "exterieur",
                                     ", ".join("%s %+.4f" % kv for kv in zip(
                                         ("constante", "log(lambda)", "ecart 1X2", "p(+2.5)-0.5"),
                                         c[cote]))))

    variantes = {
        "reference": lambda l: l["ref"],
        "profond": lambda l: l["prof"],
        "marche": lambda l: _predire(l, c_ref, "ref"),
        "marche 1X2 seul": lambda l: _predire(l, c_1x2, "ref"),
        "profond + marche": lambda l: _predire(l, c_prof, "prof"),
    }
    rapport(reglage, variantes, args.grandeur, "reglage (jusqu'a 2022-23)")
    rapport(test, variantes, args.grandeur, "TEST (2023-24 et apres)")
    for ch in (sorted({l["championnat"] for l in test}) if detail else ()):
        rapport([l for l in test if l["championnat"] == ch], variantes, args.grandeur,
                "TEST, %s" % ch)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
