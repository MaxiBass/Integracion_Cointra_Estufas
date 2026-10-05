# Integracion_Cointra_Estufas

Integración custom de Home Assistant para radiadores eléctricos WiFi
**Cointra** (marca de Ferroli), construida por ingeniería inversa de la app
oficial COINTRA ELECTRIC (no existe integración oficial ni de terceros).
Habla con la nube de Ferroli (`gateway.grupoferroli.es`): los radiadores no
exponen nada en la red local.

Historial de decisiones, pruebas y fallos resueltos: [`docs/DECISIONES.md`](docs/DECISIONES.md).

## Qué crea

- Un **dispositivo por radiador**, colgando del dispositivo de la cuenta, con:
  - un termostato (`climate`): encendido/apagado, temperatura objetivo y
    los modos de la app (confort, eco, antihielo, programa, manual),
  - interruptores «Bloqueo de teclado», «Detección de ventana abierta» y
    «Arranque adaptativo»,
  - controles «Brillo de pantalla», «Duración del brillo» y «Límite de
    potencia»,
  - el sensor «Potencia estimada» (potencia nominal × límite mientras
    calienta; es una estimación, la nube no da consumo real),
  - los sensores «Calentando» y «Error»,
  - si se le ha dado una IP local: «Conexión local» (ping cada 5 minutos) y
    el botón «Comprobar disponibilidad ahora».
- En el dispositivo de la cuenta: «Servidor Cointra», que indica si la nube
  responde.

Un radiador está «no disponible» si la nube no responde o si, teniendo IP,
falla el ping dos veces seguidas; vuelve en cuanto contesta a uno.

Además, en la ficha de la integración (Ajustes → Dispositivos y servicios):

- **Configurar**: el intervalo de actualización con la nube (60 s por
  defecto, mínimo 15) y la IP local de cada radiador.
- **Reconfigurar**: cambia la cuenta (correo y contraseña) sin borrar la
  integración ni perder sus entidades.
- Si Cointra deja de aceptar la contraseña, HA avisa y la pide de nuevo.
- **Descargar diagnóstico**: lo que devuelve la nube y el estado del ping,
  sin el correo, la contraseña, las IPs ni los nombres de los radiadores.
- Un radiador dado de baja en la app se puede eliminar desde su ficha.

## Instalación vía HACS

1. HACS → menú ⋮ → **Repositorios personalizados**.
2. URL: `https://github.com/MaxiBass/Integracion_Cointra_Estufas`,
   categoría **Integración**.
3. Instalar **Cointra Electric** desde HACS.
4. Reiniciar Home Assistant.
5. Ajustes → Dispositivos y servicios → Añadir integración → **Cointra Electric**,
   e introducir el correo y la contraseña de la cuenta de Cointra. Después
   pide, opcionalmente, la IP local de cada radiador.

## Instalación manual (sin HACS)

Copia `custom_components/cointra` a `/config/custom_components/cointra` en
tu HA y reinicia.

## Pruebas

```bash
python3 -m venv /tmp/hav
/tmp/hav/bin/pip install homeassistant==2026.9.4 icmplib==3.0.4 pillow
/tmp/hav/bin/python tests/test_cointra.py
```

Arrancan un Home Assistant real con una nube de Cointra simulada en
`127.0.0.1` y un ping simulado.
