"""Plataforma climate para los radiadores Cointra."""
from __future__ import annotations

import logging

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_TEMP_FIELD, MODE_TO_PRESET, PRESET_TO_MODE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        CointraRadiador(coordinator, radiador_id)
        for radiador_id in coordinator.data.keys()
    ]
    async_add_entities(entities)


class CointraRadiador(CoordinatorEntity, ClimateEntity):
    """Representa un radiador Cointra como entidad climate."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = ["comfort", "eco", "antifrost", "program", "manual"]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_min_temp = 7
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5
    _attr_has_entity_name = True
    _attr_name = None  # usa el nombre del dispositivo tal cual
    _attr_icon = "mdi:heating-coil"

    def __init__(self, coordinator, radiador_id: str):
        super().__init__(coordinator)
        self._radiador_id = radiador_id
        self._attr_unique_id = f"cointra_{radiador_id}_climate"

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
    def current_temperature(self):
        return self._data.get("TempActual")

    @property
    def hvac_mode(self):
        return HVACMode.HEAT if self._data.get("Encendido") else HVACMode.OFF

    @property
    def hvac_action(self):
        if not self._data.get("Encendido"):
            return "off"
        return "heating" if self._data.get("Calentando") else "idle"

    @property
    def preset_mode(self):
        modo = self._data.get("IdModoActual", "C")
        return MODE_TO_PRESET.get(modo, "comfort")

    @property
    def target_temperature(self):
        modo = self._data.get("IdModoActual", "C")
        campo = MODE_TEMP_FIELD.get(modo, "TempComfort")
        return self._data.get(campo)

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        encendido = "true" if hvac_mode == HVACMode.HEAT else "false"
        await self.coordinator.api.set_radiador(
            self._radiador_id, [{"Campo": "Encendido", "Valor": encendido}]
        )
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        modo = PRESET_TO_MODE.get(preset_mode, "C")
        cambios = [
            {"Campo": "IdModoActual", "Valor": modo},
            {"Campo": "Encendido", "Valor": "true"},
        ]
        await self.coordinator.api.set_radiador(self._radiador_id, cambios)
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        modo = self._data.get("IdModoActual", "C")
        campo = MODE_TEMP_FIELD.get(modo, "TempComfort")
        await self.coordinator.api.set_radiador(
            self._radiador_id, [{"Campo": campo, "Valor": str(temperature)}]
        )
        await self.coordinator.async_request_refresh()
