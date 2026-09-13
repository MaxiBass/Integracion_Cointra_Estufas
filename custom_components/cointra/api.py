"""Cliente API para la nube de Cointra Electric (gateway.grupoferroli.es)."""
import logging
import time

import aiohttp

from .const import API_BASE, CLIENT_ID

_LOGGER = logging.getLogger(__name__)


class CointraApiError(Exception):
    """Error genérico de la API de Cointra."""


class CointraAuthError(CointraApiError):
    """Error de autenticación."""


async def _parse_json(resp: aiohttp.ClientResponse):
    """Decodifica el cuerpo JSON de una respuesta, envolviendo cuerpos vacíos/no-JSON."""
    try:
        return await resp.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError) as err:
        raise CointraApiError(
            f"Respuesta no válida del servidor de Cointra (HTTP {resp.status}): {err}"
        ) from err


class CointraApiClient:
    """Encapsula login, refresco de token y llamadas a la API de Cointra."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str):
        self._session = session
        self._username = username
        self._password = password

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float = 0
        self.id_cia: int | None = None
        self.id_user: str | None = None

    async def _login(self) -> None:
        data = {
            "grant_type": "password",
            "username": self._username,
            "password": self._password,
            "client_id": CLIENT_ID,
        }
        try:
            async with self._session.post(f"{API_BASE}/token", data=data) as resp:
                payload = await _parse_json(resp)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CointraApiError(
                f"No se pudo conectar con el servidor de Cointra: {err}"
            ) from err

        if resp.status != 200 or "access_token" not in payload:
            raise CointraAuthError(f"Login fallido: {payload}")
        self._store_tokens(payload)

    async def _refresh(self) -> None:
        if not self._refresh_token:
            await self._login()
            return
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": CLIENT_ID,
        }
        try:
            async with self._session.post(f"{API_BASE}/token", data=data) as resp:
                payload = await _parse_json(resp)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CointraApiError(
                f"No se pudo conectar con el servidor de Cointra: {err}"
            ) from err

        if resp.status != 200 or "access_token" not in payload:
            _LOGGER.debug("Refresh falló, reintentando login completo: %s", payload)
            await self._login()
            return
        self._store_tokens(payload)

    def _store_tokens(self, payload: dict) -> None:
        self._access_token = payload["access_token"]
        self._refresh_token = payload.get("refresh_token", self._refresh_token)
        expires_in = int(payload.get("expires_in", 1100))
        # Nos damos un margen de 60s antes de la expiración real
        self._token_expires_at = time.time() + expires_in - 60
        self.id_cia = int(payload.get("company", 0) or 0)
        self.id_user = payload.get("idUser")

    async def _ensure_token(self) -> None:
        if not self._access_token or time.time() >= self._token_expires_at:
            if self._access_token:
                await self._refresh()
            else:
                await self._login()

    async def _authed_request(self, method: str, url: str, **kwargs):
        await self._ensure_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        headers["Accept"] = "application/json"

        try:
            async with self._session.request(method, url, headers=headers, **kwargs) as resp:
                if resp.status == 401:
                    # Token rechazado a mitad de camino: reintenta una vez tras login
                    await self._login()
                    headers["Authorization"] = f"Bearer {self._access_token}"
                    async with self._session.request(
                        method, url, headers=headers, **kwargs
                    ) as resp2:
                        payload, status = await _parse_json(resp2), resp2.status
                else:
                    payload, status = await _parse_json(resp), resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CointraApiError(
                f"No se pudo conectar con el servidor de Cointra: {err}"
            ) from err

        if status >= 400:
            raise CointraApiError(f"Cointra devolvió HTTP {status}: {payload}")
        return payload

    async def get_instalaciones(self) -> list[dict]:
        result = await self._authed_request("GET", f"{API_BASE}/api/Instalaciones")
        if not isinstance(result, list) or not result:
            raise CointraApiError(f"Respuesta inesperada al listar instalaciones: {result}")
        return result

    async def get_radiadores(self, id_instalacion: int, id_zona: int = 0) -> list[dict]:
        url = f"{API_BASE}/api/Radiador?IdInstalacion={id_instalacion}&IdZona={id_zona}"
        result = await self._authed_request("GET", url)
        if isinstance(result, dict) and "Message" in result:
            raise CointraApiError(result["Message"])
        return result

    async def set_radiador(self, id_radiador: str, cambios: list[dict]) -> dict:
        """Cambia campos de UN radiador concreto.

        idInstalacion debe ir a 0 para que el filtro por 'Heaters' funcione
        y no se aplique el cambio a toda la instalación (comportamiento
        verificado empíricamente contra la API real).
        """
        body = {
            "Zones": [],
            "Heaters": [id_radiador],
            "Cambios": cambios,
            "idCia": self.id_cia or 0,
            "idInstalacion": 0,
        }
        result = await self._authed_request("PUT", f"{API_BASE}/api/Radiador", json=body)
        if isinstance(result, dict) and str(result.get("traza", "")).startswith("---"):
            raise CointraApiError(f"Error del servidor Cointra: {result.get('traza')}")
        return result