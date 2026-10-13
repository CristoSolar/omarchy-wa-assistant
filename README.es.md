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

Esto aplica por cada número que vinculás, no una sola vez por instalación.
Emparejar una segunda cuenta (`wa-pair <account-id> <número>`, ver
"Emparejar" más abajo) vincula un segundo número de teléfono de la misma
forma, por el mismo puente, bajo el mismo riesgo — no hace más seguro al
primer número, y duplica la cantidad de cuentas que pueden terminar
baneadas. Si ese segundo número es un número de trabajo, la cuenta no es
la misma, es peor: perder un número personal es una molestia, perder el
número por el que te llaman tu jefe o tus clientes no lo es. Decidí cuenta
por cuenta si la comodidad vale el riesgo, teniendo esto en cuenta para
cada una que agregues — la herramienta te va a dejar emparejar tantos
números como quieras, pero no te va a decir cuáles valen la pena.

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
- **El sink** (`sink/server.py`) recibe los webhooks de wuzapi, descarta
  los mensajes de grupo salvo que el grupo esté explícitamente habilitado,
  y escribe el resto en una cola y en un anillo de contexto corto por chat
  (los últimos 10 mensajes). Lo que mandás vos — desde el teléfono, desde
  WhatsApp Web o desde este plugin — nunca entra a la cola: va al anillo
  marcado como tuyo, y saca ese chat de la cola, igual que leerlo en otro
  dispositivo (ver "Qué saca un chat de la cola" más abajo). Nunca envía
  nada, y nunca descarga nada.
- **`bin/wa-reply`** es el único componente que puede enviar un mensaje.
  Rechaza un borrador vacío, rechaza cualquier destinatario que no sea un
  JID completo, y se niega a enviar directamente a menos que
  `WA_DRY_RUN=0` esté seteado. **Una respuesta siempre sale por el número
  al que llegó.** La cuenta se lee de la propia entrada de la cola (que el
  sink escribe a partir del `instanceName` del webhook cuando llegó el
  mensaje) — no hay flag, variable de entorno ni ajuste en ningún lado que
  pueda mandar una respuesta desde una cuenta distinta a la que la recibió,
  y una entrada que nombra una cuenta que ya no está en `accounts.json` se
  rechaza en vez de enviarse desde otra. Esto es una garantía deliberada,
  no una limitación: contestarle a un cliente desde tu número personal
  porque algo tomó un valor por defecto mal es exactamente la falla que
  esto cierra por construcción.
- **El widget de barra y el panel** (`BarWidget.qml`, `Panel.qml`) muestran
  los chats pendientes, te dejan escribir la idea, llaman a `claude -p`
  para redactar una respuesta según el archivo de tono de la cuenta de ese
  chat (ver "Múltiples cuentas" más abajo) y la envían. Elegir un chat abre
  primero un extracto corto de la conversación, con el adjunto que traiga
  el mensaje pendiente (una foto se ve ahí mismo, un audio se reproduce ahí
  mismo), y recién **Responder** pasa a escribir — ver "El extracto de la
  conversación" y "Adjuntos" más abajo. Hay una sola caja de
  texto: escribís la idea y apretás **Redactar** (o Enter) para redactarla;
  esa misma caja pasa a tener el mensaje redactado — editable, con la idea
  de la que salió mostrada arriba — y recién ahí se habilita **Enviar**.
  Cada chat en la cola muestra una etiqueta con la cuenta a la que
  pertenece, y al abrirlo la vista de detalle dice explícitamente desde qué
  número va a salir la respuesta.
- **`bin/wa-media`** descarga un adjunto cuando pedís verlo, con el token
  de la cuenta por la que llegó el mensaje, a una caché local — **sin
  cifrar**, ver "Adjuntos" más abajo. No tiene ningún camino de envío.
- **El engranaje (⚙)** abre una pantalla de configuración incrustada en el
  mismo panel: la lista de cuentas configuradas, emparejar una nueva,
  desvincular una, editar el tono y las respuestas rápidas de cada cuenta,
  y vaciar la caché de adjuntos — ver "Pantalla de configuración" más
  abajo.

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

Clonar y construir wuzapi es un costo único, deliberadamente puesto en
`install.sh` y no en el uso diario. `bin/wa-up` (el comando que corrés
para levantar el puente) **no** construye la imagen de wuzapi: si falta,
`wa-up` imprime los dos comandos de arriba y sale con error. Clonar y
construir de forma automática una imagen de terceros bajada de internet
en un `wa-up` de rutina sería un efecto secundario lento y sorpresivo
para un comando que corrés seguido; construirla una sola vez, durante la
instalación, no lo es.

Con la imagen del sink es distinto: **`wa-up` reconstruye
`omarchy-wa-sink` desde este repo en cada corrida**, siempre. Se
construye desde código local, así que con la caché de capas de Docker una
reconstrucción sin cambios tarda un par de segundos. Ese costo compra una
garantía que vale más: el sink que está corriendo nunca puede ser más
viejo que el código de este repo. Antes se construía solo si la imagen no
existía, lo que permitía que una instalación siguiera corriendo un sink
anterior a un cambio — y, en la actualización a multi-cuenta, `wa-up`
habría migrado tu estado al formato nuevo para después levantar un sink
que nunca supo de él.

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

## Sobrevivir a reinicios

Los dos contenedores se levantan con `--restart unless-stopped`, así que
un cuelgue los trae de vuelta solos, y el manejo de reinicio de Docker
más la resolución de nombres propia de la red `omarchy-wa` convergen sin
importar cuál contenedor arranque primero — `omarchy-wuzapi` y
`omarchy-wa-sink` no necesitan arrancar en un orden particular para
encontrarse por nombre.

