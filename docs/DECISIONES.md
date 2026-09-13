# Cointra Electric — Registro de decisiones y problemas resueltos

Integración custom de Home Assistant para radiadores eléctricos WiFi
Cointra (marca de Ferroli), construida por ingeniería inversa de la app
oficial COINTRA ELECTRIC (Android, paquete `com.csa.cointra.hc`), ya que
no existe integración oficial ni de terceros.

Este documento NO explica qué hace el código (ver manifest.json / los
docstrings de cada fichero). Es un historial de por qué se hizo así, qué
se probó y falló antes, y qué queda pendiente.

---

## 1. Cómo se descubrió la API (ingeniería inversa)

### 1.1. Primer intento: identificar el proveedor cloud

Hipótesis inicial: la app tenía toda la pinta de un dispositivo IoT
"white-label" (vinculación por QR, sin hub físico), así que se sospechó
que usaba Tuya por debajo (patrón muy común en este tipo de hardware).

**Se descartó tras comprobar:**
- El APK no contiene ninguna referencia a `tuya`, `thingclips` ni
  `thing.smart` (se hizo `grep` sobre `classes.dex` y sobre los bundles
  JS).
- No aparece "Powered by Tuya" en la app.

**Conclusión real:** es una nube propietaria de Ferroli
(`gateway.grupoferroli.es`), no hay forma de operar el radiador 100% en
local. El firmware WiFi es un módulo Espressif (ESP32/ESP8266) — se
confirmó por la presencia de los ficheros de protocolo de aprovisionamiento
`cloud_pb.js`, `sec0_pb.js`, `sec1_pb.js`, `session_pb.js`,
`wifi_config_pb.js`, `wifi_scan_pb.js` (son del SDK de
`esp-idf-provisioning`), pero ese protocolo solo se usa en el emparejamiento
inicial por BLE/SoftAP — una vez emparejado, el radiador habla directo con
la nube de Ferroli por HTTPS, no expone nada en la LAN.

### 1.2. Estructura de la app: cambió de framework entre versiones

El primer APK descargado (de un sitio de terceros tipo apkpure) era la
versión **1.1.3.1** (de 2020), una app **Ionic 2 + Cordova** con webpack de
chunks numéricos (`assets/www/build/*.js`). Ese APK apuntaba a
`https://gateway.grupoferroli.es/APIGWR` — pero esa ruta ya no existe (da
404 de IIS, y la raíz del dominio devuelve la página de bienvenida por
defecto de IIS, señal de que ahí no hay nada desplegado hoy).

Hubo que sacar el **APK real instalado en el móvil** vía `adb pull` (no
fiarse de sitios de descarga de terceros, que sirven versiones
desactualizadas). La versión real en uso era la **2.1.7**, migrada a
**Angular + Capacitor** (bundles con hash tipo
`main.875d7bfbddc5ec0ba4f3.js` en `assets/public/`, ya no Cordova puro).

**Lección aprendida:** el path base cambió de `/APIGWR` a `/APIGW` (sin la
R) entre esas dos versiones. Si en el futuro la integración deja de
funcionar de golpe, lo primero a comprobar es si Ferroli ha vuelto a mover
la ruta base — hay que repetir el proceso de `adb pull` + grep sobre los
bundles JS para localizar el nuevo `apiURL`.

### 1.3. Limitación de `grep` en macOS

Al buscar contexto amplio alrededor de coincidencias en los bundles JS
(que vienen minificados en una sola línea gigante), `grep -E` con
`.{n}` por encima de 255 repeticiones falla en macOS/BSD
(`grep: maximum repetition exceeds 255`). Se resolvió cambiando a scripts
Python (`re.finditer` + slicing manual del string) en vez de depender de
`grep` para contexto largo.

---

## 2. Mapa de la API (verificado empíricamente, no solo leído del código)

Base URL actual (app 2.1.7): `https://gateway.grupoferroli.es/APIGW`
Backend real (visible en un stack trace de error 500 filtrado durante las
pruebas): ASP.NET / Entity Framework —
`ATC.Comunicaciones.WebAPI.Controllers.RadiadorController`, IIS.

