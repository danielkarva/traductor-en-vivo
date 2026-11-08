# Traductor en vivo

Una ventana flotante que **escucha lo que suena en tu PC** (una llamada de Discord, un video, lo que sea), lo **transcribe con Whisper** y te muestra la **traducción en tiempo real**, encima de todo.

Pensado para quien está en llamadas en inglés y le cuesta seguirlas.

- **Varios idiomas a la vez**: detecta el idioma de cada frase (inglés, hindi, griego… unos 100) y lo traduce todo a tu idioma. Cada frase lleva su etiqueta (`EN`, `HI`, `EL`), y lo que ya está en tu idioma se muestra sin traducir.
- Muestra **quién está hablando** en tu canal de voz de Discord, cada persona con su color.
- Cada oración aparece traducida apenas termina, aunque la persona siga hablando.
- Marca en naranja las palabras que Whisper no entendió bien.
- Con **▶** y **🐢** vuelves a escuchar cualquier frase, a velocidad normal o más lenta.
- **No guarda nada** de las conversaciones: ni historial ni grabaciones.

> Solo funciona en **Windows 10/11**.

---

## Requisitos

| | Mínimo | Recomendado |
|---|---|---|
| Sistema | Windows 10 u 11 | |
| Python | 3.10 – 3.13 ([descargar](https://www.python.org/downloads/), marca **"Add python.exe to PATH"**) | 3.12 |
| Tarjeta gráfica | No es obligatoria (sin ella va más lento y algo menos preciso) | NVIDIA con 8 GB o más |
| Disco | ~3 GB | ~8 GB (con NVIDIA) |
| Internet | Para traducir (usa Google Translate) | |

Según tu tarjeta gráfica, el programa elige el modelo solo:

| Tu PC | Modelo |
|---|---|
| NVIDIA con 7 GB o más | `large-v3-turbo` para el texto en vivo + `large-v3`, el más preciso, para la frase final |
| NVIDIA con menos | `large-v3-turbo` para todo |
| Sin NVIDIA | `small` en el procesador, usando solo 4 núcleos para no acaparar el PC |

---

## Instalación

1. Descarga el proyecto: botón verde **Code → Download ZIP** y descomprímelo donde quieras. O usa `git clone`.
2. Haz doble clic en **`instalar.bat`**. Instala todo en una carpeta propia (`.venv`), sin tocar el resto del PC, y crea el acceso directo **"Traductor en vivo"** en el escritorio.
3. Abre **"Traductor en vivo"**. La primera vez descarga el modelo de Whisper (de 1,5 a 4,5 GB), así que ten paciencia.

### ¿Prefieres que lo instale tu IA?
Si usas **Claude Code**, **Codex** u otro asistente que trabaje en tu PC, abre la carpeta del proyecto con él y dile:

> Instala y configura este proyecto siguiendo AGENTS.md. Guíame paso a paso con lo que tenga que hacer yo.

El archivo [`AGENTS.md`](AGENTS.md) le explica a la IA exactamente qué hacer.

---

## Cómo se usa

- **Para abrirlo:** usa el acceso directo **"Traductor en vivo"** del escritorio, o búscalo en el menú Inicio. Si quieres tenerlo siempre a mano, haz clic derecho en su botón de la barra de tareas y elige **Anclar a la barra de tareas**.
- **Mientras está abierto** tiene su **botón en la barra de tareas** y un **ícono junto al reloj**, al lado del micrófono y el volumen:
  - **Clic** en el ícono: muestra la ventana.
  - **Clic derecho**: un menú con *Mostrar ventana*, *Modo burbujas*, *Pausar* y *Salir*.
  - Si no ves el ícono, está escondido detrás de la flecha **^**. Arrástralo afuera para que quede siempre visible.
- La ventana se mueve arrastrando la barra de arriba y se agranda con el **◢** de la esquina.
- **Arriba** queda el historial de la conversación. **Abajo**, en la franja oscura, ves lo que alguien está diciendo en ese momento.
- Si subes con la rueda del ratón para releer, el texto deja de bajar solo. Para volver al final, haz clic en **⬇ nuevas**.

| Botón | Qué hace |
|---|---|
| ⏸ | Pausar o reanudar |
| A− / A+ | Tamaño de la letra |
| EN | Mostrar u ocultar el idioma original |
| ◐ | Transparencia |
| 🔊 | Volver a detectar el audio si cambias de audífonos o parlantes |
| 🗑 | Limpiar la pantalla |
| — | **Minimizar**: la ventana se esconde y cada traducción aparece como una **burbuja** al costado de la pantalla, con el nombre de quien habló, y se desvanece sola. Ideal para jugar: las burbujas no le quitan el foco al juego y los clics pasan a través. Para volver, haz clic en **💬 Traductor** |
| ▶ / 🐢 | Al lado de cada frase: volver a escucharla, normal o más lenta |

---

## Nombres de Discord (opcional)

Para que el traductor diga **quién habla**, se conecta a la app de Discord de tu PC. Discord exige que cada usuario cree su propia "aplicación". Es gratis y toma 5 minutos:

1. Entra a <https://discord.com/developers/applications> con tu cuenta.
2. **New Application**: ponle un nombre (por ejemplo `Traductor`), acepta los términos y dale **Create**.
3. En el menú de la izquierda entra a **OAuth2**:
   - Copia el **Client ID**.
   - Dale **Reset Secret** y copia el **Client Secret**. Discord solo lo muestra una vez.
   - En **Redirects** dale **Add Redirect**, escribe `http://localhost` y guarda con **Save Changes**.
4. Abre `config.json` (en la carpeta del traductor) con el Bloc de notas y pega los dos datos:
   ```json
   "discord_client_id": "TU_CLIENT_ID",
   "discord_client_secret": "TU_CLIENT_SECRET"
   ```
5. Vuelve a abrir el traductor. Discord te mostrará una ventana pidiendo permiso: dale **Autorizar**. Solo la primera vez.

Tienes que usar la **app de escritorio de Discord**, no la versión web. **No compartas tu Client Secret con nadie** y no subas tu `config.json` a ningún lado.

---

## Configuración (`config.json`)

| Clave | Qué hace | Por defecto |
|---|---|---|
| `source_lang` | Idioma que se escucha. `auto` detecta el de **cada frase** y recuerda el de cada persona, así puedes estar con gente que habla inglés, hindi y griego a la vez. También puedes fijar uno (`en`, `fr`…) | `auto` |
| `target_lang` | Tu idioma: todo se traduce a este. Lo que ya esté en tu idioma se muestra tal cual, sin traducir | `es` |
| `expected_languages` | Opcional: los idiomas que se hablan en tu grupo, por ejemplo `["en", "es", "hi", "el"]`. En frases muy cortas Whisper a veces se confunde de idioma; con la lista solo elige entre esos | `[]` |
| `model` / `final_model` | Modelo de Whisper. `auto` lo elige según tu PC | `auto` |
| `glossary` | Palabras que Whisper debe reconocer bien: nombres, juegos, herramientas | `[]` |
| `silence_end` | Segundos de silencio que cierran una frase | `0.6` |
| `max_segment` | Duración máxima de una frase antes de mandarla a traducir | `5` |
| `commit_on_comma` | Mandar a traducir también al llegar a una coma (si van 4 palabras o más), aunque la persona siga hablando. Más rápido, aunque con un poco menos de contexto | `true` |
| `commit_min_seconds` | Pedazo mínimo que se manda a traducir antes de que termine de hablar | `1.5` |
| `doubt_threshold` | Por debajo de esta seguridad, una palabra sale en naranja (de 0 a 1) | `0.35` |
| `bubble_side` | Lado de la pantalla de las burbujas: `right` o `left` | `right` |
| `bubble_top` | Altura donde empiezan las burbujas (0 = arriba, 1 = abajo) | `0.15` |
| `bubble_max` / `bubble_font` | Cuántas burbujas se ven a la vez / tamaño de la letra | `4` / `14` |

> Las burbujas aparecen encima de juegos en **ventana sin bordes** (*borderless*). En **pantalla completa exclusiva** Windows no deja mostrar nada encima: cambia el juego a "ventana sin bordes".

Después de cambiar algo, cierra el traductor con ✕ y vuelve a abrirlo.

---

## Privacidad

- **No se guarda** el texto ni el audio de las conversaciones. El audio de las últimas frases (para ▶) vive solo en la memoria y se borra al cerrar.
- **Whisper corre en tu PC.** Lo único que sale de tu PC es el **texto** que se manda a Google Translate para traducirlo.
- El traductor transcribe a **todas** las personas de la llamada. **Avísale a tu grupo** que lo usas.

---

## Problemas comunes

<details>
<summary><b>La instalación falla con <code>CERTIFICATE_VERIFY_FAILED</code></b></summary>

Tu antivirus (Avast, AVG, Kaspersky, ESET…) intercepta las conexiones seguras. Tienes dos opciones:
- Desactiva el "escaneo HTTPS" del antivirus mientras instalas.
- O exporta el certificado raíz del antivirus y júntalo con los de Python en un archivo `ca-bundle.pem` dentro de la carpeta. `instalar.bat` y el traductor lo usan automáticamente si existe. Tu IA puede hacerlo por ti: ver `AGENTS.md`.
</details>

<details>
<summary><b>No traduce nada / la ventana dice "Escuchando" pero no aparece texto</b></summary>

- El traductor escucha **la salida de audio predeterminada de Windows**. Si Discord suena por otros audífonos, cambia la salida predeterminada o el dispositivo de salida en Discord, y luego toca **🔊**.
- Revisa `traductor.log`, en la carpeta del traductor: ahí quedan los errores.
</details>

<details>
<summary><b>Va lento o se atrasa</b></summary>

Sin tarjeta NVIDIA, Whisper corre en el procesador. Puedes forzar un modelo más pequeño en `config.json`: `"model": "base.en"` (o `"base"` si no escuchas inglés).
</details>

<details>
<summary><b>Discord: "no está abierto" o "desconectado"</b></summary>

Usa la app de escritorio de Discord, no la web, y revisa que el Client ID, el Client Secret y el redirect `http://localhost` estén bien. Si cambiaste el secret, borra `discord_token.json` y vuelve a abrir el traductor.
</details>

---

## Cómo funciona por dentro

- **Audio:** WASAPI loopback, la salida de audio de Windows, con `pyaudiowpatch` y conversión de calidad a 16 kHz con `soxr`.
- **Voz a texto:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Un modelo rápido para el texto en vivo y uno preciso para la frase final, con la seguridad y el tiempo de cada palabra.
- **Quién habla:** Discord RPC local (eventos `SPEAKING_START` y `SPEAKING_STOP`), cruzado con el tiempo de cada palabra, y con el desfase calibrado automáticamente.
- **Traducción:** Google Translate (el servicio gratuito que usa Chrome).

## Licencia

MIT. Ver [LICENSE](LICENSE).
