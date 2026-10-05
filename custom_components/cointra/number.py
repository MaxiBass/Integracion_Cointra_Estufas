"""Plataforma number para ajustes numéricos de los radiadores Cointra.

Expone como controles deslizantes independientes:
- Brillo de la pantalla del radiador
- Duración del brillo tras pulsar un botón
- Límite de potencia (%) sobre la potencia nominal del radiador

Rangos: la app muestra el brillo en pasos de 30 (0/30/60/90); la nube lo
guarda de 0 a 100 y, tras una prueba real, se deja de uno en uno.
DuracionBrillo es un campo numérico de 1 a 240 s en la app.
"""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import formatear_valor
from .const import DOMAIN
from .entity import CointraRadiadorEntity

_LOGGER = logging.getLogger(__name__)

# (campo, nombre_amigable, icono, min, max, step, unidad)
NUMBER_DEFS = [
    ("Brillo", "Brillo de pantalla", "mdi:brightness-6", 0, 100, 1, "%"),
    ("DuracionBrillo", "Duración del brillo", "mdi:timer-outline", 1, 240, 1, "s"),
    ("LimitePotencia", "Límite de potencia", "mdi:flash", 10, 100, 5, "%"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for radiador_id in coordinator.data.keys():
        for campo, nombre, icono, min_v, max_v, step, unidad in NUMBER_DEFS:
            entities.append(
                CointraNumber(coordinator, radiador_id, campo, nombre, icono, min_v, max_v, step, unidad)
            )
    async_add_entities(entities)


class CointraNumber(CointraRadiadorEntity, NumberEntity):
    """Control numérico genérico para un campo del radiador Cointra."""

    def __init__(
        self,
        coordinator,
        radiador_id: str,
        campo: str,
        nombre: str,
        icono: str,
        min_v: float,
        max_v: float,
        step: float,
        unidad: str,
    ):
        super().__init__(coordinator, radiador_id)
        self._campo = campo
        self._attr_name = nombre
        self._attr_icon = icono
        self._attr_native_min_value = min_v
        self._attr_native_max_value = max_v
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unidad
        self._attr_unique_id = f"cointra_{radiador_id}_{campo}"

    @property
    def native_value(self):
        return self._data.get(self._campo)

    async def async_set_native_value(self, value: float) -> None:
        # La API de Cointra espera enteros "limpios" (ej. "10"), no "10.0".
        # La app oficial siempre manda enteros sin decimales, así que
        # forzamos el mismo formato para evitar que el backend descarte
        # el cambio silenciosamente (200 OK sin aplicar el valor).
        valor_str = formatear_valor(value)

        _LOGGER.debug(
            "Cointra number: enviando Campo=%s Valor=%s (raw value=%r) para radiador %s",
            self._campo, valor_str, value, self._radiador_id,
        )

        await self.coordinator.api.set_radiador(
            self._radiador_id, [{"Campo": self._campo, "Valor": valor_str}]
        )
        await self.coordinator.async_request_refresh()
