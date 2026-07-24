"""F2/F3/§5.1 : parsing, filtres, requêtes conditionnelles, erreurs typées, retries."""
import pytest
import requests

from veilleur.config import Evenement
from veilleur.fetch import ClientTicketmaster, normaliser_libelle, parse_payload
from veilleur.models import FetchError, PageIntrouvable, ParseInvalide, RateLimited

EV = Evenement(
    url="https://www.ticketmaster.fr/fr/manifestation/x-billet/idmanif/642735",
    tm_event_id="642735",
    libelle="Test",
)

PAYLOAD = [
    {"idSeance": 1, "labels": ["Samedi 18 juillet 2026 à 19:00"], "available": True, "priceMin": 53.0},
    {"idSeance": 2, "labels": ["Dimanche 19 juillet 2026 à 15:00"], "available": False, "priceMin": None},
    {"idSeance": 3, "dateSeance": "2026-07-20", "available": True},
]


# --- parse_payload (F3) ---


def test_parse_normalise_les_statuts_et_prix():
    cats = parse_payload(PAYLOAD)
    assert len(cats) == 3
    assert cats[0].disponible is True
    assert cats[0].prix == "53.0"
    assert cats[1].disponible is False
    assert cats[2].categorie == "2026-07-20"  # repli dateSeance sans labels
    assert all(c.places is None for c in cats)  # non exposé à ce niveau


def test_parse_liste_vide_est_valide():
    assert parse_payload([]) == []


def test_parse_non_liste_rejete():
    with pytest.raises(ParseInvalide):
        parse_payload({"objet": "inattendu"})


def test_parse_element_non_objet_rejete():
    with pytest.raises(ParseInvalide):
        parse_payload(["chaine"])


def test_filtre_insensible_casse_et_accents():
    assert normaliser_libelle("Carré Or") == "carre or"
    cats = parse_payload(PAYLOAD, filtres=("SAMEDI",))
    assert [c.categorie for c in cats] == ["Samedi 18 juillet 2026 à 19:00"]


# --- client HTTP simulé ---


class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("pas de JSON")
        return self._json


class FakeSession:
    """File de réponses (ou d'exceptions) ; enregistre chaque appel."""

    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.appels = []
        self.headers = {}

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        self.appels.append({"url": url, "headers": headers or {}})
        suivant = self.reponses.pop(0)
        if isinstance(suivant, Exception):
            raise suivant
        return suivant


def _client(reponses, **kwargs):
    session = FakeSession(reponses)
    dormeur = []
    client = ClientTicketmaster(
        "test-agent", 15, session=session, sleep=dormeur.append, **kwargs
    )
    return client, session, dormeur


def test_200_produit_un_snapshot():
    client, session, _ = _client([FakeResponse(200, PAYLOAD)])
    snap = client.relever(EV)
    assert snap.tm_event_id == "642735"
    assert len(snap.categories) == 3
    assert "idmanif/642735" in session.appels[0]["url"]


def test_conditionnelle_etag_puis_304_reutilise_le_corps():
    compteur_304 = []
    client, session, _ = _client(
        [FakeResponse(200, PAYLOAD, headers={"ETag": '"v1"'}), FakeResponse(304)],
        on_304=lambda: compteur_304.append(1),
    )
    snap1 = client.relever(EV)
    snap2 = client.relever(EV)
    assert "If-None-Match" not in session.appels[0]["headers"]
    assert session.appels[1]["headers"]["If-None-Match"] == '"v1"'
    assert [c.categorie for c in snap2.categories] == [c.categorie for c in snap1.categories]
    assert len(compteur_304) == 1


def test_conditionnelle_last_modified_renvoye():
    lm = "Wed, 01 Jul 2026 10:00:00 GMT"
    client, session, _ = _client(
        [FakeResponse(200, PAYLOAD, headers={"Last-Modified": lm}), FakeResponse(200, PAYLOAD)]
    )
    client.relever(EV)
    client.relever(EV)
    assert session.appels[1]["headers"]["If-Modified-Since"] == lm


