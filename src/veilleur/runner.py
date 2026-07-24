"""Boucle de surveillance — F2 (périodique), F7 (intervalle + jitter), §5.2 (robustesse).

Chaque événement a sa propre échéance et son propre backoff : sur échec, son
intervalle double (plafonné à backoff_max_secondes) sans toucher aux autres — un
événement en erreur n'arrête ni ne ralentit les autres (§5.2). Le jitter est un
retard aléatoire 0..N s ajouté à chaque échéance (F7).

Ordre d'un relevé : fetch → comparaison → PERSISTANCE → notification. L'état est
écrit avant l'envoi Discord : un crash pendant l'envoi ne rejoue jamais l'alerte
au redémarrage (F5).
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable

from veilleur import notify
from veilleur.config import Config, Evenement
from veilleur.detect import comparer, en_etat
from veilleur.fetch import ClientTicketmaster
from veilleur.models import Alerte, FetchError, PageIntrouvable, RateLimited
from veilleur.state import Etat

logger = logging.getLogger(__name__)

PAUSE_MAX_S = 5.0  # granularité de la boucle (réactivité au Ctrl+C)
SYNTHESE_INTERVALLE_S = 3600  # §5.3 : ligne de compteurs au plus toutes les heures


class Veilleur:
    def __init__(
        self,
        config: Config,
        etat: Etat,
        client: ClientTicketmaster,
        sleep: Callable[[float], None] = time.sleep,
        monotone: Callable[[], float] = time.monotonic,
        alea: Callable[[float, float], float] = random.uniform,
    ):
        self.config = config
        self.etat = etat
        self.client = client
        self._sleep = sleep
        self._monotone = monotone
        self._alea = alea
        self._echeances: dict[str, float] = {}
        self._erreurs: dict[str, int] = {}  # échecs consécutifs par événement (backoff)
        self._derniere_synthese = 0.0

    # --- un relevé ---

    def verifier_evenement(self, ev: Evenement) -> list[Alerte]:
        """Relevé complet d'un événement. Lève une FetchError en cas d'échec."""
        snapshot = self.client.relever(ev)
        persiste = self.etat.evenement(ev.tm_event_id)
        precedent = persiste.get("categories")  # None au premier relevé (baseline)
        alertes = comparer(precedent, snapshot.categories)

        persiste["categories"] = en_etat(snapshot.categories)
        persiste["dernier_releve_utc"] = snapshot.horodatage_utc
        persiste["libelle"] = ev.libelle
        self.etat.incrementer("verifications")
        if alertes:
            self.etat.incrementer("alertes", len(alertes))
        self.etat.sauver()  # AVANT notification (F5)

        if precedent is None:
            logger.info(
                "%s : baseline établie (%d catégorie(s)) — aucune alerte sur l'existant",
                ev.libelle, len(snapshot.categories),
            )
        for a in alertes:
            logger.info("ALERTE %s : %s [%s]", ev.libelle, a.categorie, a.type)
        if alertes and self.config.webhook_discord:
            notify.envoyer(
                self.config.webhook_discord,
                notify.construire_message(ev.libelle, ev.url, alertes),
                timeout_s=self.config.timeout_secondes,
            )
        return alertes

    # --- isolation et backoff (§5.1 / §5.2) ---

    def _verifier_avec_isolation(self, ev: Evenement) -> None:
        try:
            self.verifier_evenement(ev)
        except (RateLimited, PageIntrouvable, FetchError) as exc:
            self._noter_erreur(ev, str(exc))
        except Exception:  # noqa: BLE001 — §5.2 : jamais d'effondrement global
            logger.exception("%s : erreur inattendue — les autres événements continuent", ev.libelle)
            self._noter_erreur(ev, "erreur interne")
        else:
            if self._erreurs.pop(ev.tm_event_id, 0):
                logger.info("%s : retour à la normale, intervalle nominal rétabli", ev.libelle)

    def _noter_erreur(self, ev: Evenement, motif: str) -> None:
        n = self._erreurs.get(ev.tm_event_id, 0) + 1
        self._erreurs[ev.tm_event_id] = n
        self.etat.incrementer("erreurs")
        self.etat.sauver()
        logger.warning("%s : relevé en échec (%s) — %d échec(s) consécutif(s)", ev.libelle, motif, n)

    def _prochain_delai(self, ev: Evenement) -> float:
        base = float(self.config.intervalle_secondes)
        n = self._erreurs.get(ev.tm_event_id, 0)
        delai = min(base * (2**n), float(self.config.backoff_max_secondes))
        if self.config.jitter_secondes:
            delai += self._alea(0.0, float(self.config.jitter_secondes))
        return delai

    # --- entrées CLI ---

    def balayer_une_fois(self) -> int:
        """check-once : une passe sur tous les événements."""
        for ev in self.config.evenements:
            self._verifier_avec_isolation(ev)
        return len(self.config.evenements)

    def boucle(self) -> None:
        """run : surveillance continue (interrompue par KeyboardInterrupt)."""
        maintenant = self._monotone()
        for ev in self.config.evenements:
            self._echeances[ev.tm_event_id] = maintenant  # premier relevé immédiat
        logger.info(
            "surveillance démarrée : %d événement(s), intervalle %d s (+ jitter 0..%d s)",
            len(self.config.evenements), self.config.intervalle_secondes, self.config.jitter_secondes,
        )
        self._derniere_synthese = maintenant
        while True:
            maintenant = self._monotone()
            for ev in self.config.evenements:
                if maintenant >= self._echeances[ev.tm_event_id]:
                    self._verifier_avec_isolation(ev)
                    self._echeances[ev.tm_event_id] = self._monotone() + self._prochain_delai(ev)
            if self._monotone() - self._derniere_synthese >= SYNTHESE_INTERVALLE_S:
                self._derniere_synthese = self._monotone()
                c = self.etat.compteurs()
                logger.info(
                    "compteurs : verifications=%d alertes=%d erreurs=%d reponses_304=%d",
                    c["verifications"], c["alertes"], c["erreurs"], c["reponses_304"],
                )
            prochaine = min(self._echeances.values())
            pause = prochaine - self._monotone()
            self._sleep(max(0.2, min(pause, PAUSE_MAX_S)))
