"""Constantes para la integración Cointra Electric."""
from datetime import timedelta

DOMAIN = "cointra"

API_BASE = "https://gateway.grupoferroli.es/APIGW"
CLIENT_ID = "AppCointra"

CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 60  # segundos, refresco contra la nube de Cointra
MIN_UPDATE_INTERVAL = 15  # por debajo de esto se arriesga rate-limit/bloqueo de cuenta

CONF_RADIATOR_IPS = "radiator_ips"  # dict {IdRadiador: "192.168.x.x"}

PING_INTERVAL = timedelta(minutes=5)
PING_TIMEOUT_SECONDS = 2
PING_FAIL_THRESHOLD = 2  # fallos consecutivos para marcar offline (debounce);
                         # para volver a "online" basta 1 solo ping exitoso

# IdModoActual (Cointra) <-> preset_mode (Home Assistant)
MODE_TO_PRESET = {
    "C": "comfort",
    "E": "eco",
    "A": "antifrost",
    "P": "program",
    "M": "manual",
}
PRESET_TO_MODE = {v: k for k, v in MODE_TO_PRESET.items()}

# Qué campo de temperatura corresponde a cada modo
MODE_TEMP_FIELD = {
    "C": "TempComfort",
    "E": "TempEco",
    "A": "TempAntiFrost",
    "M": "TempForzado",
}