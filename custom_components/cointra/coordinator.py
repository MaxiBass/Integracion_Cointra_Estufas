"""Coordinator de Cointra: sondeo de la nube y ping local a cada radiador."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from icmplib import SocketPermissionError, async_ping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CointraApiClient, CointraApiError, CointraAuthError
from .const import (
    DOMAIN,
    PING_FAIL_THRESHOLD,
    PING_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


async def _ping_host(ip: str) -> bool:
    """Hace un ping ICMP a una IP local usando icmplib.

    Igual que la integración oficial 'Ping' de Home Assistant: primero
    intenta en modo privilegiado (socket raw, más fiable, requiere
    CAP_NET_RAW), y si el contenedor no tiene permisos para ello, cae a
    modo no privilegiado. Sin permisos icmplib lanza SocketPermissionError,
    que NO es un OSError: capturar solo OSError dejaba el fallback sin
    efecto y daba el radiador por caído.
    """
    if not ip:
        return True
    try:
        host = await async_ping(ip, count=1, timeout=PING_TIMEOUT_SECONDS, privileged=True)
        return bool(host.is_alive)
    except (OSError, SocketPermissionError):
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


class CointraCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Mantiene en caché los radiadores de la cuenta, indexados por IdRadiador."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: CointraApiClient,
        update_interval: int,
        radiator_ips: dict[str, str],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )
        self.api = api
        self.id_instalacion: int | None = None
        self.radiator_ips = radiator_ips
        # Estado del ping por radiador. Si un radiador no tiene IP
        # configurada, simplemente no aparece aquí y se asume disponible
        # (no podemos comprobarlo, así que no penalizamos su disponibilidad).
        self.ping_status: dict[str, bool] = {}
        self.last_ping_ok: dict[str, str] = {}  # radiador_id -> ISO timestamp del último ping exitoso
        self.consecutive_fails: dict[str, int] = {}

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            if self.id_instalacion is None:
                self.id_instalacion = await self.api.get_id_instalacion()
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
                self.consecutive_fails[radiador_id] = 0
                self.last_ping_ok[radiador_id] = dt_util.utcnow().isoformat()
                nuevo_estado = True
            else:
                fallos = self.consecutive_fails.get(radiador_id, 0) + 1
                self.consecutive_fails[radiador_id] = fallos
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
