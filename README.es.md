# WhatsApp Assistant

*[Read in English](README.md)*

Un widget de barra para Omarchy que encola los mensajes de WhatsApp que
llegan y redacta respuestas con Claude. Vos das la idea en una línea ("decile
que voy a llegar tarde"), Claude escribe el mensaje usando el contexto de ese
chat, y no se envía nada hasta que apretás **Enviar** — el panel arranca
cada instalación en modo dry-run, que nunca toca la red.

## Leé esto antes de emparejar nada

El puente que habla con WhatsApp es [wuzapi](https://github.com/asternic/wuzapi),
construido sobre [whatsmeow](https://github.com/tulir/whatsmeow). **No** es
la API oficial de WhatsApp Business — se loguea como si fuera un segundo
teléfono vinculado a tu cuenta, igual que WhatsApp Web, pero sin la
bendición de WhatsApp. El propio proyecto wuzapi dice sin vueltas que
usarlo puede hacer que baneen tu número. Ese riesgo es real, no es
hipotético, y este README no lo va a suavizar: si emparejás tu número
principal, estás aceptando que WhatsApp puede bloqueártelo. Considerá usar
un número de repuesto para cualquier uso que no sea casual y de bajo
volumen.

Hay una segunda cosa que hay que saber antes de emparejar: vincular un
dispositivo hace que WhatsApp vuelque *todo* el historial de mensajes de la
cuenta en la base SQLite local de wuzapi, sin cifrar. En una prueba
anterior eso fueron del orden de 15.000 mensajes. La configuración que
parece que debería evitar esto, `days_to_sync_history`, no lo hace — puesta
en `0` significa "el valor por defecto de WhatsApp", no "ninguno".
`wa-pair` se encarga de esto por vos (ver "Emparejar" más abajo), pero
conviene entender que está pasando y por qué tarda un minuto.

## Qué hace, en resumen

```
WhatsApp ──> wuzapi (Docker, solo loopback) ──webhook──> sink (Docker, red privada)
                                                              │ escribe queue.json, contexto
                                                              │ por chat, purga el historial
                                                              ▼
                                        Widget de barra ──> Panel: idea → claude -p → borrador → Enviar
```

- **wuzapi** es la única pieza que habla con WhatsApp. Está publicado solo
  en `127.0.0.1:8080` — nada fuera de tu máquina puede alcanzarlo.
- **El sink** (`sink/server.py`) recibe los webhooks de wuzapi, descarta lo
  que enviaste vos mismo, descarta los mensajes de grupo salvo que el grupo
  esté explícitamente habilitado, y escribe el resto en una cola y en un
  anillo de contexto corto por chat (los últimos 10 mensajes). Nunca envía
  nada.
- **`bin/wa-reply`** es el único componente que puede enviar un mensaje.
  Rechaza un borrador vacío, rechaza cualquier destinatario que no sea un
  JID completo, y se niega a enviar directamente a menos que
  `WA_DRY_RUN=0` esté seteado.
- **El widget de barra y el panel** (`BarWidget.qml`, `Panel.qml`) muestran
  los chats pendientes, te dejan escribir la idea, llaman a `claude -p`
  para redactar una respuesta según las instrucciones del agente
  (`agents/respuesta.md`) y la envían.

## Requisitos

`docker`, `curl`, `python3` y `claude` (Claude Code) en el `PATH`.

## Instalación

```sh
git clone <este repo>
cd omarchy-wa-assistant
./install.sh
```

`install.sh` enlaza el plugin en
`~/.config/omarchy/plugins/io.github.cristosolar.waassistant` y construye
**dos** imágenes de Docker:

1. `omarchy-wa-sink`, construida a partir del `docker/sink.Dockerfile` de
   este repo.
2. `omarchy-wuzapi`, construida clonando `asternic/wuzapi` desde GitHub y
   corriendo `docker build` sobre eso — solo si no existe ya una imagen
   con ese nombre.

Este es un costo único, deliberadamente puesto en `install.sh` y no en el
uso diario. `bin/wa-up` (el comando que corrés para levantar el puente) **no**
construye la imagen de wuzapi: si falta, `wa-up` imprime los dos comandos
de arriba y sale con error. Clonar y construir de forma automática una
imagen de terceros bajada de internet en un `wa-up` de rutina sería un
efecto secundario lento y sorpresivo para un comando que corrés seguido;
construirla una sola vez, durante la instalación, no lo es.

Después de que `install.sh` termine:

```sh
./bin/wa-up
omarchy-restart-bar
```

`wa-up` crea `~/.local/share/omarchy-wa/`, genera un token de admin, una
clave de cifrado y un token de API al azar en `secrets.env` la primera vez,
y levanta los dos contenedores (`omarchy-wuzapi` y `omarchy-wa-sink`) en
una red Docker privada llamada `omarchy-wa`. Solo se publica el puerto de
`omarchy-wuzapi`, y solo hacia `127.0.0.1`.

## Emparejar

```sh
./bin/wa-pair <número, formato internacional, sin +>
```

Esto imprime un código de vinculación de 8 dígitos y te dice dónde
ingresarlo: **WhatsApp > Dispositivos vinculados > Vincular un dispositivo
> Vincular con número de teléfono**. Una vez que WhatsApp reporta la sesión
como logueada, `wa-pair` no se detiene ahí: vigila la cantidad de filas en
la tabla de historial de wuzapi hasta que deja de crecer, reinicia el
contenedor del sink (que purga esa tabla al arrancar), y después chequea
que la tabla haya quedado realmente vacía. Esperá que todo este paso tarde
uno o dos minutos después de ingresar el código — es el volcado de
historial llegando y siendo borrado, no que se colgó.

Si sale bien vas a ver algo como:

```
linked.
waiting for WhatsApp to finish sending history...
history purged: 14832 rows deleted, table now empty.
```

Si en cambio ves:

```
WARNING: history may NOT have been fully cleared.
  History was still arriving when purge ran: settled=False
  Rows before restart: 9021, rows remaining: 342
  Please run 'docker restart omarchy-wa-sink' in a minute and check again.
```

hacé exactamente eso: esperá un minuto, corré `docker restart
omarchy-wa-sink` vos mismo, y volvé a revisar. El sink purga la tabla de
historial cada vez que arranca, así que un segundo reinicio después de que
el volcado haya terminado de verdad debería dejar la tabla vacía. No des
por terminado el emparejamiento hasta confirmar eso.

## Habilitar un grupo

Los chats de grupo se ignoran por defecto. Para dejar pasar uno, agregá su
JID a `groups_enabled` en `~/.local/share/omarchy-wa/config.json`:

```json
{"groups_enabled": ["120363012345678901@g.us"]}
```

Los cambios se aplican al instante — el sink relee `config.json` en cada
mensaje entrante, así que no hace falta reiniciar nada después de
editarlo.

Es un archivo que se edita a mano en este MVP, así que necesitás el JID del
grupo antes de poder habilitarlo. Para conseguirlo: pedile a alguien que
escriba en el grupo (o esperá el próximo mensaje ahí), y después revisá el
log del sink:

```sh
docker logs omarchy-wa-sink
```

Cada mensaje de grupo que descarta por no estar en `groups_enabled` todavía
imprime una línea, por ejemplo:

```
skipping messages from group not in groups_enabled: jid='120363012345678901@g.us' sender='Familia'
```

El JID es lo que copiás a `groups_enabled` — esa parte es estable.
`sender` **no** es el nombre ni el asunto del grupo; no hay ningún campo
así disponible acá. Es el nombre visible de quien haya mandado el mensaje
que disparó esa línea, así que va a cambiar de un mensaje a otro según
quién escriba. Usalo solo como pista aproximada de qué grupo es, nunca
como un título estable, y confiá en el JID para distinguir un grupo de
otro.

Registra cada JID una sola vez por proceso, no por mensaje, así que un
grupo con mucho tráfico no inunda el log — si no ves la línea, esperá otro
mensaje. Copiá el JID al arreglo de arriba.

## Desactivar el dry-run

Todo envío pasa por `bin/wa-reply`, y por defecto no hace nada más que
reportar lo que *habría* enviado — el panel va a mostrar "DRY RUN — no se
envió" y ninguna petición llega a wuzapi. Este es el estado por defecto de
la herramienta en cada instalación nueva, no algo que haya que acordarse
de activar.

Para que efectivamente envíe mensajes a personas reales, agregá esta línea
a `~/.local/share/omarchy-wa/secrets.env`:

```
WA_DRY_RUN=0
```

Sé consciente de lo que hace esta línea: es lo único que convierte una
herramienta de redacción inofensiva en algo que le manda mensajes a
personas reales en tu nombre, sobre un puente que puede hacer que te baneen
el número. Activala solo cuando estés cómodo con ambos hechos.

## El resultado de envío "unknown"

Si un envío se cae por timeout o se corta la conexión a mitad de camino,
`wa-reply` no puede saber si wuzapi lo recibió antes de la falla. En vez de
adivinar, reporta el resultado como desconocido, y el panel muestra:

> sin confirmación: puede que el mensaje ya se haya enviado — revisá
> WhatsApp antes de reintentar

y deshabilita el botón **Enviar** para esa entrada. Esto es intencional:
reintentar a ciegas arriesga mandarle el mismo mensaje dos veces a la misma
persona. Revisá WhatsApp vos mismo para ver si salió, y después volvé a la
cola (**Volver**) y volvé a seleccionar la entrada — eso es lo que
rehabilita el botón de envío.

## Dónde vive el estado, y cómo borrarlo

Todo vive bajo `~/.local/share/omarchy-wa/` (modo 700):

- `secrets.env` — el token de admin, la clave de cifrado, el token de API
  y (si lo pusiste) `WA_DRY_RUN=0`. Modo 600.
- `queue.json` — los mensajes pendientes que se ven en el panel.
- `ctx/` — un archivo JSON chico por chat, los últimos 10 mensajes de
  contexto usados al redactar.
- `config.json` — `groups_enabled` y cualquier otro ajuste editado a mano.
- `wuzdata/` — la base SQLite y los datos de sesión propios de wuzapi,
  montados en los dos contenedores. Ahí es donde cae el volcado de
  historial antes de que `wa-pair`/el sink lo purguen.

Para borrar todo y empezar de cero:

```sh
docker rm -f omarchy-wuzapi omarchy-wa-sink
rm -rf ~/.local/share/omarchy-wa
```

## Desvincular

Hacé esto en orden:

1. En el teléfono: **WhatsApp > Dispositivos vinculados**, y sacá este
   dispositivo de la lista. Hacé esto primero — es el paso que realmente le
   avisa a WhatsApp que el vínculo terminó.
2. `docker rm -f omarchy-wuzapi omarchy-wa-sink`
3. `rm -rf ~/.local/share/omarchy-wa`

Saltearse el paso 1 deja el dispositivo listado como vinculado del lado de
WhatsApp aunque ya hayas tirado abajo los contenedores acá.

## Licencia

MIT
