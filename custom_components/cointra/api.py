"""Cliente API para la nube de Cointra Electric (gateway.grupoferroli.es)."""
import logging
import time

import aiohttp

from .const import API_BASE, CLIENT_ID, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# Un login rechazado por usuario/contraseña es el 400 `invalid_grant` de OAuth
# (o un 401/403). Cualquier otra cosa —un 5xx con el volcado de ASP.NET en
# JSON, un 404, un 429— es el servidor, no la contraseña: confundirlas
# lanzaba un reauth (y paraba las actualizaciones) en cada caída de la nube.
_STATUS_CREDENCIALES = (400, 401, 403)
_ERRORES_CREDENCIALES = ("invalid_grant", "invalid_client", "unauthorized_client", "access_denied")


class CointraApiError(Exception):
    """Error genérico de la API de Cointra."""


class CointraAuthError(CointraApiError):
    """Error de autenticación."""


def formatear_valor(valor) -> str:
    """El valor tal como lo escribe la app (JavaScript): 22.0 → "22", 22.5 → "22.5".

    La nube contesta 200 y descarta en silencio un número con ".0"
    (DECISIONES §3.3), así que los enteros van siempre sin decimales.
    """
    numero = float(valor)
    return str(int(numero)) if numero.is_integer() else str(numero)


def _son_credenciales_rechazadas(status: int, payload) -> bool:
    if isinstance(payload, dict) and payload.get("error") in _ERRORES_CREDENCIALES:
        return True
    return status in _STATUS_CREDENCIALES


async def _parse_json(resp: aiohttp.ClientResponse):
    """Decodifica el cuerpo JSON de una respuesta, envolviendo cuerpos vacíos/no-JSON."""
    try:
        return await resp.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError) as err:
        raise CointraApiError(
            f"Respuesta no válida del servidor de Cointra (HTTP {resp.status}): {err}"
        ) from err


def _sin_conexion(err: Exception) -> CointraApiError:
    return CointraApiError(f"No se pudo conectar con el servidor de Cointra: {err!r}")


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

    async def _post_token(self, data: dict) -> tuple[int, object]:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        try:
            async with self._session.post(f"{API_BASE}/token", data=data, timeout=timeout) as resp:
                return resp.status, await _parse_json(resp)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _sin_conexion(err) from err

    async def _login(self) -> None:
        status, payload = await self._post_token(
            {
                "grant_type": "password",
                "username": self._username,
                "password": self._password,
                "client_id": CLIENT_ID,
            }
        )
        if status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
            if _son_credenciales_rechazadas(status, payload):
                raise CointraAuthError(f"Login rechazado (HTTP {status}): {payload}")
            raise CointraApiError(f"El servidor de Cointra falló en el login (HTTP {status}): {payload}")
        self._store_tokens(payload)

    async def _refresh(self) -> None:
        if not self._refresh_token:
            await self._login()
            return
        status, payload = await self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": CLIENT_ID,
            }
        )
        if status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
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

    async def _send(self, method: str, url: str, headers: dict, **kwargs) -> tuple[int, object]:
        """Una petición. En un 401 no se lee el cuerpo: puede venir vacío o no ser JSON."""
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with self._session.request(
            method, url, headers=headers, timeout=timeout, **kwargs
        ) as resp:
            if resp.status == 401:
                return 401, None
            return resp.status, await _parse_json(resp)

    async def _authed_request(self, method: str, url: str, **kwargs):
        await self._ensure_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        headers["Accept"] = "application/json"

        try:
            status, payload = await self._send(method, url, headers, **kwargs)
            if status == 401:
                # Token rechazado a mitad de camino: reintenta una vez tras login
                await self._login()
                headers["Authorization"] = f"Bearer {self._access_token}"
                status, payload = await self._send(method, url, headers, **kwargs)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _sin_conexion(err) from err

        if status >= 400:
            raise CointraApiError(f"Cointra devolvió HTTP {status}: {payload}")
        return payload

    async def get_instalaciones(self) -> list[dict]:
        result = await self._authed_request("GET", f"{API_BASE}/api/Instalaciones")
        if not isinstance(result, list) or not result:
            raise CointraApiError(f"Respuesta inesperada al listar instalaciones: {result}")
        return result

    async def get_id_instalacion(self) -> int:
        """Id de la primera instalación de la cuenta (las demás no se usan)."""
        instalaciones = await self.get_instalaciones()
        try:
            return int(instalaciones[0]["datos"]["IdInstalacion"])
        except (KeyError, IndexError, TypeError, ValueError) as err:
            raise CointraApiError(
                f"La instalación no trae `datos.IdInstalacion`: {str(instalaciones[0])[:200]}"
            ) from err

    async def get_radiadores(self, id_instalacion: int, id_zona: int = 0) -> list[dict]:
        url = f"{API_BASE}/api/Radiador?IdInstalacion={id_instalacion}&IdZona={id_zona}"
        result = await self._authed_request("GET", url)
        if isinstance(result, dict) and "Message" in result:
            raise CointraApiError(result["Message"])
        if not isinstance(result, list) or not all(
            isinstance(r, dict) and "IdRadiador" in r for r in result
        ):
            raise CointraApiError(f"Respuesta inesperada al listar radiadores: {str(result)[:200]}")
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
