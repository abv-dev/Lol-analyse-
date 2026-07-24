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
        # Nombre de match ids demandés par joueur échantillonné
        self.matches_per_player = int(os.environ.get("MATCHES_PER_PLAYER", "20"))
        # Profondeur max de pagination League-Exp par tier/division avant de
        # passer à la suivante (diversité de l'échantillon)
        self.max_pages_per_division = int(os.environ.get("MAX_PAGES_PER_DIVISION", "30"))
        # Rafraîchissement de la version ddragon (secondes)
        self.patch_check_interval = int(os.environ.get("PATCH_CHECK_INTERVAL", str(6 * 3600)))

    def require_api_key(self) -> None:
        if not self.api_key:
            raise SystemExit(
                "RIOT_API_KEY manquante. Copie .env.example vers .env et renseigne ta clé."
            )
