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

`install.sh` también pone el widget del plugin en la barra (con
`omarchy-bar put`, que es seguro correr de nuevo — deja el widget donde
está si ya se encuentra ahí). Después de que `install.sh` termine:

```sh
./bin/wa-up
omarchy-restart-shell
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
la tabla de historial de wuzapi hasta que aparezcan filas y después dejen
de crecer, reinicia el contenedor del sink (que purga esa tabla al
arrancar), espera a que la purga termine, y recién ahí chequea que la
tabla haya quedado realmente vacía. Esperá que todo este paso tarde un par
de minutos después de ingresar el código — es el volcado de historial
llegando y siendo borrado, no que se colgó.

Hay exactamente tres cosas que te puede decir, y significan cosas
distintas.

**1. Salió bien.**

```
linked.
waiting for WhatsApp to finish sending history...
history purged: 14832 rows deleted, table now empty.
```

**2. No salió bien.**

```
WARNING: history may NOT have been fully cleared.
  History was still arriving when purge ran: settled=False
  Rows before restart: 9021, rows remaining: 342
  Please run 'docker restart omarchy-wa-sink' in a minute and check again.
```

Hacé exactamente eso: esperá un minuto, corré `docker restart
omarchy-wa-sink` vos mismo, y volvé a revisar. El sink purga la tabla de
historial cada vez que arranca, así que un segundo reinicio después de que
el volcado haya terminado de verdad debería dejar la tabla vacía. No des
por terminado el emparejamiento hasta confirmar eso.

**3. No puede saberlo.**

```
history: no rows were ever observed in wuzapi's message_history table.
  That may mean this account has no history to sync, or it may mean
  WhatsApp had not started sending it yet. This run cannot tell which.
  Nothing was confirmed purged, because nothing was ever seen.
