# Integracion_Cointra_Estufas

Repositorio de la integración custom de Home Assistant "Cointra Electric"
(radiadores WiFi Cointra/Ferroli) de Maxi. Es la **fuente de verdad** del
código y, a la vez, la fuente desde la que HACS instala/actualiza la
integración — este repo es **público** a propósito, porque HACS solo
puede leer repos públicos (no soporta repos privados; se probó y da 404
incluso con GitHub autenticado).

## Entorno

- Repo local clonado en: `~/Downloads/GitHub/Integracion_Cointra_Estufas`
- El HA real está montado por Samba en el Mac:
  - `/Volumes/config/custom_components/cointra` → donde HACS instala
    finalmente la integración
- Esta sesión de Claude Code **no tiene credenciales de GitHub**: puede
  hacer `git add` y `git commit`, pero **no puede hacer `git push` ni
  `git fetch`**. El push lo hace siempre el usuario, normalmente desde
  GitHub Desktop.
- Un clasificador de seguridad automático puede bloquear `git add` sobre
  archivos cuyo nombre suene a secreto (`credentials.py`, `token.py`,
  etc.) aunque no tengan datos sensibles reales. Si pasa, que el usuario
  haga ese `git add` puntual él mismo.

## Estructura

```
custom_components/cointra/    manifest.json, __init__.py, config_flow.py...
docs/DECISIONES.md            historial de decisiones y bugs, NO va en custom_components
hacs.json
README.md
```

`docs/DECISIONES.md` está fuera de `custom_components/cointra/` a
propósito: cuando HACS instala la integración copia esa carpeta entera a
`/config/custom_components/cointra`, y no tiene sentido que la
documentación de decisiones viaje a la instalación real de HA.

## Por qué este repo es público

Se evaluó mantenerlo privado, pero HACS usa una GitHub OAuth App con
permiso único de **"Access public information (read-only)"** — no existe
forma de darle acceso a repos privados (a diferencia del Supervisor de
HA, que sí soporta un token embebido en la URL para add-ons privados). Se
revisó el código antes de hacerlo público: no contiene credenciales ni
tokens (el login de Cointra se guarda cifrado en el `.storage` de HA, no
en el código). Lo único "sensible" que se hace público es el propio
contrato de la API no oficial de Cointra/Ferroli (en `docs/DECISIONES.md`),
que de todas formas es reproducible por cualquiera con el mismo esfuerzo
de ingeniería inversa.

## Flujo para editar la integración

1. Editar los archivos **en el repo**, dentro de `custom_components/cointra/`.
2. Commit en el repo (yo puedo). Push lo hace el usuario.
3. Subir el número de `version` en `manifest.json` cuando el cambio esté
   listo para probarse — HACS detecta la actualización por ese número.
4. El usuario actualiza desde HACS y reinicia HA para probar.
5. Si algo falla, se corrige, se sube la versión otra vez, commit, push,
   actualizar, reiniciar.

No copiar directamente en `/Volumes/config/custom_components/cointra` sin
antes editar en el repo — se perdería el historial de lo que realmente se
probó.

## Credenciales

La integración no guarda credenciales en el código: el login de Cointra
se introduce vía `config_flow` y HA lo guarda cifrado en su propio
almacenamiento (`.storage/core.config_entries`), fuera de este
repositorio.
