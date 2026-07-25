# veilleur — moniteur de disponibilité Ticketmaster.fr

Implémentation du **cahier des charges v1.1** : surveillance périodique de pages
d'événements Ticketmaster.fr par **requêtes HTTP directes uniquement** (sans navigateur
headless, sans proxy), détection des transitions « indisponible → disponible » et
**notification par webhook Discord**.

Projet autonome et volontairement léger : Python + `requests` + `PyYAML`, état persisté
dans un fichier JSON. Aucune dépendance à un navigateur ou à une base de données.

## Installation

```bat
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .
copy config.example.yaml config.yaml
```

Puis renseigner le webhook et les événements dans `config.yaml`. L'étape `pip install -e .`
est indispensable : c'est elle qui rend le paquet `veilleur` importable.

## Commandes (F9)

```bat
.venv\Scripts\python -m veilleur list          # vérifier les événements chargés
.venv\Scripts\python -m veilleur test-notify   # notification de test sur le webhook
.venv\Scripts\python -m veilleur check-once    # une passe : établit les baselines
.venv\Scripts\python -m veilleur run           # surveillance continue (Ctrl+C pour arrêter)
```

`--config chemin.yaml` s'écrit avant ou après la sous-commande. Au quotidien :
double-cliquer `lancer.bat` (surveillance continue) ou `test-une-fois.bat` (une passe).
`run` pose un verrou mono-instance : une seconde instance s'arrête net avec un message.

## Fonctionnement

- **Premier relevé d'un événement = baseline** : l'état existant au lancement ne
  déclenche jamais d'alerte. S'il contient du disponible, un unique message
  « 👁️ surveillance active » liste l'état initial (c'est notamment ainsi qu'une **levée
  de file d'attente** est signalée) ; sinon le démarrage est silencieux. Ensuite, chaque
  relevé est comparé au précédent (persisté dans `state.json`) et seules les
  **transitions positives** déclenchent une alerte : réouverture (indisponible →
  disponible), nouvelle catégorie disponible, quota en hausse.
- **File d'attente (Queue-it)** : jamais contournée. L'événement est simplement resondé
  plus tard, au plus toutes les 5 minutes, pour capter la levée de la file au plus tôt.
- **Dé-doublonnage (F5)** : détection « sur front » + état écrit **avant** l'envoi de
  la notification — un crash pendant l'envoi ne rejoue jamais une alerte.
- **Robustesse (§5.2)** : chaque événement est isolé ; en cas d'échec, son intervalle
  double (backoff, plafonné) sans affecter les autres. L'état survit aux redémarrages.
- **Optimisation (§5.1)** : requêtes conditionnelles (ETag / Last-Modified → 304),
  au moins 1 s entre deux requêtes, 3 tentatives sur erreur réseau.
- **Observabilité (F10/§5.3)** : journaux horodatés + compteurs cumulés
  (vérifications, alertes, erreurs, réponses 304) persistés et affichés.

## Traçabilité cahier des charges → code

| Réf. | Exigence | Module | Tests |
|---|---|---|---|
| F1 | Config (URL, libellé, catégories) | `config.py` | `test_config.py` |
| F2 | Sondage HTTP direct | `fetch.py` | `test_fetch.py` |
| F3 | `AvailabilitySnapshot` | `models.py`, `fetch.parse_payload` | `test_fetch.py` |
| F4 | Transitions positives | `detect.py` | `test_detect.py` |
| F5 | Dé-doublonnage | `detect.py` + persistance avant notification (`runner.py`) | `test_detect.py`, `test_runner.py` |
| F6 | Webhook Discord (catégories + places) | `notify.py` | `test_notify.py` |
| F7 | Intervalle + jitter | `config.py`, `runner.py` | `test_config.py`, `test_runner.py` |
| F9 | CLI run / check-once / list / test-notify | `__main__.py` | `test_cli.py` |
| F10 | Journalisation | tous (logging) | — |
| F11 | Persistance | `state.py` | `test_state.py` |
| §5.1 | Conditionnelles + backoff | `fetch.py`, `runner.py` | `test_fetch.py`, `test_runner.py` |
| §5.2 | Isolation, reprise | `runner.py`, `state.py` | `test_runner.py`, `test_state.py` |
| §5.3 | Compteurs | `state.py`, `runner.py` | `test_state.py` |

## Limites assumées

- L'endpoint n'émet aujourd'hui **ni ETag ni Last-Modified** (constaté le 2026-07-24 :
  seulement `cache-control: max-age=60`) : le support des requêtes conditionnelles est
  en place et testé, mais `reponses_304` restera à 0 tant que le site n'émet pas de
  validateurs.

- L'endpoint public interrogé (`liste-seance`) expose la disponibilité **par séance**
  (disponible / épuisé, prix minimum) mais **pas le nombre de places** : quand le site
  ne l'expose pas, l'alerte l'indique (« nombre non exposé par le site »). Obtenir les
  quantités par catégorie exigerait un navigateur — hors périmètre du CDC v1.1 (§2.1).
- Posture : requêtes espacées (≥ 60 s par événement, ≥ 1 s entre requêtes), User-Agent
  identifiable, aucun contournement (pas de proxy, pas de bypass de file d'attente ni
  de CAPTCHA). Une file d'attente détectée est traitée comme « réessayer plus tard ».

## Tests

```bat
.venv\Scripts\python -m pytest -q
```