- **Login**: `POST /token`, body `application/x-www-form-urlencoded`:
  `grant_type=password&username=...&password=...&client_id=AppCointra`.
  `client_id` es un valor fijo hardcodeado en la app (`AppCointra`), no un
  secreto de usuario.
- **Refresh**: mismo endpoint, `grant_type=refresh_token&refresh_token=...&client_id=AppCointra`.
  Expira en ~1199s (~20 min).
- **Listar instalaciones**: `GET /api/Instalaciones` (sin parámetros).
  Ojo: en versiones antiguas de la app este modelo no existía (era
  Usuario→Gateway directo); en la 2.1.7 la jerarquía real es
  Usuario→**Instalación**→Gateway→Zona→Radiador.
- **Gateways de una instalación**: `GET /api/Gateway?IdInstalacion=X`.
- **Radiadores**: `GET /api/Radiador?IdInstalacion=X&IdZona=0` (en esta
  instalación concreta, cada radiador es su propio "gateway" individual —
  `IdRadiador` == `IdGateway`, sin zonas intermedias reales, de ahí
  `IdZona=0` fijo).
- **Cambiar un radiador**: `PUT /api/Radiador` — ver sección 3, tiene
  comportamiento no obvio importante.
- **Consumo** (confirmado, con datos reales): `GET /api/Radiador/{IdRadiador}/consumo/{periodo}`
  donde `{periodo}` es una palabra clave literal: `diario` | `semanal` |
  `mensual` | `anual` (NO una fecha, como se probó erróneamente al
  principio — se perdió tiempo probando formatos de fecha antes de
  encontrar en el JS que el parámetro es un string fijo). Devuelve
  `ConsumoValle`/`ConsumoPico` (probablemente kWh, sin confirmar unidad
  exacta), `PrecioValle`/`PrecioPico`, y un array `detalleConsumo` con
  desglose horario/diario/mensual según el periodo. **Decisión: no se usó
  en la integración** (ver sección 4.7) por ser un agregado retrospectivo,
  no una medición en tiempo real, y ser el endpoint menos usado de la app
  (mayor riesgo de que quede desactualizado).

### 2.1. Autenticación — por qué OAuth2 password grant vía formulario, no config_flow "estándar" simple

Se implementó un `config_flow` completo con reautenticación (ver sección
4.1/4.2) porque el token expira cada ~20 min y requiere refresh_token — no
es una API key estática. Sin un mecanismo de refresh automático + reauth
ante fallo, la integración se quedaría muerta cada 20 minutos.

---

## 3. Bugs raros y comportamientos inesperados de la API (el contenido más importante de este documento)

### 3.1. El `PUT /api/Radiador` aplica el cambio a TODOS los radiadores si `idInstalacion` no es 0

Este fue el hallazgo más importante y menos obvio de toda la sesión.

**Síntoma:** al mandar `PUT /api/Radiador` con
`{"Zones": [], "Heaters": ["<id_radiador>"], "Cambios": [...], "idCia": 1002, "idInstalacion": 11191}`
(usando el `idInstalacion` real de la cuenta), el servidor devolvía
`"traza":"COUNT: 2"` y el campo `Heaters` en la respuesta traía **ambos**
radiadores de la instalación, no solo el que se pidió. Se comprobó con un
`GET` posterior: el cambio se había aplicado a los dos radiadores, no solo
al indicado en `Heaters`.

