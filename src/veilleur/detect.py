"""F4 : détection des transitions positives — F5 : dé-doublonnage.

La comparaison est « sur front » : chaque relevé est comparé au précédent (persisté),
une alerte n'est émise qu'au moment exact de la transition. Comme l'état est écrit
AVANT l'envoi de la notification (voir runner), un même front ne peut jamais produire
deux alertes, y compris après un crash ou un redémarrage (F5, §5.2).

Transitions positives reconnues :
- reouverture        : catégorie connue, indisponible -> disponible ;
- nouvelle_categorie : catégorie jamais vue, qui apparaît disponible ;
- quota_augmente     : catégorie disponible dont le nombre de places augmente
                       (seulement si le site expose les quantités).
Premier relevé d'un événement = baseline : aucune alerte (l'état existant au
lancement n'est pas une « nouvelle » disponibilité).
"""
from __future__ import annotations

from typing import Sequence

from veilleur.models import Alerte, CategoryAvailability


def en_etat(categories: Sequence[CategoryAvailability]) -> dict[str, dict]:
    """Forme persistable d'un relevé : mapping catégorie -> état."""
    return {
        c.categorie: {"disponible": c.disponible, "places": c.places, "prix": c.prix}
        for c in categories
    }


def comparer(
    precedent: dict[str, dict] | None, categories: Sequence[CategoryAvailability]
) -> list[Alerte]:
    """Compare le relevé courant à l'état persisté. `precedent` vaut None tant
    qu'aucune baseline n'existe (premier relevé : toujours zéro alerte)."""
    if precedent is None:
        return []

    alertes: list[Alerte] = []
    for cat in categories:
        avant = precedent.get(cat.categorie)
        if avant is None:
            if cat.disponible:
                alertes.append(Alerte("nouvelle_categorie", cat.categorie, places=cat.places))
            continue
        if cat.disponible and not avant.get("disponible"):
            alertes.append(Alerte("reouverture", cat.categorie, places=cat.places))
            continue
        places_avant = avant.get("places")
        if (
            cat.disponible
            and avant.get("disponible")
            and places_avant is not None
            and cat.places is not None
            and cat.places > places_avant
        ):
            alertes.append(
                Alerte("quota_augmente", cat.categorie, places=cat.places, delta=cat.places - places_avant)
            )
    return alertes
