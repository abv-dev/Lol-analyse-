"""Configuration : .env, régions, buckets de tiers."""

import os

# Routing régional Riot -> plateforme échantillonnée
REGIONS = {
    "europe": "euw1",
    "asia": "kr",
    "americas": "na1",
}

# Buckets de tiers pour l'échantillonnage (ordre = ordre du round-robin)
BUCKETS = {
    "IRON_BRONZE": ["IRON", "BRONZE"],
    "SILVER_GOLD": ["SILVER", "GOLD"],
    "PLAT_EMERALD": ["PLATINUM", "EMERALD"],
    "DIAMOND_PLUS": ["DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"],
}

# Tiers apex : une seule "division" I côté League-Exp-V4
APEX_TIERS = {"MASTER", "GRANDMASTER", "CHALLENGER"}
DIVISIONS = ["I", "II", "III", "IV"]

QUEUE_ID = 420  # ranked solo/duo

# Limites de requêtes par région : (req/s, req/2min).
# Riot limite PAR CLÉ et PAR RÉGION (20/s, 100/2min pour une clé personnelle).
# Cible en régime réel : ~42 req/min par région (84/2min, marge sur les
# 50/min de la clé), et ~34 req/min sur europe (68/2min) car lol-live-coach
# tourne sur la MÊME clé (euw1) : ses briefs live et analyses post-game ne
# doivent pas se prendre de 429 à cause du collecteur.
DEFAULT_RATE_LIMITS = {
    "europe": (14, 68),
    "asia": (18, 84),
    "americas": (18, 84),
}

DDRAGON_VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"


def load_env(path: str = ".env") -> None:
    """Charge un .env minimaliste (KEY=VALUE) sans dépendance externe."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Config:
    def __init__(self):
        load_env()
        self.api_key = os.environ.get("RIOT_API_KEY", "")
        self.db_path = os.environ.get("DB_PATH", "data/matches.db")
        self.log_dir = os.environ.get("LOG_DIR", "logs")
        self.pid_file = os.environ.get("PID_FILE", "collector.pid")
        # DEBUG pour tracer chaque acquire du rate limiter (attente + occupation)
        self.log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
        # Nombre de match ids demandés par joueur échantillonné
        self.matches_per_player = int(os.environ.get("MATCHES_PER_PLAYER", "20"))
        # Fenêtre glissante de collecte : seuls les matchs plus récents que
        # MATCH_MAX_AGE_DAYS sont demandés (filtre startTime côté Riot).
        # Évite de remplir la base de vieux patchs via les joueurs inactifs.
        self.match_max_age_days = int(os.environ.get("MATCH_MAX_AGE_DAYS", "28"))
        # Fraction des matchs dont la timeline est collectée (tirage
        # déterministe sur le match_id). 1.0 = tous (débit de matchs divisé
        # par ~2), 0 = aucune.
        self.timeline_sample_rate = float(os.environ.get("TIMELINE_SAMPLE_RATE", "0.33"))
        # Profondeur max de pagination League-Exp par tier/division avant de
        # passer à la suivante (diversité de l'échantillon)
        self.max_pages_per_division = int(os.environ.get("MAX_PAGES_PER_DIVISION", "30"))
        # Rafraîchissement de la version ddragon (secondes)
        self.patch_check_interval = int(os.environ.get("PATCH_CHECK_INTERVAL", str(6 * 3600)))
        # Overrides par région : RATE_LIMIT_EUROPE=14,72 (req/s, req/2min)
        self.rate_limits = {}
        for region, default in DEFAULT_RATE_LIMITS.items():
            raw = os.environ.get(f"RATE_LIMIT_{region.upper()}")
            if raw:
                per_s, per_2min = (int(x) for x in raw.split(","))
                self.rate_limits[region] = (per_s, per_2min)
            else:
                self.rate_limits[region] = default

    def require_api_key(self) -> None:
        if not self.api_key:
            raise SystemExit(
                "RIOT_API_KEY manquante. Copie .env.example vers .env et renseigne ta clé."
            )
