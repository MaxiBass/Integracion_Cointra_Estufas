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
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        CointraPingButton(coordinator, radiador_id)
        for radiador_id in coordinator.data.keys()
        if radiador_id in coordinator.radiator_ips
    ]
    async_add_entities(entities)


class CointraPingButton(CoordinatorEntity, ButtonEntity):
    """Fuerza un ping inmediato a todos los radiadores con IP configurada."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:lan-check"

    def __init__(self, coordinator, radiador_id: str):
        super().__init__(coordinator)
        self._radiador_id = radiador_id
        self._attr_name = "Comprobar disponibilidad ahora"
        self._attr_unique_id = f"cointra_{radiador_id}_ping_ahora"

    @property
    def _data(self) -> dict:
        return self.coordinator.data.get(self._radiador_id, {})

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._radiador_id)},
            name=self._data.get("Nombre", self._radiador_id),
            manufacturer="Ferroli / Cointra",
            model=self._data.get("Tipo", "Radiador WIFI"),
            sw_version=self._data.get("Software"),
        )

    async def async_press(self) -> None:
        # Fuerza un ciclo de ping completo (afecta a todos los radiadores
        # con IP configurada, no solo a este; es barato y así se simplifica
        # la lógica en vez de aislar un único host).
        await self.coordinator.async_ping_all()
