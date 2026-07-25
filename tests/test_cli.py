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
