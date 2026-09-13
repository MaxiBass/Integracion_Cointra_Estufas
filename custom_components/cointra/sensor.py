"""Plataforma sensor para los radiadores Cointra.

Expone la potencia instantánea ESTIMADA de cada radiador (no medida por
hardware, ya que la API de Cointra no reporta consumo en tiempo real):
Potencia nominal × LimitePotencia% si está calentando, si no 0 W.

Para el consumo ACUMULADO (kWh), añade un Helper "Integral de Riemann"
en Home Assistant sobre este sensor de potencia — mismo patrón que ya
usas en la calculadora de ahorro solar de la piscina. Ver el manual de
instalación para el paso a paso.
"""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        CointraPotenciaEstimadaSensor(coordinator, radiador_id)
        for radiador_id in coordinator.data.keys()
    ]
    async_add_entities(entities)


class CointraPotenciaEstimadaSensor(CoordinatorEntity, SensorEntity):
    """Potencia instantánea estimada, no medida (la API no da consumo real)."""

    _attr_has_entity_name = True
    _attr_name = "Potencia estimada"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash-outline"

    def __init__(self, coordinator, radiador_id: str):
        super().__init__(coordinator)
        self._radiador_id = radiador_id
        self._attr_unique_id = f"cointra_{radiador_id}_potencia_estimada"

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
    def native_value(self) -> float:
        if not self._data.get("Calentando"):
            return 0.0
        potencia_nominal = self._data.get("Potencia", 0) or 0
        limite_pct = self._data.get("LimitePotencia", 100) or 100
        return round(potencia_nominal * (limite_pct / 100), 1)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "potencia_nominal_w": self._data.get("Potencia", 0),
            "limite_potencia_pct": self._data.get("LimitePotencia", 100),
            "nota": "Estimación basada en potencia nominal, no una medición real",
        }
