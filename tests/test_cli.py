"""F9 : CLI — analyse des arguments, commandes list / test-notify, verrou mono-instance."""
import pytest

from veilleur import notify
from veilleur.__main__ import _acquerir_verrou, build_parser, cmd_list, cmd_test_notify, main
from veilleur.config import Config, Evenement

EV = Evenement(
    url="https://www.ticketmaster.fr/fr/manifestation/x-billet/idmanif/642735",
    tm_event_id="642735",
    libelle="Concert X",
    categories=("Fosse",),
)


@pytest.mark.parametrize("commande", ["run", "check-once", "list", "test-notify"])
def test_parser_accepte_chaque_sous_commande(commande):
    assert build_parser().parse_args([commande]).commande == commande


def test_parser_config_avant_ou_apres():
    p = build_parser()
    assert p.parse_args(["--config", "x.yaml", "run"]).config == "x.yaml"
    assert p.parse_args(["run", "--config", "y.yaml"]).config == "y.yaml"


def test_parser_sans_sous_commande_refuse():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_cmd_list_affiche_evenements_et_webhook(capsys):
    config = Config(evenements=[EV], webhook_discord=None)
    assert cmd_list(config) == 0
    sortie = capsys.readouterr().out
    assert "[642735] Concert X" in sortie
    assert "catégories : Fosse" in sortie
    assert "ABSENT" in sortie


def test_cmd_test_notify_sans_webhook_echoue(capsys):
    assert cmd_test_notify(Config(evenements=[EV])) == 1


def test_cmd_test_notify_avec_webhook(monkeypatch, capsys):
    envois = []
    monkeypatch.setattr(notify, "envoyer", lambda url, msg, timeout_s: envois.append(url) or True)
    config = Config(evenements=[EV], webhook_discord="https://hook")
    assert cmd_test_notify(config) == 0
    assert envois == ["https://hook"]


def test_main_list_de_bout_en_bout(tmp_path, capsys):
    chemin = tmp_path / "config.yaml"
    chemin.write_text(
        "evenements:\n"
        "  - url: https://www.ticketmaster.fr/fr/manifestation/x-billet/idmanif/642735\n"
        "    libelle: Concert X\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as sortie:
        main(["list", "--config", str(chemin)])
    assert sortie.value.code == 0
    assert "Concert X" in capsys.readouterr().out


def test_main_config_absente_code_1(tmp_path, capsys):
    with pytest.raises(SystemExit) as sortie:
        main(["list", "--config", str(tmp_path / "absent.yaml")])
    assert sortie.value.code == 1


def _config_pour(tmp_path):
    import json as json_mod

    chemin = tmp_path / "config.yaml"
    chemin.write_text(
        f"etat_fichier: {json_mod.dumps(str(tmp_path / 'state.json'))}\n"
        'journal_fichier: ""\n'  # hermétique : pas d'écriture dans le veilleur.log du projet
        "evenements:\n"
        "  - url: https://www.ticketmaster.fr/fr/manifestation/x-billet/idmanif/642735\n"
        "    libelle: Concert X\n",
        encoding="utf-8",
    )
    return chemin


def test_main_check_once_de_bout_en_bout(tmp_path, capsys, monkeypatch):
    # F9 : check-once complet (config réelle, client simulé), état écrit sur disque
    from datetime import datetime, timezone

    from veilleur.fetch import ClientTicketmaster
    from veilleur.models import AvailabilitySnapshot

    def faux_relever(self, ev):
        return AvailabilitySnapshot(
            ev.cle, ev.tm_event_id, datetime.now(timezone.utc).isoformat(), ()
        )

    monkeypatch.setattr(ClientTicketmaster, "relever", faux_relever)
    with pytest.raises(SystemExit) as sortie:
        main(["check-once", "--config", str(_config_pour(tmp_path))])
    assert sortie.value.code == 0
    assert "1 événement(s) vérifié(s)" in capsys.readouterr().out
    assert (tmp_path / "state.json").exists()


def test_main_run_interrompu_libere_le_verrou(tmp_path, monkeypatch):
    # F9 : run construit le veilleur, pose le verrou, et le libère même sur Ctrl+C
    from veilleur.runner import Veilleur

    def boucle_interrompue(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(Veilleur, "boucle", boucle_interrompue)
    with pytest.raises(SystemExit) as sortie:
        main(["run", "--config", str(_config_pour(tmp_path))])
    assert sortie.value.code == 0
    verrou = _acquerir_verrou(str(tmp_path / "state.json"))
    assert verrou is not None  # bien libéré après l'arrêt
    verrou.close()


def test_journal_fichier_recoit_les_lignes(tmp_path):
    import logging

    from veilleur.__main__ import _setup_logging

    chemin = tmp_path / "veilleur.log"
    _setup_logging(Config(evenements=[EV], journal_fichier=str(chemin)))
    logging.getLogger("veilleur.test").info("ligne de contrôle")
    racine = logging.getLogger()
    for h in list(racine.handlers):
        h.flush()
        h.close()
    racine.handlers.clear()
    assert "ligne de contrôle" in chemin.read_text(encoding="utf-8")


def test_verrou_mono_instance_refuse_la_seconde(tmp_path):
    chemin = str(tmp_path / "state.json")
    premier = _acquerir_verrou(chemin)
    assert premier is not None
    assert _acquerir_verrou(chemin) is None  # une instance tourne déjà
    premier.close()  # fin de la première instance : le verrou tombe
    second = _acquerir_verrou(chemin)
    assert second is not None
    second.close()


def test_main_config_invalide_message_propre(tmp_path, capsys):
    chemin = tmp_path / "config.yaml"
    chemin.write_text("evenements: []\n", encoding="utf-8")
    with pytest.raises(SystemExit) as sortie:
        main(["list", "--config", str(chemin)])
    assert sortie.value.code == 1
    assert "Configuration invalide" in capsys.readouterr().err
