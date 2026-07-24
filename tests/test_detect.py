"""F4 (transitions positives) et F5 (front unique = pas de doublon)."""
from veilleur.detect import comparer, en_etat
from veilleur.models import CategoryAvailability as Cat


def test_baseline_sans_alerte():
    assert comparer(None, [Cat("Fosse", True, places=10)]) == []


def test_reouverture_indisponible_vers_disponible():
    avant = en_etat([Cat("Fosse", False)])
    alertes = comparer(avant, [Cat("Fosse", True)])
    assert [a.type for a in alertes] == ["reouverture"]
    assert alertes[0].categorie == "Fosse"


def test_nouvelle_categorie_disponible():
    avant = en_etat([Cat("Fosse", True)])
    alertes = comparer(avant, [Cat("Fosse", True), Cat("Balcon", True, places=4)])
    assert [a.type for a in alertes] == ["nouvelle_categorie"]
    assert alertes[0].places == 4


def test_nouvelle_categorie_indisponible_silencieuse():
    avant = en_etat([Cat("Fosse", True)])
    assert comparer(avant, [Cat("Fosse", True), Cat("Balcon", False)]) == []


def test_quota_augmente_avec_delta():
    avant = en_etat([Cat("Fosse", True, places=2)])
    alertes = comparer(avant, [Cat("Fosse", True, places=7)])
    assert [a.type for a in alertes] == ["quota_augmente"]
    assert alertes[0].delta == 5
    assert alertes[0].places == 7


def test_quota_en_baisse_ou_stable_silencieux():
    avant = en_etat([Cat("Fosse", True, places=5)])
    assert comparer(avant, [Cat("Fosse", True, places=5)]) == []
    assert comparer(avant, [Cat("Fosse", True, places=3)]) == []


def test_places_inconnues_pas_de_quota():
    # sans quantités exposées, seule la transition de statut compte
    avant = en_etat([Cat("Fosse", True, places=None)])
    assert comparer(avant, [Cat("Fosse", True, places=None)]) == []


def test_fermeture_silencieuse():
    avant = en_etat([Cat("Fosse", True)])
    assert comparer(avant, [Cat("Fosse", False)]) == []


def test_categorie_disparue_silencieuse():
    avant = en_etat([Cat("Fosse", True), Cat("Balcon", True)])
    assert comparer(avant, [Cat("Fosse", True)]) == []


def test_front_unique_pas_de_double_alerte():
    # F5 : une fois l'état mis à jour, le même relevé ne réalerte pas
    avant = en_etat([Cat("Fosse", False)])
    courant = [Cat("Fosse", True)]
    assert len(comparer(avant, courant)) == 1
    apres = en_etat(courant)
    assert comparer(apres, courant) == []


def test_transitions_multiples_en_un_releve():
    avant = en_etat([Cat("Fosse", False), Cat("Balcon", True, places=1)])
    alertes = comparer(avant, [Cat("Fosse", True), Cat("Balcon", True, places=3), Cat("Or", True)])
    assert sorted(a.type for a in alertes) == ["nouvelle_categorie", "quota_augmente", "reouverture"]
