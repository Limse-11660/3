"""F2 : récupération de l'état par requête HTTP directe (aucun navigateur).

Endpoint public utilisé (JSON, non protégé) :
GET https://www.ticketmaster.fr/api/liste-seance/manifestation/idmanif/{id}/idtier/{idtier}
    ?codLang=FR&codmodco=WEB

§5.1 : requêtes conditionnelles — si la réponse porte ETag/Last-Modified, les
validateurs sont renvoyés (If-None-Match / If-Modified-Since) et un 304 réutilise le
dernier corps reçu. Reprise sur erreur réseau : 3 tentatives (backoff 1 s puis 2 s).
Politesse : au moins 1 s entre deux requêtes, quelle qu'en soit la cible.

Limite assumée du niveau « séance » : le site n'expose pas le nombre de places à ce
niveau — `places` reste None et les alertes l'indiquent honnêtement.
"""
from __future__ import annotations

import logging
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from veilleur.config import Evenement
from veilleur.models import (
    AvailabilitySnapshot,
    CategoryAvailability,
    FetchError,
    PageIntrouvable,
    ParseInvalide,
    RateLimited,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ticketmaster.fr"
IDTIER_DEFAUT = "78768"
TENTATIVES_RESEAU = 3
BACKOFF_RESEAU_S = (1.0, 2.0)
ESPACEMENT_REQUETES_S = 1.0


def normaliser_libelle(texte: str) -> str:
    """Minuscules et sans accents, pour comparer les libellés de catégories."""
    decompose = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in decompose if not unicodedata.combining(c))


def _fusionner_prix(a: str | None, b: Any) -> str | None:
    """Prix minimum connu entre l'existant et une nouvelle séance de même libellé."""
    if b is None:
        return a
    if a is None:
        return str(b)
    try:
        return str(min(float(a), float(b)))
    except (TypeError, ValueError):
        return a


def parse_payload(payload: Any, filtres: tuple[str, ...] = ()) -> list[CategoryAvailability]:
    """Normalise la réponse liste-seance (F3). Une liste vide est un état valide
    (plus rien à la vente), pas une erreur de parsing.

    Les séances partageant un même libellé sont FUSIONNÉES (disponible si l'une l'est,
    prix minimum connu) : une clé doit être unique dans un relevé, sinon l'état persisté
    oscillerait entre les doublons et rejouerait la même alerte à chaque relevé (F5).
    """
    if not isinstance(payload, list):
        raise ParseInvalide(f"réponse inattendue (type {type(payload).__name__})")
    filtres_norm = [normaliser_libelle(f) for f in filtres]
    fusion: dict[str, CategoryAvailability] = {}  # dict ordonné : ordre du payload conservé
    for seance in payload:
        if not isinstance(seance, dict):
            raise ParseInvalide("élément de liste inattendu (pas un objet)")
        labels = seance.get("labels") or []
        libelle = str(labels[0]) if labels else str(seance.get("dateSeance") or seance.get("idSeance") or "?")
        if filtres_norm and not any(f in normaliser_libelle(libelle) for f in filtres_norm):
            continue
        disponible = bool(seance.get("available"))
        prix = seance.get("priceMin")
        existant = fusion.get(libelle)
        if existant is None:
            fusion[libelle] = CategoryAvailability(
                categorie=libelle,
                disponible=disponible,
                places=None,  # non exposé par l'endpoint liste-seance
                prix=str(prix) if prix is not None else None,
            )
        else:
            fusion[libelle] = CategoryAvailability(
                categorie=libelle,
                disponible=existant.disponible or disponible,
                places=existant.places,
                prix=_fusionner_prix(existant.prix, prix),
            )
    return list(fusion.values())


@dataclass
class _Validateurs:
    etag: str | None
    last_modified: str | None
    payload: Any


class ClientTicketmaster:
    """Client HTTP direct. `session` et `sleep` sont injectables pour les tests."""

    def __init__(
        self,
        user_agent: str,
        timeout_s: float,
        idtier: str = IDTIER_DEFAUT,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_304: Callable[[], None] | None = None,
    ):
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self._timeout = timeout_s
        self._idtier = idtier
        self._sleep = sleep
        self._on_304 = on_304
        self._validateurs: dict[str, _Validateurs] = {}
        self._derniere_requete = 0.0

    def relever(self, ev: Evenement) -> AvailabilitySnapshot:
        url = (
            f"{BASE_URL}/api/liste-seance/manifestation/idmanif/{ev.tm_event_id}"
            f"/idtier/{self._idtier}?codLang=FR&codmodco=WEB"
        )
        cache = self._validateurs.get(ev.tm_event_id)
        conditionnels: dict[str, str] = {}
        if cache is not None:
            if cache.etag:
                conditionnels["If-None-Match"] = cache.etag
            if cache.last_modified:
                conditionnels["If-Modified-Since"] = cache.last_modified

        reponse = self._requete(url, conditionnels, contexte=ev.libelle)

        if reponse.status_code == 304:
            if cache is None:
                raise FetchError("HTTP 304 reçu sans requête conditionnelle")
            logger.debug("%s : 304 Not Modified — corps précédent réutilisé", ev.libelle)
            if self._on_304 is not None:
                self._on_304()
            payload = cache.payload
        else:
            payload = self._interpreter(reponse, ev)

        return AvailabilitySnapshot(
            event_key=ev.cle,
            tm_event_id=ev.tm_event_id,
            horodatage_utc=datetime.now(timezone.utc).isoformat(),
            categories=tuple(parse_payload(payload, ev.categories)),
        )

    def _espacer(self) -> None:
        attente = ESPACEMENT_REQUETES_S - (time.monotonic() - self._derniere_requete)
        if attente > 0:
            self._sleep(attente)
        self._derniere_requete = time.monotonic()

    def _requete(self, url: str, headers: dict[str, str], contexte: str) -> requests.Response:
        derniere: Exception | None = None
        for tentative in range(TENTATIVES_RESEAU):
            if tentative:
                self._sleep(BACKOFF_RESEAU_S[tentative - 1])
            self._espacer()
            try:
                return self._session.get(
                    url, headers=headers or None, timeout=self._timeout, allow_redirects=False
                )
            except requests.RequestException as exc:
                derniere = exc
                logger.warning(
                    "%s : échec réseau (tentative %d/%d) : %s", contexte, tentative + 1, TENTATIVES_RESEAU, exc
                )
        raise FetchError(f"échec réseau après {TENTATIVES_RESEAU} tentatives : {derniere}")

    def _interpreter(self, reponse: requests.Response, ev: Evenement) -> Any:
        code = reponse.status_code
        if 300 <= code < 400:
            destination = reponse.headers.get("location", "")
            if "wait.ticketmaster" in destination or "queue" in destination.lower():
                raise RateLimited(f"file d'attente active ({destination!r})")
            raise FetchError(f"redirection inattendue {code} -> {destination!r}")
        if code in (404, 410):
            raise PageIntrouvable(f"HTTP {code}")
        if code in (429, 503):
            raise RateLimited(f"HTTP {code}")
        if code != 200:
            raise FetchError(f"HTTP {code}: {reponse.text[:120]!r}")
        try:
            payload = reponse.json()
        except ValueError as exc:
            raise ParseInvalide(f"corps non-JSON (challenge probable) : {exc}") from exc

        etag = reponse.headers.get("ETag")
        last_modified = reponse.headers.get("Last-Modified")
        if etag or last_modified:
            self._validateurs[ev.tm_event_id] = _Validateurs(etag, last_modified, payload)
        else:
            self._validateurs.pop(ev.tm_event_id, None)
        return payload