def test_sans_validateurs_pas_de_conditionnelle():
    client, session, _ = _client([FakeResponse(200, PAYLOAD), FakeResponse(200, PAYLOAD)])
    client.relever(EV)
    client.relever(EV)
    assert "If-None-Match" not in session.appels[1]["headers"]
    assert "If-Modified-Since" not in session.appels[1]["headers"]


def test_304_sans_cache_est_une_erreur():
    client, _, _ = _client([FakeResponse(304)])
    with pytest.raises(FetchError):
        client.relever(EV)


def test_404_page_introuvable():
    client, _, _ = _client([FakeResponse(404)])
    with pytest.raises(PageIntrouvable):
        client.relever(EV)


@pytest.mark.parametrize("code", [429, 503])
def test_429_503_rate_limited(code):
    client, _, _ = _client([FakeResponse(code)])
    with pytest.raises(RateLimited):
        client.relever(EV)


def test_file_d_attente_traitee_comme_ralentissement():
    reponse = FakeResponse(302, headers={"location": "https://wait.ticketmaster.fr/?c=x"})
    client, _, _ = _client([reponse])
    with pytest.raises(RateLimited):
        client.relever(EV)


def test_redirection_inattendue_erreur():
    reponse = FakeResponse(302, headers={"location": "https://www.ticketmaster.fr/autre"})
    client, _, _ = _client([reponse])
    with pytest.raises(FetchError):
        client.relever(EV)


def test_corps_non_json_parse_invalide():
    client, _, _ = _client([FakeResponse(200, None, text="<html>challenge</html>")])
    with pytest.raises(ParseInvalide):
        client.relever(EV)


def test_libelles_en_doublon_fusionnes_disponible_si_l_un_l_est():
    # F5 : sans fusion, l'état oscillerait entre les doublons et rejouerait l'alerte
    payload = [
        {"labels": ["Fosse"], "available": True, "priceMin": 80.0},
        {"labels": ["Fosse"], "available": False, "priceMin": 53.0},
    ]
    cats = parse_payload(payload)
    assert len(cats) == 1
    assert cats[0].disponible is True
    assert cats[0].prix == "53.0"  # prix minimum connu


def test_libelles_en_doublon_tous_indisponibles():
    payload = [
        {"labels": ["Fosse"], "available": False},
        {"labels": ["Fosse"], "available": False, "priceMin": 40.0},
    ]
    cats = parse_payload(payload)
    assert len(cats) == 1
    assert cats[0].disponible is False
    assert cats[0].prix == "40.0"


def test_filtres_appliques_a_travers_relever():
    ev_filtre = Evenement(
        url=EV.url, tm_event_id="642735", libelle="Test", categories=("dimanche",)
    )
    client, _, _ = _client([FakeResponse(200, PAYLOAD)])
    snap = client.relever(ev_filtre)
    assert [c.categorie for c in snap.categories] == ["Dimanche 19 juillet 2026 à 15:00"]
    assert snap.event_key == "642735#dimanche"  # clé composite propre à l'entrée


def test_espacement_de_politesse_entre_requetes():
    client, _, dormeur = _client([FakeResponse(200, PAYLOAD), FakeResponse(200, PAYLOAD)])
    client.relever(EV)
    client.relever(EV)  # immédiatement après -> attente imposée
    assert any(0.0 < d <= 1.0 for d in dormeur)


def test_erreur_reseau_deux_fois_puis_succes():
    client, session, dormeur = _client(
        [
            requests.ConnectionError("coupé"),
            requests.ConnectionError("coupé"),
            FakeResponse(200, PAYLOAD),
        ]
    )
    snap = client.relever(EV)
    assert len(snap.categories) == 3
    assert len(session.appels) == 3
    assert 1.0 in dormeur and 2.0 in dormeur  # backoff réseau 1 s puis 2 s


def test_erreur_reseau_epuisee_leve_fetcherror():
    client, session, _ = _client(
        [requests.ConnectTimeout("t"), requests.ConnectTimeout("t"), requests.ConnectTimeout("t")]
    )
    with pytest.raises(FetchError):
        client.relever(EV)
    assert len(session.appels) == 3  # jamais plus de 3 tentatives