Eso cubre que los contenedores vuelvan a estar arriba. Por sí solo **no**
cubre que un reinicio de `omarchy-wuzapi` a nivel Docker siga pudiendo
*entregar* mensajes: wuzapi guarda su registro de webhook en memoria, y un
reinicio (cuelgue, OOM, reboot) lo pierde aunque `GET /webhook` siga
mostrándolo seteado en la base de datos.

### Autosanación automática: `wa-heal`

`install.sh` instala y habilita un timer de systemd de **usuario**,
`wa-heal.timer`, que corre `./bin/wa-heal` cada 5 minutos y una vez al
iniciar sesión. `wa-heal` reafirma el webhook cuando hay una sesión
logueada, y después lo relee con `GET /webhook` para confirmar que
realmente quedó puesto, en vez de confiar en el 200 del `POST`. No hace
nada si todavía no emparejaste nada o no hay sesión logueada, y es seguro
correrlo tan seguido como quiera el timer.

Esto es lo que hace seguro usar `--restart unless-stopped` en
`omarchy-wuzapi`: sin esto, un contenedor que vuelve de un cuelgue o un
reboot se ve completamente sano — contenedores arriba, `wa-status`
reportando `ok: true, logged_in: true`, la pastilla de la barra en verde —
mientras no recibe nada en silencio, porque nadie volvió a registrar el
webhook. `wa-heal` cierra ese hueco solo, con un timer, sin que nadie
tenga que notar que el stack se veía bien pero no lo estaba y correr
`wa-up` a mano. `wa-up` mismo sigue llamando al mismo script `wa-heal`
(no una segunda copia de la lógica) justo después de levantar los
contenedores, así que un `wa-up` recién corrido te sigue dando una
respuesta inmediata.

`wa-up` no llama a `wa-heal` apenas la API de wuzapi contesta: las
credenciales de una sesión ya emparejada sobreviven a recrear los
contenedores, pero wuzapi igual tiene que reconectarse a los servidores de
WhatsApp, y `/session/status` contesta mucho antes de que esa reconexión
termine. Sanar demasiado temprano vería `loggedIn: false` y decidiría
(correctamente) que no hay nada que hacer — saltándose el único reasserto
que importa. Por eso `wa-up` espera (hasta 30s) a que la sesión vuelva a
loguearse antes de pasarle la posta a `wa-heal`, y te dice cuál de dos
cosas distintas pasó:

- **Nunca se emparejó nada** — `wa-up` lo dice con calma y te apunta a
  `wa-pair <account-id> <número>`. No es un problema.
- **Una sesión emparejada no volvió a loguearse en 30s** — `wa-up` dice
  que el webhook no se pudo reafirmar, que los mensajes entrantes se van a
  perder hasta que se reafirme, y que el timer de `wa-heal` va a reintentar
  dentro de cinco minutos, o que podés correr `bin/wa-heal` vos mismo ya.

**Verificar que está funcionando:**

```sh
systemctl --user status wa-heal.timer      # ¿está agendado el timer?
journalctl --user -u wa-heal.service -n 20 # ¿qué reportaron las últimas corridas?
```

Una corrida sana loguea `wa-heal: webhook healthy (...)` o un no-op de
"nada que sanar". Un reintento fallido loguea un
`wa-heal: FAILED to re-assert the webhook` bien visible en el stderr de la
unidad (capturado por journald), y el servicio termina con código
distinto de cero, lo que `systemctl --user status` va a mostrar como una
última corrida fallida.

**Desactivarlo:**

```sh
systemctl --user disable --now wa-heal.timer
```

Igual podés correr `./bin/wa-heal` a mano cuando quieras, o volver a
correr `./bin/wa-up` después de un reinicio, como antes.

`--restart unless-stopped` (no `always`) es deliberado: este stack tiene
un vínculo vivo con tu sesión personal de WhatsApp, así que un
`docker stop` hecho a propósito debe quedarse detenido, no resucitar
solo en el próximo arranque. Reexaminado ahora que existe `wa-heal`:
`unless-stopped` sigue siendo la decisión correcta, y con más razón
todavía — que el contenedor vuelva a estar arriba es una condición
necesaria para que `wa-heal` pueda sanar algo; una política que lo
dejara caído significaría que el timer no tiene nada contra qué
reafirmar.

**`--restart unless-stopped` por sí solo no hace que el stack vuelva
después de un reboot** a menos que el propio demonio de Docker esté
configurado para arrancar al boot. Eso es un cambio a nivel de sistema,
hecho con `sudo`, fuera de este repo:

```sh
sudo systemctl enable docker
```

Tené en cuenta que habilitar el servicio de Docker así arranca **todos**
los contenedores de la máquina que tengan una política de reinicio, no
solo los dos de este plugin — eso es consecuencia de habilitar el demonio
en sí, no algo que haga este plugin.

## Emparejar

Esto se puede hacer desde la terminal, tal cual abajo, o desde la pantalla
de configuración del panel (el engranaje, ⚙ — "+ Vincular cuenta nueva"),
que corre este mismo script por detrás. Ver "Pantalla de configuración" más
abajo para cómo se ve el camino desde la UI; el comportamiento de fondo no
cambia entre uno y otro.

```sh
./bin/wa-pair <account-id> <número, formato internacional, sin +>
```

`<account-id>` nombra la cuenta que estás emparejando — es lo que
`accounts.json` va a usar para llamar a este número en el resto del
sistema: se convierte en el usuario de wuzapi, en el nombre por defecto del
archivo de tono, y en la etiqueta que se muestra en las badges del panel
(ver "Múltiples cuentas" más abajo). En una instalación nueva, elegí el id
que quieras para tu primer número, por ejemplo:

```sh
./bin/wa-pair personal 5491122334455
```

