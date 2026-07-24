"""Modèles de données (F3) et erreurs typées de récupération."""
from __future__ import annotations

from dataclasses import dataclass


class FetchError(Exception):
    """Échec de récupération (réseau, HTTP inattendu)."""


class PageIntrouvable(FetchError):
    """HTTP 404/410 : la page de l'événement n'existe plus."""


class RateLimited(FetchError):
    """HTTP 429/503 ou file d'attente : le site demande de ralentir."""


class ParseInvalide(FetchError):
    """Réponse 200 mais corps inexploitable."""


@dataclass(frozen=True)
class CategoryAvailability:
    """Disponibilité d'une catégorie (ou séance) au moment du relevé."""

    categorie: str
    disponible: bool
    places: int | None = None  # nombre de places si exposé par le site, sinon None
    prix: str | None = None


@dataclass(frozen=True)
class AvailabilitySnapshot:
    """F3 : état normalisé d'un événement à un instant donné."""

    event_key: str
    tm_event_id: str
    horodatage_utc: str
    categories: tuple[CategoryAvailability, ...]


@dataclass(frozen=True)
class Alerte:
    """Transition positive détectée (F4)."""

    type: str  # 'reouverture' | 'nouvelle_categorie' | 'quota_augmente'
    categorie: str
    places: int | None = None
    delta: int | None = None
