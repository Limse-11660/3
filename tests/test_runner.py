"""Boucle : baseline, alerte + notification, persistance avant envoi (F5),
isolation par événement (§5.2), backoff (§5.1), jitter (F7)."""
from datetime import datetime, timezone

import pytest

from veilleur import notify
from veilleur.config import Config, Evenement
from veilleur.models import (
    AvailabilitySnapshot,
    CategoryAvailability as Cat,
    FetchError,
    FileAttente,
)
from veilleur.runner import PLAFOND_FILE_ATTENTE_S, SEUIL_VIDES_CONSECUTIFS, Veilleur
from veilleur.state import Etat

EV_A = Evenement(url="https://www.ticketmaster.fr/a/idmanif/1111", tm_event_id="1111", libelle="A")
EV_B = Evenement(url="https://www.ticketmaster.fr/b/idmanif/2222", tm_event_id="2222", libelle="B")


def _snap(tm_id, categories):
    return AvailabilitySnapshot(
        event_key=tm_id,
        tm_event_id=tm_id,
        horodatage_utc=datetime.now(timezone.utc).isoformat(),
        categories=tuple(categories),
    )


class FakeClient:
    """Rend, par événement, une file de snapshots ou d'exceptions."""

    def __init__(self, plan):
        self.plan = {k: list(v) for k, v in plan.items()}
        self.releves = []

    def relever(self, ev):
        self.releves.append(ev.cle)
        suivant = self.plan[ev.cle].pop(0)
        if isinstance(suivant, Exception):
            raise suivant
        return suivant


def _veilleur(tmp_path, plan, evenements=None, webhook="https://hook", jitter=0, alea=None):
    config = Config(
        evenements=evenements or [EV_A],
        webhook_discord=webhook,
        jitter_secondes=jitter,
        etat_fichier=str(tmp_path / "state.json"),
    )
    etat = Etat.charger(config.etat_fichier)
    client = FakeClient(plan)
    return Veilleur(config, etat, client, sleep=lambda s: None, alea=alea or (lambda a, b: 0.0))


def test_baseline_puis_reouverture_notifie_une_fois(tmp_path, monkeypatch):
    envois = []
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: envois.append(a) or True)
    v = _veilleur(
        tmp_path,
        {"1111": [_snap("1111", [Cat("Fosse", False)]), _snap("1111", [Cat("Fosse", True)])]},
    )
    assert v.verifier_evenement(EV_A) == []  # baseline : aucune alerte (EF implicite du CDC)
    assert envois == []
    alertes = v.verifier_evenement(EV_A)
    assert [a.type for a in alertes] == ["reouverture"]
    assert len(envois) == 1
    assert v.etat.compteurs()["alertes"] == 1
    assert v.etat.compteurs()["verifications"] == 2


def test_etat_persiste_avant_notification_pas_de_double_alerte(tmp_path, monkeypatch):
    # F5 : si l'envoi Discord plante, l'état est déjà écrit -> le front ne rejoue pas
    def envoyer_qui_plante(*a, **k):
        raise RuntimeError("crash pendant l'envoi")

    monkeypatch.setattr(notify, "envoyer", envoyer_qui_plante)
    v = _veilleur(
        tmp_path,
        {
            "1111": [
                _snap("1111", [Cat("Fosse", False)]),
                _snap("1111", [Cat("Fosse", True)]),
                _snap("1111", [Cat("Fosse", True)]),
            ]
        },
    )
    v.verifier_evenement(EV_A)
    with pytest.raises(RuntimeError):
        v.verifier_evenement(EV_A)
    # l'état relu depuis le disque connaît déjà la disponibilité
    relu = Etat.charger(v.etat.chemin)
    assert relu.evenement("1111")["categories"]["Fosse"]["disponible"] is True
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: True)
    assert v.verifier_evenement(EV_A) == []  # pas de seconde alerte pour le même front


def test_isolation_un_evenement_en_erreur_n_arrete_pas_les_autres(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: True)
    v = _veilleur(
        tmp_path,
        {"1111": [FetchError("panne")], "2222": [_snap("2222", [Cat("Or", True)])]},
        evenements=[EV_A, EV_B],
    )
    assert v.balayer_une_fois() == 2
    assert v.client.releves == ["1111", "2222"]  # B vérifié malgré la panne de A
    assert v.etat.compteurs()["erreurs"] == 1
    assert v.etat.evenement("2222")["categories"]["Or"]["disponible"] is True


def test_backoff_double_et_plafonne(tmp_path):
    v = _veilleur(tmp_path, {})
    assert v._prochain_delai(EV_A) == 60.0
    v._erreurs["1111"] = 1
    assert v._prochain_delai(EV_A) == 120.0
    v._erreurs["1111"] = 2
    assert v._prochain_delai(EV_A) == 240.0
    v._erreurs["1111"] = 10
    assert v._prochain_delai(EV_A) == 900.0  # plafond backoff_max_secondes