Un account id solo puede contener letras ASCII minúsculas, dígitos y guion
bajo. Esa restricción existe por una razón concreta, no por estilo: el id
se pasa a mayúsculas para armar el nombre de la variable de entorno que
guarda su token (`WA_TOKEN_<ID>`, por ejemplo `trabajo` → `WA_TOKEN_TRABAJO`).
Si dos ids difirieran solo en mayúsculas/minúsculas — `trabajo` y `Trabajo`
— los dos pasarían a mayúsculas al *mismo* nombre de variable, y el que se
emparejara segundo pisaría en silencio el token del primero en
`secrets.env`; una respuesta pensada para un número podría entonces salir
por el otro sin que nada lo impida. Este fue un defecto real detectado en
revisión, no un caso hipotético, y la regla del charset es lo que lo hace
imposible en vez de simplemente improbable. `wa-pair` rechaza un id
inválido (a través de `wa-reply token-var`, el único lugar donde se
verifica esto) antes de crear un usuario de wuzapi, escribir un token o
tocar `accounts.json` — un id rechazado nunca deja una cuenta a medio
crear.

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
history purged: 14832 row(s) seen for trabajo, table now empty.
```

**2. No salió bien.**

```
WARNING: history may NOT have been fully cleared.
  History was still arriving when purge ran: settled=False
  Rows seen for trabajo before restart: 9021, rows remaining (all accounts): 342
  Please run 'docker restart omarchy-wa-sink' in a minute and check again.
```

Hacé exactamente eso: esperá un minuto, corré `docker restart
omarchy-wa-sink` vos mismo, y volvé a revisar. El sink purga la tabla de
historial cada vez que arranca, así que un segundo reinicio después de que
el volcado haya terminado de verdad debería dejar la tabla vacía. No des
por terminado el emparejamiento hasta confirmar eso. Notá que la cantidad
de filas restantes es la de **todas** las cuentas juntas, no solo la que
acabás de emparejar — la purga siempre vacía la tabla entera, que es
compartida.

**3. No puede saberlo.**

```
history: no rows for trabajo were ever observed in wuzapi's
  message_history table. That may mean this account has no
  history to sync, or it may mean WhatsApp had not started
  sending it yet. This run cannot tell which. Nothing was
  confirmed purged for this account, because nothing was ever seen.
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

## Múltiples cuentas

Podés vincular más de un número de WhatsApp a la misma instalación — por
ejemplo un número personal y uno de trabajo aparte, cada uno respondido con
su propia voz. Emparejá un segundo número exactamente igual que el
primero, con un account id nuevo:

```sh
./bin/wa-pair trabajo 5491122223333
```

Eso es todo el procedimiento — `wa-pair` crea un usuario nuevo en wuzapi,
siembra una entrada para `trabajo` en `accounts.json`, y copia
`agents/respuesta.md` a `agents/trabajo.md` como archivo de tono inicial,
todo esto antes de guiarte para vincular el teléfono de la misma forma
descrita arriba.

**Dónde viven los ajustes por cuenta.** Cada cuenta configurada es una
entrada en `~/.local/share/omarchy-wa/state/accounts.json`, una lista JSON.
Cada entrada tiene:

- `id` — el account id que le diste a `wa-pair`.
- `label` — se muestra en las badges del panel y en la línea "sale desde";
  por defecto es el id con los guiones bajos convertidos en espacios y con
  la primera letra de cada palabra en mayúscula (`trabajo` → `Trabajo`), y
  podés cambiarla a lo que quieras.
- `wuzapi_user` — el nombre del usuario de wuzapi al que corresponde esta
  cuenta; esto es lo que llega como `instanceName` del webhook y es cómo el
  sink sabe a qué cuenta pertenece un mensaje. Normalmente es igual a `id`.
- `agent` — el nombre del archivo, bajo `agents/`, que es el tono de esta
  cuenta — las instrucciones que recibe `claude -p` al redactar una
  respuesta para esta cuenta. Editá `agents/<ese archivo>` para cambiar
  cómo responde esta cuenta.
- `quick_replies` — las respuestas rápidas propias de esta cuenta, una
  lista simple de strings que se muestran como botones en el panel cuando
  está abierto un chat de esta cuenta. Editá este arreglo a mano para
  agregar, cambiar o borrar una; no hace falta reiniciar nada, el panel lo
  relee (vía `wa-reply quick`) cada vez que abrís un chat.

Para cambiarle el nombre a una cuenta, editá su entrada en `accounts.json`
directamente y guardá — hoy no hay un comando para esto. Su tono y sus
respuestas rápidas también se pueden editar a mano de la misma forma (el
archivo que indica su campo `agent`, y su array `quick_replies`
respectivamente), o desde la pantalla de configuración del panel (el
engranaje, ⚙ — ver "Pantalla de configuración" más abajo), que lee y
escribe exactamente los mismos archivos vía `wa-reply tone-get`/`tone-set`
y `quick-get`/`quick-set`. Ninguna de las dos vías es una capa de
almacenamiento distinta; la pantalla es otra puerta a los mismos archivos.

**`groups_enabled` no es por cuenta.** Se queda exactamente donde siempre
estuvo, en `~/.local/share/omarchy-wa/state/config.json`, compartido por
todas las cuentas (ver "Habilitar un grupo" más abajo). Eso es deliberado,
no un descuido: el JID de un grupo es único en todo WhatsApp, y un grupo
dado o tiene a alguna de tus cuentas como miembro o no la tiene — no hay
ningún escenario donde el mismo JID necesite habilitación distinta según
la cuenta, así que separar este ajuste por cuenta solo agregaría un lugar
donde el mismo JID podría contradecirse a sí mismo.

