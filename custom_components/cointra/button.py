"""Plataforma button para los radiadores Cointra.

Un botón por radiador para forzar una comprobación de disponibilidad
inmediata por ping, sin esperar al ciclo automático de 5 minutos. Útil
justo después de volver a enchufar físicamente un radiador.
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .entity import CointraRadiadorEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        CointraPingButton(coordinator, radiador_id)
        for radiador_id in coordinator.data.keys()
        if radiador_id in coordinator.radiator_ips
    ]
    async_add_entities(entities)


class CointraPingButton(CointraRadiadorEntity, ButtonEntity):
    """Fuerza un ping inmediato a todos los radiadores con IP configurada."""

    _attr_icon = "mdi:lan-check"
    _attr_name = "Comprobar disponibilidad ahora"

    def __init__(self, coordinator, radiador_id: str):
        super().__init__(coordinator, radiador_id)
        self._attr_unique_id = f"cointra_{radiador_id}_ping_ahora"

    @property
    def available(self) -> bool:
        # No depende del ping del radiador (justo sirve para comprobarlo
        # cuando está caído), solo de que la última lectura de la nube fuera bien.
        return self.coordinator.last_update_success

    async def async_press(self) -> None:
        # Fuerza un ciclo de ping completo (afecta a todos los radiadores
        # con IP configurada, no solo a este; es barato y así se simplifica
        # la lógica en vez de aislar un único host).
        await self.coordinator.async_ping_all()
