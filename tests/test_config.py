"""F1/F7 : chargement, validation, bornes."""
import pytest

from veilleur.config import PLANCHER_INTERVALLE_S, ConfigError, load_config

MINIMAL = (
    "evenements:\n"
    "  - url: https://www.ticketmaster.fr/fr/manifestation/x-billet/idmanif/642735\n"
)


def _ecrire(tmp_path, contenu):
    p = tmp_path / "config.yaml"
    p.write_text(contenu, encoding="utf-8")
    return str(p)


def test_config_minimale_et_defauts(tmp_path):
    cfg = load_config(_ecrire(tmp_path, MINIMAL))
    assert len(cfg.evenements) == 1
    ev = cfg.evenements[0]
    assert ev.tm_event_id == "642735"
    assert ev.libelle == "evenement-642735"  # libellé par défaut
    assert ev.categories == ()
    assert cfg.intervalle_secondes == 60
    assert cfg.jitter_secondes == 5
    assert cfg.webhook_discord is None


def test_libelle_et_categories(tmp_path):
    cfg = load_config(
        _ecrire(
            tmp_path,
            "evenements:\n"
            "  - url: https://www.ticketmaster.fr/fr/manifestation/x/idmanif/1111\n"
            "    libelle: Mon concert\n"
            "    categories: [Fosse, 'Carré Or']\n",
        )
    )
    ev = cfg.evenements[0]
    assert ev.libelle == "Mon concert"
    assert ev.categories == ("Fosse", "Carré Or")


def test_sans_evenements_rejete(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_ecrire(tmp_path, "evenements: []\n"))


def test_url_hors_ticketmaster_rejetee(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_ecrire(tmp_path, "evenements:\n  - url: https://exemple.fr/idmanif/1\n"))


def test_url_sans_idmanif_rejetee(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            _ecrire(tmp_path, "evenements:\n  - url: https://www.ticketmaster.fr/fr/page\n")
        )


def test_doublon_rejete(tmp_path):
    contenu = MINIMAL + "  - url: https://www.ticketmaster.fr/fr/manifestation/y-billet/idmanif/642735\n"
    with pytest.raises(ConfigError):
        load_config(_ecrire(tmp_path, contenu))


def test_intervalle_sous_le_plancher_rejete(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_ecrire(tmp_path, MINIMAL + f"intervalle_secondes: {PLANCHER_INTERVALLE_S - 1}\n"))


def test_jitter_negatif_rejete(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_ecrire(tmp_path, MINIMAL + "jitter_secondes: -1\n"))


def test_backoff_max_sous_l_intervalle_rejete(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_ecrire(tmp_path, MINIMAL + "backoff_max_secondes: 30\n"))


def test_yaml_malforme_donne_configerror(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_ecrire(tmp_path, "evenements: [\n  broken"))


def test_valeur_numerique_invalide_donne_configerror(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_ecrire(tmp_path, MINIMAL + "intervalle_secondes: soixante\n"))


def test_meme_idmanif_filtres_differents_accepte_avec_cles_distinctes(tmp_path):
    contenu = (
        MINIMAL
        + "  - url: https://www.ticketmaster.fr/fr/manifestation/y-billet/idmanif/642735\n"
        + "    categories: [Fosse]\n"
    )
    cfg = load_config(_ecrire(tmp_path, contenu))
    cles = [ev.cle for ev in cfg.evenements]
    assert len(set(cles)) == 2  # jamais de collision d'état entre les deux entrées
    assert cles[0] == "642735"
    assert cles[1].startswith("642735#")


def test_cle_injective_meme_avec_separateur_dans_le_filtre():
    from veilleur.config import Evenement

    a = Evenement(url="https://www.ticketmaster.fr/x/idmanif/1", tm_event_id="1",
                  libelle="A", categories=("a|b",))
    b = Evenement(url="https://www.ticketmaster.fr/x/idmanif/1", tm_event_id="1",
                  libelle="B", categories=("a", "b"))
    assert a.cle != b.cle


def test_idmanif_hors_chemin_rejete(tmp_path):
    # idmanif dans la query string : refusé (le chemin seul fait foi)
    with pytest.raises(ConfigError):
        load_config(_ecrire(
            tmp_path,
            "evenements:\n  - url: https://www.ticketmaster.fr/fr/page?u=/idmanif/999\n",
        ))


def test_webhook_charge(tmp_path):
    cfg = load_config(_ecrire(tmp_path, MINIMAL + "webhook_discord: https://discord.com/api/webhooks/a/b\n"))
    assert cfg.webhook_discord == "https://discord.com/api/webhooks/a/b"