**En el panel:** cada chat en la cola lleva una pequeña badge con el
nombre de la cuenta a la que llegó, y una vez que abrís un chat, la vista
de detalle dice explícitamente por qué número va a salir la respuesta
("La respuesta va a salir desde: …") — nunca una elección, porque una
respuesta siempre sale por el número al que llegó (ver "Qué hace, en
resumen" más arriba).

### Migrar desde una instalación de una sola cuenta

Si estás actualizando una instalación de antes de multi-cuenta, no tenés
que hacer nada a mano: corré `./bin/wa-up`. En ese orden, hace:

1. reconstruye la imagen `omarchy-wa-sink` desde este repo (siempre, ver
   "Qué se construye" más arriba), así el sink que va a arrancar es el
   nuevo;
2. **baja los dos contenedores** (`docker rm -f`), para que nada esté
   escribiendo tu estado mientras cambia de forma. Por eso la migración
   es segura: un mensaje que llegara en el medio lo escribiría el sink
   viejo todavía vivo *después* de que la cola fue reescrita, y quedaría
   con el formato viejo para siempre, porque la migración corre una sola
   vez;
3. migra tu instalación existente al formato `accounts.json`;
4. vuelve a levantar los dos contenedores, el sink ya con la imagen nueva.

El puente queda abajo lo que duran los pasos 2 a 4 (segundos), y los
mensajes que lleguen en esa ventana los entrega WhatsApp cuando vuelve a
subir, igual que después de cualquier reinicio. Vas a ver una de estas
dos líneas:

```
migrated to accounts.json
```
o, en cada corrida después de la primera:
```
accounts.json already present
```

La migración crea una única cuenta llamada `personal` (`wuzapi_user:
omarchy`, igual al usuario de wuzapi que tu instalación ya tenía), copia tu
`WA_TOKEN` existente a `WA_TOKEN_PERSONAL` en `secrets.env` (el `WA_TOKEN`
original se deja intacto), mueve las `quick_replies` que ya tenías en
`config.json` a esa cuenta, y mueve tus archivos de contexto por chat
existentes a `state/ctx/personal/`. **Tus conversaciones pendientes se
preservan**: cada entrada de la cola que ya estaba en `queue.json` se
reescribe para llevar `"account": "personal"` en vez de descartarse o
reiniciarse, y ninguna de tus respuestas rápidas ni tu historial de
contexto se pierde en el proceso. `groups_enabled` en `config.json` queda
exactamente donde estaba.

Es seguro correrla más de una vez y seguro interrumpirla: es un no-op una
vez que existe `accounts.json`, y si se interrumpe a la mitad (un cuelgue a
mitad de `wa-up`), el próximo `wa-up` retoma desde donde quedó en vez de
rehacer pasos ya terminados o perder algo.

## Pantalla de configuración

El engranaje (⚙) en la esquina superior derecha del panel abre una pantalla
de configuración incrustada en el mismo panel; el mismo botón, ahora
rotulado "Volver", la cierra y vuelve a la cola de mensajes exactamente
donde la dejaste. Lista todas las cuentas configuradas, y desde ahí podés
emparejar una nueva, editar el tono o las respuestas rápidas de una cuenta
existente, o desvincularla. Nunca envía un mensaje — no hay ningún camino
de envío en la pantalla de configuración, solo los subcomandos de lectura y
escritura de tono y respuestas rápidas de `wa-pair`/`wa-unlink`/`wa-reply`
— y sigue corriendo un emparejamiento o una desvinculación en segundo plano
aunque cierres la pantalla de configuración o todo el panel mientras uno
está en curso.

Arriba de la lista de cuentas, un recuadro "Caché de adjuntos" muestra
cuánto disco ocupa la caché de adjuntos (según `bin/wa-media size`) y un
botón **Vaciar caché** que la vacía, para todas las cuentas, de un clic y
sin paso de confirmación — ver "Adjuntos" más abajo.

### Emparejar desde la UI

"+ Vincular cuenta nueva" pide lo mismo que el comando de terminal — un id
de cuenta y un número de teléfono — y después corre `wa-pair` en modo
`--json` y muestra su progreso etapa por etapa:

1. **Validando** el id de cuenta.
2. **El código** — el mismo código de 8 dígitos que `wa-pair` imprime en la
   terminal, mostrado grande, con las mismas instrucciones: WhatsApp >
   Dispositivos vinculados > Vincular un dispositivo > Vincular con número
   de teléfono.
3. **Vinculado. Confirmando el webhook…**
4. **Esperando que WhatsApp termine de mandar el historial** — la espera
   del volcado de historial descrita en "Emparejar" más arriba. Puede
   tardar varios minutos; la pantalla lo dice y se queda abierta. No pasa
   nada raro si tarda un rato acá.
5. Uno de los mismos tres resultados de historial que `wa-pair` siempre
   reporta (ver "Emparejar" más arriba), mostrado como texto simple en vez
   de algo que tengas que interpretar de una terminal:
   - **purgado y verificado** — se vio historial y se confirmó que quedó
     vacío;
   - **quedan filas** — el historial puede no haberse borrado del todo,
     mostrado como advertencia, con la misma indicación de "reiniciá el
     sink y volvé a chequear";
   - **nunca se vio una fila** — también mostrado como advertencia, nunca
     como éxito. Es incertidumbre honesta, no un resultado vacío
     confirmado: puede significar que esta cuenta no tiene historial, o que
     WhatsApp todavía no había empezado a mandarlo, y la pantalla dice
     exactamente eso en vez de elegir una de las dos.

Si `wa-pair` se corta a la mitad (un reinicio de la barra, un crash) antes
de reportar alguno de los tres resultados, la pantalla lo dice claramente
en vez de adivinar — no sabe si la cuenta terminó vinculada, y te pide que
lo chequees a mano (la lista de cuentas, o `docker logs omarchy-wa-sink`)
antes de reintentar.

**El camino de terminal sigue funcionando, sin cambios, y no está
deprecado:**

```sh
./bin/wa-pair <account-id> <número, formato internacional, sin +>
```

Su modo `--json` (`./bin/wa-pair <account-id> <número> --json`, un objeto
JSON por línea) es exactamente lo que la pantalla de configuración corre y
consume — no hay un camino de emparejamiento separado, exclusivo de la UI.

Una pantalla de configuración que hace más fácil emparejar no hace más
segura la operación de emparejar. Si acaso, baja la fricción de una acción
que carga el riesgo de baneo descrito al principio de este README — leé esa
advertencia antes de usar cualquiera de los dos caminos, terminal o UI.

### Desvincular

"Desvincular" junto a una cuenta pide una confirmación escrita: tenés que
tipear el id de la cuenta exactamente antes de que se habilite el botón.
Antes de pedirla, calcula — en ese momento, nunca desde una copia guardada
de cuando se abrió la pantalla — cuántas conversaciones pendientes de esa
cuenta está por descartar, y lo dice en el texto de confirmación. Se niega
a seguir si ese número no se pudo calcular, así que nunca te pide confirmar
contra un número desconocido.

Esas conversaciones pendientes se descartan, no se conservan, porque
`bin/wa-reply` se niega para siempre a contestar una entrada de la cola
cuya cuenta ya no existe (ver "Qué hace, en resumen" más arriba) —
dejarlas en la cola después de que la cuenta desaparece las dejaría
varadas ahí, visibles y sin poder responderse nunca. Descartarlas al
desvincular es el resultado honesto, no una limitación a esquivar.

**Desvincular no borra el archivo de tono** en `agents/<account-id>.md`.
Eso es deliberado: no tiene ninguna credencial, es prosa que escribiste y
editaste a mano, y volver a emparejar el mismo id de cuenta más adelante te
lo devuelve en vez de hacerte escribirlo de nuevo.

**Desvincular tampoco vacía la caché de adjuntos** de esa cuenta
(`media/<account-id>/`, ver "Adjuntos" más abajo). Vaciala con **Vaciar
caché** (todas las cuentas) o con `./bin/wa-media clear --account
<account-id>` (solo esa).

**Desvincular sí borra el usuario de wuzapi** de esa cuenta, no solo su
sesión. Eso es lo que hace que desvincular y volver a vincular el mismo id
funcione: un usuario que queda en wuzapi sigue siendo dueño del token
viejo, así que el siguiente `wa-pair` para ese id genera un token nuevo,
recibe un "already exists" al crear el usuario, y después falla en cada
llamada con un token que wuzapi no reconoce. Si el usuario no se puede
borrar — wuzapi inalcanzable, o sin `WUZAPI_ADMIN_TOKEN` en el archivo —
la limpieza local igual termina (un contenedor caído nunca puede dejar una
cuenta imposible de sacar de acá), pero la pantalla lo dice explícitamente
en vez de informar una desvinculación limpia, porque ese usuario que quedó
es justamente lo que te rompería el próximo intento de vincular ese id.

**Si un paso falla, la desvinculación se detiene ahí y dice cuál.** Cada
paso local — `accounts.json`, el token, el anillo de contexto, las
entradas de la cola — se verifica, y una falla (un directorio de estado
sin permiso de escritura, un disco lleno) termina la corrida con un error
que nombra ese paso, en vez de anunciar "desvinculada" sobre una cuenta
que sigue en disco. Sacar la cuenta de `accounts.json` es el punto de
commit: una falla antes no cambia nada, y una falla después deja una
cuenta que `wa-reply` ya rechaza como desconocida, así que volver a correr
`./bin/wa-unlink <account-id>` una vez arreglada la causa termina el
trabajo.

El comando equivalente en terminal:

```sh
./bin/wa-unlink <account-id>
```

con `--json` (el mismo vocabulario de eventos que `wa-pair --json`, y el
transporte que usa la propia pantalla de configuración) y `--count-only`
(imprime solo la cantidad de conversaciones a descartar para esa cuenta y
no cambia nada — lo primero que calcula el diálogo de confirmación de
arriba). Ver "Desvincular" más abajo para qué hace y qué no hace del lado
de la lista de dispositivos de WhatsApp, y para tirar abajo toda la
instalación en vez de una sola cuenta.

### Editar tono y respuestas rápidas

"Tono" y "Respuestas rápidas" abren un editor para el archivo de tono de
esa cuenta (`agents/<account-id>.md`) y su lista `quick_replies` en
`accounts.json`, respectivamente. Los dos siguen siendo archivos comunes,
editables a mano — ver "Múltiples cuentas" más arriba — la pantalla de
configuración es otra puerta a los mismos archivos, no un reemplazo de
editarlos directamente.

**Conflictos al guardar.** Los dos editores leen el contenido del archivo y
un hash de ese contenido al abrirse, y mandan ese hash de vuelta al
guardar. Si el archivo cambió en disco desde que se leyó, el guardado se
rechaza directamente y no se escribe nada — nunca se combina, nunca se
sobrescribe en silencio. Usá "Recargar" para traer el contenido actual y
volver a aplicar tu cambio. "Recargar" descarta lo que haya en el cuadro,
así que con cambios sin guardar pregunta primero: el botón pasa a decir
"¿Descartar cambios?" y recién el segundo clic recarga.

El editor de tono arranca vacío y su botón "Guardar" queda deshabilitado
hasta que el texto de esa cuenta llegó de verdad. Abrir Tono para una
cuenta, volver, y abrirlo para otra no muestra el texto de la primera
debajo del título de la segunda: no muestra nada hasta que llega el suyo.

La protección de respuestas rápidas es más burda que la del tono: calcula
el hash de *todo* el archivo `accounts.json`, no solo de la entrada
`quick_replies` de esa cuenta, así que un cambio a mano (u otro guardado
desde la pantalla de configuración) a una cuenta *distinta*, entre que se
abrió esta pantalla y se guarda, también puede bloquear este guardado,
aunque nada de la cuenta que estás editando haya cambiado en realidad. Esto
nunca pierde datos — solo te obliga a recargar y reintentar — pero es más
burdo de lo que hace falta, y el propio texto de la pantalla lo dice.

## Habilitar un grupo

Los chats de grupo se ignoran por defecto. `groups_enabled` es compartido
por todas las cuentas configuradas, no se setea por cuenta — ver
"Múltiples cuentas" más arriba para el motivo. Para dejar pasar uno, agregá su
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

## Respuestas rápidas

La vista de detalle del panel muestra una fila de botones de respuesta
rápida arriba del cuadro de redacción. **Tocar uno manda ese texto tal
cual, al instante — no hay confirmación, no hay deshacer, no hay borrador
para revisar antes.** No es un atajo para llenar el cuadro; el mensaje
sale con ese clic. Quedan deshabilitados mientras hay un borrador o un
envío en curso, y una entrada cuyo último envío quedó sin confirmar
(`unknown`/`sending`) también exige el mismo reconocimiento que pide
`Enviar` antes de dejar pasar una respuesta rápida — porque esa guarda
vive del lado de `wa-reply send`, no en el botón.

Las respuestas rápidas son por cuenta: la lista propia de cada cuenta vive
en su campo `quick_replies` dentro de
`~/.local/share/omarchy-wa/state/accounts.json` (ver "Múltiples cuentas"
más arriba), como una lista simple de strings, por ejemplo:

```json
[{"id": "personal", "label": "Personal", "wuzapi_user": "omarchy",
  "agent": "respuesta.md",
  "quick_replies": [
    "Hola, soy el asistente AI de Cristóbal, en estos momentos no puede responder pero responderá en breve.",
    "Gracias, te respondo apenas pueda."
  ]}]
```

`wa-pair` siembra el `quick_replies` de una cuenta nueva con una entrada
por defecto cuando la crea; nunca toca la lista de una cuenta que ya
existe. Una instalación migrada desde una sola cuenta conserva sus
`quick_replies` anteriores (que estaban en `config.json`) trasladadas a la
cuenta `personal` — ver "Migrar desde una instalación de una sola cuenta"
más arriba.

Para agregar, editar o borrar una respuesta rápida de una cuenta dada:
editá el arreglo `quick_replies` de esa cuenta en `accounts.json` a mano
(agregá un string, cambiá uno, o borrá uno) y guardá. No hace falta
reiniciar nada — el panel lo relee con `wa-reply quick` cada vez que abrís
un chat que pertenece a esa cuenta.

## El extracto de la conversación

Elegir un chat en la cola no te lleva directo al compositor. Abre una
pantalla intermedia, "Extracto de la conversación", con los mensajes
recientes del chat, del más viejo al más nuevo: los del otro a la
izquierda con el nombre del contacto, los tuyos a la derecha con "Vos".
**Responder** es la única forma de pasar de ahí al compositor, y
**Volver** vuelve a la lista; el Volver del compositor (o Escape) vuelve
al extracto, no a la lista, así que salir a mitad de un borrador no te
hace perder qué chat tenías abierto. Leer y escribir son pasos separados a
propósito.

El extracto es el mismo anillo de contexto por chat que usa la redacción —
como mucho los últimos 10 mensajes, por chat y por cuenta
(`state/ctx/<account-id>/`, ver "Dónde vive el estado" más abajo) — leído
con `wa-reply thread <entry-id>`, que le pasa al panel solo la dirección y
el texto de cada mensaje. De qué lado va cada mensaje sale de una marca
`direction` explícita guardada con él, nunca de comparar nombres: un
contacto se puede llamar igual que vos.

Tu lado de la conversación llega por un solo camino: el sink registra cada
mensaje que WhatsApp informa como enviado **por vos**, desde el
dispositivo que sea — el teléfono, WhatsApp Web, ZapZap o este plugin (sus
propios envíos le vuelven al sink como ese mismo eco; `wa-reply` ya no
escribe en el anillo). Por eso un dry run nunca aparece como enviado:
no se envió nada. El prompt de redacción también ve los dos lados, con
"Yo" para los tuyos, así que Claude redacta sobre la conversación real en
vez de adivinar qué dijiste.

Dos cosas para tener en cuenta:

- **El anillo solo tiene lo que pasó desde que el plugin está corriendo.**
  Para una conversación más vieja, el extracto va a estar vacío o casi, y
  el panel lo dice en lugar de mostrar una pantalla en blanco — es el caso
  normal, no una falla. No se reconstruye nada desde el historial de
  WhatsApp (ese historial es justamente lo que `wa-pair` y el sink purgan,
  ver "Emparejar").
- **Lo que mandás ahora queda guardado localmente**, junto con lo que
  recibís: el anillo guarda hasta 10 mensajes de cada chat en las dos
  direcciones, en JSON plano bajo `state/ctx/`. Desvincular una cuenta
  borra su anillo.

Si el último envío del chat quedó sin confirmar (`unknown`/`sending`, ver
"Envíos cuyo resultado nadie conoce" más abajo), el aviso y su botón
*"Revisé WhatsApp, quiero reenviar"* están en el extracto, y
**Responder** queda deshabilitado hasta que lo apretás.

## Adjuntos

Cuando alguien manda una foto, un audio, un video, un sticker o un
documento, la cola sigue mostrando el marcador (`[imagen]`, `[audio]`,
...), y el extracto muestra un bloque "Adjunto" con un botón:

- **Imágenes y stickers** — **Ver imagen** la descarga y la muestra ahí
  mismo, ajustada al ancho del panel (y con un alto máximo); se decodifica
  más o menos al tamaño en que se muestra, no a resolución completa.
- **Audios y notas de voz** — **Reproducir** lo descarga y lo reproduce en
  el panel mismo, con pausa, tiempo transcurrido/total y una barra para
  adelantar o volver.
- **Videos y documentos** — **Abrir** lo descarga y le pasa el archivo a
  tu visor del sistema con `xdg-open`. El panel espera el código de salida
  de `xdg-open` antes de decir "Se abrió con el visor del sistema."; si
  falla, lo dice y vuelve a ofrecer el botón.
- **Ubicaciones y contactos** — no hay nada que descargar (wuzapi no tiene
  endpoint para eso), y el panel lo dice en vez de mostrar un botón que
  nunca podría funcionar. Lo mismo para un adjunto cuyo evento llegó sin
  algún campo que la descarga necesita.

Una descarga que falla dice por qué y el botón pasa a **Reintentar**. Un
archivo que estaba en la caché pero ya se podó se informa igual, nunca
como una imagen rota. Solo se puede abrir el adjunto de la entrada de la
cola en sí — el último mensaje de ese chat —; los mensajes anteriores del
extracto se ven como su texto marcador. Ver es todo lo que hace: no hay
forma de *mandar* un adjunto desde el panel.

**Quién descarga.** `bin/wa-media`, nunca el sink: el sink sigue sin
tener credenciales. Igual que `wa-reply`, toma la cuenta de la entrada de
la cola y de nada más — ni flag, ni variable de entorno —, usa el token de
esa cuenta, rechaza una entrada cuya cuenta no está en `accounts.json`, y
solo habla con wuzapi en `127.0.0.1`. El modo dry-run no aplica acá:
controla el envío, y descargar no envía nada.

```sh
./bin/wa-media get <entry-id>             # descarga (o reusa) e imprime la ruta
./bin/wa-media size [--account <id>]      # bytes que ocupa la caché en disco
./bin/wa-media clear [--account <id>]     # vacía toda la caché, o la de una cuenta
```

### La caché, y lo que significa para la privacidad

Los adjuntos descargados quedan en:

```
~/.local/share/omarchy-wa/media/<account-id>/
```

(bajo `$WA_STATE_ROOT` si lo moviste). Los directorios tienen modo 0700,
los archivos 0600. Los nombres de archivo son hashes, no ids de mensaje
ni números de teléfono. `media/` está al lado de `state/`, no adentro, así
que no se monta en ningún contenedor.

**Estos archivos quedan guardados en disco sin cifrar.** Eso incluye
fotos, audios y documentos que te mandan terceros que no tienen idea de
que este plugin existe. Este proyecto se esfuerza en purgar el historial
de texto de WhatsApp; la caché de adjuntos va en la dirección contraria, y
fue una decisión deliberada del dueño, tomada viendo exactamente este
costo — no un descuido. Si no la querés, vaciá la caché y no aprietes los
botones.

La caché tiene límites:

- **30 días** de antigüedad máxima y **500 MiB** en total, por defecto.
  Los dos se pueden cambiar en `state/config.json` con
  `media_cache_max_age_days` y `media_cache_max_bytes` (un valor ausente,
  cero, negativo o no numérico vuelve al default).
- La poda corre **después de cada descarga nueva** — no con un timer —:
  primero borra lo que pasó el límite de antigüedad, después lo más viejo
  hasta que el total entre. El archivo recién descargado nunca se poda,
  así que la caché puede pasarse del límite por ese único archivo. Entre
  descargas, un archivo viejo sigue en disco hasta la próxima descarga o
  hasta que vacíes la caché.
- Pedir dos veces el mismo adjunto reusa el archivo en caché en vez de
  descargarlo de nuevo.

Para vaciarla: el botón **Vaciar caché** de la pantalla de configuración
(toda la caché, todas las cuentas, sin confirmación), o
`./bin/wa-media clear` — con `--account <id>` para vaciar solo la de una
cuenta. `./bin/wa-media size` te dice cuánto ocupa ahora. Desvincular una
cuenta **no** vacía sus adjuntos; para eso corré
`./bin/wa-media clear --account <id>`.

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
todavía no leíste. (Hay un hueco en esto, por el vaciado automático de
abajo: ver el último punto de la sección siguiente.)

## Qué saca un chat de la cola

Un chat sale de la cola de tres formas, no de una sola:

- **Lo contestás desde el panel** y el envío se confirma (con la excepción
  de la sección anterior).
- **Lo contestás desde otro dispositivo** — el teléfono, WhatsApp Web,
  ZapZap. WhatsApp le avisa tu propio mensaje a cada dispositivo
  vinculado; el sink lo registra en el extracto como tuyo y saca la
  entrada de ese chat.
- **Lo leés en otro dispositivo.** Cuando otro de tus dispositivos marca
  el chat como leído, WhatsApp manda un acuse de lectura y el sink saca la
  entrada de ese chat — **aunque no lo hayas contestado**. Es deliberado:
  el dueño eligió una cola corta sabiendo que un mensaje que leíste y
  pensabas contestar después también desaparece de ella. Si usás la cola
  como lista de pendientes, no abras esos chats en el teléfono, o dejalos
  sin leer ahí.

Los acuses de lectura viajan en las dos direcciones, y solo una cuenta. El
sink actúa únicamente sobre el estado `ReadSelf` de wuzapi — *vos* leíste
el mensaje *del otro* en otro dispositivo. El estado `Read` a secas — *el
otro* leyó *tu* mensaje — nunca saca nada; si no, cualquier contacto que
abra tu mensaje vaciaría justo el chat que está esperando su respuesta.

Lo que **nunca** se saca solo: una entrada cuyo último envío quedó en
`unknown` o `sending` (ver "Envíos cuyo resultado nadie conoce" más
arriba). Esa entrada es el único registro de que un mensaje puede haber
salido sin confirmación; se queda, intacta, ante un acuse de lectura y
ante un mensaje tuyo, hasta que la resuelvas en el panel.

Un acuse de lectura o un mensaje tuyo para un chat sin entrada no hace
nada, y uno que llega por un número que no está en `accounts.json` se
descarta y se registra en el log una vez. Los mensajes entrantes nuevos se
encolan igual que antes.

Los acuses de lectura solo le llegan al sink si el webhook de la cuenta
está suscrito a ellos: `wa-pair` suscribe las cuentas nuevas a `Message` y
`ReadReceipt`, y `wa-heal` reafirma esa misma suscripción en cada
corrida, así que una instalación ya emparejada la toma en el próximo tick
del timer sin volver a emparejar.

Un hueco conocido: los envíos del propio plugin también le vuelven al sink
como un mensaje tuyo, y ese eco saca la entrada del chat como cualquier
otro. Si llega después de que el envío se confirmó, puede sacar una
entrada que "Un mensaje que llega mientras estás redactando" más arriba
dejó a propósito, así que un mensaje que llegó mientras redactabas puede
igual salir de la cola. Después de enviar, si el chat estaba activo, mirá
WhatsApp.

## Dónde vive el estado, y cómo borrarlo

Todo vive bajo `~/.local/share/omarchy-wa/` (modo 700):

```
~/.local/share/omarchy-wa/
├── secrets.env     modo 600 — NO se monta en ningún contenedor
├── state/          se monta en el sink como /state
│   ├── queue.json
│   ├── accounts.json
│   ├── ctx/
│   │   └── <account-id>/
│   └── config.json
├── media/          caché de adjuntos — NO se monta en ningún contenedor
│   └── <account-id>/
└── wuzdata/        se monta en los dos contenedores
```

- `secrets.env` — el token de admin, la clave de cifrado, y un token de API
  por cada cuenta emparejada (`WA_TOKEN_<ID>`, por ejemplo
  `WA_TOKEN_PERSONAL`, `WA_TOKEN_TRABAJO`), más (si lo pusiste)
  `WA_DRY_RUN=0`. Modo 600. Está **afuera** de `state/` a propósito: el
  sink es el contenedor que recibe lo que llega de internet, y no tiene
  por qué tener una credencial que pueda mandar mensajes en tu nombre. No
  está montado en ningún lado.
- `state/queue.json` — los mensajes pendientes que se ven en el panel,
  cada uno con la cuenta a la que llegó.
- `state/accounts.json` — las cuentas configuradas: una entrada por
  número, ver "Múltiples cuentas" más arriba.
- `state/ctx/<account-id>/` — un archivo JSON chico por chat, por cuenta:
  los últimos 10 mensajes en las dos direcciones, usados al redactar y
  mostrados en el extracto (ver "El extracto de la conversación" más
  arriba).
- `state/config.json` — `groups_enabled` (compartido por todas las
  cuentas), los límites opcionales de la caché de adjuntos
  `media_cache_max_bytes` y `media_cache_max_age_days`, y cualquier otro
  ajuste editado a mano, a nivel de toda la instalación. Las respuestas
  rápidas ya no están acá — viven por cuenta en `accounts.json`.
- `media/<account-id>/` — los adjuntos descargados, **sin cifrar**, hasta
  500 MiB / 30 días por defecto. Solo los escribe `bin/wa-media`, en el
  host; se vacía con `./bin/wa-media clear` o con **Vaciar caché** en la
  pantalla de configuración (ver "Adjuntos" más arriba).
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

Para sacar **una sola** cuenta y dejar el resto de la instalación
funcionando, usá:

```sh
./bin/wa-unlink <account-id>
```

o el botón "Desvincular" de la pantalla de configuración (ver "Pantalla de
configuración" más arriba para el flujo de confirmación, qué descarta, y
por qué). Esto cierra la sesión de esa cuenta en wuzapi, borra el usuario
de wuzapi de esa cuenta (para que volver a vincular el mismo id después
funcione en vez de chocar con el viejo), la saca de `accounts.json`, borra
su token de `secrets.env`, borra su anillo de contexto, y descarta sus
entradas pendientes en la cola — pero deja a propósito
`agents/<account-id>.md` (su archivo de tono) intacto. También deja en
disco la caché de adjuntos de esa cuenta (`media/<account-id>/`); vaciala
con `./bin/wa-media clear --account <account-id>`. Los dos pasos
contra wuzapi son best-effort, porque un contenedor inalcanzable no puede
dejar una cuenta imposible de sacar localmente; que cualquiera de los dos
falle se informa, nunca se traga en silencio. No saca
por sí solo el dispositivo de la lista de "Dispositivos vinculados" de
WhatsApp; hacelo también en el teléfono si querés que WhatsApp considere
desvinculado el número, no solo esta instalación.

Para tirar abajo **toda** la instalación — todas las cuentas emparejadas
de una, no solo una — los `docker rm -f`/`rm -rf` de abajo siguen siendo
el camino:

1. En **cada número de teléfono** que emparejaste: **WhatsApp >
   Dispositivos vinculados**, y sacá este dispositivo de la lista. Hacé
   esto primero, para cada cuenta — es el paso que realmente le avisa a
   WhatsApp que el vínculo terminó, y los pasos 2/3 borran el registro
   local de qué números habías emparejado, así que después no queda nada
   acá que te recuerde a qué teléfonos les falta.
2. `docker rm -f omarchy-wuzapi omarchy-wa-sink`
3. `rm -rf ~/.local/share/omarchy-wa`

Saltearse el paso 1 deja el dispositivo listado como vinculado del lado de
WhatsApp aunque ya hayas tirado abajo los contenedores acá.

## Licencia

MIT
