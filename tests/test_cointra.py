"""Pruebas de la integración Cointra Electric. Se ejecutan sin pytest:

    /tmp/hav/bin/python tests/test_cointra.py

Arrancan un Home Assistant real (el del venv) en un directorio temporal, con
la integración enlazada en `custom_components/`. La nube de Cointra es un
servidor aiohttp de verdad en 127.0.0.1 que imita lo que se sabe de la API
real (docs/DECISIONES.md §2 y §3): el cliente de `api.py` —login, refresco
del token, reintento tras un 401, cuerpo del PUT— es el real; solo cambia la
URL base. El ping también es un doble: nunca se hace ICMP de verdad.

Todos los correos, nombres, ids e IPs son inventados: este repositorio es
público.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
BASE = RAIZ / "custom_components" / "cointra"
DOMINIO = "cointra"

CORREO = "ana@example.com"
CLAVE = "clave-de-prueba"
IP_1 = "10.0.0.31"
IP_2 = "10.0.0.32"

fallos: list[str] = []


def comprobar(condicion: bool, etiqueta: str) -> None:
    if condicion:
        print(f"  OK    {etiqueta}")
    else:
        print(f"  FALLO {etiqueta}")
        fallos.append(etiqueta)


def importar(nombre: str):
    """El módulo de la integración, o None si todavía no existe."""
    try:
        return importlib.import_module(f"custom_components.cointra.{nombre}")
    except ImportError:
        return None


# ── Ficheros estáticos ───────────────────────────────────────────────


def test_manifest_y_traducciones() -> None:
    print("\nManifest y traducciones")

    manifest = json.loads((BASE / "manifest.json").read_text("utf-8"))
    comprobar(
        "github.com/MaxiBass/" in manifest.get("documentation", "")
        and "github.com/MaxiBass/" in manifest.get("issue_tracker", ""),
        "manifest apunta al repo (documentation, issue_tracker)",
    )
    comprobar(manifest.get("codeowners") == ["@MaxiBass"], f"codeowners {manifest.get('codeowners')}")
    comprobar(manifest.get("integration_type") == "hub", f"integration_type {manifest.get('integration_type')!r}")
    comprobar(
        "aiohttp" not in manifest.get("requirements", []),
        "manifest no pide aiohttp (ya viene con Home Assistant)",
    )
    comprobar(
        "updatetest" not in (BASE / "__init__.py").read_text("utf-8"),
        "sin la marca de prueba de HACS en __init__.py",
    )

    es = json.loads((BASE / "translations" / "es.json").read_text("utf-8"))
    en = json.loads((BASE / "translations" / "en.json").read_text("utf-8"))
    cadenas = json.loads((BASE / "strings.json").read_text("utf-8"))

    def claves(d: dict, prefijo: str = "") -> set[str]:
        salida = set()
        for k, v in d.items():
            salida |= claves(v, f"{prefijo}{k}.") if isinstance(v, dict) else {f"{prefijo}{k}"}
        return salida

    comprobar(claves(es) == claves(en), "es.json y en.json tienen las mismas claves")
    comprobar(es == cadenas, "strings.json coincide con es.json")
    comprobar(es != en, "en.json no es una copia de es.json")

    from PIL import Image

    tamanos = {}
    for nombre in ("icon.png", "icon@2x.png"):
        ruta = BASE / "brand" / nombre
        tamanos[nombre] = Image.open(ruta).size if ruta.exists() else None
    comprobar(tamanos == {"icon.png": (256, 256), "icon@2x.png": (512, 512)},
              f"icono propio en brand/ con los tamaños de HA {tamanos}")
    comprobar(not (RAIZ / "icon.png").exists() and not (RAIZ / "logo.png").exists(),
              "sin iconos sueltos en la raíz del repo (HACS no los lee)")


# ── Nube de Cointra falsa ────────────────────────────────────────────


def _radiador(rid: str, nombre: str) -> dict:
    return {
        "IdRadiador": rid, "Nombre": nombre, "Tipo": "Radiador WIFI", "Software": "1.2.3",
        "TempActual": 19.5, "Encendido": True, "Calentando": True, "IdModoActual": "C",
        "TempComfort": 21.0, "TempEco": 18.0, "TempAntiFrost": 7.0, "TempForzado": 22.0,
        "Potencia": 1000, "LimitePotencia": 100, "Brillo": 60, "DuracionBrillo": 10,
        "TecladoBloqueado": False,
        "VentanasAbiertas": True, "VentanasAbiertasStatus": False,
        "ArranqueAdaptativo": False, "ArranqueAdaptativoStatus": False,
        "Error": 0, "ErrorList": "",
    }


class NubeFalsa:
    """Lo que el cliente ve de gateway.grupoferroli.es, con fallos a demanda."""

    def __init__(self) -> None:
        self.cuentas = {CORREO: CLAVE}
        self.modo_token = "ok"  # ok | json_503 | html_502 | colgado
        self.api_caida = False  # /api/* responde 503 con cuerpo JSON
        self.instalaciones_raras = False
        self.radiadores_raros = False
        self.token: str | None = None
        self.refresh: str | None = None
        self.logins = 0
        self.refrescos = 0
        self.puts: list[dict] = []
        self.radiadores = {
            "RAD000001": _radiador("RAD000001", "Salón"),
            "RAD000002": _radiador("RAD000002", "Dormitorio"),
        }
        self._n = 0
        self.base = ""

    async def arrancar(self) -> None:
        from aiohttp import web

        app = web.Application()
        app.router.add_post("/APIGW/token", self._token)
        app.router.add_get("/APIGW/api/Instalaciones", self._instalaciones)
        app.router.add_get("/APIGW/api/Radiador", self._leer)
        app.router.add_put("/APIGW/api/Radiador", self._escribir)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sitio = web.TCPSite(self._runner, "127.0.0.1", 0)
        await sitio.start()
        puerto = sitio._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{puerto}/APIGW"

    async def parar(self) -> None:
        await self._runner.cleanup()

    def invalidar_token(self) -> None:
        """El servidor deja de aceptar el token que el cliente cree vigente."""
        self.token = "caducado-en-el-servidor"

    # Respuestas ------------------------------------------------------

    async def _token(self, peticion):
        from aiohttp import web

        if self.modo_token == "json_503":
            return web.json_response({"Message": "Se ha producido un error."}, status=503)
        if self.modo_token == "html_502":
            return web.Response(text="<html>Bad gateway</html>", status=502, content_type="text/html")
        if self.modo_token == "colgado":
            # El cliente se cansa antes; después ya no hay a quién contestar.
            await asyncio.sleep(30)
            return web.Response(status=504)
        datos = await peticion.post()
        malo = web.json_response(
            {"error": "invalid_grant", "error_description": "usuario o contraseña incorrectos"}, status=400
        )
        if datos.get("grant_type") == "password":
            # El correo no distingue mayúsculas, como en cualquier cuenta de correo.
            if self.cuentas.get(datos.get("username", "").lower()) != datos.get("password"):
                return malo
            self.logins += 1
        else:
            if datos.get("refresh_token") != self.refresh:
                return malo
            self.refrescos += 1
        self._n += 1
        self.token, self.refresh = f"tok{self._n}", f"ref{self._n}"
        return web.json_response(
            {"access_token": self.token, "refresh_token": self.refresh, "expires_in": 1199,
             "company": 7, "idUser": "u1"}
        )

    def _no_autorizado(self, peticion):
        from aiohttp import web

        if self.api_caida:
            return web.json_response({"Message": "Se ha producido un error."}, status=503)
        if peticion.headers.get("Authorization") != f"Bearer {self.token}":
            return web.json_response({"Message": "Authorization has been denied"}, status=401)
        return None

    async def _instalaciones(self, peticion):
        from aiohttp import web

        if (error := self._no_autorizado(peticion)) is not None:
            return error
        if self.instalaciones_raras:
            return web.json_response([{"otra_cosa": 1}])
        return web.json_response([{"datos": {"IdInstalacion": 5001}}])

    async def _leer(self, peticion):
        from aiohttp import web

        if (error := self._no_autorizado(peticion)) is not None:
            return error
        if self.radiadores_raros:
            return web.json_response({"inesperado": True})
        return web.json_response(list(self.radiadores.values()))

    async def _escribir(self, peticion):
        from aiohttp import web

        if (error := self._no_autorizado(peticion)) is not None:
            return error
        cuerpo = await peticion.json()
        self.puts.append(cuerpo)
        # DECISIONES §3.1: con un idInstalacion real el servidor ignora
        # `Heaters` y aplica el cambio a todos los radiadores.
        objetivos = (
            list(self.radiadores) if cuerpo.get("idInstalacion") != 0 else list(cuerpo.get("Heaters", []))
        )
        for rid in objetivos:
            for cambio in cuerpo["Cambios"]:
                self._aplicar(self.radiadores[rid], cambio["Campo"], str(cambio["Valor"]))
        return web.json_response({"traza": f"COUNT: {len(objetivos)}"})

    @staticmethod
    def _aplicar(radiador: dict, campo: str, valor: str) -> None:
        if valor in ("true", "false"):
            radiador[campo] = valor == "true"
        elif re.fullmatch(r"\d+\.0", valor):
            # DECISIONES §3.3: con "10.0" el servidor contesta 200 y no
            # aplica nada; la app manda siempre "10" (así lo escribe JS).
            return
        elif re.fullmatch(r"\d+", valor):
            radiador[campo] = int(valor)
        elif re.fullmatch(r"\d+\.\d+", valor):
            radiador[campo] = float(valor)
        else:
            radiador[campo] = valor


class PingFalso:
    """Sustituye icmplib.async_ping: contesta quien esté en `vivos`."""

    def __init__(self) -> None:
        self.vivos = {IP_1, IP_2}
        self.llamadas: list[tuple[str, bool]] = []

    async def __call__(self, ip, count=1, timeout=2, privileged=True):
        self.llamadas.append((ip, privileged))
        return types.SimpleNamespace(is_alive=ip in self.vivos)


# ── Cliente de la API, sin Home Assistant ────────────────────────────


async def _api_sin_ha() -> None:
    import aiohttp

    from custom_components.cointra import api

    nube = NubeFalsa()
    await nube.arrancar()
    api.API_BASE = nube.base
    sesion = aiohttp.ClientSession()
    try:
        print("\n  · login y errores")
        cliente = api.CointraApiClient(sesion, CORREO, "mala")
        try:
            await cliente.get_instalaciones()
            tipo = None
        except Exception as err:  # noqa: BLE001
            tipo = type(err)
        comprobar(tipo is api.CointraAuthError, f"contraseña mala (HTTP 400 invalid_grant) → CointraAuthError ({tipo})")

        nube.modo_token = "json_503"
        cliente = api.CointraApiClient(sesion, CORREO, CLAVE)
        try:
            await cliente.get_instalaciones()
            tipo = None
        except Exception as err:  # noqa: BLE001
            tipo = type(err)
        comprobar(tipo is api.CointraApiError,
                  f"servidor caído en el login (HTTP 503 con JSON) NO es un error de contraseña ({tipo})")

        nube.modo_token = "html_502"
        try:
            await cliente.get_instalaciones()
            tipo = None
        except Exception as err:  # noqa: BLE001
            tipo = type(err)
        comprobar(tipo is api.CointraApiError, f"servidor caído con una página HTML (502) → CointraApiError ({tipo})")

        print("\n  · token: refresco y reintento tras un 401")
        nube.modo_token = "ok"
        cliente = api.CointraApiClient(sesion, CORREO, CLAVE)
        await cliente.get_instalaciones()
        comprobar(nube.logins == 1 and cliente.id_cia == 7, "login con la contraseña correcta")
        cliente._token_expires_at = 0
        await cliente.get_instalaciones()
        comprobar(nube.refrescos == 1 and nube.logins == 1, "token caducado → se refresca sin volver a hacer login")
        nube.invalidar_token()
        await cliente.get_instalaciones()
        comprobar(nube.logins == 2, "el servidor rechaza el token (401) → un login nuevo y se repite la petición")

        print("\n  · escribir en un radiador")
        await cliente.set_radiador("RAD000002", [{"Campo": "Brillo", "Valor": "30"}])
        cuerpo = nube.puts[-1]
        comprobar(cuerpo["idInstalacion"] == 0 and cuerpo["Heaters"] == ["RAD000002"] and cuerpo["idCia"] == 7,
                  f"el PUT va con idInstalacion 0 y solo ese radiador ({cuerpo})")
        comprobar(nube.radiadores["RAD000002"]["Brillo"] == 30 and nube.radiadores["RAD000001"]["Brillo"] == 60,
                  "y solo cambia ese radiador")

        print("\n  · respuestas con una forma inesperada")
        nube.instalaciones_raras = True
        try:
            await cliente.get_id_instalacion()
            tipo = None
        except AttributeError:
            tipo = AttributeError
        except Exception as err:  # noqa: BLE001
            tipo = type(err)
        comprobar(tipo is api.CointraApiError, f"instalaciones sin `datos.IdInstalacion` → CointraApiError ({tipo})")
        nube.instalaciones_raras = False
        if hasattr(cliente, "get_id_instalacion"):
            comprobar(await cliente.get_id_instalacion() == 5001, "id de la instalación")
        nube.radiadores_raros = True
        try:
            await cliente.get_radiadores(5001)
            tipo = None
        except Exception as err:  # noqa: BLE001
            tipo = type(err)
        comprobar(tipo is api.CointraApiError, f"radiadores que no vienen como lista → CointraApiError ({tipo})")
        nube.radiadores_raros = False

        print("\n  · servidor que no contesta")
        nube.modo_token = "colgado"
        api.REQUEST_TIMEOUT = 1  # solo existe tras el arreglo; antes, el límite era el de aiohttp (5 min)
        cliente = api.CointraApiClient(sesion, CORREO, CLAVE)
        inicio = time.monotonic()
        try:
            await asyncio.wait_for(cliente.get_instalaciones(), timeout=8)
            tipo = None
        except TimeoutError:
            tipo = "sin límite propio (esperaba más de 8 s)"
        except Exception as err:  # noqa: BLE001
            tipo = type(err)
        comprobar(tipo is api.CointraApiError and time.monotonic() - inicio < 5,
                  f"un servidor colgado da CointraApiError en pocos segundos ({tipo})")
        nube.modo_token = "ok"
    finally:
        await sesion.close()
        await nube.parar()


# ── Ping ─────────────────────────────────────────────────────────────


async def _ping_sin_ha() -> None:
    import icmplib

    modulo = importar("coordinator")
    print("\n  · ping a un radiador")

    llamadas: list[bool] = []

    async def sin_permiso_raw(ip, count=1, timeout=2, privileged=True):
        llamadas.append(privileged)
        if privileged:
            raise icmplib.SocketPermissionError(privileged=True)
        return types.SimpleNamespace(is_alive=True)

    original = modulo.async_ping
    modulo.async_ping = sin_permiso_raw
    try:
        vivo = await modulo._ping_host(IP_1)
    finally:
        modulo.async_ping = original
    comprobar(vivo and llamadas == [True, False],
              f"sin permiso para sockets raw, prueba sin privilegios y el radiador sigue vivo ({llamadas}, {vivo})")
    comprobar(await modulo._ping_host("") is True, "sin IP configurada no se pinga y se da por disponible")


# ── Home Assistant real ──────────────────────────────────────────────


class _Registros(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.mensajes: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.mensajes.append(record.getMessage())

    def con(self, *trozos: str) -> list[str]:
        return [m for m in self.mensajes if all(t in m for t in trozos)]


async def _arrancar_hass(directorio: Path):
    from homeassistant import bootstrap, config_entries, core, loader
    from homeassistant.core_config import async_process_ha_core_config
    from homeassistant.helpers import aiohttp_client
    from homeassistant.setup import async_setup_component

    # La sesión HTTP de HA resuelve nombres con zeroconf (mDNS), que en un HA
    # de pruebas sin los componentes de red no existe. Aquí solo se habla con
    # 127.0.0.1, así que basta un resolutor normal.
    import aiohttp

    aiohttp_client._async_make_resolver = lambda hass: aiohttp.ThreadedResolver()

    hass = core.HomeAssistant(str(directorio))
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await loader.async_get_custom_components(hass)
    assert await bootstrap.async_load_base_functionality(hass)
    for dominio in bootstrap.CORE_INTEGRATIONS:
        assert await async_setup_component(hass, dominio, {}), dominio
    await async_process_ha_core_config(hass, {"time_zone": "Europe/Madrid"})
    hass.set_state(core.CoreState.running)
    return hass


def _entidad(hass, plataforma: str, unique_id: str) -> str:
    from homeassistant.helpers import entity_registry as er

    entity_id = er.async_get(hass).async_get_entity_id(plataforma, DOMINIO, unique_id)
    assert entity_id, f"no existe {plataforma} {unique_id}"
    return entity_id


def _estado(hass, plataforma: str, unique_id: str):
    return hass.states.get(_entidad(hass, plataforma, unique_id))


async def _llamar(hass, dominio: str, servicio: str, datos: dict) -> bool:
    """True si el servicio se ejecuta; False si HA lo rechaza con un error."""
    from homeassistant.exceptions import HomeAssistantError

    try:
        await hass.services.async_call(dominio, servicio, datos, blocking=True)
    except HomeAssistantError:
        return False
    return True


def _unique_ids_esperados(entry_id: str) -> set[tuple[str, str]]:
    """Los unique_id que ya existen en casa (docs/DECISIONES.md §6.3)."""
    esperados = {("binary_sensor", f"cointra_{entry_id}_servidor")}
    for rid in ("RAD000001", "RAD000002"):
        esperados |= {
            ("climate", f"cointra_{rid}_climate"),
            ("switch", f"cointra_{rid}_TecladoBloqueado"),
            ("switch", f"cointra_{rid}_VentanasAbiertas"),
            ("switch", f"cointra_{rid}_ArranqueAdaptativo"),
            ("number", f"cointra_{rid}_Brillo"),
            ("number", f"cointra_{rid}_DuracionBrillo"),
            ("number", f"cointra_{rid}_LimitePotencia"),
            ("sensor", f"cointra_{rid}_potencia_estimada"),
            ("button", f"cointra_{rid}_ping_ahora"),
            ("binary_sensor", f"cointra_{rid}_calentando"),
            ("binary_sensor", f"cointra_{rid}_error"),
            ("binary_sensor", f"cointra_{rid}_conexion_local"),
        }
    return esperados


async def _paso(nombre: str, coro) -> None:
    """Un fallo inesperado en un apartado no impide ver los demás."""
    try:
        await coro
    except Exception as err:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        comprobar(False, f"{nombre}: excepción inesperada {err!r}")


async def _recorrido(directorio: Path) -> None:
    registros = _Registros()
    logging.getLogger().addHandler(registros)
    hass = await _arrancar_hass(directorio)
    nube = NubeFalsa()
    await nube.arrancar()
    ping = PingFalso()

    from custom_components.cointra import api

    api.API_BASE = nube.base
    importar("coordinator").async_ping = ping
    ctx: dict = {"hass": hass, "nube": nube, "ping": ping, "registros": registros, "directorio": directorio}

    try:
        await _paso("alta", _alta(ctx))
        if "entrada" in ctx:
            for nombre, funcion in (
                ("entidades", _entidades),
                ("lectura", _lectura),
                ("escritura", _escritura),
                ("ping", _ping_en_ha),
                ("nube", _fallos_de_nube),
                ("formas raras", _formas_raras),
                ("opciones", _opciones),
                ("diagnóstico", _diagnostico),
                ("reconfigurar", _reconfigurar),
                ("radiador dado de baja", _radiador_de_baja),
                ("recarga", _recarga),
                ("borrar", _borrar),
            ):
                await _paso(nombre, funcion(ctx))
    finally:
        await hass.async_stop(force=True)
        await nube.parar()
        logging.getLogger().removeHandler(registros)


async def _alta(ctx) -> None:
    """El formulario de alta, con sus errores. Crea la entrada que usa el resto."""
    from homeassistant.config_entries import ConfigEntryState

    hass, nube = ctx["hass"], ctx["nube"]
    print("\n  · alta por el formulario")

    flujo = await hass.config_entries.flow.async_init(DOMINIO, context={"source": "user"})
    comprobar(flujo.get("step_id") == "user", "el formulario pide correo y contraseña")

    mal = await hass.config_entries.flow.async_configure(
        flujo["flow_id"], {"username": CORREO, "password": "mala"}
    )
    comprobar(mal.get("errors") == {"base": "invalid_auth"}, f"contraseña mala → invalid_auth ({mal.get('errors')})")

    nube.modo_token = "json_503"
    caido = await hass.config_entries.flow.async_configure(
        flujo["flow_id"], {"username": CORREO, "password": CLAVE}
    )
    comprobar(caido.get("errors") == {"base": "cannot_connect"},
              f"servidor caído → cannot_connect, no «contraseña incorrecta» ({caido.get('errors')})")
    nube.modo_token = "ok"

    ips = await hass.config_entries.flow.async_configure(
        flujo["flow_id"], {"username": CORREO, "password": CLAVE}
    )
    campos = [str(k) for k in ips["data_schema"].schema] if ips.get("data_schema") else []
    comprobar(ips.get("step_id") == "ips" and campos == ["Salón", "Dormitorio"],
              f"con la contraseña buena pide la IP de cada radiador por su nombre {campos}")

    rara = await hass.config_entries.flow.async_configure(
        flujo["flow_id"], {"Salón": "abc", "Dormitorio": ""}
    )
    comprobar(rara.get("step_id") == "ips" and rara.get("errors") == {"base": "invalid_ip"},
              f"una IP mal escrita se rechaza en el formulario ({rara.get('errors')})")
    if rara.get("type") == "create_entry":
        # La integración la ha aceptado y ha creado la entrada: se borra y se
        # repite el alta con datos buenos para poder seguir con el resto.
        await hass.config_entries.async_remove(rara["result"].entry_id)
        await hass.async_block_till_done()
        flujo = await hass.config_entries.flow.async_init(DOMINIO, context={"source": "user"})
        await hass.config_entries.flow.async_configure(flujo["flow_id"], {"username": CORREO, "password": CLAVE})

    fin = await hass.config_entries.flow.async_configure(
        flujo["flow_id"], {"Salón": f" {IP_1} ", "Dormitorio": IP_2}
    )
    await hass.async_block_till_done()
    entrada = fin.get("result")
    comprobar(fin.get("type") == "create_entry" and entrada is not None, f"crea la entrada ({fin.get('type')})")
    if entrada is None:
        return
    comprobar(entrada.state is ConfigEntryState.LOADED, f"la entrada carga ({entrada.state})")
    comprobar(entrada.title == CORREO and entrada.unique_id == CORREO and entrada.version == 1,
              "título y unique_id son el correo; versión 1 (no hace falta migrar las entradas de casa)")
    comprobar(set(entrada.data) == {"username", "password", "radiator_ips"}
              and entrada.data["radiator_ips"] == {"RAD000001": IP_1, "RAD000002": IP_2} and entrada.options == {},
              "los datos tienen la misma forma que los de la entrada de casa (la IP, sin espacios)")
    ctx["entrada"] = entrada

    otra = await hass.config_entries.flow.async_init(DOMINIO, context={"source": "user"})
    repetida = await hass.config_entries.flow.async_configure(
        otra["flow_id"], {"username": CORREO.upper(), "password": CLAVE}
    )
    comprobar(repetida.get("type") == "abort" and repetida.get("reason") == "already_configured",
              "dar de alta la misma cuenta otra vez (aunque cambien las mayúsculas) se rechaza")


async def _entidades(ctx) -> None:
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    hass, entrada, registros = ctx["hass"], ctx["entrada"], ctx["registros"]
    print("\n  · dispositivos, entidades y avisos de HA")

    existentes = {(e.domain, e.unique_id) for e in er.async_entries_for_config_entry(er.async_get(hass), entrada.entry_id)}
    esperados = _unique_ids_esperados(entrada.entry_id)
    comprobar(existentes == esperados,
              f"las 25 entidades de siempre, con los mismos unique_id (sobran {existentes - esperados}, faltan {esperados - existentes})")

    registro = dr.async_get(hass)
    dispositivos = dr.async_entries_for_config_entry(registro, entrada.entry_id)
    cuenta = registro.async_get_device_by_identifier((DOMINIO, entrada.entry_id), entrada.entry_id)
    radiadores = [d for d in dispositivos if d.model == "Radiador WIFI"]
    comprobar(cuenta is not None and len(dispositivos) == 3 and len(radiadores) == 2,
              f"dispositivo de la cuenta + 2 radiadores → {len(dispositivos)} dispositivos")
    comprobar(cuenta is not None and bool(radiadores) and all(d.via_device_id == cuenta.id for d in radiadores),
              "cada radiador cuelga del dispositivo de la cuenta (via_device_id)")
    comprobar({d.name for d in radiadores} == {"Salón", "Dormitorio"} and {d.sw_version for d in radiadores} == {"1.2.3"},
              "nombre y versión del radiador salen de la nube")

    propios = [m for m in registros.mensajes if "cointra" in m.lower() and "has not been tested" not in m]
    comprobar(not propios, f"ningún aviso de HA sobre Cointra ({propios})")


async def _lectura(ctx) -> None:
    hass, entrada = ctx["hass"], ctx["entrada"]
    print("\n  · lectura del estado")
    clima = _estado(hass, "climate", "cointra_RAD000001_climate")
    a = clima.attributes
    comprobar(clima.state == "heat" and a.get("current_temperature") == 19.5 and a.get("temperature") == 21.0
              and a.get("preset_mode") == "comfort" and a.get("hvac_action") == "heating",
              f"termostato: {clima.state} {a.get('current_temperature')} → {a.get('temperature')} {a.get('preset_mode')}")
    comprobar(_estado(hass, "binary_sensor", "cointra_RAD000001_calentando").state == "on"
              and _estado(hass, "binary_sensor", "cointra_RAD000001_error").state == "off"
              and _estado(hass, "binary_sensor", f"cointra_{entrada.entry_id}_servidor").state == "on"
              and _estado(hass, "binary_sensor", "cointra_RAD000001_conexion_local").state == "on",
              "calentando, error, servidor y conexión local")
    comprobar(_estado(hass, "sensor", "cointra_RAD000001_potencia_estimada").state == "1000.0",
              "potencia estimada = nominal × límite")
    ventana = _estado(hass, "switch", "cointra_RAD000001_VentanasAbiertas")
    comprobar(ventana.state == "on" and ventana.attributes.get("detectado_ahora_mismo") is False,
              "el interruptor de ventana lee el campo de configuración, no el *Status")
    comprobar(float(_estado(hass, "number", "cointra_RAD000001_Brillo").state) == 60, "brillo")


async def _escritura(ctx) -> None:
    hass, nube, entrada = ctx["hass"], ctx["nube"], ctx["entrada"]
    print("\n  · escribir desde HA")
    coord = hass.data[DOMINIO][entrada.entry_id]
    r1, r2 = nube.radiadores["RAD000001"], nube.radiadores["RAD000002"]
    clima = _entidad(hass, "climate", "cointra_RAD000001_climate")

    await hass.services.async_call("climate", "set_temperature", {"entity_id": clima, "temperature": 22.0}, blocking=True)
    comprobar(r1["TempComfort"] == 22 and r2["TempComfort"] == 21,
              f"temperatura 22,0 → la nube la aplica y solo a ese radiador (TempComfort={r1['TempComfort']}, el otro {r2['TempComfort']})")
    comprobar("." not in nube.puts[-1]["Cambios"][0]["Valor"],
              f"un entero se manda como entero, como la app ({nube.puts[-1]['Cambios'][0]['Valor']!r})")
    await hass.services.async_call("climate", "set_temperature", {"entity_id": clima, "temperature": 22.5}, blocking=True)
    comprobar(r1["TempComfort"] == 22.5, "y los medios grados, tal cual")

    await hass.services.async_call("climate", "set_preset_mode", {"entity_id": clima, "preset_mode": "eco"}, blocking=True)
    comprobar(r1["IdModoActual"] == "E" and r1["Encendido"] is True, "preset eco → modo E y encendido")
    await coord.async_refresh()
    await hass.async_block_till_done()
    await hass.services.async_call("climate", "set_temperature", {"entity_id": clima, "temperature": 17.5}, blocking=True)
    comprobar(r1["TempEco"] == 17.5 and r1["TempComfort"] == 22.5, "en modo eco la temperatura va al campo TempEco")

    comprobar(await _llamar(hass, "climate", "turn_off", {"entity_id": clima}) and r1["Encendido"] is False,
              "climate.turn_off apaga el radiador")
    comprobar(await _llamar(hass, "climate", "turn_on", {"entity_id": clima}) and r1["Encendido"] is True,
              "climate.turn_on lo enciende")
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": clima, "hvac_mode": "off"}, blocking=True)
    comprobar(r1["Encendido"] is False, "set_hvac_mode off también")
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": clima, "hvac_mode": "heat"}, blocking=True)

    await hass.services.async_call("switch", "turn_off",
                                   {"entity_id": _entidad(hass, "switch", "cointra_RAD000001_VentanasAbiertas")}, blocking=True)
    comprobar(r1["VentanasAbiertas"] is False and r1["VentanasAbiertasStatus"] is False, "el interruptor de ventana escribe el campo base")
    await hass.services.async_call("switch", "turn_on",
                                   {"entity_id": _entidad(hass, "switch", "cointra_RAD000001_TecladoBloqueado")}, blocking=True)
    comprobar(r1["TecladoBloqueado"] is True, "bloqueo de teclado")

    await hass.services.async_call("number", "set_value",
                                   {"entity_id": _entidad(hass, "number", "cointra_RAD000001_Brillo"), "value": 30}, blocking=True)
    comprobar(r1["Brillo"] == 30 and r2["Brillo"] == 60, "brillo 30 → «30», no «30.0», y solo a ese radiador")
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": _entidad(hass, "number", "cointra_RAD000001_LimitePotencia"), "value": 50}, blocking=True)
    await coord.async_refresh()
    await hass.async_block_till_done()
    comprobar(_estado(hass, "sensor", "cointra_RAD000001_potencia_estimada").state == "500.0",
              "con el límite al 50 %, la potencia estimada baja a la mitad")
    r1["Calentando"] = False
    await coord.async_refresh()
    await hass.async_block_till_done()
    comprobar(_estado(hass, "sensor", "cointra_RAD000001_potencia_estimada").state == "0.0", "sin calentar, 0 W")
    r1["Calentando"] = True


async def _ping_en_ha(ctx) -> None:
    hass, ping, entrada = ctx["hass"], ctx["ping"], ctx["entrada"]
    print("\n  · disponibilidad por ping")
    coord = hass.data[DOMINIO][entrada.entry_id]
    clima = lambda: _estado(hass, "climate", "cointra_RAD000002_climate").state  # noqa: E731
    local = lambda: _estado(hass, "binary_sensor", "cointra_RAD000002_conexion_local")  # noqa: E731

    ping.vivos = {IP_1}
    await coord.async_ping_all()
    await hass.async_block_till_done()
    comprobar(clima() != "unavailable" and local().state == "on", "un fallo suelto no marca el radiador como caído")
    await coord.async_ping_all()
    await hass.async_block_till_done()
    comprobar(clima() == "unavailable" and local().state == "off",
              f"dos fallos seguidos → no disponible ({clima()}), y «Conexión local» lo dice")
    comprobar(_estado(hass, "climate", "cointra_RAD000001_climate").state != "unavailable",
              "el otro radiador no se ve afectado")
    comprobar(local().attributes.get("ip") == IP_2, "«Conexión local» muestra la IP")

    ping.vivos = {IP_1, IP_2}
    await hass.services.async_call("button", "press",
                                   {"entity_id": _entidad(hass, "button", "cointra_RAD000002_ping_ahora")}, blocking=True)
    await hass.async_block_till_done()
    comprobar(clima() != "unavailable" and local().state == "on" and local().attributes.get("ultimo_ping_exitoso"),
              "un solo ping bueno basta para recuperarlo; el botón fuerza la comprobación")


async def _fallos_de_nube(ctx) -> None:
    from homeassistant.config_entries import ConfigEntryState

    hass, nube, entrada = ctx["hass"], ctx["nube"], ctx["entrada"]
    print("\n  · la nube falla")
    coord = hass.data[DOMINIO][entrada.entry_id]
    servidor = lambda: _estado(hass, "binary_sensor", f"cointra_{entrada.entry_id}_servidor")  # noqa: E731
    clima = lambda: _estado(hass, "climate", "cointra_RAD000001_climate")  # noqa: E731
    reauth = lambda: [f for f in hass.config_entries.flow.async_progress_by_handler(DOMINIO)  # noqa: E731
                      if f["context"]["source"] == "reauth"]

    nube.api_caida = True
    await coord.async_refresh()
    await hass.async_block_till_done()
    comprobar(clima().state == "unavailable" and servidor().state == "off",
              f"nube caída → los radiadores no están disponibles ({clima().state}) y «Servidor Cointra» se apaga")
    comprobar(servidor().state != "unavailable", "«Servidor Cointra» sigue disponible para poder avisar")
    nube.api_caida = False
    await coord.async_refresh()
    await hass.async_block_till_done()
    comprobar(clima().state != "unavailable" and servidor().state == "on", "cuando vuelve, todo se recupera solo")

    # El servidor da un 503 con cuerpo JSON justo cuando hay que renovar el token.
    nube.modo_token = "json_503"
    coord.api._token_expires_at = 0
    await coord.async_refresh()
    await hass.async_block_till_done()
    comprobar(not reauth(), f"un 503 del servidor no pide la contraseña ({len(reauth())} flujos de reauth)")
    comprobar(entrada.state is ConfigEntryState.LOADED and not coord.last_update_success,
              "solo falla esa actualización; la integración sigue cargada")
    nube.modo_token = "ok"
    await coord.async_refresh()
    await hass.async_block_till_done()
    comprobar(coord.last_update_success and clima().state != "unavailable", "y a la siguiente se recupera sola")
    for flujo in reauth():  # por si el fallo anterior dejó uno abierto
        hass.config_entries.flow.async_abort(flujo["flow_id"])

    # Contraseña cambiada de verdad.
    print("\n  · contraseña cambiada en la cuenta")
    nube.cuentas[CORREO] = "nueva"
    coord.api._token_expires_at = 0
    nube.refresh = "otro"  # el refresco ya no vale
    await coord.async_refresh()
    await hass.async_block_till_done()
    pendientes = reauth()
    comprobar(len(pendientes) == 1 and pendientes[0]["step_id"] == "reauth_confirm",
              "HA pide la contraseña nueva")
    if pendientes:
        mal = await hass.config_entries.flow.async_configure(pendientes[0]["flow_id"], {"password": "otra-mala"})
        comprobar(mal.get("errors") == {"base": "invalid_auth"}, "una contraseña mala se rechaza en el formulario")
        fin = await hass.config_entries.flow.async_configure(pendientes[0]["flow_id"], {"password": "nueva"})
        await hass.async_block_till_done()
        comprobar(fin.get("reason") == "reauth_successful" and entrada.data["password"] == "nueva"
                  and entrada.data["username"] == CORREO and entrada.state is ConfigEntryState.LOADED,
                  "al darla, se guarda y la integración vuelve a cargar")
        comprobar(set(entrada.data) == {"username", "password", "radiator_ips"}, "sin perder las IPs de los radiadores")
    nube.cuentas[CORREO] = CLAVE
    for flujo in reauth():
        hass.config_entries.flow.async_abort(flujo["flow_id"])
    # Deja la entrada con la contraseña buena para lo que sigue.
    hass.config_entries.async_update_entry(entrada, data={**entrada.data, "password": CLAVE})
    await hass.config_entries.async_reload(entrada.entry_id)
    await hass.async_block_till_done()


async def _formas_raras(ctx) -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed

    hass, nube, entrada = ctx["hass"], ctx["nube"], ctx["entrada"]
    print("\n  · respuestas con una forma inesperada")
    coord = hass.data[DOMINIO][entrada.entry_id]

    async def error_de(coro):
        try:
            await coro
        except Exception as err:  # noqa: BLE001
            return type(err)
        return None

    nube.instalaciones_raras = True
    coord.id_instalacion = None
    tipo = await error_de(coord._async_update_data())
    comprobar(tipo is UpdateFailed, f"instalaciones sin el campo esperado → UpdateFailed, no un KeyError ({tipo})")
    nube.instalaciones_raras = False
    nube.radiadores_raros = True
    tipo = await error_de(coord._async_update_data())
    comprobar(tipo is UpdateFailed, f"radiadores que no vienen como lista → UpdateFailed, no un TypeError ({tipo})")
    nube.radiadores_raros = False
    await coord.async_refresh()
    await hass.async_block_till_done()
    comprobar(coord.last_update_success, "con respuestas normales, se recupera")


async def _opciones(ctx) -> None:
    import custom_components.cointra as integracion
    from homeassistant.config_entries import ConfigEntryState

    hass, nube, entrada = ctx["hass"], ctx["nube"], ctx["entrada"]
    print("\n  · opciones")
    ips_originales = {"RAD000001": IP_1, "RAD000002": IP_2}

    flujo = await hass.config_entries.options.async_init(entrada.entry_id)
    campos = [str(k) for k in flujo["data_schema"].schema]
    comprobar(flujo.get("step_id") == "init" and campos == ["update_interval", "Salón", "Dormitorio"],
              f"el formulario pide el intervalo y la IP de cada radiador {campos}")

    rara =await hass.config_entries.options.async_configure(
        flujo["flow_id"], {"update_interval": 60, "Salón": "no-es-una-ip", "Dormitorio": IP_2}
    )
    comprobar(rara.get("type") == "form" and rara.get("errors") == {"base": "invalid_ip"}
              and entrada.data["radiator_ips"] == ips_originales,
              f"una IP mal escrita se rechaza y no se guarda nada ({rara.get('errors')})")
    if rara.get("type") != "form":
        # Aceptada: se deshace lo guardado y se abre otro formulario para
        # poder seguir con el resto.
        hass.config_entries.async_update_entry(
            entrada, data={**entrada.data, "radiator_ips": ips_originales}, options={}
        )
        await hass.async_block_till_done()
        flujo = await hass.config_entries.options.async_init(entrada.entry_id)

    # Una IP que da pie a «radiador caído» por un simple error de teclado
    # no puede llegar a guardarse; una buena sí, y con una sola recarga.
    cargas = {"n": 0}
    original = integracion.async_setup_entry

    async def contando(hass_, entry):
        cargas["n"] += 1
        return await original(hass_, entry)

    integracion.async_setup_entry = contando
    try:
        fin = await hass.config_entries.options.async_configure(
            flujo["flow_id"], {"update_interval": 90, "Salón": IP_1, "Dormitorio": ""}
        )
        await hass.async_block_till_done()
    finally:
        integracion.async_setup_entry = original
    comprobar(fin.get("type") == "create_entry" and entrada.options == {"update_interval": 90}
              and entrada.data["radiator_ips"] == {"RAD000001": IP_1},
              f"guarda el intervalo (en options) y quita la IP en blanco ({entrada.options}, {entrada.data['radiator_ips']})")
    comprobar(cargas["n"] == 1, f"y recarga la integración una sola vez ({cargas['n']})")
    comprobar(hass.data[DOMINIO][entrada.entry_id].update_interval.total_seconds() == 90, "con el intervalo nuevo")

    # La nube caída cuando se abre «Configurar»: el formulario no conoce los
    # radiadores, pero guardar el intervalo no puede borrar las IPs.
    print("\n  · opciones con la integración sin cargar")
    hass.config_entries.async_update_entry(entrada, data={**entrada.data, "radiator_ips": ips_originales})
    await hass.async_block_till_done()
    nube.api_caida = True
    await hass.config_entries.async_reload(entrada.entry_id)
    await hass.async_block_till_done()
    comprobar(entrada.state is ConfigEntryState.SETUP_RETRY, f"nube caída al arrancar → HA reintenta solo ({entrada.state})")
    flujo = await hass.config_entries.options.async_init(entrada.entry_id)
    campos = [str(k) for k in flujo["data_schema"].schema]
    comprobar(campos == ["update_interval"], f"sin radiadores conocidos, solo se pide el intervalo {campos}")
    fin = await hass.config_entries.options.async_configure(flujo["flow_id"], {"update_interval": 60})
    nube.api_caida = False
    comprobar(entrada.data["radiator_ips"] == ips_originales,
              f"guardar el intervalo no borra las IPs de los radiadores ({entrada.data['radiator_ips']})")
    await hass.async_block_till_done()
    await hass.config_entries.async_reload(entrada.entry_id)
    await hass.async_block_till_done()
    comprobar(entrada.state is ConfigEntryState.LOADED, "y al volver la nube, carga")

    # Dos radiadores con el mismo nombre no pueden pisarse en el formulario.
    config_flow = importar("config_flow")
    nombres = getattr(config_flow, "nombres_radiadores", None)
    comprobar(nombres is not None and len(nombres([{"Nombre": "Salón", "IdRadiador": "A"}, {"Nombre": "Salón", "IdRadiador": "B"}])) == 2,
              "dos radiadores con el mismo nombre salen como dos campos distintos")


async def _diagnostico(ctx) -> None:
    from homeassistant import loader

    hass, entrada = ctx["hass"], ctx["entrada"]
    print("\n  · icono y diagnóstico")
    integ = await loader.async_get_integration(hass, DOMINIO)
    comprobar(integ.has_branding, "HA ve el icono propio (carpeta brand/)")

    diagnostics = importar("diagnostics")
    comprobar(diagnostics is not None, "existe diagnostics.py")
    if diagnostics is None:
        return
    diag = await diagnostics.async_get_config_entry_diagnostics(hass, entrada)
    texto = json.dumps(diag, default=str)
    privados = [dato for dato in (CORREO, CLAVE, IP_1, IP_2, "Salón", "Dormitorio", "RAD000001", "RAD000002") if dato in texto]
    comprobar(not privados, f"el diagnóstico tapa el correo, la contraseña, las IPs, los nombres y los ids (se cuelan {privados})")
    comprobar(len(diag["radiators"]) == 2 and diag["radiators"][0].get("TempComfort") is not None
              and diag["cloud"]["last_update_success"] is True,
              "y trae lo que devuelve la nube y el estado de la actualización")
    comprobar(len(diag["ping"]) == 2 and all("status" in p for p in diag["ping"]), "y el estado del ping de cada radiador")


async def _reconfigurar(ctx) -> None:
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import entity_registry as er

    hass, nube, entrada = ctx["hass"], ctx["nube"], ctx["entrada"]
    print("\n  · reconfigurar")
    eid = entrada.entry_id
    ids_antes = {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), eid)}
    flujo = await hass.config_entries.flow.async_init(DOMINIO, context={"source": "reconfigure", "entry_id": eid})
    sugeridos = {str(k): (k.description or {}).get("suggested_value") for k in flujo["data_schema"].schema} \
        if flujo.get("data_schema") else {}
    comprobar(flujo.get("step_id") == "reconfigure" and sugeridos.get("username") == CORREO
              and sugeridos.get("password") is None,
              f"el formulario sale con el correo actual y sin la contraseña {sugeridos}")
    mal = await hass.config_entries.flow.async_configure(flujo["flow_id"], {"username": CORREO, "password": "mala"})
    comprobar(mal.get("errors") == {"base": "invalid_auth"}, "con una contraseña mala, avisa y no cambia nada")

    nube.cuentas["luis@example.com"] = "otra-clave"
    bien = await hass.config_entries.flow.async_configure(
        flujo["flow_id"], {"username": "luis@example.com", "password": "otra-clave"}
    )
    await hass.async_block_till_done()
    ids_despues = {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), eid)}
    comprobar(bien.get("reason") == "reconfigure_successful" and entrada.data["username"] == "luis@example.com"
              and entrada.data["password"] == "otra-clave" and entrada.unique_id == "luis@example.com"
              and entrada.title == "luis@example.com" and entrada.data["radiator_ips"],
              "cambia la cuenta (correo, título y unique_id) sin perder las IPs")
    comprobar(entrada.state is ConfigEntryState.LOADED and ids_antes == ids_despues,
              "se recarga sola y conserva todas las entidades")

    # Contraseña vacía: se conserva la que había.
    flujo = await hass.config_entries.flow.async_init(DOMINIO, context={"source": "reconfigure", "entry_id": eid})
    nube.cuentas["luis@example.com"] = "otra-clave"
    await hass.config_entries.flow.async_configure(flujo["flow_id"], {"username": "luis@example.com"})
    await hass.async_block_till_done()
    comprobar(entrada.data["password"] == "otra-clave" and entrada.state is ConfigEntryState.LOADED,
              "con la contraseña vacía conserva la que había")

    # Vuelve a la cuenta de siempre.
    flujo = await hass.config_entries.flow.async_init(DOMINIO, context={"source": "reconfigure", "entry_id": eid})
    await hass.config_entries.flow.async_configure(flujo["flow_id"], {"username": CORREO, "password": CLAVE})
    await hass.async_block_till_done()


async def _radiador_de_baja(ctx) -> None:
    import custom_components.cointra as integracion
    from homeassistant.helpers import device_registry as dr

    hass, nube, entrada = ctx["hass"], ctx["nube"], ctx["entrada"]
    print("\n  · radiador dado de baja en la app")
    coord = hass.data[DOMINIO][entrada.entry_id]
    registro = dr.async_get(hass)
    dispositivo = lambda ident: registro.async_get_device_by_identifier((DOMINIO, ident), entrada.entry_id)  # noqa: E731
    quitar = getattr(integracion, "async_remove_config_entry_device", None)

    guardado = nube.radiadores.pop("RAD000002")
    await coord.async_refresh()
    await hass.async_block_till_done()
    comprobar(_estado(hass, "climate", "cointra_RAD000002_climate").state == "unavailable",
              "sus entidades pasan a no disponibles")
    comprobar(quitar is not None and await quitar(hass, entrada, dispositivo("RAD000002")),
              "y su dispositivo se puede eliminar desde HA")
    comprobar(quitar is not None and not await quitar(hass, entrada, dispositivo("RAD000001"))
              and not await quitar(hass, entrada, dispositivo(entrada.entry_id)),
              "el de un radiador que sigue en la cuenta, o el de la cuenta, no")
    nube.radiadores["RAD000002"] = guardado
    await coord.async_refresh()
    await hass.async_block_till_done()


async def _recarga(ctx) -> None:
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import device_registry as dr

    hass, ping, entrada = ctx["hass"], ctx["ping"], ctx["entrada"]
    print("\n  · recarga y descarga")
    registro = dr.async_get(hass)
    antes = {d.id for d in dr.async_entries_for_config_entry(registro, entrada.entry_id)}
    await hass.config_entries.async_reload(entrada.entry_id)
    await hass.async_block_till_done()
    despues = {d.id for d in dr.async_entries_for_config_entry(registro, entrada.entry_id)}
    comprobar(entrada.state is ConfigEntryState.LOADED and antes == despues, "tras recargar, los mismos dispositivos")

    await hass.config_entries.async_unload(entrada.entry_id)
    await hass.async_block_till_done()
    comprobar(entrada.state is ConfigEntryState.NOT_LOADED and entrada.entry_id not in hass.data.get(DOMINIO, {}),
              "al descargar, no queda nada en hass.data")
    n = len(ping.llamadas)
    comprobar(not [t for t in asyncio.all_tasks() if "async_ping_all" in repr(t)], "ni un ping pendiente")
    await hass.config_entries.async_setup(entrada.entry_id)
    await hass.async_block_till_done()
    comprobar(entrada.state is ConfigEntryState.LOADED and len(ping.llamadas) > n, "y vuelve a cargar con su primer ping")


async def _borrar(ctx) -> None:
    hass, entrada = ctx["hass"], ctx["entrada"]
    print("\n  · borrar la integración")
    await hass.config_entries.async_remove(entrada.entry_id)
    await hass.async_block_till_done()
    comprobar(not hass.config_entries.async_entries(DOMINIO), "la entrada se borra sin errores")


def test_home_assistant() -> None:
    try:
        import homeassistant  # noqa: F401
    except ImportError:
        print("\nHome Assistant no instalado: se saltan las pruebas de integración")
        return

    print("\nCliente de la API contra la nube falsa")
    asyncio.run(_api_sin_ha())
    print("\nPing")
    asyncio.run(_ping_sin_ha())

    print("\nIntegración en un Home Assistant real")
    directorio = Path(tempfile.mkdtemp(prefix="cointra_"))
    try:
        (directorio / "custom_components").mkdir()
        (directorio / "custom_components" / DOMINIO).symlink_to(BASE)
        asyncio.run(_recorrido(directorio))
    finally:
        shutil.rmtree(directorio, ignore_errors=True)


if __name__ == "__main__":
    test_manifest_y_traducciones()
    test_home_assistant()
    print()
    if fallos:
        print(f"{len(fallos)} FALLOS:")
        for f in fallos:
            print(f"  - {f}")
    else:
        print("Todo OK")
    # os._exit y no sys.exit: con HA 2026.9 y el Python 3.14 del venv, el
    # intérprete da un segfault al cerrarse si hay cualquier entrada de
    # configuración cargada (lo mismo pasa en Integracion_Matriculas).
    sys.stdout.flush()
    sys.stderr.flush()
    import os

    os._exit(1 if fallos else 0)
