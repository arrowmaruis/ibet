"""Modele auxiliaire des buts attendus (xG).

Grandeur estimee mais jamais pariee. Elle passe par exactement le meme moteur
que les buts -- moyenne de la competition, forces d'attaque et de defense,
regularisation -- ce qui en fait une SECONDE ESTIMATION de la meme quantite, et
non une grandeur de plus. Le critere 11 la confronte aux buts et le modele des
buts la melange a la sienne ; sans contexte, elle n'est pas calculee du tout.

Ses seuils ne servent a rien et ne sont jamais lus : aucune proposition n'est
construite sur elle.
"""

from __future__ import annotations

from .base import ModeleEvenement


class ModeleXG(ModeleEvenement):
    cle = "xg"
    version = "1.0.0"
    libelle = "Buts attendus"
    champ = "xg"
    seuil = 2.5
    seuils_equipe = (0.5, 1.5, 2.5)
    seuils_total = (1.5, 2.5, 3.5)
