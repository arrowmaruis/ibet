"""Export des matchs normalises vers CSV / JSON, et rendu console."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from ibet import chemins
from ibet.modeles import bookmakers as _bk
from ibet.sources.api_client import STAT_ORDER, stat_number

EXPORT_DIR = chemins.EXPORTS

COLUMNS = [
    "date",
    "heure",
    "pays",
    "championnat",
    "journee",
    "domicile",
    "exterieur",
    "statut",
    "score_domicile",
    "score_exterieur",
    "kickoff_utc",
    "provider",
    "match_id",
    "url",
]


def _resolve(path: str | Path | None, date: str, extension: str) -> Path:
    if path is not None:
        return Path(path)
    EXPORT_DIR.mkdir(exist_ok=True)
    return EXPORT_DIR / ("matchs_%s.%s" % (date, extension))


def to_csv(
    matches: Sequence[dict[str, Any]], date: str, path: str | Path | None = None
) -> Path:
    """Ecrit un CSV UTF-8 avec BOM (ouverture directe dans Excel)."""
    target = _resolve(path, date, "csv")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Les colonnes de statistiques ne sont ajoutees que si au moins un match
    # en porte : sinon le CSV se retrouve avec 15 colonnes vides.
    columns = list(COLUMNS)
    if any(m.get("stats") for m in matches):
        columns += stat_columns()

    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for match in matches:
            row = dict(match)
            row.update(flatten_stats(match))
            writer.writerow({key: _cell(row.get(key)) for key in columns})
    return target


def _cell(value: Any) -> Any:
    return "" if value is None else value


def to_json(
    matches: Sequence[dict[str, Any]], date: str, path: str | Path | None = None
) -> Path:
    target = _resolve(path, date, "json")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": date, "count": len(matches), "matches": list(matches)}
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


# ---------------------------------------------------------------------------
# Affichage console
# ---------------------------------------------------------------------------


def _score(match: dict[str, Any]) -> str:
    home, away = match.get("score_domicile"), match.get("score_exterieur")
    if home is None or away is None:
        return "-"
    return "%s - %s" % (home, away)


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _pretty_date(iso: str) -> str:
    """2026-09-12 -> 'samedi 12/09/2026'. Sans locale, donc portable."""
    try:
        day = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return iso
    return "%s %s" % (JOURS[day.weekday()], day.strftime("%d/%m/%Y"))


def render_console(matches: Sequence[dict[str, Any]], title: str) -> str:
    """Tableau texte groupe par date puis par championnat.

    La sortie reste en ASCII : la console Windows utilise cp1252 par defaut et
    mangerait les tirets cadratins ou les points de suspension typographiques.
    """
    if not matches:
        return "\n  Aucun match : %s\n" % title

    header = "  %d match(s) - %s" % (len(matches), title)
    lines = ["", header, "  " + "=" * (len(header) - 2)]

    multi_day = len({m["date"] for m in matches if m["date"]}) > 1
    current_date = None
    current_league = None

    for match in matches:
        if multi_day and match["date"] != current_date:
            current_date = match["date"]
            current_league = None
            lines.append("")
            lines.append("  [ %s ]" % _pretty_date(current_date))

        # Le pays fait partie de la cle : plusieurs pays ont une "Ligue 1", et
        # les fusionner sous un seul en-tete afficherait un pays faux.
        league = (match["championnat"] or "Championnat inconnu", match["pays"])
        if league != current_league:
            current_league = league
            name, country = league
            label = "%s (%s)" % (name, country) if country else name
            lines.append("")
            lines.append("  " + label)
            lines.append("  " + "-" * len(label))

        lines.append(
            "  %-5s  %-26s %-7s %-26s  %s"
            % (
                match["heure"] or "--:--",
                _truncate(match["domicile"], 26),
                _score(match),
                _truncate(match["exterieur"], 26),
                match["statut"],
            )
        )

    lines.append("")
    return "\n".join(lines)


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


# ---------------------------------------------------------------------------
# Statistiques de match
# ---------------------------------------------------------------------------


# Demi-largeur des barres comparatives. Les deux moities divergent depuis un
# axe central : l'oeil compare deux longueurs opposees plus vite que deux
# nombres alignes en colonnes.
BAR_WIDTH = 12

# Largeurs de la fiche : libelle, puis la zone valeur+barre de chaque equipe.
LABEL_WIDTH = 16
SIDE_WIDTH = 5 + 1 + BAR_WIDTH  # valeur + separateur + barre


def _bars(left: Any, right: Any) -> tuple[str, str]:
    """Les deux moities d'une barre, proportionnelles au partage entre equipes.

    Rend deux blancs quand la comparaison n'a pas de sens : valeur manquante,
    non numerique, ou total nul (0 - 0, cas des cartons rouges). Une valeur non
    nulle recoit toujours au moins un caractere, sinon un 1 contre 30 paraitrait
    egal a zero.
    """
    low, high = stat_number(left), stat_number(right)
    blank = " " * BAR_WIDTH
    if low is None or high is None or low < 0 or high < 0:
        return blank, blank
    total = low + high
    if total == 0:
        return blank, blank

    left_len = int(round(BAR_WIDTH * low / total))
    right_len = int(round(BAR_WIDTH * high / total))
    if low > 0:
        left_len = min(BAR_WIDTH, max(1, left_len))
    if high > 0:
        right_len = min(BAR_WIDTH, max(1, right_len))
    return ("#" * left_len).rjust(BAR_WIDTH), ("#" * right_len).ljust(BAR_WIDTH)


def render_stats(matches: Sequence[dict[str, Any]]) -> str:
    """Fiche statistique par match : barres comparatives domicile / exterieur."""
    with_stats = [m for m in matches if m.get("stats")]
    if not with_stats:
        return ""

    lines = ["", "  Statistiques", "  " + "=" * 12]
    notes: list[str] = []

    for match in with_stats:
        stats = match["stats"]
        home, away = stats["domicile"], stats["exterieur"]

        title = "%s %s %s" % (match["domicile"], _score(match), match["exterieur"])
        lines.append("")
        lines.append("  " + title)
        lines.append("  " + "-" * len(title))

        context = " - ".join(
            part
            for part in (
                "%s (%s)" % (match["championnat"], match["pays"])
                if match.get("pays")
                else match.get("championnat", ""),
                "%s a %s" % (match["date"], match["heure"])
                if match.get("heure")
                else match.get("date", ""),
            )
            if part
        )
        if context:
            lines.append("  " + context)

        if stats.get("arbitre"):
            lines.append("  Arbitre : %s" % stats["arbitre"])
        elif stats.get("source") in ("thesportsdb", "flashscore"):
            lines.append("  Arbitre : non fourni par cette source")

        if match.get("url"):
            lines.append("  " + match["url"])

        # En-tete : chaque nom d'equipe au-dessus de sa propre colonne de valeurs.
        lines.append("")
        lines.append(
            (
                "  %s %s %s"
                % (
                    " " * LABEL_WIDTH,
                    _truncate(match["domicile"], SIDE_WIDTH).ljust(SIDE_WIDTH),
                    _truncate(match["exterieur"], SIDE_WIDTH).rjust(SIDE_WIDTH),
                )
            ).rstrip()
        )

        for field, label in STAT_ORDER:
            left, right = home.get(field), away.get(field)
            if left is None and right is None:
                continue
            left_bar, right_bar = _bars(left, right)
            lines.append(
                (
                    "  %-*s %5s %s|%s %-5s"
                    % (
                        LABEL_WIDTH,
                        label,
                        _stat(left),
                        left_bar,
                        right_bar,
                        _stat(right),
                    )
                ).rstrip()
            )

        missing = [
            dict(STAT_ORDER)[f] for f, _ in STAT_ORDER
            if f in (stats.get("indisponible") or [])
        ]
        if missing:
            lines.append("  (indisponible : %s)" % ", ".join(missing).lower())

        if stats.get("note") and stats["note"] not in notes:
            notes.append(stats["note"])

    for note in notes:
        lines.append("")
        for chunk in _wrap(note, 76):
            lines.append("  " + chunk)

    lines.append("")
    return "\n".join(lines)


def render_form(matches: Sequence[dict[str, Any]]) -> str:
    """Forme recente des deux equipes : bilan, liste des matchs, moyennes."""
    with_form = [m for m in matches if m.get("form")]
    if not with_form:
        return ""

    lines = ["", "  Forme recente", "  " + "=" * 13]

    for match in with_form:
        form = match["form"]
        title = "%s - %s" % (match["domicile"], match["exterieur"])
        lines.append("")
        lines.append("  " + title)
        lines.append("  " + "-" * len(title))

        for side in ("domicile", "exterieur"):
            data = form[side]
            played = len(data["matchs"])
            if not played:
                lines.append("")
                lines.append("  %s : aucun match joue trouve." % data["equipe"])
                continue

            tally = data["bilan"]
            scored, conceded = data["buts_pour"], data["buts_contre"]
            lines.append("")
            lines.append(
                "  %s  -  %d derniers matchs" % (data["equipe"], played)
            )
            lines.append(
                "    %d V  %d N  %d D    buts %d-%d  (%.1f marque, %.1f encaisse "
                "par match)"
                % (
                    tally["V"], tally["N"], tally["D"],
                    scored, conceded,
                    scored / played, conceded / played,
                )
            )
            # Serie la plus recente en premier : elle se lit de gauche a droite.
            lines.append(
                "    serie : %s   (le plus recent a gauche)"
                % " ".join(e["resultat"] or "?" for e in data["matchs"])
            )
            if data["amicaux"]:
                lines.append(
                    "    dont %d amical(aux), marques (A) : peu representatifs"
                    % data["amicaux"]
                )

            lines.append("")
            for entry in data["matchs"]:
                lines.append(
                    "    %-10s %-4s %-24s %s-%s  %s %s"
                    % (
                        entry["date"],
                        "dom." if entry["lieu"] == "domicile" else "ext.",
                        _truncate(entry["adversaire"], 24),
                        _stat(entry["buts_pour"]),
                        _stat(entry["buts_contre"]),
                        entry["resultat"] or "?",
                        "(A) " + _truncate(entry["competition"], 28)
                        if entry["amical"]
                        else _truncate(entry["competition"], 32),
                    )
                )

            averages = data.get("moyennes")
            if averages:
                covered = averages["matchs_couverts"]
                if not covered:
                    lines.append("")
                    lines.append(
                        "    Aucun de ces matchs n'a de statistiques chez Flashscore."
                    )
                else:
                    lines.append("")
                    lines.append(
                        "    Moyennes par match (sur %d match(s) avec statistiques) :"
                        % covered
                    )
                    for field, label in STAT_ORDER:
                        value = averages["valeurs"].get(field)
                        if value is None:
                            continue
                        suffix = "%" if field == "possession" else ""
                        lines.append(
                            "      %-18s %s%s" % (label, value, suffix)
                        )

    lines.append("")
    return "\n".join(lines)


def _pct(value: float) -> str:
    return "%4.1f%%" % (100 * value)


# Marque de disponibilite d'un critere. Trois etats, et non deux : « pas
# encore publie » et « mesure, sans effet » menent au meme multiplicateur et ne
# disent pas la meme chose.
_ETAT = {True: "ok", "partiel": "~", False: "--"}


def render_criteria(contexte: dict[str, Any] | None, indent: str = "  ") -> list[str]:
    """Les quatorze criteres d'une prevision, un par ligne.

    L'effet applique figure a cote du critere qui l'a produit. Une correction
    dont on ne verrait pas la cause serait inverifiable : le lecteur doit
    pouvoir remonter d'un nombre attendu deplace au critere qui l'a deplace.
    """
    if not contexte or not contexte.get("criteres"):
        return []

    lines = [
        "",
        "%sCriteres de decision" % indent,
        "%s%s" % (indent, "-" * 20),
    ]
    for critere in contexte["criteres"]:
        effets = " ".join(
            "%s %+.0f%%" % (grandeur, 100 * (facteur["domicile"] - 1))
            if abs(facteur["domicile"] - facteur["exterieur"]) < 1e-9
            else "%s %+.0f%%/%+.0f%%"
            % (grandeur, 100 * (facteur["domicile"] - 1),
               100 * (facteur["exterieur"] - 1))
            for grandeur, facteur in (critere.get("effet") or {}).items()
            if abs(facteur["domicile"] - 1) > 1e-9
            or abs(facteur["exterieur"] - 1) > 1e-9
        )
        lines.append(
            "%s%2d %-3s %-26s %s"
            % (
                indent,
                critere["numero"],
                _ETAT.get(critere["disponible"], "?"),
                _truncate(critere["libelle"], 26),
                _truncate(critere["resume"], 44),
            )
        )
        if effets:
            lines.append("%s        -> %s" % (indent, effets))

    confiance = contexte.get("confiance") or {}
    if confiance:
        lines.append(
            "%s  Confiance : %s (%.2f) -- %d critere(s) sur %d renseigne(s)"
            % (
                indent,
                confiance.get("niveau", "?"),
                confiance.get("score", 0.0),
                confiance.get("criteres_renseignes", 0),
                confiance.get("criteres_total", 0),
            )
        )
    return lines


def render_lineups(
    contexte: dict[str, Any] | None,
    teams: tuple[str, str] = ("domicile", "exterieur"),
    indent: str = "  ",
) -> list[str]:
    """Composition probable des deux equipes, ligne par ligne.

    Deux natures de composition, distinguees en toutes lettres : celle
    ANNONCEE par la source (a une heure du coup d'envoi, elle fait foi) et
    celle DEDUITE des titularisations recentes. Confondre les deux ferait
    passer une deduction pour une annonce.

    Chaque joueur porte son nombre de titularisations sur la periode : c'est ce
    qui permet de voir d'un coup d'oeil qu'un onze repose sur cinq matchs
    concordants ou sur deux titularisations isolees.
    """
    critere = next(
        (
            c
            for c in (contexte or {}).get("criteres", [])
            if c.get("numero") == 3
        ),
        None,
    )
    if not critere:
        return []

    lines: list[str] = []
    for side, name in (("domicile", teams[0]), ("exterieur", teams[1])):
        compo = (critere["valeur"].get(side) or {}).get("composition_probable") or {}
        if not compo.get("onze"):
            continue
        if not lines:
            lines.extend(["", "%sComposition probable" % indent,
                          "%s%s" % (indent, "-" * 20)])
        origine = (
            "annoncee"
            if compo.get("annoncee")
            else "deduite de %s match(s)" % compo.get("matchs_couverts", 0)
        )
        lines.append("")
        lines.append(
            "%s%s  -  %s  (%s)"
            % (indent, _truncate(name, 28), compo.get("systeme") or "systeme inconnu",
               origine)
        )
        ligne = None
        for joueur in compo["onze"]:
            if joueur.get("ligne") != ligne:
                ligne = joueur.get("ligne")
                lines.append("%s  %s" % (indent, ligne))
            marques = []
            if joueur.get("incertain"):
                marques.append("incertain")
            if joueur.get("remplace_un_absent"):
                marques.append("remplacant probable")
            lines.append(
                "%s    %3s  %-24s %s%s"
                % (
                    indent,
                    joueur.get("numero") or "-",
                    _truncate(joueur["joueur"], 24),
                    "%d titularisation(s)" % joueur["titularisations"]
                    if joueur.get("titularisations") is not None
                    else "",
                    "  (%s)" % ", ".join(marques) if marques else "",
                )
            )
        if compo.get("ecartes"):
            lines.append(
                "%s  ecartes : %s"
                % (
                    indent,
                    ", ".join(
                        "%s (%s)" % (j["joueur"], j["motif"] or "absent")
                        for j in compo["ecartes"][:6]
                    ),
                )
            )
    return lines


def render_prediction(matches: Sequence[dict[str, Any]]) -> str:
    """Previsions en tableaux : valeurs attendues, issue, puis propositions."""
    with_prediction = [m for m in matches if m.get("prediction")]
    if not with_prediction:
        return ""

    lines = ["", "  Prevision", "  " + "=" * 9]

    for match in with_prediction:
        prediction = match["prediction"]
        home, away = prediction["equipes"]
        sample = prediction["echantillon"]
        # Largeur des colonnes d'equipe : assez pour les deux noms, bornee pour
        # que le tableau tienne dans une console de 80 colonnes.
        width = max(12, min(22, len(home), len(away)))
        width = max(12, min(22, max(len(home), len(away))))

        title = "%s - %s" % (home, away)
        lines.append("")
        lines.append("  " + title)
        lines.append("  " + "=" * len(title))
        if not prediction.get("a_venir"):
            detail = prediction.get("score_reel")
            lines.append(
                "  /!\\ Match %s%s : ce n'est pas une prevision mais un calcul "
                "a posteriori." % (
                    (prediction.get("statut") or "deja joue").lower(),
                    ", resultat reel %s" % detail if detail else "",
                )
            )
        lines.append(
            "  Base : %d et %d matchs officiels (%d amical(aux) ecarte(s))"
            % (sample["domicile"], sample["exterieur"], sample["amicaux_ecartes"])
        )

        # --- Tableau 1 : ce que le modele attend, par equipe -----------------
        header = "  %-16s %*s %*s %8s" % (
            "Attendu", width, _truncate(home, width), width,
            _truncate(away, width), "Total",
        )
        lines.append("")
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for metric in prediction["grandeurs"]:
            lines.append(
                "  %-16s %*.2f %*.2f %8.2f"
                % (
                    metric["libelle"],
                    width, metric["lambda_domicile"],
                    width, metric["lambda_exterieur"],
                    metric["total_attendu"],
                )
            )

        # --- Tableau 2 : issue du match --------------------------------------
        goals = next(
            (m for m in prediction["grandeurs"] if m["cle"] == "buts"), None
        )
        if goals:
            result = goals["resultat"]
            lines.append("")
            lines.append(
                "  %-16s %*s %*s %8s"
                % ("Issue", width, "1 (dom.)", width, "2 (ext.)", "N (nul)")
            )
            lines.append(
                "  %-16s %*s %*s %8s"
                % (
                    "probabilite",
                    width, _pct(result["domicile"]),
                    width, _pct(result["exterieur"]),
                    _pct(result["nul"]),
                )
            )
            lines.append(
                "  Scores les plus probables : %s"
                % "   ".join(
                    "%d-%d %s" % (h, a, _pct(p))
                    for h, a, p in goals["scores_probables"]
                )
            )

        # --- Tableau 3 : echelles completes, seuil par seuil ------------------
        for metric in prediction["grandeurs"]:
            ladders = metric.get("echelles")
            if not ladders:
                continue
            lower = metric["libelle"].lower()
            lines.append("")
            lines.append(
                "  %-18s %s"
                % (
                    metric["libelle"],
                    "".join("%8s" % ("+%.1f" % l) for l in ladders["seuils_equipe"]),
                )
            )
            for side, name in (("domicile", home), ("exterieur", away)):
                lines.append(
                    "    %-16s %s"
                    % (
                        _truncate(name, 16),
                        "".join("%8s" % _pct(p) for p in ladders[side]),
                    )
                )
            lines.append(
                "  %-18s %s"
                % (
                    "total du match",
                    "".join("%8s" % ("+%.1f" % l) for l in ladders["seuils_total"]),
                )
            )
            lines.append(
                "    %-16s %s"
                % (
                    "les deux equipes",
                    "".join("%8s" % _pct(p) for p in ladders["total"]),
                )
            )

        # --- Confrontations directes ------------------------------------------
        meetings = prediction.get("confrontations") or []
        if meetings:
            recent = meetings[:5]
            span = "%s a %s" % (meetings[-1]["date"][:4], meetings[0]["date"][:4])
            lines.append("")
            lines.append(
                "  Confrontations directes : %d rencontres (%s)"
                % (len(meetings), span)
            )
            for meeting in recent:
                lines.append(
                    "    %-11s %-26s %d - %d"
                    % (
                        meeting["date"],
                        _truncate(meeting["competition"], 26),
                        meeting["buts_domicile"],
                        meeting["buts_exterieur"],
                    )
                )
            # Le critere 5 les utilise des que son poids est non nul ; sans
            # contexte, le modele n'a toujours aucun terme de confrontation
            # directe. Dire l'un pour l'autre tromperait le lecteur sur ce qui
            # a produit le nombre affiche.
            poids_h2h = (
                ((prediction.get("contexte") or {}).get("poids") or {})
                .get("confrontations", 0.0)
            )
            lines.append(
                "    (scores vus depuis %s ; %s)"
                % (
                    _truncate(home, 24),
                    "critere 5 actif" if poids_h2h else "non utilisees dans le calcul",
                )
            )

        # --- Les quatorze criteres --------------------------------------------
        lines.extend(render_criteria(prediction.get("contexte")))
        lines.extend(render_lineups(prediction.get("contexte"), (home, away)))

        # --- Tableau 4 : propositions les plus exploitables -------------------
        lines.append("")
        # La famille est affichee : sans elle, six propositions se ressemblent
        # toutes, et le lecteur ne voit pas qu'il a devant lui six paris de
        # natures differentes plutot que six variantes du meme.
        # « Cote juste » : sous cette cote, le pari perd de l'argent selon le
        # modele. C'est le chiffre a comparer a celle du bookmaker.
        lines.append(
            "  %-14s %-13s %-46s %8s %6s  %s"
            % ("Grandeur", "Type", "Proposition", "Reussite", "Cote", "Chez les %d bookmakers" % len(_bk.BOOKMAKERS))
        )
        lines.append("  " + "-" * 122)
        for metric in prediction["grandeurs"]:
            offers = metric.get("offres") or []
            for index, offer in enumerate(offers):
                lines.append(
                    "  %-14s %-13s %-46s %8s %6s  %s"
                    % (
                        metric["libelle"] if index == 0 else "",
                        _truncate(offer.get("famille", ""), 13),
                        _truncate(offer["libelle"], 46),
                        _pct(offer["p"]),
                        _cote(offer),
                        _ou_la_prendre(offer),
                    )
                )

        # --- Tableau 4 bis : la fiche vue de chaque bookmaker -----------------
        lines.extend(render_bookmakers(prediction))

        # --- Tableau 5 : les autres marches, par famille -----------------------
        # Les echelles couvrent deja les seuils ; ce tableau montre ce qu'elles
        # ne montrent pas, y compris sous 50 % -- un pari peu probable reste un
        # pari, et le masquer revient a choisir a la place du lecteur.
        board = [
            (metric['libelle'], row)
            for metric in prediction['grandeurs']
            for row in metric.get('marches') or []
        ]
        if board:
            lines.append('')
            lines.append(
                '  %-14s %-13s %-46s %8s %6s'
                % ('Grandeur', 'Marche', 'Proposition', 'Modele', 'Cote')
            )
            lines.append('  ' + '-' * 93)
            previous = ''
            for label, row in board:
                for index, offer in enumerate(row['propositions']):
                    lines.append(
                        '  %-14s %-13s %-46s %8s %6s'
                        % (
                            label if label != previous else '',
                            _truncate(row['famille'], 13) if index == 0 else '',
                            _truncate(offer['libelle'], 46),
                            _pct(offer['p']),
                            _cote(offer),
                        )
                    )
                    previous = label

        # --- Forces et dispositifs -------------------------------------------
        #
        # La ligne « methode » affirme « corrige du niveau des adversaires »
        # sans jamais dire de combien ni dans quel sens. Ce bloc montre les
        # forces qui ont PRODUIT les nombres attendus, et surtout l'ecart entre
        # ce qui a ete observe et ce que le modele en retient.
        forces_par_grandeur = [
            (metric["libelle"], metric["forces"])
            for metric in prediction["grandeurs"]
            if metric.get("forces")
        ]
        if forces_par_grandeur:
            lines.append("")
            lines.append("  Forces d'attaque et de defense")
            lines.append("  " + "-" * 32)
            lines.append(
                "  %-16s %-22s %8s %8s %9s %9s %7s"
                % (
                    "Grandeur", "Equipe", "Attaque", "Defense",
                    "Att.reg.", "Def.reg.", "Matchs",
                )
            )
            lines.append("  " + "-" * 84)
            for libelle, forces in forces_par_grandeur:
                premier = True
                for cote in ("domicile", "exterieur"):
                    part = forces.get(cote)
                    if not part:
                        continue
                    lines.append(
                        "  %-16s %-22s %8.2f %8.2f %9.2f %9.2f %7g"
                        % (
                            libelle if premier else "",
                            _truncate(part["equipe"], 22),
                            part["attaque"],
                            part["defense"],
                            part["attaque_regularisee"],
                            part["defense_regularisee"],
                            part["matchs"],
                        )
                    )
                    premier = False
            lines.append("")
            for chunk in _wrap(
                "Une force vaut 1 quand l'equipe est exactement dans la moyenne "
                "de sa competition : une attaque a 1.40 produit 40 % de plus que "
                "la moyenne, une defense a 0.85 en encaisse 15 % de moins. Les "
                "colonnes REGULARISEES sont celles que le modele emploie "
                "reellement : la brute est ramenee vers 1 proportionnellement a "
                "la taille de l'echantillon, parce que sur quatre matchs un 1.40 "
                "tient autant du bruit que du signal. A quatre matchs, la "
                "regularisation ne garde que 29 % de ce qui a ete observe ; a "
                "douze, 55 %. C'est la raison pour laquelle deux equipes tres "
                "differentes ressortent souvent proches -- et un ecart large "
                "entre brute et regularisee n'est pas un defaut du modele, c'est "
                "lui qui dit qu'il ne sait pas encore.",
                74,
            ):
                lines.append("    " + chunk)

        criteres_fiche = {
            c.get("cle"): c
            for c in ((prediction.get("contexte") or {}).get("criteres") or [])
        }
        effectif = criteres_fiche.get("systeme_et_effectif")
        if effectif and isinstance(effectif.get("valeur"), dict):
            rangs = []
            for cote in ("domicile", "exterieur"):
                part = effectif["valeur"].get(cote) or {}
                annonce = part.get("systeme_annonce") or ""
                habituel = part.get("systeme_habituel") or ""
                if annonce or habituel:
                    rangs.append((cote, annonce, habituel, part))
            if rangs:
                lines.append("")
                lines.append("  Dispositifs")
                lines.append("  " + "-" * 13)
                for cote, annonce, habituel, part in rangs:
                    manque = part.get("manque") or {}
                    absents = len(part.get("absents") or [])
                    lines.append(
                        "  %-10s habituel %-12s annonce %-12s %s"
                        % (
                            cote,
                            habituel or "inconnu",
                            annonce or "pas encore",
                            "%d absent(s)" % absents if absents else "",
                        )
                    )
                lines.append("")
                for chunk in _wrap(
                    "Le systeme HABITUEL est deduit des titularisations "
                    "recentes ; l'ANNONCE ne parait qu'environ une heure avant "
                    "le coup d'envoi, et « pas encore » est donc le cas normal "
                    "d'une fiche emise la veille. Le modele ne corrige rien sur "
                    "le seul dispositif : un 4-4-2 n'est pas plus offensif "
                    "qu'un 4-3-3, et ce qu'un dispositif produit est deja dans "
                    "les comptages -- le critere 1 y lit le bloc bas et la "
                    "contre-attaque mieux qu'une etiquette ne le ferait. Seul "
                    "un CHANGEMENT par rapport a l'habitude apporterait une "
                    "information neuve, et il n'est mesurable que sur des "
                    "fiches emises pres du coup d'envoi.",
                    74,
                ):
                    lines.append("    " + chunk)

        # --- Methode, par grandeur -------------------------------------------
        lines.append("")
        for metric in prediction["grandeurs"]:
            used_home, used_away = metric["matchs_utilises"]
            lines.append(
                "  %-16s %s%s (sur %d et %d matchs)"
                % (
                    metric["libelle"],
                    metric["methode"],
                    " (competition seulement)"
                    if metric.get("restreint_competition")
                    else "",
                    used_home,
                    used_away,
                )
            )

        lines.append("")
        for chunk in _wrap(
            "Loi de Poisson, scores supposes independants. Les pourcentages sont "
            "ceux du modele, pas des cotes : ils ne tiennent compte ni des "
            "absents, ni du contexte, et reposent sur une dizaine de matchs par "
            "equipe. A ne pas utiliser pour parier.",
            74,
        ):
            lines.append("    " + chunk)

    lines.append("")
    return "\n".join(lines)



def _cote(offer: dict[str, Any]) -> str:
    """Cote juste d'une proposition (1 / p), ou un tiret."""
    cote = offer.get("cote_juste") or (1.0 / offer["p"] if offer.get("p") else None)
    return "%.2f" % cote if cote else "-"


def _ou_la_prendre(offer: dict[str, Any]) -> str:
    """Fourchette des cotes sur les bookmakers de reference, et chez
    combien d'entre eux la proposition reste au-dessus de la cote minimale."""
    resume = offer.get("bookmakers") or {}
    fourchette = resume.get("fourchette")
    if not fourchette:
        return ""
    return "%.2f a %.2f  (%d/%d)" % (
        fourchette[0], fourchette[1],
        resume.get("operateurs_ok", 0), resume.get("operateurs", 0))


def render_bookmakers(prediction: dict[str, Any]) -> list[str]:
    """Pour chacun des bookmakers de reference : les propositions de la fiche qu'il
    paie au-dessus de la cote minimale, et la meilleure d'entre elles.

    Chaque utilisateur joue chez l'un ou l'autre : ce tableau lui montre ce
    que la fiche vaut CHEZ LUI, plutot que chez le moins margine."""
    from ibet.modeles import bookmakers as bk
    from ibet.modeles.offres import COTE_MIN

    offres = [o for m in prediction["grandeurs"] for o in m.get("offres") or []]
    if not offres:
        return []
    lignes = ["", "  %-14s %-13s %6s %11s  %s" % (
        "Bookmaker", "Zone", "Marge", "Jouables", "Meilleure proposition chez lui"),
              "  " + "-" * 114]
    for b in bk.BOOKMAKERS:
        jouables = []
        for o in offres:
            cote = dict((o.get("bookmakers") or {}).get("toutes") or []).get(b.nom)
            if cote and cote >= COTE_MIN:
                jouables.append((o["p"], cote, o["libelle"]))
        meilleure = max(jouables) if jouables else None
        lignes.append("  %-14s %-13s %5.1f%% %7d/%-3d  %s" % (
            b.nom, b.zone, 100 * b.marge, len(jouables), len(offres),
            "%s a %.2f" % (_truncate(meilleure[2], 60), meilleure[1]) if meilleure else "-"))
    lignes.append("")
    for chunk in _wrap(
        "Cotes estimees a partir de la marge mesuree de chaque operateur sur le "
        "1X2 (majoree de 3 points sur corners, tirs cadres et cartons), pas "
        "relevees chez lui. Comparez-les a la cote affichee sur son site.",
        74,
    ):
        lignes.append("    " + chunk)
    return lignes


def _best_offer(prediction: dict[str, Any]) -> dict[str, Any] | None:
    """Proposition la plus sure d'une fiche, toutes grandeurs confondues."""
    offers = [
        dict(offer, grandeur=metric["libelle"])
        for metric in prediction["grandeurs"]
        for offer in metric.get("offres") or []
    ]
    return max(offers, key=lambda offer: offer["p"]) if offers else None


def render_ranking(matches: Sequence[dict[str, Any]], top: int) -> str:
    """Previsions des matchs a venir, les plus tranchees d'abord.

    Le classement porte sur la proposition la plus sure de chaque fiche, et non
    sur la probabilite de victoire : un match sans favori peut etre tres lisible
    sur les corners ou le nombre de buts, et l'inverse est vrai aussi. C'est
    aussi la seule grandeur comparable d'une fiche a l'autre, les grandeurs
    disponibles variant avec la couverture statistique de la competition.

    Les matchs deja joues sont ecartes : sur eux, le calcul reste juste mais
    ce n'est plus une prevision.
    """
    ranked = []
    for match in matches:
        prediction = match.get("prediction")
        if not prediction or not prediction.get("a_venir"):
            continue
        offer = _best_offer(prediction)
        if offer:
            ranked.append((match, prediction, offer))
    if not ranked:
        return ""

    ranked.sort(key=lambda row: row[2]["p"], reverse=True)
    ranked = ranked[: max(1, top)]

    lines = ["", "  Previsions classees", "  " + "=" * 19, ""]
    lines.append(
        "  %-2s %-5s %-28s %-36s %7s"
        % ("#", "Heure", "Match", "Proposition", "p")
    )
    lines.append("  " + "-" * 82)
    for rank, (match, prediction, offer) in enumerate(ranked, start=1):
        home, away = prediction["equipes"]
        lines.append(
            "  %-2d %-5s %-28s %-36s %7s"
            % (
                rank,
                match.get("heure", ""),
                _truncate("%s - %s" % (home, away), 28),
                _truncate(offer["libelle"], 36),
                _pct(offer["p"]),
            )
        )

    lines.append("")
    for chunk in _wrap(
        "Le rang mesure l'assurance du modele, pas sa justesse : une "
        "proposition tres probable sur un echantillon de cinq matchs reste une "
        "proposition sur cinq matchs. La mesurer demande --backtest.",
        74,
    ):
        lines.append("    " + chunk)
    lines.append("")
    return "\n".join(lines)


def _signed(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else "%+.*f" % (digits, value)


def render_selection(rapport: dict[str, Any]) -> str:
    """Ou se servir du modele : les paris dont l'esperance est positive.

    Toutes les autres sorties du projet repondent a « que va-t-il se passer ? ».
    Celle-ci repond a « ou parier ? », et ce n'est pas la meme question. Un
    modele parfaitement calibre joue sur tous les matchs perd exactement la
    marge de l'operateur, a chaque coup : ce qui fait gagner n'est pas d'avoir
    raison souvent, c'est d'avoir raison la ou le marche a tort.
    """
    lines = ["", "  Paris de valeur", "  " + "=" * 17, ""]
    lines.append(
        "  %d fiche(s) en attente, %d cotee(s), %d releve(s) enregistre(s)."
        % (
            rapport["fiches_en_attente"],
            rapport["fiches_cotees"],
            rapport["releves_enregistres"],
        )
    )
    for incident in rapport.get("incidents") or []:
        lines.append("  Cotes indisponibles pour %s" % incident)
    non_apparies = rapport.get("non_apparies") or []
    if non_apparies:
        lines.append("")
        for chunk in _wrap(
            "%d match(s) que l'agregateur couvre peut-etre mais dont le nom n'a "
            "pas ete reconnu : %s. Ils sont dits plutot que devines -- les "
            "identifiants de l'agregateur ne sont pas ceux de Flashscore, le "
            "rapprochement se fait sur les noms d'equipes, et un appariement "
            "force valoriserait un pari avec le prix d'un AUTRE match sans "
            "jamais lever d'erreur. Leurs cotes restent saisissables a la main "
            "(--cote)."
            % (len(non_apparies), ", ".join(non_apparies[:4])),
            74,
        ):
            lines.append("    " + chunk)

    paris = rapport.get("paris") or []
    if not paris:
        lines.append("")
        for chunk in _wrap(
            "Aucun pari de valeur pour l'instant. Ce n'est pas un echec du "
            "modele : c'est le cas NORMAL. Le marche est bien calibre, et un "
            "ecart suffisant pour couvrir la marge de l'operateur est rare par "
            "construction. Une selection vide vaut mieux qu'une selection "
            "forcee -- et s'il n'y a aucune fiche cotee, c'est qu'aucune n'a "
            "ete emise avec son contexte pour un match a venir.",
            74,
        ):
            lines.append("    " + chunk)
        lines.append("")
        return "\n".join(lines)

    meilleures = rapport.get("par_match") or []
    if meilleures:
        lines.append("")
        lines.append("  La meilleure option de chaque match")
        lines.append("  " + "-" * 35)
        lines.append(
            "  %-28s %-26s %7s %7s %9s %8s"
            % ("Match", "Option retenue", "Modele", "Cote", "Esperance", "Examinees")
        )
        lines.append("  " + "-" * 90)
        for ligne in meilleures:
            lines.append(
                "  %-28s %-26s %7s %7.2f %8.1f %% %8d"
                % (
                    _truncate(ligne["match"], 28),
                    _truncate(ligne["pari"], 26),
                    _pct(ligne["probabilite"]),
                    ligne["cote"],
                    100 * ligne["valeur"],
                    ligne["options_examinees"],
                )
            )
        lines.append("")
        for chunk in _wrap(
            "Une ligne par rencontre : un match dont trois options ressortent "
            "n'a pas trois fois plus de valeur qu'un autre, il a une MEILLEURE "
            "option et les suivantes engagent le meme resultat. Les ecarts "
            "demesures sont ecartes avant le classement, et non signales comme "
            "plus bas : il n'y a qu'une ligne par match, et y laisser un +129 % "
            "reviendrait a recommander exactement ce que le garde-fou dit de ne "
            "pas jouer. Un match dont toutes les options sont demesurees ne "
            "sort pas -- le modele y est mal informe, il n'a rien a proposer. "
            "La colonne Examinees dit sur combien d'options le choix a porte : "
            "une valeur retenue parmi trente n'est pas la meme chose que la "
            "meme valeur retenue parmi trois.",
            74,
        ):
            lines.append("    " + chunk)

    lines.append("")
    lines.append("  Toutes les options valorisees")
    lines.append("  " + "-" * 29)
    lines.append(
        "  %-30s %-18s %7s %7s %10s"
        % ("Match", "Pari", "Modele", "Cote", "Esperance")
    )
    lines.append("  " + "-" * 76)
    for pari in paris:
        lines.append(
            "  %-30s %-18s %7s %7.2f %10s"
            % (
                _truncate(pari["match"], 30),
                _truncate(pari["pari"], 18),
                _pct(pari["probabilite"]),
                pari["cote"],
                "%+.1f %%%s%s"
                % (
                    100 * pari["valeur"],
                    " !" if pari.get("demesure") else "",
                    " ?" if pari.get("sans_reference") else "",
                ),
            )
        )
    lines.append("")
    for chunk in _wrap(
        "L'esperance est le gain moyen par euro engage : `p x cote - 1`. Elle "
        "tient compte de ce qu'on touche reellement, la ou un ecart de "
        "probabilite au marche n'en dit rien. Un point d'exclamation signale un "
        "ecart DEMESURE : au-dela de 25 % d'esperance, ce n'est presque jamais "
        "une occasion, c'est le signe que le modele est mal informe -- le marche "
        "integre l'effectif, la nouvelle du matin et l'argent de gens qui ont "
        "tort a leurs frais. Ces lignes sont rendues en dernier plutot que "
        "cachees, parce qu'elles disent quelque chose : sur le modele, pas sur "
        "le match.",
        74,
    ):
        lines.append("    " + chunk)
    if any(pari.get("sans_reference") for pari in paris):
        lines.append("")
        for chunk in _wrap(
            "Un point d'interrogation signale une prevision de REPLI : aucune "
            "reference de competition n'a pu etre etablie, et le modele s'est "
            "rabattu sur les moyennes brutes des deux equipes, sans correction "
            "du niveau des adversaires. C'est le cas de toute la Coupe "
            "d'Europe, et c'est la ou le modele est le plus faible : il "
            "normalise chaque equipe dans SON championnat puis multiplie, sans "
            "aucune notion de force relative entre competitions. Comparer la "
            "moyenne de buts d'un club grec a celle d'un club autrichien "
            "revient a les supposer equivalents. Un ecart au marche sur ces "
            "lignes n'est pas une occasion, c'est une limite connue du modele.",
            74,
        ):
            lines.append("    " + chunk)
    lines.append("")
    for chunk in _wrap(
        "Rien ici n'est verifie : ce sont des esperances calculees sur des "
        "probabilites estimees, pas des resultats. `--bilan` dira, une fois ces "
        "matchs joues, si la valeur annoncee s'est traduite en rendement. Tant "
        "que cette mesure n'existe pas, jouer sur la valeur reste une croyance.",
        74,
    ):
        lines.append("    " + chunk)
    lines.append("")
    return "\n".join(lines)


def render_options(bilan: dict[str, Any]) -> str:
    """Ce que les propositions REELLEMENT EMISES ont donne.

    L'epreuve la plus honnete du projet, et la seule qui porte sur ce qui a ete
    engage : le banc d'essai rejoue des matchs et juge des propositions
    qu'aucune fiche n'a affichees, alors qu'ici chaque ligne a ete ecrite avant
    le coup d'envoi et verifiee apres.
    """
    if not bilan.get("propositions"):
        return (
            "\n  Propositions emises\n  " + "=" * 21 + "\n\n"
            "    Aucune proposition tranchee. Emettez des fiches, puis lancez\n"
            "    `python -m ibet verifier` une fois les matchs joues.\n"
        )

    lines = ["", "  Propositions emises", "  " + "=" * 21, ""]
    lines.append(
        "  %d proposition(s) tranchee(s) sur %d match(s)."
        % (bilan["propositions"], bilan["matchs"])
    )

    def table(titre: str, rows: list[dict[str, Any]]) -> None:
        lines.append("")
        lines.append("  " + titre)
        lines.append(
            "  %-20s %8s %7s %9s %9s %16s"
            % ("", "Propos.", "Matchs", "Annonce", "Observe", "Ecart")
        )
        lines.append("  " + "-" * 74)
        for row in rows:
            erreur = row.get("erreur_type")
            lines.append(
                "  %-20s %8d %7d %9s %9s %16s"
                % (
                    _truncate(row["nom"], 20),
                    row["propositions"],
                    row["matchs"],
                    _pct(row["annonce"]),
                    _pct(row["observe"]),
                    "%+.1f pt%s%s"
                    % (
                        100 * row["ecart"],
                        " +/- %.1f" % (100 * erreur) if erreur is not None else "",
                        " *" if row.get("significatif") else "",
                    ),
                )
            )

    table("Par grandeur", bilan["par_grandeur"])
    table("Par famille de proposition", bilan["par_famille"])
    table("Par tranche de probabilite annoncee", bilan["par_tranche"])

    ensemble = bilan["ensemble"]
    lines.append("")
    lines.append(
        "  ENSEMBLE  annonce %s, observe %s, ecart %+.1f pt%s"
        % (
            _pct(ensemble["annonce"]),
            _pct(ensemble["observe"]),
            100 * ensemble["ecart"],
            " +/- %.1f" % (100 * ensemble["erreur_type"])
            if ensemble["erreur_type"] is not None
            else "",
        )
    )

    lines.append("")
    for chunk in _wrap(
        "Ecart positif = le modele promet plus qu'il ne tient. Les erreurs types "
        "sont GROUPEES PAR MATCH : une fiche porte une douzaine de propositions "
        "tirees du meme lambda et tranchees par le meme resultat, qui se "
        "realisent ou echouent ensemble. Les compter comme independantes divise "
        "l'erreur type par la racine d'un effectif que l'echantillon n'a pas -- "
        "ici par presque deux, assez pour faire passer un ecart ordinaire pour "
        "un resultat. C'est la colonne Matchs, et non celle des propositions, "
        "qui dit ce que la mesure vaut ; sous cinq matchs, aucune erreur type "
        "n'est rendue.",
        74,
    ):
        lines.append("    " + chunk)

    # La lecture qui manque le plus, et qu'aucun tableau ne donne : ce que ces
    # probabilites valent une fois confrontees a un prix.
    seuil_annonce = ensemble["annonce"]
    seuil_observe = ensemble["observe"]
    if seuil_annonce > 0 and seuil_observe > 0:
        lines.append("")
        for chunk in _wrap(
            "Traduit en cotes : une proposition annoncee a %.1f %% n'est "
            "rentable qu'au-dela de %.3f, et au taux REELLEMENT observe "
            "(%.1f %%) qu'au-dela de %.3f. L'ecart entre les deux bornes est "
            "ce que la calibration coute. Un operateur qui prend 5 %% de marge "
            "cote un tel evenement autour de %.3f -- sous les deux. Un "
            "portefeuille concentre a ce niveau de probabilite perd de "
            "l'argent meme parfaitement calibre : ce n'est pas la calibration "
            "qu'il faut corriger, c'est le CHOIX des propositions. `--valeur` "
            "ne regarde pas la probabilite mais `p x cote - 1`."
            % (
                100 * seuil_annonce,
                1 / seuil_annonce,
                100 * seuil_observe,
                1 / seuil_observe,
                1 / (seuil_annonce * 1.05),
            ),
            74,
        ):
            lines.append("    " + chunk)
    lines.append("")
    return "\n".join(lines)


def render_bilan(
    marche: dict[str, Any], criteres: dict[str, Any]
) -> str:
    """Ce que les fiches deja emises ont appris, et que rien d'autre ne dit.

    Deux mesures que le banc d'essai ne peut pas produire. Il rejoue des matchs
    passes : il ne connait ni les cotes qui etaient affichees ce jour-la, ni
    l'arbitre qui avait ete designe. Elles ne peuvent venir que de l'usage reel,
    fiche apres fiche, et elles repondent aux deux seules questions que la
    calibration laisse ouvertes -- le modele bat-il le MARCHE, et les criteres
    qu'on n'a jamais pu mesurer valent-ils quelque chose ?
    """
    lines = ["", "  Ce que les fiches emises ont appris", "  " + "=" * 36, ""]

    # --- Le marche ---------------------------------------------------------
    lines.append(
        "  %d fiche(s) tranchee(s), %d avec cotes d'avant-match, soit %d pari(s) "
        "valorise(s)."
        % (
            marche["matchs_tranches"],
            marche["matchs_avec_cotes"],
            marche["paris_valorises"],
        )
    )
    if marche["hors_portee"]:
        for chunk in _wrap(
            "%d fiche(s) tranchee(s) n'ont aucune cote relevee avant leur coup "
            "d'envoi et ne sont donc pas mesurables. Le bilan porte sur les "
            "trois ISSUES de chaque fiche -- c'est ce que la base cote. Les "
            "totaux, les lignes par equipe et les doubles chances, qui sont "
            "l'essentiel de ce qu'une fiche met en avant, restent hors de "
            "portee tant qu'aucune source ne les cote."
            % marche["hors_portee"],
            74,
        ):
            lines.append("    " + chunk)

    tranches = marche.get("tranches") or []
    if tranches:
        lines.append("")
        lines.append(
            "  %-18s %7s %7s %9s %10s %16s"
            % ("Valeur annoncee", "Paris", "Matchs", "Valeur", "Reussite",
               "Rendement")
        )
        lines.append("  " + "-" * 72)
        for tranche in tranches:
            erreur = tranche.get("rendement_erreur_type")
            lines.append(
                "  %-18s %7d %7d %8s %10s %16s"
                % (
                    tranche["tranche"],
                    tranche["paris"],
                    # Le match est l'unite de tirage : plusieurs paris d'une
                    # meme rencontre sont mutuellement exclusifs, et c'est leur
                    # nombre de matchs qui dit ce que la mesure vaut.
                    tranche.get("matchs", 0),
                    _pct(tranche["valeur_moyenne"]),
                    _pct(tranche["reussite"]),
                    "%+.1f %% +/- %.1f%s"
                    % (
                        100 * tranche["rendement"],
                        100 * (erreur or 0.0),
                        " *" if tranche.get("significatif") else "",
                    ),
                )
            )
        ensemble = marche["ensemble"]
        lines.append("  " + "-" * 72)
        lines.append(
            "  %-18s %7d %7d %8s %10s %16s"
            % (
                "ENSEMBLE",
                ensemble["paris"],
                ensemble.get("matchs", 0),
                _pct(ensemble["valeur_moyenne"]),
                _pct(ensemble["reussite"]),
                "%+.1f %% +/- %.1f"
                % (
                    100 * ensemble["rendement"],
                    100 * (ensemble.get("rendement_erreur_type") or 0.0),
                ),
            )
        )
        lines.append("")
        for chunk in _wrap(
            "Mise plate de un euro par pari. La lecture attendue si l'ecart au "
            "marche vaut quelque chose : le rendement MONTE avec la tranche de "
            "valeur, et la premiere ligne -- les paris que le modele juge "
            "perdants -- rend moins que les autres. S'il est plat, le modele "
            "diverge du marche sans le battre, et la selection ne sert a rien : "
            "c'est un resultat, pas un echec. Le rendement d'un pari a cote 3 "
            "vaut +2 ou -1, donc sa variance est enorme et l'erreur type n'est "
            "pas decorative -- sur deux cents paris, +8 % ne se distingue pas "
            "de zero. Elle est groupee par match : plusieurs paris d'une meme "
            "rencontre sont mutuellement exclusifs, un seul peut gagner, et les "
            "compter comme independants annoncerait une precision que "
            "l'echantillon n'a pas.",
            74,
        ):
            lines.append("    " + chunk)
    else:
        lines.append("")
        for chunk in _wrap(
            "Aucun pari valorisable pour l'instant : il faut qu'une fiche ait "
            "ete emise AVEC son contexte -- les cotes sont relevees a ce "
            "moment-la --, puis que son match ait ete verifie. Chaque fiche en "
            "apporte alors trois, une par issue. Tant que la table des cotes "
            "est vide, la question « le modele bat-il le marche ? » reste sans "
            "reponse, et c'est la seule que la calibration ne pose pas.",
            74,
        ):
            lines.append("    " + chunk)

    # --- Les quatre criteres a poids zero ----------------------------------
    lines.append("")
    lines.append("  Les criteres laisses a poids zero")
    lines.append("  " + "-" * 34)
    lines.append(
        "  %d fiche(s) tranchee(s) au dossier, %d requise(s) par critere."
        % (criteres["fiches_tranchees"], criteres["seuil"])
    )
    lines.append("")
    lines.append(
        "  %-22s %-16s %7s %22s"
        % ("Critere", "Grandeur jugee", "Fiches", "Pente du residu")
    )
    lines.append("  " + "-" * 70)
    for ligne in criteres["criteres"]:
        if ligne.get("signal_constant"):
            verdict = "signal constant"
        elif not ligne.get("assez"):
            verdict = "il en manque %d" % ligne["manquantes"]
        elif ligne.get("erreur_type") is None:
            verdict = "-"
        else:
            verdict = "%+.2f +/- %.2f%s" % (
                ligne["pente"],
                ligne["erreur_type"],
                " *" if ligne.get("significatif") else "",
            )
        lines.append(
            "  %-22s %-16s %7d %22s"
            % (
                _truncate(ligne["libelle"], 22),
                _truncate(ligne["grandeur"], 16),
                ligne["fiches"],
                verdict,
            )
        )
    lines.append("")
    for chunk in _wrap(
        "Ces quatre-la sont calcules et affiches mais ne deplacent rien, faute "
        "d'avoir pu etre mesures : leurs sources ne publient que l'etat courant "
        "ou publient apres coup, si bien que les rejouer sur des matchs passes "
        "leur donnerait ce que personne n'avait avant le coup d'envoi. Chaque "
        "fiche emise enregistre en revanche leur signal tel qu'il etait connu, "
        "ce qui les rend mesurables au fil de l'usage. La pente regresse le "
        "residu du modele (reel / prevu) sur ce signal : nulle, le critere ne "
        "dit rien que le modele ignore, et le poids zero devient une decision "
        "mesuree plutot qu'une abstention ; positive et signalee par une "
        "etoile, il voit ce que le modele rate, et c'est la seule justification "
        "acceptable pour lui donner un poids. Aucun poids n'est change ici.",
        74,
    ):
        lines.append("    " + chunk)
    lines.append("")
    return "\n".join(lines)


def render_backtest(report: dict[str, Any]) -> str:
    """Resultat d'une evaluation du modele sur des matchs deja joues.

    Trois blocs, dans l'ordre ou ils se lisent : les scores des trois modeles,
    l'ecart appariee du modele a ses references (c'est lui qui tranche), puis
    ce qui a ete ecarte et pourquoi. Un score seul ne dit rien : c'est la
    comparaison au modele de frequences qui dit si la machinerie attaque /
    defense apporte quelque chose.
    """
    lines = ["", "  Evaluation du modele", "  " + "=" * 20, ""]
    lines.append(
        "  %d match(s) evalue(s), %d ecarte(s)."
        % (report["matchs_evalues"], report["matchs_ignores"])
    )

    if not report["matchs_evalues"]:
        lines.append("")
        for chunk in _wrap(
            "Aucun match evaluable : il faut des matchs TERMINES dont "
            "l'historique des deux equipes est publie. Elargissez la selection "
            "(--date, --league) ou verifiez les causes ci-dessous.",
            74,
        ):
            lines.append("    " + chunk)
    else:
        lines.append("")
        lines.append(
            "  %-30s %6s %16s %9s %9s"
            % ("Modele", "Matchs", "Brier", "Log-loss", "Reussite")
        )
        lines.append("  " + "-" * 74)
        for summary in report["modeles"]:
            if not summary.get("matchs"):
                continue
            error = summary.get("brier_erreur_type")
            lines.append(
                "  %-30s %6d %16s %9.3f %8s"
                % (
                    _truncate(summary["nom"], 30),
                    summary["matchs"],
                    "%.3f +/- %.3f" % (summary["brier"], error)
                    if error is not None
                    else "%.3f" % summary["brier"],
                    summary["log_loss"],
                    _pct(summary["reussite"]),
                )
            )

        lines.append("")
        for label, key in (
            ("frequences de la competition", "vs_frequences"),
            ("tirage uniforme", "vs_uniforme"),
        ):
            comparison = report.get(key)
            if not comparison:
                continue
            lines.append(
                "  Ecart de Brier face au(x) %-28s %s +/- %.3f   (t = %+.1f)"
                % (
                    label + " :",
                    _signed(comparison["ecart"]),
                    comparison["erreur_type"],
                    comparison["t"],
                )
            )
        lines.append("")
        for chunk in _wrap(
            "Ecart negatif = le modele fait mieux que la reference. L'ecart est "
            "calcule match par match, sur les memes rencontres : au-dela de "
            "|t| = 2, il ne s'explique plus par le seul hasard de "
            "l'echantillon ; en deca, il ne prouve rien.",
            74,
        ):
            lines.append("    " + chunk)

        scores = report.get("scores") or {}
        if scores.get("matchs"):
            lines.append("")
            lines.append("  Grille des scores")
            lines.append("  " + "-" * 19)
            lines.append(
                "  Score exact en tete       %s des matchs (annonce %s)"
                % (_pct(scores["taux_tete"]), _pct(scores["tete_annoncee"]))
            )
            lines.append(
                "  Reel parmi les trois      %s" % _pct(scores["couverture_top3"])
            )
            erreur = scores.get("log_loss_erreur_type")
            lines.append(
                "  Log-loss du score reel    %.3f%s"
                % (
                    scores["log_loss"],
                    " +/- %.3f" % erreur if erreur is not None else "",
                )
            )
            gap = scores.get("vs_reference")
            if gap:
                lines.append(
                    "  Face aux moyennes de la competition : %s +/- %.3f (t = %+.1f)"
                    % (_signed(gap["ecart"]), gap["erreur_type"], gap["t"])
                )
            lines.append("")
            for chunk in _wrap(
                "Le taux de tete se lit A COTE de ce qui etait annonce, jamais "
                "seul : un modele qui met le bon score en tete 10 % du temps en "
                "annoncant 10 % est honnete, un qui en annonce 17 % ment. Et le "
                "plafond est bas -- un oracle connaissant le VRAI nombre de buts "
                "attendu de chaque equipe ne place le bon score en tete "
                "qu'environ 12,5 % du temps, et ses trois premiers ne couvrent "
                "que 33 % des cas. Le score exact n'est pas previsible au-dela, "
                "par personne. La seule mesure qui juge vraiment est le "
                "log-loss du score REEL, quel que soit son rang : il compare la "
                "probabilite donnee a ce qui s'est produit. L'ecart a la "
                "reference dit ce que le modele apporte en connaissant les deux "
                "equipes, par rapport a la meme grille batie sur les seules "
                "moyennes de la competition ; negatif = il apporte quelque "
                "chose.",
                74,
            ):
                lines.append("    " + chunk)

        if report.get("erreur_buts") is not None:
            lines.append("")
            lines.append(
                "  Erreur absolue moyenne sur le total de buts : %.2f"
                % report["erreur_buts"]
            )

        # Calibration : le Brier dit si le modele classe bien, pas si ses
        # pourcentages sont sinceres. Une tranche annoncee a 70 % qui ne se
        # realise qu'une fois sur deux rend l'affichage trompeur, meme quand
        # l'ordre des propositions est bon.
        bands = report.get("calibration") or []
        if bands:
            lines.append("")
            lines.append(
                "  %-12s %8s %10s %10s" % ("Tranche", "Cas", "Annonce", "Observe")
            )
            lines.append("  " + "-" * 44)
            for band in bands:
                lines.append(
                    "  %-12s %8d %10s %10s"
                    % (
                        band["tranche"],
                        band["matchs"],
                        _pct(band["annonce"]),
                        _pct(band["observe"]),
                    )
                )

        # Corners, tirs et cartons : le Brier ne juge que l'issue, donc ces
        # colonnes n'avaient aucune garantie derriere elles. Le biais compte
        # autant que l'erreur : une erreur acceptable peut cacher un exces
        # systematique dans un sens.
        quantities = [q for q in report.get("grandeurs") or [] if q.get("matchs")]
        if quantities:
            lines.append("")
            lines.append(
                "  %-16s %7s %9s %8s %8s %8s"
                % ("Grandeur", "Matchs", "Biais", "MAE", "Prevu", "Reel")
            )
            lines.append("  " + "-" * 62)
            for quantity in quantities:
                error = quantity.get("biais_erreur_type")
                lines.append(
                    "  %-16s %7d %9s %8.2f %8.2f %8.2f"
                    % (
                        _truncate(quantity["grandeur"], 16),
                        quantity["matchs"],
                        "%+.2f" % quantity["biais"]
                        + (" " if error is None else ""),
                        quantity["mae"],
                        quantity["prevu_moyen"],
                        quantity["reel_moyen"],
                    )
                )
            lines.append("")
            for chunk in _wrap(
                "Biais positif = le modele annonce trop. Il se lit avec son "
                "erreur type : un biais inferieur a deux fois celle-ci ne se "
                "distingue pas de zero.",
                74,
            ):
                lines.append("    " + chunk)

        # Les propositions : ce que la fiche engage reellement. Le Brier juge
        # l'issue, le biais juge les nombres attendus ; ni l'un ni l'autre ne
        # dit si « Plus de 1.5 buts, 86 % » tient sa promesse. C'est pourtant la
        # seule ligne sur laquelle un lecteur decide.
        families = [f for f in report.get("propositions") or [] if f.get("propositions")]
        if families:
            lines.append("")
            lines.append(
                "  %-24s %8s %9s %9s %8s"
                % ("Propositions", "Nombre", "Annonce", "Observe", "Ecart")
            )
            lines.append("  " + "-" * 62)
            for family in families:
                lines.append(
                    "  %-24s %8d %9s %9s %8s"
                    % (
                        _truncate(family["famille"], 24),
                        family["propositions"],
                        _pct(family["annonce_moyen"]),
                        _pct(family["observe"]),
                        "%+.1f pt%s"
                        % (100 * family["ecart"], " *" if family["significatif"] else ""),
                    )
                )
            lines.append("")
            for chunk in _wrap(
                "Chaque proposition est enumeree avec son complementaire : le "
                "resume ne porte donc que sur celle des deux ou le modele "
                "penche (probabilite >= 50 %), sans quoi annonce et observe "
                "vaudraient 50 % par construction. Ecart positif = le modele "
                "promet plus qu'il ne tient ; une etoile signale un ecart de "
                "plus de deux erreurs types.",
                74,
            ):
                lines.append("    " + chunk)

        # Le tableau precedent juge les propositions en moyenne. Une moyenne
        # juste peut recouvrir deux erreurs egales et opposees : c'est ce que
        # cherche celui-ci, en ventilant les memes propositions selon le
        # desequilibre attendu de l'affiche et selon le cote qu'elles engagent.
        conditionnelles = [
            c for c in report.get("calibration_conditionnelle") or []
            if c.get("tranches")
        ]
        if conditionnelles:
            lines.append("")
            lines.append("  Calibration selon le desequilibre de l'affiche")
            lines.append("  " + "-" * 46)
            lines.append(
                "  %-14s %10s %7s %11s %11s %11s %11s"
                % ("Grandeur", "Ecart lam", "Matchs", "Favori", "Outsider",
                   "Total", "Asymetrie")
            )
            lines.append("  " + "-" * 70)
            for bloc in conditionnelles:
                for tranche in bloc["tranches"]:
                    asymetrie = tranche.get("asymetrie") or {}
                    lines.append(
                        "  %-14s %10s %7s %11s %11s %11s %11s"
                        % (
                            _truncate(bloc["grandeur"], 14)
                            if tranche["tranche"] == 1
                            else "",
                            "%.2f-%.2f"
                            % (
                                tranche["desequilibre_min"],
                                tranche["desequilibre_max"],
                            ),
                            # Le compte de MATCHS, seul denominateur honnete :
                            # un match fournit une dizaine de propositions, et
                            # afficher celles-ci laisserait croire a une
                            # precision que l'echantillon n'a pas.
                            max(
                                (tranche[cote]["matchs"]
                                 for cote in ("favori", "outsider", "total")
                                 if tranche.get(cote)),
                                default=0,
                            ),
                            # Chaque cote porte son propre test, et pas
                            # seulement l'asymetrie : la signature attendue des
                            # cartons est un ecart sur le TOTAL, qui sans etoile
                            # se lirait a l'oeil.
                            *[
                                "%+.1f pt%s"
                                % (
                                    100 * tranche[cote]["ecart"],
                                    " *"
                                    if abs(tranche[cote]["ecart"])
                                    > 2 * tranche[cote]["erreur_type"]
                                    else "",
                                )
                                if tranche.get(cote)
                                else "-"
                                for cote in ("favori", "outsider", "total")
                            ],
                            "%+.1f pt%s"
                            % (
                                100 * asymetrie["valeur"],
                                " *" if asymetrie.get("significatif") else "",
                            )
                            if asymetrie
                            else "-",
                        )
                    )
            lines.append("")
            for chunk in _wrap(
                "Les quatre grandeurs passent par la meme structure -- attaque "
                "x defense, chaque force agissant separement --, etablie pour "
                "les buts seulement. Chacune a donc une signature attendue "
                "differente, et c'est leur concordance qui vaut preuve, pas un "
                "ecart isole. BUTS : rien nulle part, c'est le temoin ; si "
                "cette ligne s'allume, c'est la mesure qui est fausse. CORNERS "
                "et TIRS CADRES : l'equipe menee pousse, celle qui mene gere, "
                "donc l'asymetrie grandit avec le desequilibre pendant que le "
                "Total reste plat, les deux effets se compensant dans la somme. "
                "CARTONS : signature inverse -- un match serre se hache, un "
                "match plie s'apaise --, donc c'est le Total qui doit bouger et "
                "non l'asymetrie ; mais le premier responsable des cartons est "
                "l'arbitre (critere 12, a poids zero faute de mesure), et un "
                "effet trouve ici devra etre confirme a arbitre comparable. Une "
                "etoile signale un ecart de plus de deux erreurs types ; sans "
                "etoile, il n'y a rien a conclure et rien a corriger. Les "
                "erreurs types sont groupees par match, et non par proposition "
                "-- un meme match en fournit une dizaine, tirees du meme lambda "
                "et du meme resultat, qui se realisent ou echouent ensemble. "
                "C'est la colonne Matchs qui dit ce que la mesure vaut.",
                74,
            ):
                lines.append("    " + chunk)

        # Les matchs ou le modele s'est le plus trompe : c'est la qu'on voit ce
        # qu'il ne sait pas modeliser (un carton rouge, une equipe remaniee).
        worst = sorted(report.get("details") or [], key=lambda row: row["p_issue"])[:5]
        if worst:
            lines.append("")
            lines.append(
                "  %-34s %7s %8s %9s %7s"
                % ("Previsions les plus fausses", "Score", "p(issue)", "Buts prevus", "Reels")
            )
            lines.append("  " + "-" * 70)
            for row in worst:
                lines.append(
                    "  %-34s %7s %8s %9.2f %7d"
                    % (
                        _truncate(row["match"], 34),
                        row["score"],
                        _pct(row["p_issue"]),
                        row["attendu"],
                        row["reel"],
                    )
                )

    reglages = report.get("reglages") or {}
    if reglages.get("classement"):
        lines.append("")
        lines.append("  Reglage compare : %s" % reglages["cle"])
        lines.append("  " + "-" * (20 + len(reglages["cle"])))
        montre_score = any(
            "score_log_loss" in row for row in reglages["classement"]
        )
        lines.append(
            "  %-12s %7s %9s %8s%s   %s"
            % (
                "Valeur", "Matchs", "Log-loss", "Brier",
                "   Score" if montre_score else "",
                "Ecart appariee (issue)",
            )
        )
        for row in reglages["classement"]:
            gap = row.get("vs_neutre")
            lines.append(
                "  %-12g %7d %9.4f %8.4f%s   %s"
                % (
                    getattr(row["params"], reglages["cle"]),
                    row["matchs"],
                    row["log_loss"],
                    row["brier"],
                    "  %7.4f" % row["score_log_loss"]
                    if montre_score and "score_log_loss" in row
                    else ("        -" if montre_score else ""),
                    "reference"
                    if not gap or gap.get("ecart") == 0
                    else "%s +/- %.4f (t = %+.1f)"
                    % (_signed(gap["ecart"]), gap["erreur_type"], gap["t"]),
                )
            )
        if montre_score:
            lines.append("")
            lines.append("  Le meme classement, juge sur la GRILLE DES SCORES :")
            for row in reglages["classement"]:
                gap = row.get("vs_neutre_score")
                lines.append(
                    "  %-12g %s"
                    % (
                        getattr(row["params"], reglages["cle"]),
                        "reference"
                        if not gap or gap.get("ecart") == 0
                        else "%s +/- %.4f (t = %+.1f)"
                        % (_signed(gap["ecart"]), gap["erreur_type"], gap["t"]),
                    )
                )
        lines.append("")
        for chunk in _wrap(
            "Classe par log-vraisemblance, la mesure que Dixon et Coles "
            "minimisent : c'est elle qui punit le plus nettement une certitude "
            "erronee. Les reglages voient exactement les memes rencontres, avec "
            "le meme historique et la meme coupure -- sans quoi on comparerait "
            "des moyennes calculees sur des matchs differents. Ecart negatif = "
            "mieux que la valeur de reference (la premiere donnee). Au-dela de "
            "|t| = 2 seulement, l'ecart ne s'explique plus par le hasard de "
            "l'echantillon : en deca, le classement ne justifie AUCUN "
            "changement, meme s'il en designe un premier. Les deux colonnes "
            "d'ecart ne jugent pas la meme chose et peuvent se contredire : "
            "certains reglages -- `estimation_dispersion`, `rho` -- agissent "
            "d'abord sur la grille des scores et ne touchent l'issue "
            "qu'indirectement. C'est celle qui correspond a l'usage vise qui "
            "doit trancher.",
            74,
        ):
            lines.append("    " + chunk)

    contexte = report.get("contexte") or []
    if contexte:
        lines.append("")
        lines.append("  Avec et sans les quatorze criteres")
        lines.append("  " + "-" * 34)
        lines.append(
            "  %-24s %7s %9s   %s"
            % ("Variante", "Matchs", "Log-loss", "Ecart apparie")
        )
        for row in contexte:
            gap = row.get("vs_neutre")
            lines.append(
                "  %-24s %7d %9.4f   %s"
                % (
                    _truncate(str(row["params"]), 24),
                    row["matchs"],
                    row["log_loss"],
                    "reference"
                    if not gap
                    else "%s +/- %.4f (t = %+.1f)"
                    % (_signed(gap["ecart"]), gap["erreur_type"], gap["t"]),
                )
            )
        lines.append("")
        for chunk in _wrap(
            "Les deux variantes voient exactement les memes rencontres, avec le "
            "meme historique et la meme coupure : c'est le seul protocole qui "
            "permette de dire si le contexte apporte quelque chose. Le "
            "classement et l'arbitre sont coupes -- sur un match deja joue, le "
            "premier contient le resultat cherche et le second n'aurait pas ete "
            "connu avant. Cette mesure ne juge donc que les criteres qui, eux, "
            "se reconstituent a la date du match.",
            74,
        ):
            lines.append("    " + chunk)

    causes = report.get("causes_exclusion") or {}
    if causes:
        lines.append("")
        lines.append("  Matchs ecartes, par cause :")
        for cause, count in sorted(causes.items(), key=lambda c: -c[1]):
            lines.append("    %-58s %4d" % (_truncate(cause, 58), count))

    lines.append("")
    return "\n".join(lines)


def _stat(value: Any) -> str:
    return "-" if value is None else str(value)


def _wrap(text: str, width: int) -> list[str]:
    words, line, out = text.split(), "", []
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = "%s %s" % (line, word) if line else word
    if line:
        out.append(line)
    return out


def flatten_stats(match: dict[str, Any]) -> dict[str, Any]:
    """Aplatit les stats en colonnes `<champ>_dom` / `<champ>_ext` pour le CSV."""
    stats = match.get("stats")
    if not stats:
        return {}
    flat: dict[str, Any] = {"arbitre": stats.get("arbitre") or ""}
    for field, _ in STAT_ORDER:
        flat[field + "_dom"] = stats["domicile"].get(field)
        flat[field + "_ext"] = stats["exterieur"].get(field)
    return flat


def stat_columns() -> list[str]:
    columns = ["arbitre"]
    for field, _ in STAT_ORDER:
        columns.extend([field + "_dom", field + "_ext"])
    return columns
