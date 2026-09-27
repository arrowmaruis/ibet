"""Genere un bundle de certificats compatible avec les antivirus / proxys HTTPS.

Certains antivirus (Avast, Kaspersky, ESET...) et les proxys d'entreprise
inspectent le trafic HTTPS en le re-signant avec leur propre autorite racine.
Python ne connait pas cette autorite : toute requete echoue alors avec
`SSL: CERTIFICATE_VERIFY_FAILED`.

Ce script fusionne les certificats publics de `certifi` avec la ou les
autorites locales detectees, puis ecrit le resultat dans `donnees/certificats/bundle.pem`.
Il suffit ensuite de renseigner CA_BUNDLE dans le fichier .env.

Usage :
    python -m ibet certificats                     # detection automatique
    python -m ibet certificats chemin/vers/ca.pem  # certificat fourni manuellement
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import certifi

from ibet import chemins

OUTPUT = chemins.CERTIFICATS

# Emplacements connus des autorites racine installees par des antivirus.
KNOWN_CA_PATHS = [
    r"C:\ProgramData\Avast Software\Avast\wscert.pem",
    r"C:\ProgramData\AVG\Antivirus\wscert.pem",
    r"C:\ProgramData\Kaspersky Lab\AVP21.3\Data\Cert\(fake)Kaspersky Anti-Virus personal root certificate.cer",
    r"C:\ProgramData\ESET\ESET Security\ca.pem",
]


def discover(explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(item) for item in explicit]

    found = []
    # NODE_EXTRA_CA_CERTS est souvent renseigne par ces memes antivirus.
    from_env = os.getenv("NODE_EXTRA_CA_CERTS", "").strip().strip('"')
    if from_env:
        found.append(Path(from_env))
    found.extend(Path(path) for path in KNOWN_CA_PATHS)

    seen, result = set(), []
    for path in found:
        resolved = str(path).replace("\\\\", "\\")
        if resolved in seen:
            continue
        seen.add(resolved)
        candidate = Path(resolved)
        if candidate.is_file():
            result.append(candidate)
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m ibet certificats",
        description="Genere donnees/certificats/bundle.pem (certifi + autorites "
        "locales detectees).",
    )
    parser.add_argument("certificats", nargs="*", help="Certificats .pem a ajouter")
    candidates = discover(parser.parse_args(argv).certificats)
    if not candidates:
        print(
            "Aucune autorite racine locale detectee.\n"
            "Si vos requetes echouent en SSL, exportez le certificat racine de "
            "votre antivirus / proxy puis relancez :\n"
            "    python -m ibet certificats chemin/vers/certificat.pem",
            file=sys.stderr,
        )
        return 1

    parts = [Path(certifi.where()).read_text(encoding="utf-8")]
    for path in candidates:
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            print("Ignore %s : %s" % (path, exc), file=sys.stderr)
            continue
        print("Ajoute : %s" % path)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(parts), encoding="utf-8")

    print("\nBundle ecrit : %s" % OUTPUT)
    print("Ajoutez cette ligne dans votre fichier .env :")
    print("    CA_BUNDLE=%s" % OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