```

Esto **no** es un mensaje de éxito, aunque el comando salga con código 0.
`loggedIn` se prende al final del handshake de emparejamiento, *antes* de
que whatsmeow empiece a mandar el historial, así que una tabla vacía es
genuinamente ambigua y nada en la base puede resolver esa ambigüedad. Si
querés certeza, esperá unos minutos y después:

```sh
docker restart omarchy-wa-sink
docker logs omarchy-wa-sink   # buscá la línea "purged N history rows"
```

## Habilitar un grupo

Los chats de grupo se ignoran por defecto. Para dejar pasar uno, agregá su
JID a `groups_enabled` en `~/.local/share/omarchy-wa/state/config.json`:

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

## Logs de los contenedores y contenido de los mensajes

`bin/wa-up` levanta wuzapi con `LOG_LEVEL=warn`. Sin esa variable, wuzapi
usa por defecto su nivel más verboso y escribe el texto completo de cada
mensaje —tuyo y de la otra persona— en `docker logs omarchy-wuzapi` en
texto plano. Eso anula directamente la purga de historial de arriba: no
tiene mucho sentido borrar 15.000 filas de SQLite si el mismo contenido
queda guardado en el journal de Docker. `warn` mantiene visibles los
problemas de conexión (logins fallidos, sesiones caídas, el warning "No
webhook set for user") y a la vez descarta las líneas de debug por mensaje
que contienen el contenido.

El log del sink (`docker logs omarchy-wa-sink`) no se ve afectado por este
ajuste y sigue como se describió arriba: solo registra JIDs y nombres
visibles para las decisiones de descarte de grupos, nunca el cuerpo de los
mensajes.

Si querés mirar la actividad reciente de wuzapi sin revisar todo el
historial, usá `--since`:

```sh
docker logs --since 10m omarchy-wuzapi
```

Si corridas anteriores a nivel DEBUG ya escribieron contenido de mensajes
en el journal y querés que desaparezca, el log está atado al contenedor,
no a un archivo aparte que se pueda editar selectivamente — eliminalo junto
con el contenedor:

```sh
docker rm -f omarchy-wuzapi
```

`wa-up` lo vuelve a crear en la próxima corrida con el logging ya en
`warn`.

## Desactivar el dry-run

Todo envío pasa por `bin/wa-reply`, y por defecto no hace nada más que
reportar lo que *habría* enviado. Ninguna petición llega a wuzapi y nada
cambia en la cola: el panel muestra "DRY RUN — no se envió nada. La
entrada sigue pendiente en la cola.", la entrada queda abierta y su
estado guardado queda tal cual estaba — incluido un `unknown` o un
`sending`, que un dry run nunca debe borrar — así que podés seguir
iterando el borrador.
Este es el estado por defecto de la herramienta en cada instalación nueva,
no algo que haya que acordarse de activar.

Para que efectivamente envíe mensajes a personas reales, agregá esta línea
a `~/.local/share/omarchy-wa/secrets.env`:

```
WA_DRY_RUN=0
```

Sé consciente de lo que hace esta línea: es lo único que convierte una
herramienta de redacción inofensiva en algo que le manda mensajes a
personas reales en tu nombre, sobre un puente que puede hacer que te baneen
el número. Activala solo cuando estés cómodo con ambos hechos.

## Envíos cuyo resultado nadie conoce

Hay dos cosas que pueden dejar una entrada en un estado donde el mensaje
quizás ya llegó a WhatsApp y nadie registró la respuesta:

- **`unknown`** — el POST se cayó por timeout o se cortó la conexión a
  mitad de camino.
- **`sending`** — `wa-reply` murió entre que empezó el POST y que supo el
  resultado: un reinicio de la barra, un logout, un OOM kill. La petición
  puede tardar hasta 20 segundos, así que esa ventana es real.

Los dos reciben el mismo tratamiento, porque los dos tienen la misma
consecuencia. `wa-reply` escribe el estado en esa entrada de `queue.json`,
y el panel muestra (con la redacción ajustada a cuál de los dos fue):

> sin confirmación: puede que el mensaje ya se haya enviado — revisá
> WhatsApp antes de reintentar

Ese registro es durable, y es lo que leen las dos mitades de la
protección:

- `bin/wa-reply send` **se niega** a enviar una entrada cuyo estado
  guardado sea `unknown` o `sending`, y devuelve
  `{"ok": false, "needs_ack": true, "status": "...", ...}` salvo que se lo
  llame con `--acknowledged`.
- El panel marca la entrada en la lista de la cola ("⚠ sin confirmar" o
  "⚠ envío interrumpido"), no solo cuando la abrís, y deja **Enviar**
  deshabilitado hasta que apretés *"Revisé WhatsApp, quiero reenviar"*.
  Apretar eso es lo que pasa `--acknowledged`.
- Un mensaje nuevo en el mismo chat **no** limpia ninguno de los dos
  estados.

Cerrar el popup, reiniciar la barra, dejar que la cola se refresque o hacer
un dry run **no** levanta el bloqueo: la advertencia se recalcula desde lo
que hay en disco cada vez, así que no se pierde reabriendo el panel ni
apretando Enviar con el dry-run todavía activo. Revisá WhatsApp vos mismo
para ver si salió antes de confirmar.

## Un mensaje que llega mientras estás redactando

Si alguien vuelve a escribir en el mismo chat entre que abrís la entrada y
que el envío se confirma, la entrada **no** se elimina: queda en la cola
con el mensaje nuevo, y el panel lo dice:

> enviado. Llegó un mensaje nuevo en este chat mientras redactabas, así que
> la entrada sigue en la cola.

Tu respuesta sí salió. La cola simplemente no tira a la basura lo que
todavía no leíste.

## Dónde vive el estado, y cómo borrarlo

Todo vive bajo `~/.local/share/omarchy-wa/` (modo 700):

```
~/.local/share/omarchy-wa/
├── secrets.env     modo 600 — NO se monta en ningún contenedor
├── state/          se monta en el sink como /state
│   ├── queue.json
│   ├── ctx/
│   └── config.json
└── wuzdata/        se monta en los dos contenedores
```

- `secrets.env` — el token de admin, la clave de cifrado, el token de API
  y (si lo pusiste) `WA_DRY_RUN=0`. Modo 600. Está **afuera** de `state/`
  a propósito: el sink es el contenedor que recibe lo que llega de
  internet, y no tiene por qué tener la credencial que puede mandar
  mensajes en tu nombre. No está montado en ningún lado.
- `state/queue.json` — los mensajes pendientes que se ven en el panel.
- `state/ctx/` — un archivo JSON chico por chat, los últimos 10 mensajes
  de contexto usados al redactar.
- `state/config.json` — `groups_enabled` y cualquier otro ajuste editado a
  mano.
- `wuzdata/` — la base SQLite y los datos de sesión propios de wuzapi,
  montados en los dos contenedores. Ahí es donde cae el volcado de
  historial antes de que `wa-pair`/el sink lo purguen.

Tres variables de entorno permiten mover cada cosa por separado, que es el
punto de haberlas separado: `WA_STATE_ROOT` (la raíz de arriba),
`WA_DATA_DIR` (el directorio que se monta en el sink, por defecto
`$WA_STATE_ROOT/state`) y `WA_SECRETS_FILE` (por defecto
`$WA_STATE_ROOT/secrets.env`). La ruta de los secretos nunca se deriva de
`WA_DATA_DIR`.

Si venías de una versión anterior, `wa-up` mueve `queue.json`,
`config.json` y `ctx/` adentro de `state/` en la próxima corrida.

El sink corre como **tu** usuario (`--user "$(id -u):$(id -g)"`), no como
root, así que todo lo que escribe en `state/` queda legible para vos.
Versiones anteriores corrían el sink como root, lo que dejaba `queue.json`
y demás archivos con dueño root e ilegibles para vos — la cola tenía
mensajes reales pero `wa-reply list`, `wa-status` y el panel reportaban
todo vacío, porque un error de permisos y una cola vacía se veían
idénticos. `wa-up` ahora detecta archivos de estado que no puede leer como
tu usuario y repara el dueño automáticamente antes de (re)iniciar nada; si
no puede (por ejemplo, si Docker no responde), imprime el comando exacto
para correr a mano y se niega a arrancar en vez de dejar una instalación
rota corriendo. Si esto vuelve a pasar, un estado ilegible o corrupto
ahora lo dice a los gritos en vez de reportar cero en silencio: `wa-reply
list` imprime `{"error": "..."}` (nunca un `[]` a secas) y sale con código
distinto de cero, `wa-status` reporta `ok: false` (que el widget de la
barra muestra como `WA ✕`), y el panel muestra un aviso rojo en vez de una
lista vacía.

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
