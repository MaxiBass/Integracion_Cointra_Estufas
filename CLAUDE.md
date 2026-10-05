# Integracion_Cointra_Estufas

Repositorio de la integración custom de Home Assistant "Cointra Electric"
(radiadores WiFi Cointra/Ferroli) de Maxi. Es la **fuente de verdad** del
código y la fuente desde la que HACS la instala.

Es **público** a propósito, porque HACS no lee repos privados (se probó y
da 404 incluso con GitHub autenticado; ver más abajo). Por eso:

- **Nunca** subir datos reales de la cuenta ni de casa: ni el correo, ni los
  ids de la instalación o de los radiadores, ni sus nombres (son las
  habitaciones), ni las IPs. Las pruebas y la documentación usan datos
  inventados (`ana@example.com`, `RAD000001`, `10.0.0.x`).
- Las comprobaciones con datos reales se hacen en el scratchpad de la sesión.

## Entorno

- Repo local: `~/Downloads/GitHub/Integracion_Cointra_Estufas`
- HA real por Samba: `/Volumes/config` (si no está montado:
  `osascript -e 'mount volume "smb://192.168.1.9/config"'`).
  `/Volumes/config/custom_components/cointra` es donde la instala HACS.
- Esta sesión **no tiene credenciales de GitHub**: `git add` y `git commit`
  sí, `git push` no. El push lo hace el usuario desde GitHub Desktop.
- Un clasificador de seguridad automático puede bloquear `git add` sobre
  archivos cuyo nombre suene a secreto (`credentials.py`, `token.py`,
  etc.) aunque no tengan datos sensibles reales. Si pasa, que el usuario
  haga ese `git add` puntual él mismo.

## Estructura

```
custom_components/cointra/
  api.py          cliente de la nube de Ferroli (sin HA): login OAuth, token, PUT
  coordinator.py  sondeo de la nube y ping local a cada radiador
  __init__.py     alta de la entrada, dispositivo de la cuenta, ping periódico
  entity.py       dispositivo y disponibilidad comunes de cada radiador
  climate.py, switch.py, number.py, sensor.py, binary_sensor.py, button.py
  config_flow.py  alta, IPs locales, opciones, reconfigurar y reauth
  diagnostics.py  diagnóstico descargable, sin datos personales
  strings.json, translations/
  brand/          icono propio (HA 2026.9 lo lee de aquí)
docs/DECISIONES.md     por qué es así, mapa de la API; NO va en custom_components
docs/icono/generar.py  dibuja brand/icon.png e icon@2x.png
tests/test_cointra.py
```

`docs/` está fuera de `custom_components/cointra/` a propósito: HACS copia
esa carpeta entera a `/config/custom_components/cointra`, y la
documentación no tiene que viajar a la instalación real de HA.

## Antes de tocar nada, lee `docs/DECISIONES.md`

En particular:

- El PUT de un radiador lleva **`idInstalacion: 0`**; con el id real, la
  nube aplica el cambio a todos los radiadores (§3.1).
- La nube contesta 200 y **descarta en silencio** un número con «.0»: los
  valores numéricos pasan siempre por `api.formatear_valor` (§3.3, §6.1).
- `VentanasAbiertas` y `VentanasAbiertasStatus` son campos distintos (§3.2).
- No cambiar ningún `unique_id`, los identificadores de los dispositivos ni
  la forma de la entrada (§6.3).

## Flujo para editar

1. Editar en el repo, dentro de `custom_components/cointra/`.
2. Pasar las pruebas (abajo).
3. Commit (yo puedo). Push lo hace el usuario.
4. Subir la `version` de `manifest.json` cuando esté listo para probar: HACS
   detecta la actualización por ese número.
5. El usuario actualiza desde HACS y reinicia HA.

No copiar directamente en `/Volumes/config/custom_components/cointra` sin
editar antes en el repo: se perdería el historial de lo que realmente se
probó.

## Pruebas

```bash
python3 -m venv /tmp/hav
/tmp/hav/bin/pip install homeassistant==2026.9.4 icmplib==3.0.4 pillow
/tmp/hav/bin/python tests/test_cointra.py
```

El venv de `/tmp` lo borra a medias la limpieza de macOS; si `import
homeassistant` falla, recrearlo con `python3 -m venv --clear /tmp/hav`.

## Por qué este repo es público

HACS usa una GitHub OAuth App con un único permiso, **"Access public
information (read-only)"**: no hay forma de darle acceso a repos privados
(a diferencia del Supervisor de HA, que sí admite un token en la URL para
add-ons privados). Antes de hacerlo público se revisó que el código no
tiene credenciales ni tokens. Lo único «sensible» que se publica es el
contrato de la API no oficial de Cointra/Ferroli (`docs/DECISIONES.md`),
reproducible por cualquiera con el mismo esfuerzo de ingeniería inversa.

## Credenciales

La integración no guarda credenciales en el código: el correo y la
contraseña de Cointra se introducen en el formulario de alta y HA los
guarda en su propio almacenamiento (`.storage/core.config_entries`), fuera
de este repositorio.