def test_retour_a_la_normale_retablit_l_intervalle(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: True)
    v = _veilleur(
        tmp_path, {"1111": [FetchError("panne"), _snap("1111", [Cat("Fosse", True)])]}
    )
    v._verifier_avec_isolation(EV_A)
    assert v._prochain_delai(EV_A) == 120.0
    v._verifier_avec_isolation(EV_A)
    assert v._prochain_delai(EV_A) == 60.0


def test_jitter_est_un_retard_borne(tmp_path):
    releve_alea = []

    def alea(a, b):
        releve_alea.append((a, b))
        return b  # pire cas : jitter maximal

    v = _veilleur(tmp_path, {}, jitter=5, alea=alea)
    assert v._prochain_delai(EV_A) == 65.0
    assert releve_alea == [(0.0, 5.0)]  # jamais négatif : retard seul


def test_meme_idmanif_deux_entrees_etats_et_baselines_separes(tmp_path, monkeypatch):
    # Deux entrées partageant l'idmanif (filtres différents) : chacune sa baseline,
    # aucune fausse alerte croisée, aucun écrasement alterné
    envois = []
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: envois.append(a) or True)
    ev_tout = EV_A
    ev_filtre = Evenement(url=EV_A.url, tm_event_id="1111", libelle="A-fosse", categories=("fosse",))
    cle_filtre = ev_filtre.cle
    plan = {
        "1111": [_snap("1111", [Cat("Fosse", True), Cat("Balcon", True)])] * 2,
        cle_filtre: [_snap("1111", [Cat("Fosse", True)])] * 2,
    }
    v = _veilleur(tmp_path, plan, evenements=[ev_tout, ev_filtre])
    v.balayer_une_fois()
    n_initial = len(envois)  # messages « état initial » des 2 baselines (disponibles)
    v.balayer_une_fois()
    assert len(envois) == n_initial  # relevés stables : AUCUNE alerte ensuite
    assert v.etat.compteurs()["alertes"] == 0
    assert "1111" in v.etat.data["evenements"]
    assert cle_filtre in v.etat.data["evenements"]


def test_meme_idmanif_deux_entrees_toutes_sondees_en_boucle(tmp_path, monkeypatch):
    # Famine corrigée : la seconde entrée au même idmanif a sa propre échéance
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: True)
    ev_filtre = Evenement(url=EV_A.url, tm_event_id="1111", libelle="A-fosse", categories=("fosse",))
    v = _veilleur(
        tmp_path,
        {"1111": [_snap("1111", [])], ev_filtre.cle: [_snap("1111", [])]},
        evenements=[EV_A, ev_filtre],
    )

    def sommeil_interrompu(s):
        raise KeyboardInterrupt

    v._sleep = sommeil_interrompu
    with pytest.raises(KeyboardInterrupt):
        v.boucle()
    assert sorted(v.client.releves) == sorted(["1111", ev_filtre.cle])


def test_releve_vide_transitoire_conserve_l_etat(tmp_path, monkeypatch):
    # Un 200 avec liste vide n'écrase pas la baseline : pas de rafale de fausses
    # « nouvelle_categorie » quand les séances reviennent
    envois = []
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: envois.append(a) or True)
    v = _veilleur(
        tmp_path,
        {
            "1111": [
                _snap("1111", [Cat("Fosse", True)]),
                _snap("1111", []),
                _snap("1111", [Cat("Fosse", True)]),
            ]
        },
    )
    v.verifier_evenement(EV_A)  # baseline (avec disponible -> 1 message d'état initial)
    n_initial = len(envois)
    assert v.verifier_evenement(EV_A) == []  # vide transitoire : état conservé
    assert v.etat.evenement(EV_A.cle)["categories"]["Fosse"]["disponible"] is True
    assert v.verifier_evenement(EV_A) == []  # retour : rien de nouveau -> aucune alerte
    assert len(envois) == n_initial


def test_vide_durable_acte_puis_retour_realerte(tmp_path, monkeypatch):
    # Après SEUIL_VIDES_CONSECUTIFS relevés vides, le vide devient l'état : une vraie
    # réouverture ultérieure doit être réalertée (elle était manquée avant le correctif)
    envois = []
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: envois.append(a) or True)
    releves = (
        [_snap("1111", [Cat("Fosse", True)])]
        + [_snap("1111", [])] * SEUIL_VIDES_CONSECUTIFS
        + [_snap("1111", [Cat("Fosse", True)])]
    )
    v = _veilleur(tmp_path, {"1111": releves})
    v.verifier_evenement(EV_A)  # baseline (1 message d'état initial)
    n_initial = len(envois)
    for _ in range(SEUIL_VIDES_CONSECUTIFS - 1):
        assert v.verifier_evenement(EV_A) == []  # conservé
    assert v.verifier_evenement(EV_A) == []  # vide acté : état = {}
    assert v.etat.evenement(EV_A.cle)["categories"] == {}
    alertes = v.verifier_evenement(EV_A)  # retour de Fosse
    assert [a.type for a in alertes] == ["nouvelle_categorie"]
    assert len(envois) == n_initial + 1


