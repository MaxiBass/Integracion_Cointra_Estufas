"""Integración Cointra Electric para Home Assistant."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from icmplib import async_ping
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CointraApiClient, CointraApiError, CointraAuthError
from .const import (
    CONF_RADIATOR_IPS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PING_FAIL_THRESHOLD,
    PING_INTERVAL,
    PING_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SWITCH, Platform.NUMBER, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]


async def _ping_host(ip: str) -> bool:
    """Hace un ping ICMP a una IP local usando icmplib.

    Igual que la integración oficial 'Ping' de Home Assistant: primero
    intenta en modo privilegiado (socket raw, más fiable, requiere
    CAP_NET_RAW), y si el contenedor no tiene permisos para ello, cae a
    modo no privilegiado.
    """
    if not ip:
        return True
    try:
        host = await async_ping(ip, count=1, timeout=PING_TIMEOUT_SECONDS, privileged=True)
        return bool(host.is_alive)
    except OSError:
        _LOGGER.debug(
            "Ping privilegiado a %s falló por permisos, probando modo no privilegiado", ip
        )
        try:
            host = await async_ping(ip, count=1, timeout=PING_TIMEOUT_SECONDS, privileged=False)
            return bool(host.is_alive)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Error haciendo ping a %s", ip, exc_info=True)
            return False
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Error haciendo ping a %s", ip, exc_info=True)
        return False


class CointraCoordinator(DataUpdateCoordinator):
    """Coordina las llamadas periódicas a la API de Cointra."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: CointraApiClient,
        update_interval: int,
        radiator_ips: dict[str, str],
    ):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )
        self.api = api
        self.config_entry = entry
        self.id_instalacion: int | None = None
        self.radiator_ips = radiator_ips
        # Estado del ping por radiador. Si un radiador no tiene IP
        # configurada, simplemente no aparece aquí y se asume disponible
        # (no podemos comprobarlo, así que no penalizamos su disponibilidad).
        self.ping_status: dict[str, bool] = {}
        self.last_ping_ok: dict[str, str] = {}  # radiador_id -> ISO timestamp del último ping exitoso
        self._consecutive_fails: dict[str, int] = {}

    async def _async_update_data(self):
        try:
            if self.id_instalacion is None:
                instalaciones = await self.api.get_instalaciones()
                if not instalaciones:
                    raise UpdateFailed("No se encontraron instalaciones en la cuenta Cointra")
                self.id_instalacion = instalaciones[0]["datos"]["IdInstalacion"]

            radiadores = await self.api.get_radiadores(self.id_instalacion)
            return {r["IdRadiador"]: r for r in radiadores}
        except CointraAuthError as err:
            raise ConfigEntryAuthFailed(
                "Credenciales de Cointra rechazadas, reautenticación necesaria"
            ) from err
        except CointraApiError as err:
            raise UpdateFailed(f"Error comunicando con Cointra: {err}") from err

    async def async_ping_all(self, *_now) -> None:
        """Hace ping a todos los radiadores con IP local configurada.

        Se ejecuta cada PING_INTERVAL, independientemente del intervalo de
        refresco contra la nube. Actualiza self.ping_status y notifica a
        las entidades sin necesidad de esperar al siguiente refresh normal.

        Debounce asimétrico: para marcar un radiador como offline exigimos
        PING_FAIL_THRESHOLD fallos consecutivos (evita falsos positivos por
        un hipo puntual de la red). Para volver a marcarlo online basta con
        UN solo ping exitoso, así la recuperación se detecta lo antes
        posible en el siguiente ciclo, sin arrastrar el mismo debounce.
        """
        if not self.radiator_ips:
            return

        resultados = await asyncio.gather(
            *(_ping_host(ip) for ip in self.radiator_ips.values())
        )
        for radiador_id, ok in zip(self.radiator_ips.keys(), resultados):
            if ok:
                self._consecutive_fails[radiador_id] = 0
                self.last_ping_ok[radiador_id] = dt_util.utcnow().isoformat()
                nuevo_estado = True
            else:
                fallos = self._consecutive_fails.get(radiador_id, 0) + 1
                self._consecutive_fails[radiador_id] = fallos
                if fallos >= PING_FAIL_THRESHOLD:
                    nuevo_estado = False
                else:
                    # Aún no hemos alcanzado el umbral: mantenemos el
                    # último estado conocido (por defecto, disponible).
                    nuevo_estado = self.ping_status.get(radiador_id, True)

            if self.ping_status.get(radiador_id) != nuevo_estado:
                _LOGGER.info(
                    "Cointra: radiador %s ahora está %s (ping a %s)",
                    radiador_id,
                    "disponible" if nuevo_estado else "NO disponible",
                    self.radiator_ips[radiador_id],
                )
            self.ping_status[radiador_id] = nuevo_estado

        self.async_update_listeners()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura la integración a partir de una config entry (UI)."""
    _LOGGER.info("Cointra Electric: prueba de actualización vía HACS OK (marca updatetest-01)")
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Recarga la integración si se cambian las opciones (p. ej. intervalo o IPs)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Descarga la integración."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
