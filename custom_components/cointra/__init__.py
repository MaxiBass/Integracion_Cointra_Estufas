"""Integración Cointra Electric para Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import CointraApiClient
from .const import (
    CONF_RADIATOR_IPS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PING_INTERVAL,
)
from .coordinator import CointraCoordinator
from .entity import cuenta_device_info

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SWITCH, Platform.NUMBER, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura la integración a partir de una config entry (UI)."""
    session = async_get_clientsession(hass)
    api = CointraApiClient(
        session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )

    update_interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    radiator_ips = entry.data.get(CONF_RADIATOR_IPS, {})

    coordinator = CointraCoordinator(hass, entry, api, update_interval, radiator_ips)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Primer ping inmediato para no esperar 5 min a tener disponibilidad real,
    # y después uno periódico cada PING_INTERVAL.
    await coordinator.async_ping_all()
    entry.async_on_unload(
        async_track_time_interval(hass, coordinator.async_ping_all, PING_INTERVAL)
    )

    # El dispositivo de la cuenta tiene que existir antes que las entidades:
    # los radiadores cuelgan de él (via_device_id, ver entity.py).
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **cuenta_device_info(entry)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Recarga la integración si se cambian las opciones (p. ej. intervalo o IPs)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Deja eliminar desde HA el dispositivo de un radiador que ya no está en la cuenta.

    Sin esto, un radiador dado de baja en la app dejaría su dispositivo y sus
    entidades como «no disponibles» sin forma de quitarlos.
    """
    coordinator: CointraCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return False
    vivos = {(DOMAIN, entry.entry_id)} | {(DOMAIN, rid) for rid in coordinator.data or {}}
    return not device.identifiers & vivos


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Descarga la integración."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
