"""F1 : chargement et validation du fichier de configuration YAML.

F7 : intervalle et jitter configurables. L'intervalle est borné à 60 s minimum
(politesse : au plus une requête par événement par minute). Le jitter est un retard
aléatoire de 0..N s ajouté à chaque échéance — jamais une anticipation — donc l'écart
entre deux requêtes d'un même événement ne descend jamais sous l'intervalle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import yaml

PLANCHER_INTERVALLE_S = 60
RE_IDMANIF = re.compile(r"/idmanif/(\d+)")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Evenement:
    url: str
    tm_event_id: str
    libelle: str
    categories: tuple[str, ...] = ()  # optionnel : filtres de libellé (insensibles casse/accents)


@dataclass
class Config:
    evenements: list[Evenement] = field(default_factory=list)
    webhook_discord: str | None = None
    intervalle_secondes: int = 60
    jitter_secondes: int = 5
    timeout_secondes: int = 15
    backoff_max_secondes: int = 900
    user_agent: str = "veilleur-tm/1.0 (usage personnel)"
    etat_fichier: str = "./state.json"
    verbosite: str = "INFO"


def _extraire_id(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ConfigError(f"URL illisible : {url!r} ({exc})") from exc
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(f"URL invalide (schéma attendu http/https) : {url!r}")
    host = parsed.netloc.lower()
    if not (host == "ticketmaster.fr" or host.endswith(".ticketmaster.fr")):
        raise ConfigError(f"URL hors ticketmaster.fr : {url!r}")
    m = RE_IDMANIF.search(url)
    if not m:
        raise ConfigError(f"idmanif introuvable dans l'URL : {url!r}")
    return m.group(1)


def load_config(path: str) -> Config:
    """Charge le YAML, valide chaque événement et les bornes. Lève ConfigError sinon."""
    with open(path, encoding="utf-8") as f:
        brut = yaml.safe_load(f) or {}
    if not isinstance(brut, dict):
        raise ConfigError("le fichier de configuration doit être un mapping YAML")

    lignes = brut.get("evenements") or []
    if not isinstance(lignes, list) or not lignes:
        raise ConfigError("la liste 'evenements' est vide ou absente")

    evenements: list[Evenement] = []
    deja_vus: set[tuple[str, tuple[str, ...]]] = set()
    for i, ligne in enumerate(lignes, start=1):
        if not isinstance(ligne, dict) or not str(ligne.get("url") or "").strip():
            raise ConfigError(f"evenements[{i}] : champ 'url' requis")
        url = str(ligne["url"]).strip()
        tm_id = _extraire_id(url)
        libelle = str(ligne.get("libelle") or f"evenement-{tm_id}").strip()
        cats = tuple(str(c).strip() for c in (ligne.get("categories") or []) if str(c).strip())
        cle = (tm_id, cats)
        if cle in deja_vus:
            raise ConfigError(f"evenements[{i}] : doublon (idmanif {tm_id}, mêmes catégories)")
        deja_vus.add(cle)
        evenements.append(Evenement(url=url, tm_event_id=tm_id, libelle=libelle, categories=cats))

    cfg = Config(
        evenements=evenements,
        webhook_discord=str(brut.get("webhook_discord") or "").strip() or None,
        intervalle_secondes=int(brut.get("intervalle_secondes", 60)),
        jitter_secondes=int(brut.get("jitter_secondes", 5)),
        timeout_secondes=int(brut.get("timeout_secondes", 15)),
        backoff_max_secondes=int(brut.get("backoff_max_secondes", 900)),
        user_agent=str(brut.get("user_agent") or Config.user_agent),
        etat_fichier=str(brut.get("etat_fichier") or "./state.json"),
        verbosite=str(brut.get("verbosite") or "INFO"),
    )

    if cfg.intervalle_secondes < PLANCHER_INTERVALLE_S:
        raise ConfigError(
            f"intervalle_secondes={cfg.intervalle_secondes} < plancher {PLANCHER_INTERVALLE_S}s. "
            "Configuration rejetée."
        )
    if cfg.jitter_secondes < 0:
        raise ConfigError(f"jitter_secondes={cfg.jitter_secondes} négatif. Configuration rejetée.")
    if cfg.timeout_secondes <= 0:
        raise ConfigError(f"timeout_secondes={cfg.timeout_secondes} invalide. Configuration rejetée.")
    if cfg.backoff_max_secondes < cfg.intervalle_secondes:
        raise ConfigError(
            f"backoff_max_secondes={cfg.backoff_max_secondes} < intervalle_secondes. Configuration rejetée."
        )
    return cfg
