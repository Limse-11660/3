"""F11 : persistance JSON atomique, reprise après corruption, compteurs (§5.3)."""
import json
import os

from veilleur.state import Etat


def test_fichier_absent_donne_etat_neuf(tmp_path):
    etat = Etat.charger(str(tmp_path / "state.json"))
    assert etat.data["evenements"] == {}
    assert etat.compteurs()["verifications"] == 0


def test_sauvegarde_puis_rechargement(tmp_path):
    chemin = str(tmp_path / "state.json")
    etat = Etat.charger(chemin)
    etat.evenement("642735")["categories"] = {"Fosse": {"disponible": True, "places": None, "prix": "53.0"}}
    etat.incrementer("verifications")
    etat.incrementer("alertes", 2)
    etat.sauver()

    relu = Etat.charger(chemin)
    assert relu.evenement("642735")["categories"]["Fosse"]["disponible"] is True
    assert relu.compteurs()["verifications"] == 1
    assert relu.compteurs()["alertes"] == 2


def test_ecriture_atomique_sans_residu(tmp_path):
    chemin = str(tmp_path / "state.json")
    etat = Etat.charger(chemin)
    etat.sauver()
    assert os.path.exists(chemin)
    assert not os.path.exists(chemin + ".tmp")


def test_fichier_corrompu_mis_de_cote(tmp_path):
    chemin = tmp_path / "state.json"
    chemin.write_text("{pas du json", encoding="utf-8")
    etat = Etat.charger(str(chemin))
    assert etat.data["evenements"] == {}
    assert (tmp_path / "state.json.corrompu").exists()


def test_structure_inattendue_mise_de_cote(tmp_path):
    chemin = tmp_path / "state.json"
    chemin.write_text(json.dumps(["liste", "inattendue"]), encoding="utf-8")
    etat = Etat.charger(str(chemin))
    assert etat.data["evenements"] == {}
    assert (tmp_path / "state.json.corrompu").exists()


def test_sauver_sans_faute_absorbe_le_verrou(tmp_path, monkeypatch):
    # §5.2 : un verrou Windows sur state.json ne doit jamais tuer la surveillance
    etat = Etat.charger(str(tmp_path / "state.json"))

    def replace_verrouille(src, dst):
        raise OSError("fichier verrouillé par un autre processus")

    monkeypatch.setattr(os, "replace", replace_verrouille)
    assert etat.sauver_sans_faute() is False  # journalisé, pas levé


def test_entree_evenement_mal_formee_ecartee(tmp_path):
    chemin = tmp_path / "state.json"
    chemin.write_text(
        json.dumps(
            {
                "version": 1,
                "evenements": {
                    "111": {"categories": {"Fosse": {"disponible": True}}},
                    "222": {"categories": "pas-un-dict"},
                    "333": ["liste"],
                },
            }
        ),
        encoding="utf-8",
    )
    etat = Etat.charger(str(chemin))
    assert "111" in etat.data["evenements"]
    assert "222" not in etat.data["evenements"]  # écartée : nouvelle baseline
    assert "333" not in etat.data["evenements"]


def test_compteurs_partiels_completes(tmp_path):
    chemin = tmp_path / "state.json"
    chemin.write_text(
        json.dumps({"version": 1, "evenements": {}, "compteurs": {"verifications": 7}}),
        encoding="utf-8",
    )
    etat = Etat.charger(str(chemin))
    assert etat.compteurs()["verifications"] == 7
    assert etat.compteurs()["erreurs"] == 0
