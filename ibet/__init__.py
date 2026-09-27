"""iBET : prevision de matchs de football, un modele dedie par evenement.

Organisation du paquet, dans l'ordre ou les donnees le traversent :

    sources/      d'ou viennent les donnees : flux des matchs, cache, certificats
    stockage/     ou elles sont gardees : base des previsions, arbitres, catalogue
    collecte/     ce qui remplit le stockage apres coup : resultats, feuilles, joueurs
    modeles/      un modele par evenement (buts, issue, corners, tirs, cartons)
    prevision/    ce qui fait travailler les modeles : orchestration, contexte,
                  notes de force, emission des fiches, confrontation au marche
    evaluation/   ce qui juge les previsions : verification, banc d'essai,
                  etude des versions, mesures
    interfaces/   ce que l'on appelle : ligne de commande, API HTTP, rendu

    chemins.py    ou sont les donnees (dossier `donnees/`)
    __main__.py   `python -m ibet <commande>`

Les dependances vont du haut vers le bas de cette liste, jamais l'inverse : un
modele ne lit pas la base, une source ne connait pas les modeles.
"""