**Diagnóstico:** se probó primero `Zones: []` sin más campos → error 500
de Entity Framework (`NotSupportedException`, "No se puede crear un valor
constante NULL de tipo List<int>"), porque faltaban `idCia`/`idInstalacion`
en el body y el backend intentaba hacer un `.Contains()` sobre una lista
null. Al añadir esos dos campos con el `idInstalacion` real, el error 500
desapareció, pero apareció el bug de "aplica a todos". Se probó también
`Zones: [0]` (la zona real de ambos radiadores) → mismo resultado, sigue
aplicando a ambos, porque los dos radiadores comparten la misma
`IdZona: 0`.

**La causa real** (encontrada comparando con cómo la propia app arma el
payload en `heater-slides.page.ts`, función `enviarCambiosDatos()`): el
filtro de "a qué radiadores aplicar" lo hace el backend usando
`Zones`/`idInstalacion`, e **ignora completamente `Heaters` como filtro**
cuando `idInstalacion` apunta a una instalación real — `Heaters` solo se
usa para poblar `HeatersForFirebase` (notificaciones push), no para
decidir el alcance de la escritura.

**La solución que funciona** (verificada con `"traza":"COUNT: 1"` y
confirmando con GET que solo cambió el radiador esperado):
```json
{
  "Zones": [],
  "Heaters": ["<id_radiador>"],
  "Cambios": [...],
  "idCia": 1002,
  "idInstalacion": 0
}
```
Con **`idInstalacion: 0`** (no el real), el backend cae en el camino de
código que sí respeta `Heaters` como filtro. Esto está codificado tal
cual en `api.py::set_radiador()` con un comentario explicando el motivo —
**no tocar ese `0` sin volver a verificar contra la API real**, no es un
valor arbitrario ni un placeholder, es el valor que hace que el filtro
correcto se active.

### 3.2. Los campos de "configuración" y "estado en tiempo real" para `VentanasAbiertas` y `ArranqueAdaptativo` son campos DISTINTOS, no covarían

**Síntoma:** el switch de "Detección de ventana abierta" (`switch.py`)
parecía no responder al pulsarlo — el `PUT` se aplicaba correctamente
(confirmado con logs y traza del servidor) pero el estado mostrado en HA
no cambiaba.

**Causa:** el JSON de un radiador trae `VentanasAbiertas` (bool: ¿está la
función activada?) y `VentanasAbiertasStatus` (bool: ¿se está detectando
una ventana abierta ahora mismo?) como campos **independientes**. Lo mismo
con `ArranqueAdaptativo`/`ArranqueAdaptativoStatus`. La primera versión del
switch leía `is_on` priorizando el campo `*Status`, asumiendo que era la
fuente de verdad más "fresca" — hipótesis incorrecta.

**Verificación empírica** (se hizo explícitamente para no asumir, con
`PUT` + `GET` inmediato antes/después):
- Escribir `VentanasAbiertas: false` → el campo `VentanasAbiertas` cambió
  a `false`; `VentanasAbiertasStatus` se quedó en `false` sin moverse (ya
  estaba así antes también).
- Escribir `ArranqueAdaptativo: true` → `ArranqueAdaptativo` cambió a
  `true`; `ArranqueAdaptativoStatus` se quedó en `false`, sin moverse.

**Conclusión:** el campo `*Status` es la detección/aplicación en tiempo
real por el propio hardware (si hay una ventana abierta detectada ahora
mismo, o si el arranque adaptativo está actuando ahora mismo), no un
reflejo de la configuración. **El switch debe leer y escribir siempre el
campo base**, nunca el `*Status`. El `*Status` se dejó como atributo
informativo extra (`detectado_ahora_mismo`) en vez de descartarlo.

Con `TecladoBloqueado` no se manifestó nunca este bug porque ese campo no
tiene una variante `*Status` separada — lee y escribe el mismo nombre de
campo.

### 3.3. El brillo/duración de brillo no se aplicaba desde HA aunque sí desde la app oficial

**Síntoma:** cambiar el slider de brillo o duración desde Home Assistant
no producía ningún cambio visible en el radiador ni en la app oficial,
pero el mismo cambio hecho desde la app sí funcionaba y se reflejaba
correctamente en HA (lectura).

**Causa:** Home Assistant, al mandar el valor del `number.async_set_native_value(value: float)`,
pasaba `value` tal cual a `str()`, lo que produce `"10.0"` en vez de
`"10"`. La API de Cointra, igual que ya se había visto con el bug de
`Zones`/`Heaters`, es sensible a que el payload coincida exactamente con
el formato que manda la app oficial (que siempre manda enteros sin parte
decimal para estos campos) — con `"10.0"` el servidor responde 200 OK
pero descarta el cambio silenciosamente, sin ningún error visible.

**Fix:** en `number.py`, antes de mandar el valor, se comprueba si es un
entero exacto (`float(value).is_integer()`) y si es así se manda como
`str(int(value))` en vez de `str(value)`. Esto es genérico para
`Brillo`, `DuracionBrillo` y `LimitePotencia`.

**Patrón general a tener en cuenta para el futuro:** la API de Cointra
puede aceptar un `PUT` con 200 OK y aun así **no aplicar el cambio** si el
formato del valor no coincide byte a byte con lo que espera (visto dos
veces: aquí con el formato numérico, y en 3.1 con el alcance
`Zones`/`Heaters`). Un 200 OK no es garantía de que el cambio se haya
aplicado — conviene verificar siempre con un `GET` posterior al depurar
comportamientos nuevos de esta API.

### 3.4. `Err_Offline`, `LastOnline` y `MarcaTiempo` no reflejan desconexión física real

Se intentó usar estos tres campos para detectar si un radiador está
físicamente desconectado de la corriente. **Verificado como no fiable**:
en pruebas con radiadores realmente desenchufados durante horas,
`Err_Offline` se mantuvo siempre en `false`, `LastOnline` siempre en
`0001-01-01T00:00:00` (valor por defecto/nunca seteado), y `MarcaTiempo`
siempre en `0`. La nube de Cointra simplemente no implementa esta
detección server-side, o no la expone en este endpoint.

**Se investigó también `CurrentUTCDate`** como posible heartbeat
(hipótesis: "última vez que el radiador reportó datos a la nube"). Se
hizo una prueba temporal real: dos consultas al mismo radiador separadas
por >1 minuto, con el radiador encendido y conectado. Resultado:
`CurrentUTCDate` se quedó exactamente igual en ambas consultas (congelado).
**Conclusión: `CurrentUTCDate` tampoco es un heartbeat en tiempo real** —
es probablemente el timestamp de algún evento puntual (último cambio
hecho desde la app, o similar), no algo que avance mientras el radiador
está simplemente conectado. Descartado como señal de disponibilidad.

Se hizo también una búsqueda de términos alternativos en el código de la
app (`rssi`, `signal`, `isOnline`, `heartbeat`, `lastSeen`, etc.). El único
`rssi` real encontrado pertenece al escaneo de redes WiFi durante el
emparejamiento inicial del dispositivo (protocolo Espressif), no a un dato
reportado de forma continua una vez configurado.

**Conclusión final, tras agotar razonablemente las vías del lado cloud:**
la API de Cointra no expone ninguna señal fiable de conectividad real del
dispositivo. La solución adoptada fue **ping ICMP local** (ver sección
4.4), no un workaround adicional sobre la nube.

### 3.5. `ping` por subprocess no es fiable dentro del contenedor de Home Assistant

Al implementar la comprobación de disponibilidad por ping, la primera
versión llamaba al binario `ping` del sistema vía
`asyncio.create_subprocess_exec`. Se corrigió **antes de que fallara en
producción**, al caer en la cuenta de que muchas instalaciones de Home
Assistant OS no tienen el binario `ping` disponible o no tienen permisos
de socket raw dentro del contenedor — motivo por el cual la integración
oficial "Ping" de HA usa la librería `icmplib` (ICMP puro en Python) en
vez de invocar al binario del sistema. Se replicó el mismo patrón que usa
esa integración oficial: intento en modo `privileged=True` (socket raw,
requiere `CAP_NET_RAW`) con fallback automático a `privileged=False` si
falla por permisos. `icmplib` se añadió como dependencia en
`manifest.json` (`requirements`), instalada automáticamente por HA.

### 3.6. Al combinar ping local + nube, se perdió sin querer la señal de "servidor caído"

Al añadir la disponibilidad basada en ping local (`ping_status`), las
propiedades `available` de `climate`/`switch`/`number` se sobreescribieron
por completo, dejando de usar el `coordinator.last_update_success` que
`CoordinatorEntity` gestiona automáticamente por defecto. Esto significa
que si la nube de Cointra se cae del todo (login falla, timeout, etc.), el
`DataUpdateCoordinator` conserva los últimos datos buenos en
`coordinator.data` (solo marca `last_update_success = False`, no vacía los
datos) — así que las entidades habrían seguido mostrando **datos
cacheados obsoletos como si fueran actuales**, sin ningún indicio de que
la fuente real (la nube) no responde.

**Fix:** todas las propiedades `available` ahora comprueban
`self.coordinator.last_update_success` explícitamente, ANDed con el
resultado del ping local. Ambas señales son independientes y se
comprueban por separado (una puede fallar sin que falle la otra: la nube
puede caerse sin que el radiador esté desconectado, y viceversa).

### 3.7. Comillas inteligentes de macOS rompiendo comandos `curl` pegados en Terminal

Detalle menor pero costó un par de vueltas: al escribir/pegar comandos
`curl` directamente en Terminal.app de macOS con "Smart Quotes" activado,
las comillas rectas (`"`) se autoconvertían en comillas tipográficas
(`"` `"`), rompiendo el parseo del comando sin ningún mensaje de error
obvio (el error resultante fue un 404 que parecía un problema de la API,
no de sintaxis del shell). Se resolvió usando heredocs (`cat > file.sh <<
'EOF' ... EOF`) para escribir los scripts, que no sufren la
autocorrección.

---

## 4. Decisiones de diseño no obvias

### 4.1. Migración de YAML (`configuration.yaml`) a `config_flow` con `ConfigEntry`

La integración se construyó primero con configuración por YAML
(`async_setup` + `CONFIG_SCHEMA`), que es más simple de implementar. Se
migró a `config_flow`/`ConfigEntry` porque:
- Home Assistant **solo agrupa entidades bajo un "Dispositivo"** (para que
  Líam y Matrimonio aparezcan como tarjetas de dispositivo con sus
  entidades agrupadas) cuando la integración usa config entries — con YAML
  puro las entidades quedan sueltas sin dispositivo asociado, por diseño
  de HA, no por un fallo de implementación.
- Permite reautenticación desde la UI (ver 4.2) sin tocar ficheros.
- Permite un asistente de configuración con pasos dinámicos (pedir la IP
  de cada radiador detectado, ver 4.4).

Al migrar, se encontraron y arreglaron dos errores de compatibilidad con
versiones recientes de HA core que no estaban documentados de forma obvia:
- `DataUpdateCoordinator.async_config_entry_first_refresh()` solo es
  válido si la integración tiene una config entry real (no aplica a setup
  YAML) — usar `async_refresh()` + comprobación manual de
  `last_update_success` en el caso YAML.
- `hass.helpers.discovery` (el atajo de acceso) está obsoleto en HA
  reciente; hay que importar `from homeassistant.helpers import discovery`
  directamente.
- En `OptionsFlow`, asignar `self.config_entry = config_entry` manualmente
  en `__init__` está obsoleto en versiones recientes de HA (rompía con un
  error 500 al abrir el formulario de opciones) — HA ahora inyecta
  `self.config_entry` automáticamente; no hay que asignarlo a mano.

### 4.2. Reautenticación (`async_step_reauth`) en vez de solo fallar

Se implementó el flujo estándar de reauth de HA (`ConfigEntryAuthFailed`
lanzado desde el coordinator cuando la API devuelve un error de
autenticación específico, `CointraAuthError`, distinto de un error de red
genérico `CointraApiError`) para que un cambio de contraseña en la cuenta
Cointra no deje la integración muerta silenciosamente — HA muestra un
aviso interactivo pidiendo la contraseña nueva, sin tener que borrar y
reconfigurar la integración desde cero.

### 4.3. Intervalo de refresco de la nube: 60s por defecto, configurable

Se dejó como opción configurable (no hardcodeado) porque el polling
constante contra un servidor de terceros no documentado tiene riesgo de
rate-limiting no conocido de antemano. 60s = ~1440 peticiones/día por
instalación, un valor conservador pero no se ha probado el límite real de
la API (no hay documentación oficial de rate limits).

### 4.4. Ping local independiente del ciclo de refresco de la nube, con debounce asimétrico

Dado que la nube no ofrece ninguna señal fiable de conectividad (sección
3.4), se implementó un ping ICMP local, programado con
`async_track_time_interval` cada 5 minutos, **desacoplado** del intervalo
de refresco de la nube (que puede cambiar sin afectar al ping).

**Debounce asimétrico** (decisión explícita tras discutirlo): para marcar
un radiador como offline se exigen **2 fallos de ping consecutivos**
(~10 min), para evitar falsos positivos por un hipo puntual de la red.
Pero para volver a marcarlo online basta con **un solo ping exitoso** —
no se aplica el mismo debounce a la recuperación, para que la vuelta a
disponible se detecte lo antes posible en el siguiente ciclo. Se añadió
además un botón manual "Comprobar disponibilidad ahora" por radiador
(plataforma `button`) para forzar un ping inmediato sin esperar el ciclo
de 5 min — útil justo después de reenchufar un radiador físicamente.

La IP de cada radiador es **opcional y por radiador**: se pide durante el
asistente de configuración inicial (tras validar credenciales, se listan
los radiadores reales de la cuenta y se pide su IP local por nombre), y
se puede editar después desde "Configurar" sin reinstalar. Un radiador sin
IP configurada simplemente no se pinga y se asume disponible salvo que la
nube diga lo contrario.

### 4.5. Diagnóstico de conectividad dividido en entidades separadas (nube vs. local), no un único sensor combinado

Primera versión: un solo `binary_sensor` "Error / Offline" mezclaba
`Err_Offline` de la API, código de error real, y resultado del ping, todo
en un único booleano. Se rediseñó a petición explícita, separando:
- `binary_sensor.servidor_cointra` — **único, a nivel de cuenta** (no
  repetido por radiador, porque la caída del servidor es un hecho
  compartido por todos los radiadores de la cuenta), agrupado bajo un
  dispositivo "virtual" que representa la cuenta cloud
  (`identifiers={(DOMAIN, entry.entry_id)}`).
- `binary_sensor.<radiador>_conexion_local` — uno por radiador, solo si
  tiene IP configurada, basado puramente en `ping_status`.
- `binary_sensor.<radiador>_error` — limpiado para reflejar solo errores
  reales de funcionamiento (`Error`/`ErrorList`), sin mezclar
  conectividad.

Motivo: permite ver de un vistazo **cuál de las dos cosas está fallando**
(nube vs. radiador concreto) en vez de un aviso genérico ambiguo. La
disponibilidad de `climate`/`switch`/`number` sigue combinando ambas
señales con AND (si falla cualquiera de las dos, la entidad pasa a
`unavailable`).

### 4.6. Se descartó explícitamente un watchdog de inactividad térmica (`TempActual` estancada) para inferir offline

Se llegó a implementar una versión que marcaba un radiador como
`InferredOffline` si `TempActual` no cambiaba durante 30 min estando
`Encendido`. **Se revirtió a petición explícita** tras cuestionar si
merecía la pena la complejidad: es una inferencia con riesgo real de
falsos positivos (una habitación ya en su temperatura objetivo puede tener
`TempActual` estable sin estar desconectada), arrastra estado en memoria
que se pierde en cada reinicio de HA, y además la primera implementación
tenía un bug real (no comprobaba `Encendido`, así que apagar un radiador a
propósito lo marcaba como "offline" a los 30 min). Se sustituyó por el
ping local (sección 4.4), que es una señal directa, no una inferencia.

### 4.7. Consumo: estimado localmente (potencia nominal × límite%), no vía el endpoint `/consumo`

Ver hallazgo del endpoint real en la sección 2. Se decidió explícitamente
**no usarlo** para consumo en tiempo real/acumulado, por:
- Ser un agregado retrospectivo (por horas/días), no una medición
  instantánea real.
- No estar claro con qué frecuencia se recalculan esos datos en el
  backend.
- Ser el endpoint menos usado de la app (pantalla secundaria de
  gráficas), mayor riesgo de quedar desactualizado o cambiar de forma sin
  aviso, como ya pasó con la ruta base entre versiones.

En su lugar: `sensor.<radiador>_potencia_estimada` calcula
`Potencia_nominal × (LimitePotencia / 100)` cuando `Calentando: true`, si
no `0 W`. Explícitamente etiquetado como estimación, no medición real
(atributo `nota` en el propio sensor). El consumo acumulado (kWh) se deja
a un Helper nativo de HA (Integral de Riemann + opcionalmente Utility
Meter) sobre ese sensor, replicando el mismo patrón ya usado en el
proyecto de la piscina (`bomba_calor_predictor`) en vez de reimplementar
integración numérica dentro del `custom_component`.

---

## 5. Limitaciones conocidas y pendientes

- **No hay forma de operar los radiadores en local.** El firmware
  (Espressif) solo habla con la nube de Ferroli tras el emparejamiento
  inicial. Se valoró reflashear con ESPHome/Tasmota como alternativa
  100% local, pero requiere abrir físicamente el radiador, localizar
  pines UART/GPIO0, y reimplementar desde cero el control del
  triac/relé y la lectura de temperatura — se dejó como posible proyecto
  aparte, no abordado.
- **El endpoint `/consumo` no se usa** (ver 4.7) — sigue sin confirmarse
  la unidad exacta de `ConsumoValle`/`ConsumoPico` (probablemente
  kWh, sin verificar con un valor distinto de cero).
- **Rate limits de la API reales, desconocidos** — no hay documentación
  oficial; el intervalo de 60s es una elección conservadora, no un límite
  confirmado.
- **La API puede volver a cambiar de ruta/formato sin aviso** (ya ha
  pasado una vez, `/APIGWR` → `/APIGW`). Si la integración deja de
  funcionar de golpe, el primer paso de diagnóstico es repetir la
  extracción del APK más reciente (`adb pull` desde el móvil, nunca de
  sitios de descarga de terceros) y grep sobre los bundles JS en
  `assets/public/*.js` para localizar `apiURL`/`ClientId`/nuevas rutas.
- **El "servidor Cointra" (`last_update_success`) depende de que
  cualquier excepción durante el refresh se propague correctamente** —
  se reforzó `api.py` para envolver también fallos de red puros
  (`aiohttp.ClientError`, timeouts) en `CointraApiError` con mensaje
  legible, no solo errores de aplicación con JSON `{"Message": ...}`.
- **Rangos válidos de `Brillo`/`DuracionBrillo`/`LimitePotencia` no
  verificados exhaustivamente contra la API real** — se basan en lo
  observado en el formulario Angular de la app oficial
  (`Brillo`: 0-100 ajustado tras prueba real del usuario a step=1;
  `DuracionBrillo`: 1-240s; `LimitePotencia`: 10-100%), no en una
  confirmación explícita de qué pasa si se manda un valor fuera de esos
  márgenes.
- **El logo oficial de Cointra no puede mostrarse como icono de la
  integración** en el panel de Dispositivos y servicios sin publicar la
  marca en el repositorio público `home-assistant/brands` de GitHub — se
  descartó por no tener relación oficial con la marca; alternativa
  sugerida (no implementada): usar el logo en una tarjeta Lovelace propia
  vía `/config/www/`.

## Nota de seguridad de una sesión paralela

Una segunda sesión que trabajó en esta misma integración registró que, en
algún momento de sus pruebas, se compartieron `access_token`/`refresh_token`
reales de la cuenta de Cointra dentro del chat. El token de acceso caduca
en ~20 min, pero si ese historial de chat quedó guardado en algún sitio
con el `refresh_token` visible, conviene cambiar la contraseña de la
cuenta de Cointra como precaución.
