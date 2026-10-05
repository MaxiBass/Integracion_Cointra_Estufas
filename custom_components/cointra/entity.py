"""DeviceInfo y entidad base: cada radiador es un dispositivo, y todos cuelgan
del dispositivo de la cuenta de Cointra."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL_CUENTA, MODEL_RADIADOR
from .coordinator import CointraCoordinator


def cuenta_device_info(entry: ConfigEntry) -> DeviceInfo:
    """El dispositivo «virtual» de la cuenta: aloja «Servidor Cointra» y es el padre de los radiadores."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Cointra Electric ({entry.data.get(CONF_USERNAME, 'cuenta')})",
        manufacturer=MANUFACTURER,
        model=MODEL_CUENTA,
        entry_type=DeviceEntryType.SERVICE,
    )


class CointraRadiadorEntity(CoordinatorEntity[CointraCoordinator]):
    """Base de las entidades de un radiador: dispositivo y disponibilidad comunes."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CointraCoordinator, radiador_id: str) -> None:
        super().__init__(coordinator)
        self._radiador_id = radiador_id

    @property
    def _data(self) -> dict:
        return self.coordinator.data.get(self._radiador_id, {})

    @property
    def device_info(self) -> DeviceInfo:
        entry = self.coordinator.config_entry
        # Desde HA 2026.9 el dispositivo padre se indica por su id en el
        # registro (via_device_id) y no por su identificador (via_device, que
        # deja de funcionar en 2027.8). El de la cuenta lo registra
        # async_setup_entry antes de crear ninguna entidad.
        return DeviceInfo(
            identifiers={(DOMAIN, self._radiador_id)},
            name=self._data.get("Nombre", self._radiador_id),
            manufacturer=MANUFACTURER,
            model=self._data.get("Tipo", MODEL_RADIADOR),
            sw_version=self._data.get("Software"),
            via_device_id=dr.async_get_device_id_by_identifier(
                self.hass, (DOMAIN, entry.entry_id), config_entry_id=entry.entry_id
            ),
        )

    @property
    def available(self) -> bool:
        # La nube y el radiador son dos señales independientes (DECISIONES
        # §3.6): si falla cualquiera, la entidad no está disponible.
        if not self.coordinator.last_update_success:
            return False
        if not self._data:
            return False
        return self.coordinator.ping_status.get(self._radiador_id, True)