def test_notification_etat_initial_si_disponible(tmp_path, monkeypatch):
    envois = []
    monkeypatch.setattr(notify, "envoyer", lambda url, msg, **k: envois.append(msg) or True)
    v = _veilleur(tmp_path, {"1111": [_snap("1111", [Cat("Fosse", True), Cat("Or", False)])]})
    assert v.verifier_evenement(EV_A) == []  # baseline : pas d'alerte...
    assert len(envois) == 1  # ...mais un signal « surveillance active »
    assert "surveillance active" in envois[0]["embeds"][0]["title"]


def test_pas_de_notification_etat_initial_sans_disponible(tmp_path, monkeypatch):
    envois = []
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: envois.append(a) or True)
    v = _veilleur(tmp_path, {"1111": [_snap("1111", [Cat("Fosse", False)])]})
    v.verifier_evenement(EV_A)
    assert envois == []  # tout épuisé au lancement : démarrage silencieux


def test_plafond_backoff_file_d_attente(tmp_path, monkeypatch):
    # File d'attente permanente (cas NFL/Céline Dion) : le délai ne dépasse jamais 300 s,
    # pour capter la levée de la file sans attendre jusqu'à 15 min
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: True)
    v = _veilleur(tmp_path, {"1111": [FileAttente("file d'attente active")] * 6})
    for _ in range(6):
        v._verifier_avec_isolation(EV_A)
    assert v._prochain_delai(EV_A) == PLAFOND_FILE_ATTENTE_S
    # alors qu'une erreur ordinaire au même compteur monterait au plafond général (900 s)
    v._en_file_attente.discard(EV_A.cle)
    assert v._prochain_delai(EV_A) == 900.0


def test_derniere_erreur_tracee_puis_purgee(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: True)
    v = _veilleur(
        tmp_path, {"1111": [FetchError("panne"), _snap("1111", [Cat("Fosse", False)])]}
    )
    v._verifier_avec_isolation(EV_A)
    trace = v.etat.evenement(EV_A.cle)["derniere_erreur"]
    assert trace["motif"] == "panne"
    assert trace["echecs_consecutifs"] == 1
    v._verifier_avec_isolation(EV_A)  # relevé réussi
    assert "derniere_erreur" not in v.etat.evenement(EV_A.cle)


def test_echec_ecriture_etat_ne_tue_pas_la_surveillance(tmp_path, monkeypatch):
    # Constat bloquant de la revue : un verrou sur state.json ne doit pas tuer le processus
    envois = []
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: envois.append(a) or True)
    v = _veilleur(
        tmp_path,
        {"1111": [_snap("1111", [Cat("Fosse", False)]), _snap("1111", [Cat("Fosse", True)])]},
    )
    v.verifier_evenement(EV_A)  # baseline (écriture OK)

    def sauver_verrouille():
        raise OSError("verrou")

    monkeypatch.setattr(v.etat, "sauver", sauver_verrouille)
    v._verifier_avec_isolation(EV_A)  # ne lève pas
    assert len(envois) == 1  # l'alerte part quand même (état gardé en mémoire)
    assert v._erreurs == {}  # pas comptée comme échec de relevé


def test_boucle_replanifie_les_echeances(tmp_path, monkeypatch):
    # La replanification suit l'intervalle : 3 relevés espacés de 60 s en temps simulé
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: True)
    v = _veilleur(tmp_path, {"1111": [_snap("1111", []) for _ in range(3)]})
    horloge = {"t": 0.0}
    v._monotone = lambda: horloge["t"]

    def sommeil(s):
        if len(v.client.releves) >= 3:
            raise KeyboardInterrupt
        horloge["t"] += s

    v._sleep = sommeil
    with pytest.raises(KeyboardInterrupt):
        v.boucle()
    assert len(v.client.releves) == 3
    assert horloge["t"] >= 120.0  # au moins 2 intervalles pleins écoulés


def test_boucle_verifie_chaque_evenement_puis_dort(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "envoyer", lambda *a, **k: True)
    v = _veilleur(
        tmp_path,
        {"1111": [_snap("1111", [])], "2222": [_snap("2222", [])]},
        evenements=[EV_A, EV_B],
    )

    def sommeil_interrompu(s):
        raise KeyboardInterrupt

    v._sleep = sommeil_interrompu
    with pytest.raises(KeyboardInterrupt):
        v.boucle()
    assert sorted(v.client.releves) == ["1111", "2222"]  # premier relevé immédiat pour chacun
