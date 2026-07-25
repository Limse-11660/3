"""F11 : persistance de l'état entre deux exécutions.

Fichier JSON unique, écriture atomique (fichier temporaire puis os.replace) : un
arrêt brutal ne peut pas laisser un état à moitié écrit. Un fichier corrompu est
mis de côté (.corrompu) et l'outil repart sur un état neuf plutôt que de planter
(§5.2 : reprise après redémarrage).
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

VERSION = 1

COMPTEURS_DEFAUT = {"verifications": 0, "alertes": 0, "erreurs": 0, "reponses_304": 0}


class Etat:
    def __init__(self, chemin: str):
        self.chemin = chemin
        self.data: dict = {
            "version": VERSION,
            "evenements": {},
            "compteurs": dict(COMPTEURS_DEFAUT),
        }

    @classmethod
    def charger(cls, chemin: str) -> "Etat":
        etat = cls(chemin)
        if not os.path.exists(chemin):
            return etat
        try:
            with open(chemin, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("evenements"), dict):
                raise ValueError("structure inattendue")
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            corrompu = chemin + ".corrompu"
            logger.warning("état illisible (%s) — mis de côté dans %s, reprise à neuf", exc, corrompu)
            try:
                os.replace(chemin, corrompu)
            except OSError:
                pass
            return etat

        # Assainissement : une entrée d'événement mal formée (types inattendus) ne doit
        # pas mettre l'événement en échec permanent — elle est écartée (nouvelle baseline).
        for cle, valeur in data["evenements"].items():
            cats = valeur.get("categories") if isinstance(valeur, dict) else "invalide"
            feuilles_ok = cats is None or (
                isinstance(cats, dict) and all(isinstance(v, dict) for v in cats.values())
            )
            if isinstance(valeur, dict) and feuilles_ok:
                etat.data["evenements"][cle] = valeur
            else:
                logger.warning("état de %r mal formé — entrée écartée, nouvelle baseline", cle)
        compteurs = data.get("compteurs")
        if isinstance(compteurs, dict):
            for cle in COMPTEURS_DEFAUT:
                valeur = compteurs.get(cle)
                if isinstance(valeur, int):
                    etat.data["compteurs"][cle] = valeur
        return etat

    def sauver(self) -> None:
        tmp = self.chemin + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # durabilité : pas de state.json tronqué sur coupure
        os.replace(tmp, self.chemin)

    def sauver_sans_faute(self) -> bool:
        """Sauvegarde qui n'interrompt JAMAIS la surveillance (§5.2) : un verrou Windows
        ou un disque plein est journalisé, l'état reste en mémoire et le prochain relevé
        retentera l'écriture. Retourne False en cas d'échec."""
        try:
            self.sauver()
            return True
        except OSError as exc:
            logger.error("échec d'écriture de l'état (%s) — poursuite, nouvel essai au prochain relevé", exc)
            return False

    def evenement(self, cle: str) -> dict:
        """État persisté d'un événement (créé vide au premier accès)."""
        return self.data["evenements"].setdefault(cle, {})

    def incrementer(self, compteur: str, n: int = 1) -> None:
        """§5.3 : compteurs d'activité."""
        self.data["compteurs"][compteur] = self.data["compteurs"].get(compteur, 0) + n

    def compteurs(self) -> dict:
        return dict(self.data["compteurs"])
