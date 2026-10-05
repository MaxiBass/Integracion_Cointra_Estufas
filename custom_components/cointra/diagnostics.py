"""Diagnóstico descargable (Ajustes → Dispositivos y servicios → la entrada →
⋮ → Descargar diagnóstico). Sin correo, contraseña, IPs, nombres ni ids."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_RADIATOR_IPS, DOMAIN
from .coordinator import CointraCoordinator

# El título y el unique_id de la entrada son el correo de la cuenta.
TO_REDACT_ENTRY = {CONF_USERNAME, CONF_PASSWORD, CONF_RADIATOR_IPS, "title", "unique_id"}
TO_REDACT_RADIADOR = {"Nombre", "IdRadiador"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: CointraCoordinator = hass.data[DOMAIN][entry.entry_id]
    radiadores = coordinator.data or {}
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT_ENTRY),
        "cloud": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
            "update_interval_s": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
        },
        # Tal como los devuelve la nube, para ver campos que la integración
        # no muestra. Mismo orden que `ping`.
        "radiators": [async_redact_data(r, TO_REDACT_RADIADOR) for r in radiadores.values()],
        "ping": [
            {
                "configured": rid in coordinator.radiator_ips,
                "status": coordinator.ping_status.get(rid),
                "consecutive_fails": coordinator.consecutive_fails.get(rid, 0),
                "last_ping_ok": coordinator.last_ping_ok.get(rid),
            }
            for rid in radiadores
        ],
    }
