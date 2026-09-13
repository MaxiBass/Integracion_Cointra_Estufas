"""Plataforma switch para funciones on/off de los radiadores Cointra.

Expone como interruptores independientes:
- Bloqueo de teclado del radiador
- Detección de ventana abierta (activar/desactivar la función)
- Arranque adaptativo (activar/desactivar la función)

Nota importante (verificado empíricamente contra la API real): el campo
de configuración ("¿está la función activada?") y el campo "*Status"
("¿se está detectando/aplicando ahora mismo?") son cosas DISTINTAS.
Escribir en VentanasAbiertas/ArranqueAdaptativo cambia el campo base,
pero el campo *Status no se mueve por eso — refleja la detección en
tiempo real del propio radiador, no la configuración. El switch debe
reflejar y escribir siempre el campo base; el *Status se expone como
atributo informativo aparte.
"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# (campo_config, campo_status_informativo_o_None, nombre_amigable, icono)
SWITCH_DEFS = [
    ("TecladoBloqueado", None, "Bloqueo de teclado", "mdi:lock"),
    ("VentanasAbiertas", "VentanasAbiertasStatus", "Detección de ventana abierta", "mdi:window-open-variant"),
    ("ArranqueAdaptativo", "ArranqueAdaptativoStatus", "Arranque adaptativo", "mdi:clock-fast"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for radiador_id in coordinator.data.keys():
        for campo, campo_status, nombre, icono in SWITCH_DEFS:
            entities.append(
                CointraSwitch(coordinator, radiador_id, campo, campo_status, nombre, icono)
            )
    async_add_entities(entities)


class CointraSwitch(CoordinatorEntity, SwitchEntity):
    """Interruptor genérico para un campo booleano de configuración de un radiador Cointra."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, radiador_id: str, campo: str, campo_status: str | None, nombre: str, icono: str):
        super().__init__(coordinator)
        self._radiador_id = radiador_id
        self._campo = campo
        self._campo_status = campo_status
        self._attr_name = nombre
        self._attr_icon = icono
        self._attr_unique_id = f"cointra_{radiador_id}_{campo}"

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

    @property
    def available(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        if not self._data:
            return False
        return self.coordinator.ping_status.get(self._radiador_id, True)

    @property
    def is_on(self) -> bool:
        # SIEMPRE el campo de configuración, nunca el *Status (verificado:
        # escribir el campo base no mueve el *Status, que es la detección
        # en tiempo real del propio radiador, no la config).
        return bool(self._data.get(self._campo))

    @property
    def extra_state_attributes(self) -> dict | None:
        if self._campo_status is None:
            return None
        return {"detectado_ahora_mismo": bool(self._data.get(self._campo_status))}

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.api.set_radiador(
            self._radiador_id, [{"Campo": self._campo, "Valor": "true"}]
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.api.set_radiador(
            self._radiador_id, [{"Campo": self._campo, "Valor": "false"}]
        )
        await self.coordinator.async_request_refresh()
