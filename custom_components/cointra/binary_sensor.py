"""Plataforma binary_sensor para la integración Cointra.

Expone:
- Servidor Cointra (ÚNICO, a nivel de cuenta, no por radiador): si la
  nube de Ferroli/Cointra está respondiendo en el último ciclo de
  refresco. Es un hecho compartido por todos los radiadores, así que se
  agrupa bajo un dispositivo propio que representa la cuenta, en vez de
  duplicarse en cada radiador.
- Conexión local (por radiador, solo si tiene IP configurada): si el
  radiador responde al ping en tu red local. Señal mucho más fiable que
  cualquier campo de la API para saber si está físicamente conectado.
- Calentando (por radiador): si la resistencia está activa ahora mismo.
- Error (por radiador): si el propio radiador reporta un código de error
  real, independientemente de la conectividad.
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity import CointraRadiadorEntity, cuenta_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list = [CointraServidorSensor(coordinator, entry)]

    for radiador_id in coordinator.data.keys():
        entities.append(CointraCalentandoSensor(coordinator, radiador_id))
        entities.append(CointraErrorSensor(coordinator, radiador_id))
        if radiador_id in coordinator.radiator_ips:
            entities.append(CointraConexionLocalSensor(coordinator, radiador_id))

    async_add_entities(entities)


class CointraServidorSensor(CoordinatorEntity, BinarySensorEntity):
    """Indica si la nube de Cointra responde. Única por cuenta, no por radiador."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Servidor Cointra"

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"cointra_{entry.entry_id}_servidor"

    @property
    def device_info(self) -> DeviceInfo:
        return cuenta_device_info(self._entry)

    @property
    def available(self) -> bool:
        # Este sensor SIEMPRE está disponible: es precisamente el que te
        # avisa cuando el resto de la integración no lo está.
        return True

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.last_update_success)


class CointraConexionLocalSensor(CointraRadiadorEntity, BinarySensorEntity):
    """Indica si el radiador responde al ping en la red local."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Conexión local"

    def __init__(self, coordinator, radiador_id: str):
        super().__init__(coordinator, radiador_id)
        self._attr_unique_id = f"cointra_{radiador_id}_conexion_local"

    @property
    def available(self) -> bool:
        # Siempre visible: es el propio indicador de si hay conexión o no.
        return bool(self._data)

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.ping_status.get(self._radiador_id, True))

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "ip": self.coordinator.radiator_ips.get(self._radiador_id),
            "ultimo_ping_exitoso": self.coordinator.last_ping_ok.get(self._radiador_id),
        }


class CointraCalentandoSensor(CointraRadiadorEntity, BinarySensorEntity):
    """Indica si la resistencia del radiador está activa ahora mismo."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_name = "Calentando"

    def __init__(self, coordinator, radiador_id: str):
        super().__init__(coordinator, radiador_id)
        self._attr_unique_id = f"cointra_{radiador_id}_calentando"

    @property
    def is_on(self) -> bool:
        return bool(self._data.get("Calentando"))


class CointraErrorSensor(CointraRadiadorEntity, BinarySensorEntity):
    """Indica si el propio radiador reporta un código de error real.

    A propósito NO mezcla aquí conectividad (eso ya lo cubren 'Servidor
    Cointra' y 'Conexión local' por separado) — solo errores de
    funcionamiento reportados por la API (campo Error/ErrorList).
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Error"

    def __init__(self, coordinator, radiador_id: str):
        super().__init__(coordinator, radiador_id)
        self._attr_unique_id = f"cointra_{radiador_id}_error"

    @property
    def available(self) -> bool:
        return bool(self._data)

    @property
    def is_on(self) -> bool:
        error_code = self._data.get("Error", 0) or 0
        return error_code != 0

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "error_code": self._data.get("Error", 0),
            "error_list": self._data.get("ErrorList", ""),
        }
