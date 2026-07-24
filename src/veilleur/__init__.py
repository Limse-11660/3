"""veilleur — moniteur de disponibilité Ticketmaster.fr (HTTP direct uniquement).

Implémente le cahier des charges v1.1 : surveillance périodique de pages
d'événements par requêtes HTTP directes (sans navigateur), détection des
transitions « indisponible → disponible » et notification par webhook Discord.
"""

__version__ = "1.0.0"
