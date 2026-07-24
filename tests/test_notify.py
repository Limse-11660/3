"""F6 : contenu des messages Discord, retries et 429/Retry-After."""
import requests

from veilleur import notify
from veilleur.models import Alerte


class FakeReponse:
    def __init__(self, status_code, headers=None, json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("pas de JSON")
        return self._json


def test_message_contient_categories_et_places():
    msg = notify.construire_message(
        "Concert X",
        "https://www.ticketmaster.fr/x",
        [Alerte("reouverture", "Fosse"), Alerte("quota_augmente", "Balcon", places=7, delta=3)],
    )
    embed = msg["embeds"][0]
    assert "Concert X" in embed["title"]
    assert embed["url"] == "https://www.ticketmaster.fr/x"
    assert "Fosse" in embed["description"]
    assert "nombre non exposé" in embed["description"]  # places=None dit la vérité
    assert "+3 place(s)" in embed["description"]
    assert "total 7" in embed["description"]
    assert "Fosse" in msg["content"]


def test_message_avec_places_connues():
    msg = notify.construire_message("X", "https://t.fr", [Alerte("reouverture", "Fosse", places=12)])
    assert "12 place(s)" in msg["embeds"][0]["description"]


def test_message_test_est_un_embed():
    msg = notify.message_test()
    assert "test" in msg["embeds"][0]["title"].lower()


def test_envoi_reussi():
    appels = []

    def post(url, json=None, timeout=None, headers=None):
        appels.append({"url": url, "json": json, "headers": headers})
        return FakeReponse(204)

    ok = notify.envoyer("https://hook", {"content": "x"}, post=post, sleep=lambda s: None)
    assert ok is True
    assert appels[0]["headers"]["User-Agent"]  # UA explicite obligatoire (Cloudflare)


def test_429_attend_retry_after_puis_reessaie():
    reponses = [FakeReponse(429, headers={"Retry-After": "3"}), FakeReponse(204)]
    attentes = []

    def post(url, json=None, timeout=None, headers=None):
        return reponses.pop(0)

    ok = notify.envoyer("https://hook", {}, post=post, sleep=attentes.append)
    assert ok is True
    assert attentes == [3.0]


def test_429_retry_after_dans_le_corps_et_plafond():
    reponses = [FakeReponse(429, json_data={"retry_after": 120.0}), FakeReponse(204)]
    attentes = []
    ok = notify.envoyer("https://hook", {}, post=lambda *a, **k: reponses.pop(0), sleep=attentes.append)
    assert ok is True
    assert attentes == [notify.ATTENTE_429_MAX_S]  # plafonné, jamais 120 s


def test_message_massif_reste_dans_les_limites_discord():
    alertes = [Alerte("reouverture", f"Catégorie très longue numéro {i} " + "x" * 80) for i in range(60)]
    msg = notify.construire_message("Concert X", "https://t.fr", alertes)
    assert len(msg["content"]) <= 2000
    assert len(msg["embeds"][0]["description"]) <= 4096
    assert "autre(s) catégorie(s)" in msg["embeds"][0]["description"]


def test_erreur_reseau_epuisee_retourne_false():
    tentatives = []

    def post(url, json=None, timeout=None, headers=None):
        tentatives.append(1)
        raise requests.ConnectionError("coupé")

    attentes = []
    ok = notify.envoyer("https://hook", {}, post=post, sleep=attentes.append)
    assert ok is False
    assert len(tentatives) == notify.TENTATIVES  # exactement 3, jamais plus
    assert attentes == [notify.ATTENTE_ERREUR_RESEAU_S] * (notify.TENTATIVES - 1)


def test_refus_definitif_retourne_false():
    ok = notify.envoyer(
        "https://hook", {}, post=lambda *a, **k: FakeReponse(401), sleep=lambda s: None
    )
    assert ok is False
