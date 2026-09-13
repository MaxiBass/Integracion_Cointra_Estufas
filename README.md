# Integracion_Cointra_Estufas

Integración custom de Home Assistant para radiadores eléctricos WiFi
Cointra (marca de Ferroli), construida por ingeniería inversa de la app
oficial (no existe integración oficial ni de terceros).

Historial de decisiones, pruebas y bugs resueltos: [`docs/DECISIONES.md`](docs/DECISIONES.md).

## Instalación vía HACS

1. HACS → menú ⋮ → **Repositorios personalizados**.
2. URL: `https://github.com/MaxiBass/Integracion_Cointra_Estufas`,
   categoría **Integración**.
3. Instalar **Cointra Electric** desde HACS.
4. Reiniciar Home Assistant.
5. Ajustes → Dispositivos y servicios → Añadir integración → **Cointra Electric**,
   e introducir el usuario/contraseña de la cuenta de Cointra.

## Instalación manual (sin HACS)

Copia `custom_components/cointra` a `/config/custom_components/cointra` en
tu HA y reinicia.
