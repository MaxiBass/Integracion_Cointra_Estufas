"""Config flow para la integración Cointra Electric."""
from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CointraApiClient, CointraApiError, CointraAuthError
from .const import (
    CONF_RADIATOR_IPS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

# Como el alta, pero con la contraseña opcional: vacía, se conserva la que había.
STEP_RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


def nombres_radiadores(radiadores: list[dict]) -> dict[str, str]:
    """{nombre que se ve en el formulario: IdRadiador}.

    Los campos del formulario se llaman como el radiador. Si dos radiadores
    se llaman igual, el segundo pisaba al primero y se quedaba sin campo; se
    distinguen añadiendo el id.
    """
    salida: dict[str, str] = {}
    for radiador in radiadores:
        nombre = radiador["Nombre"]
        if nombre in salida:
            nombre = f"{nombre} ({radiador['IdRadiador']})"
        salida[nombre] = radiador["IdRadiador"]
    return salida


def _ip_valida(valor: str) -> bool:
    try:
        ipaddress.ip_address(valor)
    except ValueError:
        return False
    return True


def _leer_ips(
    nombres: dict[str, str], user_input: Mapping[str, Any]
) -> tuple[dict[str, str], bool]:
    """Las IPs escritas en el formulario: ({IdRadiador: ip}, todas_válidas).

    Una IP mal escrita (o un nombre que no resuelve) hace que el ping falle
    siempre y el radiador pase a «no disponible» sin poder controlarlo, así
    que se rechaza aquí en vez de guardarla. En blanco = sin comprobación.
    """
    ips: dict[str, str] = {}
    validas = True
    for nombre, id_radiador in nombres.items():
        valor = (user_input.get(nombre) or "").strip()
        if not valor:
            continue
        if not _ip_valida(valor):
            validas = False
        ips[id_radiador] = valor
    return ips, validas


class CointraConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Flujo de configuración de la integración Cointra Electric."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._radiadores: list[dict] = []

    async def _validar(
        self, username: str, password: str
    ) -> tuple[dict[str, str], CointraApiClient | None]:
        """Intenta login; devuelve (errores, cliente_api_o_None)."""
        errors: dict[str, str] = {}
        session = async_get_clientsession(self.hass)
        api = CointraApiClient(session, username, password)
        try:
            await api.get_instalaciones()
        except CointraAuthError:
            errors["base"] = "invalid_auth"
        except CointraApiError:
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error inesperado validando credenciales de Cointra")
            errors["base"] = "unknown"
        return errors, (api if not errors else None)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            errors, api = await self._validar(username, password)

            if not errors:
                await self.async_set_unique_id(username.lower())
                self._abort_if_unique_id_configured()

                # Sacamos la lista de radiadores para poder pedir su IP local
                # en el siguiente paso (opcional).
                try:
                    id_instalacion = await api.get_id_instalacion()
                    self._radiadores = await api.get_radiadores(id_instalacion)
                except Exception:  # noqa: BLE001
                    _LOGGER.warning(
                        "No se pudieron listar los radiadores durante la configuración; "
                        "se omitirá el paso de IPs locales.",
                        exc_info=True,
                    )
                    self._radiadores = []

                self._username = username
                self._password = password

                if self._radiadores:
                    return await self.async_step_ips()

                return self.async_create_entry(
                    title=username,
                    data={CONF_USERNAME: username, CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_ips(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Paso opcional: IP local de cada radiador, para poder hacer ping."""
        nombres = nombres_radiadores(self._radiadores)
        errors: dict[str, str] = {}
        escrito: Mapping[str, Any] = {}

        if user_input is not None:
            radiator_ips, validas = _leer_ips(nombres, user_input)
            if validas:
                return self.async_create_entry(
                    title=self._username,
                    data={
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                        CONF_RADIATOR_IPS: radiator_ips,
                    },
                )
            errors["base"] = "invalid_ip"
            escrito = user_input

        schema = {
            vol.Optional(nombre, default=escrito.get(nombre, "")): str for nombre in nombres
        }
        return self.async_show_form(
            step_id="ips", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Se dispara automáticamente cuando Cointra rechaza el usuario o la contraseña."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, _api = await self._validar(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cambia la cuenta de Cointra sin borrar la entrada (ni sus entidades)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input.get(CONF_PASSWORD) or entry.data[CONF_PASSWORD]
            errors, _api = await self._validar(username, password)
            if not errors:
                unique_id = username.lower()
                if any(
                    otra.unique_id == unique_id
                    for otra in self._async_current_entries(include_ignore=False)
                    if otra.entry_id != entry.entry_id
                ):
                    return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    title=username,
                    data={**entry.data, CONF_USERNAME: username, CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_RECONFIGURE_SCHEMA, {CONF_USERNAME: entry.data[CONF_USERNAME]}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CointraOptionsFlow:
        return CointraOptionsFlow()


class CointraOptionsFlow(config_entries.OptionsFlow):
    """Permite ajustar el intervalo de actualización y las IPs locales."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        radiadores = list(coordinator.data.values()) if coordinator and coordinator.data else []
        name_to_id = nombres_radiadores(radiadores)
        current_ips = self.config_entry.data.get(CONF_RADIATOR_IPS, {})
        errors: dict[str, str] = {}
        escrito: Mapping[str, Any] = {}

        if user_input is not None:
            update_interval = user_input[CONF_UPDATE_INTERVAL]
            nuevas, validas = _leer_ips(name_to_id, user_input)

            if validas:
                # Con la nube caída al abrir «Configurar» no se conocen los
                # radiadores, y el formulario no tiene campos de IP: lo que
                # no se ve no se toca (si no, guardar solo el intervalo
                # borraba todas las IPs).
                new_ips = {
                    rid: ip for rid, ip in current_ips.items() if rid not in name_to_id.values()
                } | nuevas
                new_options = {CONF_UPDATE_INTERVAL: update_interval}
                # Un único cambio de datos y opciones: con dos, la recarga
                # que provoca el primero quitaba el listener antes del
                # segundo y el intervalo nuevo no llegaba a aplicarse.
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_RADIATOR_IPS: new_ips},
                    options=new_options,
                )
                return self.async_create_entry(title="", data=new_options)

            errors["base"] = "invalid_ip"
            escrito = user_input

        current_interval = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        schema_dict: dict[Any, Any] = {
            # Límite inferior a propósito: un intervalo demasiado bajo (o un 0
            # por error de escritura) machacaría la nube de Cointra sin parar,
            # con riesgo de rate-limit/bloqueo de la cuenta.
            vol.Optional(
                CONF_UPDATE_INTERVAL, default=escrito.get(CONF_UPDATE_INTERVAL, current_interval)
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_UPDATE_INTERVAL)),
        }
        for nombre, id_radiador in name_to_id.items():
            actual = escrito.get(nombre, current_ips.get(id_radiador, ""))
            schema_dict[vol.Optional(nombre, default=actual)] = str

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema_dict), errors=errors
        )
