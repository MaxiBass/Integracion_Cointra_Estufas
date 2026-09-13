"""Config flow para la integración Cointra Electric."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
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


class CointraConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Flujo de configuración de la integración Cointra Electric."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._radiadores: list[dict] = []
        self._name_to_id: dict[str, str] = {}

    async def _validar(self, username: str, password: str) -> tuple[dict[str, str], CointraApiClient | None]:
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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
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
                    instalaciones = await api.get_instalaciones()
                    id_instalacion = instalaciones[0]["datos"]["IdInstalacion"]
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

    async def async_step_ips(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Paso opcional: IP local de cada radiador, para poder hacer ping."""
        self._name_to_id = {r["Nombre"]: r["IdRadiador"] for r in self._radiadores}

        if user_input is not None:
            radiator_ips = {
                self._name_to_id[nombre]: valor.strip()
                for nombre, valor in user_input.items()
                if valor and valor.strip()
            }
            return self.async_create_entry(
                title=self._username,
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_RADIATOR_IPS: radiator_ips,
                },
            )

        schema_dict = {
            vol.Optional(nombre, default=""): str for nombre in self._name_to_id
        }
        return self.async_show_form(
            step_id="ips", data_schema=vol.Schema(schema_dict)
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Se dispara automáticamente cuando falla la autenticación en segundo plano."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        assert self._reauth_entry is not None

        if user_input is not None:
            username = self._reauth_entry.data[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            errors, _api = await self._validar(username, password)
            if not errors:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={**self._reauth_entry.data, CONF_PASSWORD: password},
                )
                await self.hass.config_entries.async_reload(
                    self._reauth_entry.entry_id
                )
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={
                "username": self._reauth_entry.data[CONF_USERNAME]
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return CointraOptionsFlow()


class CointraOptionsFlow(config_entries.OptionsFlow):
    """Permite ajustar el intervalo de actualización y las IPs locales."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        radiadores = list(coordinator.data.values()) if coordinator and coordinator.data else []
        name_to_id = {r["Nombre"]: r["IdRadiador"] for r in radiadores}
        current_ips = self.config_entry.data.get(CONF_RADIATOR_IPS, {})

        if user_input is not None:
            update_interval = user_input.pop(CONF_UPDATE_INTERVAL)

            new_ips = {}
            for nombre, id_radiador in name_to_id.items():
                valor = (user_input.get(nombre) or "").strip()
                if valor:
                    new_ips[id_radiador] = valor

            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_RADIATOR_IPS: new_ips},
            )
            return self.async_create_entry(
                title="", data={CONF_UPDATE_INTERVAL: update_interval}
            )

        current_interval = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        schema_dict: dict[Any, Any] = {
            # Límite inferior a propósito: un intervalo demasiado bajo (o un 0
            # por error de escritura) machacaría la nube de Cointra sin parar,
            # con riesgo de rate-limit/bloqueo de la cuenta.
            vol.Optional(CONF_UPDATE_INTERVAL, default=current_interval): vol.All(
                vol.Coerce(int), vol.Range(min=MIN_UPDATE_INTERVAL)
            ),
        }
        for nombre, id_radiador in name_to_id.items():
            schema_dict[vol.Optional(nombre, default=current_ips.get(id_radiador, ""))] = str

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))