"""F6 : notification par webhook Discord (embed + texte compact).

Sur HTTP 429, l'attente demandée par Discord (en-tête Retry-After ou champ
retry_after du corps JSON) est respectée, plafonnée pour ne jamais bloquer la
boucle trop longtemps. Cloudflare (devant l'API Discord) exige un User-Agent
explicite pour accepter les POST.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from veilleur.models import Alerte

logger = logging.getLogger(__name__)

TENTATIVES = 3
ATTENTE_429_DEFAUT_S = 2.0
ATTENTE_429_MAX_S = 30.0
# Limites Discord : content <= 2000, description d'embed <= 4096. Un message trop long
# serait refusé (400) et l'alerte perdue — on borne avec marge.
MAX_LIGNES_ALERTE = 15
LIMITE_DESCRIPTION = 4000
LIMITE_CONTENU = 1900
ATTENTE_ERREUR_RESEAU_S = 1.0
USER_AGENT = "veilleur-tm/1.0 (usage personnel)"
COULEUR_DISPONIBILITE = 0x2ECC71  # vert
COULEUR_TEST = 0x3498DB  # bleu


def _heure_locale() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M:%S")


def _ligne_alerte(a: Alerte) -> str:
    if a.type == "quota_augmente":
        total = f" (total {a.places})" if a.places is not None else ""
        return f"**{a.categorie}** : +{a.delta} place(s){total}"
    if a.places is not None:
        return f"**{a.categorie}** : {a.places} place(s) disponibles"
    return f"**{a.categorie}** : places disponibles (nombre non exposé par le site)"


def construire_message(libelle: str, url: str, alertes: list[Alerte]) -> dict[str, Any]:
    """Message Discord d'une détection : catégories concernées + places (F6)."""
    lignes = [_ligne_alerte(a) for a in alertes[:MAX_LIGNES_ALERTE]]
    if len(alertes) > MAX_LIGNES_ALERTE:
        lignes.append(f"… et {len(alertes) - MAX_LIGNES_ALERTE} autre(s) catégorie(s)")
    embed = {
        "title": f"🎟️ {libelle} — disponibilité détectée",
        "color": COULEUR_DISPONIBILITE,
        "url": url,
        "description": "\n".join(lignes)[:LIMITE_DESCRIPTION],
        "footer": {"text": _heure_locale()},
    }
    contenu = (f"🎟️ **{libelle}** — " + " ; ".join(a.categorie for a in alertes))[:LIMITE_CONTENU]
    return {"content": contenu, "embeds": [embed]}


def message_test() -> dict[str, Any]:
    return {
        "embeds": [
            {
                "title": "🔧 veilleur — notification de test",
                "color": COULEUR_TEST,
                "description": "Le webhook Discord fonctionne. (commande `test-notify`)",
                "footer": {"text": _heure_locale()},
            }
        ]
    }


def _attente_429(reponse: Any) -> float:
    delai: float | None = None
    brut = reponse.headers.get("Retry-After") if reponse.headers else None
    if brut is not None:
        try:
            delai = float(brut)
        except (TypeError, ValueError):
            delai = None
    if delai is None:
        try:
            delai = float(reponse.json()["retry_after"])
        except Exception:  # noqa: BLE001 — corps absent/illisible : défaut
            delai = ATTENTE_429_DEFAUT_S
    return min(max(delai, 0.0), ATTENTE_429_MAX_S)


def envoyer(
    webhook_url: str,
    message: dict[str, Any],
    timeout_s: float = 15,
    post: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poste le message. True si HTTP 2xx. Ne lève jamais : la surveillance continue
    même si Discord est injoignable (l'échec est journalisé)."""
    faire_post = post or requests.post
    derniere: str | None = None
    for tentative in range(1, TENTATIVES + 1):
        try:
            reponse = faire_post(
                webhook_url, json=message, timeout=timeout_s, headers={"User-Agent": USER_AGENT}
            )
        except requests.RequestException as exc:
            derniere = f"{type(exc).__name__}: {exc}"
            logger.warning("envoi Discord : échec réseau (tentative %d/%d) : %s", tentative, TENTATIVES, derniere)
            if tentative < TENTATIVES:
                sleep(ATTENTE_ERREUR_RESEAU_S)
            continue
        if 200 <= reponse.status_code < 300:
            return True
        derniere = f"HTTP {reponse.status_code}"
        if reponse.status_code == 429 and tentative < TENTATIVES:
            attente = _attente_429(reponse)
            logger.warning("envoi Discord : 429, attente %.1f s avant nouvel essai", attente)
            sleep(attente)
            continue
        logger.warning("envoi Discord : refus (tentative %d/%d) : %s", tentative, TENTATIVES, derniere)
    logger.error("envoi Discord : échec définitif après %d tentative(s) : %s", TENTATIVES, derniere)
    return False
