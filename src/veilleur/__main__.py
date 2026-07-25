"""F9 : interface en ligne de commande.

    python -m veilleur run           # surveillance continue
    python -m veilleur check-once    # une passe sur tous les événements, puis quitte
    python -m veilleur list          # affiche les événements configurés
    python -m veilleur test-notify   # envoie une notification de test sur le webhook

`--config chemin.yaml` s'écrit avant ou après la sous-commande (défaut : config.yaml).
"""
from __future__ import annotations

import argparse
import logging
import sys

from veilleur import notify
from veilleur.config import Config, ConfigError, load_config
from veilleur.fetch import ClientTicketmaster
from veilleur.runner import Veilleur
from veilleur.state import Etat

logger = logging.getLogger("veilleur")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS : une option absente n'écrit rien dans le namespace — sans quoi le défaut
    # du sous-parseur écraserait un --config fourni avant la sous-commande.
    common.add_argument("--config", default=argparse.SUPPRESS, help="chemin du fichier de configuration")

    parser = argparse.ArgumentParser(
        prog="veilleur",
        description="Moniteur de disponibilité Ticketmaster.fr — requêtes HTTP directes uniquement",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="commande", required=True)
    sub.add_parser("run", parents=[common], help="surveillance continue")
    sub.add_parser("check-once", parents=[common], help="une seule passe puis quitte")
    sub.add_parser("list", parents=[common], help="liste les événements configurés")
    sub.add_parser("test-notify", parents=[common], help="envoie une notification de test")
    return parser


def _setup_logging(verbosite: str) -> None:
    logging.basicConfig(
        level=getattr(logging, verbosite.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _construire(config: Config) -> Veilleur:
    etat = Etat.charger(config.etat_fichier)
    client = ClientTicketmaster(
        config.user_agent,
        config.timeout_secondes,
        on_304=lambda: etat.incrementer("reponses_304"),
    )
    return Veilleur(config, etat, client)


def cmd_list(config: Config) -> int:
    print(f"{len(config.evenements)} événement(s) surveillé(s) :")
    for ev in config.evenements:
        filtres = f" — catégories : {', '.join(ev.categories)}" if ev.categories else ""
        print(f"  [{ev.tm_event_id}] {ev.libelle}{filtres}")
        print(f"      {ev.url}")
    print(f"Webhook Discord : {'configuré' if config.webhook_discord else 'ABSENT (aucune notification)'}")
    print(f"Intervalle : {config.intervalle_secondes} s (+ jitter 0..{config.jitter_secondes} s)")
    return 0


def cmd_test_notify(config: Config) -> int:
    if not config.webhook_discord:
        print("webhook_discord absent de la configuration", file=sys.stderr)
        return 1
    ok = notify.envoyer(config.webhook_discord, notify.message_test(), timeout_s=config.timeout_secondes)
    print("Notification de test : " + ("envoyée" if ok else "ECHEC"))
    return 0 if ok else 1


def cmd_check_once(config: Config) -> int:
    veilleur = _construire(config)
    n = veilleur.balayer_une_fois()
    c = veilleur.etat.compteurs()
    print(
        f"{n} événement(s) vérifié(s) — compteurs cumulés : "
        f"verifications={c['verifications']} alertes={c['alertes']} "
        f"erreurs={c['erreurs']} reponses_304={c['reponses_304']}"
    )
    return 0


def _acquerir_verrou(chemin_etat: str):
    """Verrou mono-instance : deux `run` simultanés doubleraient le sondage du site et
    les notifications. Retourne le fichier verrouillé (à garder ouvert jusqu'à la fin
    du processus — le verrou tombe à sa fermeture), ou None si une instance tourne déjà."""
    fichier = open(chemin_etat + ".lock", "a+", encoding="utf-8")
    try:
        try:
            import msvcrt  # Windows

            msvcrt.locking(fichier.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:
            import fcntl  # POSIX

            fcntl.flock(fichier.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fichier.close()
        return None
    return fichier


def cmd_run(config: Config) -> int:
    verrou = _acquerir_verrou(config.etat_fichier)
    if verrou is None:
        print(
            "Une autre instance du veilleur tourne déjà (verrou sur "
            f"{config.etat_fichier}.lock) — abandon pour éviter le double sondage.",
            file=sys.stderr,
        )
        return 1
    veilleur = _construire(config)
    try:
        veilleur.boucle()
    except KeyboardInterrupt:
        logger.info("arrêt demandé (Ctrl+C) — état persisté, reprise possible")
    finally:
        verrou.close()
    return 0


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config_path = getattr(args, "config", None) or "config.yaml"

    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(f"Configuration introuvable : {config_path}", file=sys.stderr)
        sys.exit(1)
    except ConfigError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        sys.exit(1)

    _setup_logging(config.verbosite)
    handlers = {
        "run": cmd_run,
        "check-once": cmd_check_once,
        "list": cmd_list,
        "test-notify": cmd_test_notify,
    }
    sys.exit(handlers[args.commande](config))


if __name__ == "__main__":
    main()
