"""
Traductor en vivo — escucha lo que suena en el PC (Discord, YouTube, lo que sea),
lo transcribe con Whisper (en la tarjeta gráfica NVIDIA si hay) y lo muestra
traducido en una ventana flotante encima de todo.

Configuración: config.json (se crea a partir de config.example.json).
Instrucciones: README.md (personas) y AGENTS.md (asistentes de IA).
"""
import os, sys, re, json, time, queue, threading, datetime, glob, logging, traceback

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "traductor.log")
from logging.handlers import RotatingFileHandler
# registro técnico pequeño (máx ~1 MB) y SIN el texto de las conversaciones
logging.basicConfig(handlers=[RotatingFileHandler(LOG, maxBytes=512_000, backupCount=1,
                                                  encoding="utf-8")], level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# --- Certificados ------------------------------------------------------------
# Algunos antivirus (Avast, Kaspersky…) interceptan HTTPS. truststore hace que Python
# confíe en los certificados de Windows; ca-bundle.pem (opcional) es un plan B.
_bundle = os.path.join(BASE, "ca-bundle.pem")
if os.path.exists(_bundle):
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        os.environ.setdefault(k, _bundle)
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

# --- DLLs de CUDA instaladas por pip -----------------------------------------
for d in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
    try:
        os.add_dll_directory(d)
    except Exception:
        pass
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
# sin consola (pythonw) un error en un hilo desaparece: que quede en el registro
threading.excepthook = lambda a: logging.error(
    "error en hilo %s", a.thread.name if a.thread else "?",
    exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
import tkinter as tk
import pyaudiowpatch as pyaudio
from discord_speakers import DiscordSpeakers, color_for

CONFIG_PATH = os.path.join(BASE, "config.json")
MODELS_DIR = os.path.join(BASE, "modelos")
ICON_PATH = os.path.join(BASE, "icono.ico")

DEFAULT_CONFIG = {
    "geometry": "620x420+60+60",
    "font_size": 15,
    "opacity": 0.90,
    "show_en": True,
    # "auto" elige según la tarjeta gráfica (ver pick_models). Se pueden forzar, p. ej.
    # "large-v3-turbo" / "large-v3" / "small" / "small.en"
    "model": "auto",                 # el rápido, para el texto en vivo
    "final_model": "auto",           # el más preciso, para la frase final
    "doubt_threshold": 0.35,         # palabras con menos seguridad que esto salen en naranja
    "doubt_threshold_first": 0.12,   # la 1.ª palabra de cada frase siempre sale baja en Whisper
    # "(inaudible)": se muestra cuando alguien dijo algo que Whisper no entendió
    # salida de audio a escuchar: "" = la que use Discord (o la de Windows);
    # o parte del nombre, p. ej. "Headphones"
    "audio_device": "",
    "show_inaudible": True,
    "inaudible_min_seconds": 1.2,   # sonidos más cortos (tos, teclado) se ignoran
    # "auto" = detecta el idioma de cada frase (y recuerda el de cada persona)
    "source_lang": "auto",
    # opcional: idiomas que se hablan en tu grupo, p. ej. ["en", "es", "hi", "el"];
    # con la lista, Whisper solo elige entre esos y se equivoca mucho menos
    "expected_languages": [],
    "target_lang": "es",
    "silence_end": 0.6,      # segundos de silencio que cierran una frase
    # mandar a traducir antes de que la persona termine de hablar:
    "commit_min_seconds": 1.5,  # pedazo mínimo
    "commit_on_comma": True,    # cortar también en comas (con 4+ palabras)
    "commit_gap_after": 3.0,    # sin puntuación: cortar en la pausa más larga desde aquí
    "partial_every": 0.6,       # cada cuánto se actualiza el texto en vivo (GPU)
    # palabras que Whisper debe reconocer y la traducción debe respetar
    "glossary": [],
    "max_segment": 5.0,      # una frase nunca pasa de esto
    "min_threshold": 0.004,  # volumen mínimo para considerar que alguien habla
    # burbujas (ventana minimizada)
    "bubble_side": "right",  # "right" o "left"
    "bubble_top": 0.15,      # dónde empieza la columna (fracción del alto de pantalla)
    "bubble_max": 4,         # cuántas a la vez
    "bubble_font": 14,
}

SR = 16000
FRAME = 320  # 20 ms

HALLUCINATIONS = {
    "thanks for watching", "thank you for watching", "thanks for watching!",
    "subtitles by the amara.org community", "please subscribe",
    "like and subscribe", "you", "...", "♪", "bye.",
}

# Whisper aprendió con videos subtitulados de internet y, sobre silencio, ruido o
# música, a veces "rellena" con los créditos que vio miles de veces. Cualquier oración
# que contenga esto se descarta (no se descarta la frase entera, solo esa oración).
HALLU_RE = re.compile(
    r"amara\.org"
    r"|subt[ií]tulos? (?:de|por|realizados|hechos)"
    r"|subtitles? (?:by|made|created)|captions? (?:by|made)"
    r"|transcri(?:bed|ption) by|translat(?:ed|ion) by"
    r"|thanks? (?:you )?(?:so much |very much )?for watching"
    r"|please subscribe|(?:like|don'?t forget to)(?: and)? subscribe"
    # llamados típicos de descripciones de YouTube / redes
    r"|follow (?:me|us) on|s[ií]gue(?:me|nos) en"
    r"|turn on (?:the |your |post )?notifications?|activa (?:la |las )?notificaci"
    r"|hit (?:the|that) (?:bell|like)|ring the bell|dale (?:like|a la campan)"
    r"|link in (?:the )?(?:description|bio)|enlace en la descripci"
    r"|see you (?:guys )?in the next (?:video|one)|nos vemos en el pr[oó]ximo video"
    r"|(?:my|our) (?:instagram|twitter|tiktok|facebook|patreon|channel) (?:is|for)"
    r"|www\.|https?://",
    re.IGNORECASE)

# Una persona hablando nunca "dice" un emoji ni símbolos como ✔ 🔔 ♪: si Whisper los
# escribe, está copiando texto de internet. Esas oraciones se descartan.
EMOJI_RE = re.compile(
    "[←-⇿⌀-⏿①-➿⤀-⥿⬀-⯿"
    "\U0001F000-\U0001FAFF️]")


def is_hallucinated(sentence):
    return bool(HALLU_RE.search(sentence) or EMOJI_RE.search(sentence))


def clean_hallucinated_words(words):
    """words = [(palabra, ...)] → sin las oraciones que son texto inventado.
    Si se quitó algo y lo que sobra son 1-2 palabras sueltas (p. ej. el "Thanks!"
    del final del mismo texto inventado), se descarta todo."""
    out, cur, removed = [], [], False
    for w in words:
        cur.append(w)
        if w[0].strip().endswith((".", "?", "!")):
            if is_hallucinated("".join(x[0] for x in cur)):
                removed = True
            else:
                out += cur
            cur = []
    if cur:
        if is_hallucinated("".join(x[0] for x in cur)):
            removed = True
        else:
            out += cur
    if removed and len(out) <= 2:
        return []
    return out


def clean_hallucinated_text(text):
    parts = re.split(r"(?<=[.!?])\s+", text)
    keep = [p for p in parts if not is_hallucinated(p)]
    if len(keep) < len(parts) and len(" ".join(keep).split()) <= 2:
        return ""
    return " ".join(keep).strip()


# códigos de Whisper que Google escribe distinto
GOOGLE_LANG = {"zh": "zh-CN", "he": "iw", "jw": "jv", "yue": "zh-TW"}


def same_lang(a, b):
    return bool(a and b) and a.split("-")[0].lower() == b.split("-")[0].lower()


def gpu_info():
    """(hay_cuda, GB de memoria de video o None si no se pudo leer)."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            return False, None
    except Exception:
        return False, None
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True, int(out.stdout.strip().splitlines()[0]) / 1024
    except Exception:
        return True, None


def pick_models(cfg):
    """Devuelve (modelo_vivo, modelo_final, device, compute_type, descripción)."""
    en = cfg.get("source_lang", "en") == "en"
    cuda, vram = gpu_info()
    live, final = cfg.get("model", "auto"), cfg.get("final_model", "auto")
    if cuda:
        if live == "auto":
            live = "large-v3-turbo"
        if final == "auto":
            # los dos modelos juntos ocupan ~5 GB; con menos, uno solo para todo
            final = "large-v3" if (vram or 0) >= 7 else live
        ct = "float16" if (vram is None or vram >= 4) else "int8_float16"
        return live, final, "cuda", ct, f"GPU {vram:.0f} GB" if vram else "GPU"
    small = "small.en" if en else "small"
    live = small if live == "auto" else live
    final = live if final == "auto" else final
    return live, final, "cpu", "int8", "CPU (sin tarjeta NVIDIA: más lento)"


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        logging.exception("no se pudo guardar config")


# =============================================================================
# Captura del audio que sale por los parlantes / audífonos (WASAPI loopback)
# =============================================================================
class AudioCapture(threading.Thread):
    """Escucha una salida de audio. Cuál: la que pongas en config ("audio_device"),
    si no la que usa Discord para la llamada, y si no la predeterminada de Windows.
    Si la salida elegida pasa unos segundos sin sonar nada (señal de que el audio
    va por otro lado: Windows cambió la predeterminada, cambiaste de audífonos…),
    vuelve a revisar y se cambia sola."""
    SILENT_RECHECK = 6.0

    def __init__(self, out_q, ui_q, cfg=None, speakers=None):
        super().__init__(daemon=True)
        self.out_q = out_q
        self.ui_q = ui_q
        self.cfg = cfg or {}
        self.speakers = speakers
        self.stop_flag = False
        self.restart_flag = False
        self.current = None
        self.last_chunk = 0.0

    def request_restart(self):
        self.restart_flag = True

    def _wanted_name(self, p):
        manual = (self.cfg.get("audio_device") or "").strip()
        if manual:
            return manual, "config"
        disc = getattr(self.speakers, "output_device", None) if self.speakers else None
        if disc:
            return disc, "Discord"
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        return p.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"], "Windows"

    def _find_loopback(self, p):
        want, why = self._wanted_name(p)
        lbs = list(p.get_loopback_device_info_generator())
        w = want.lower()
        for lb in lbs:
            n = lb["name"].replace(" [Loopback]", "").lower()
            if w == n or w in n or n in w:
                return lb, why
        if why != "Windows":                     # no está: la predeterminada
            logging.warning("no encontré la salida '%s' (%s); uso la de Windows", want, why)
            self.cfg = dict(self.cfg, audio_device="")
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            d = p.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"].lower()
            for lb in lbs:
                if d in lb["name"].lower():
                    return lb, "Windows"
        raise RuntimeError("No encontré la salida de audio " + want)

    def run(self):
        while not self.stop_flag:
            self.restart_flag = False
            p = pyaudio.PyAudio()
            stream = None
            try:
                dev, why = self._find_loopback(p)
                ch = max(1, int(dev["maxInputChannels"]))
                rate = int(dev["defaultSampleRate"])
                # convertidor de calidad (soxr) y continuo: sin saltos entre pedazos
                # y sin ensuciar los agudos (s, f, th) de los que depende Whisper
                import soxr
                rs = soxr.ResampleStream(rate, SR, 1, dtype="float32", quality="HQ")

                def cb(data, frames, info, status):
                    self.last_chunk = time.time()
                    a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                    a = a.reshape(-1, ch).mean(axis=1)
                    out = rs.resample_chunk(a)
                    if len(out):
                        self.out_q.put((time.time(), out.astype(np.float32)))
                    return (None, pyaudio.paContinue)

                stream = p.open(format=pyaudio.paInt16, channels=ch, rate=rate,
                                input=True, input_device_index=dev["index"],
                                frames_per_buffer=int(rate * 0.05),
                                stream_callback=cb)
                name = dev["name"].replace(" [Loopback]", "")
                self.ui_q.put(("device", name))
                if name != self.current:
                    logging.info("Capturando: %s (%d Hz, %d canales) — elegida por %s",
                                 name, rate, ch, why)
                self.current = name
                started = time.time()
                disc_dev = getattr(self.speakers, "output_device", None)
                while not self.stop_flag and not self.restart_flag and stream.is_active():
                    time.sleep(0.2)
                    now = time.time()
                    # Discord cambió de salida → cambiar ya
                    if getattr(self.speakers, "output_device", None) != disc_dev:
                        break
                    # nada suena por aquí hace rato → revisar si el audio va por otro lado
                    if now - max(self.last_chunk, started) > self.SILENT_RECHECK:
                        break
            except Exception as e:
                logging.exception("error de audio")
                self.ui_q.put(("status", f"Error de audio: {e}. Reintentando…"))
                time.sleep(2)
            finally:
                try:
                    if stream:
                        stream.stop_stream(); stream.close()
                except Exception:
                    pass
                p.terminate()


# =============================================================================
# Detecta frases, transcribe con Whisper
# =============================================================================
class Engine(threading.Thread):
    def __init__(self, audio_q, ui_q, translator, cfg, speakers=None):
        super().__init__(daemon=True)
        self.speakers = speakers
        self.audio_q = audio_q
        self.ui_q = ui_q
        self.translator = translator
        self.cfg = cfg
        self.paused = False
        self.model = None
        self.final_model = None
        self.partial_every = float(cfg.get("partial_every", 0.6))
        self.recent_finals = []
        self.last_removed = False
        self._last_inaudible = None
        self.next_id = 0
        self.context = {}            # idioma -> lo último que se dijo (contexto)
        self.lang_by_who = {}        # persona -> último idioma que habló
        self.last_lang = None        # idioma que Whisper detectó en la última llamada

    def _load_model(self):
        from faster_whisper import WhisperModel
        live, final, device, ct, desc = pick_models(self.cfg)
        logging.info("modelos: vivo=%s final=%s en %s (%s)", live, final, device, ct)
        kw = dict(device=device, compute_type=ct, download_root=MODELS_DIR)
        if device == "cpu":
            kw["cpu_threads"] = 4        # no acaparar el procesador
            self.partial_every = 2.0     # en CPU el texto en vivo se actualiza menos
        self.ui_q.put(("status", f"Cargando Whisper {live} en {desc}… "
                                 "(la primera vez descarga el modelo, puede tardar)"))
        try:
            self.model = WhisperModel(live, **kw)
            # calentamiento: la primera inferencia carga los kernels
            list(self.model.transcribe(np.zeros(SR, dtype=np.float32),
                                       language=self._fixed_lang() or "en")[0])
        except Exception:
            if device != "cuda":
                raise
            logging.exception("CUDA falló, paso a CPU")
            self.ui_q.put(("status", "La tarjeta gráfica falló; cargando modelo pequeño en CPU…"))
            self.cfg = dict(self.cfg, model="auto", final_model="auto")
            small = "small.en" if self.cfg["source_lang"] == "en" else "small"
            self.model = WhisperModel(small, device="cpu", compute_type="int8",
                                      cpu_threads=4, download_root=MODELS_DIR)
            self.partial_every = 2.0
            return "CPU"
        if final != live:
            self.ui_q.put(("status", f"Cargando el modelo preciso {final}… "
                                     "(la primera vez descarga ~3 GB)"))
            try:
                self.final_model = WhisperModel(final, **kw)
            except Exception:
                logging.exception("no cargó el modelo final, uso el rápido para todo")
        return "GPU" if device == "cuda" else "CPU"

    def _hotwords(self):
        words = list(self.cfg.get("glossary") or [])
        if self.speakers:
            with self.speakers.lock:
                words += list(self.speakers.names.values())
        return ", ".join(dict.fromkeys(w for w in words if w)) or None

    def _fixed_lang(self):
        src = self.cfg.get("source_lang", "auto")
        return None if src in (None, "", "auto") else src

    def _pick_lang(self, audio, who):
        """Idioma de una frase final: el que detecta Whisper, restringido a los idiomas
        esperados (si se configuraron) y, si duda, el último que habló esa persona."""
        fixed = self._fixed_lang()
        if fixed:
            return fixed
        key = tuple(who or [])
        prev = self.lang_by_who.get(key)
        try:
            model = self.final_model or self.model
            lang, prob, allp = model.detect_language(audio)
            allp = sorted(allp, key=lambda x: -x[1])
            expected = [l.lower() for l in (self.cfg.get("expected_languages") or [])]
            if expected:
                cands = [(l, p) for l, p in allp if l in expected]
                if cands:
                    lang, prob = cands[0]
            if prob < 0.5 and prev:
                lang = prev
        except Exception:
            logging.exception("detección de idioma")
            return prev
        self.lang_by_who[key] = lang
        return lang

    def _partial_lang(self, who):
        """Para el texto en vivo: el idioma de esa persona si ya se conoce;
        si no, None (Whisper lo detecta solo)."""
        return self._fixed_lang() or self.lang_by_who.get(tuple(who or []))

    def _transcribe(self, audio, final, words=None, lang=None):
        """Devuelve (texto, palabras) — palabras = [(palabra, seguridad, inicio, fin)].
        Las finales siempre traen palabras; las parciales solo si words=True."""
        model = (self.final_model or self.model) if final else self.model
        want_words = final if words is None else words
        lang = lang or self._fixed_lang()
        segs, info = model.transcribe(
            audio,
            language=lang,
            beam_size=5 if final else 1,
            vad_filter=final,
            condition_on_previous_text=False,
            initial_prompt=self.context.get(lang or "", "")[-200:] or None,
            hotwords=self._hotwords(),
            word_timestamps=want_words,
            without_timestamps=not want_words,
            # ignora lo "oído" en pausas largas sin voz (donde Whisper más inventa);
            # solo funciona con tiempos por palabra
            hallucination_silence_threshold=1.0 if want_words else None,
        )
        self.last_lang = lang or getattr(info, "language", None)
        parts, words = [], []
        for s in segs:
            if s.no_speech_prob > 0.6 and s.avg_logprob < -0.8:
                continue
            parts.append(s.text.strip())
            for w in (s.words or []):
                # (palabra, seguridad, inicio_s, fin_s) — tiempos relativos al audio
                words.append((w.word, float(w.probability), float(w.start), float(w.end)))
        text = " ".join(p for p in parts if p).strip()
        # quitar las "alucinaciones" de Whisper (créditos de subtítulos, etc.)
        # aunque vengan con variantes o pegadas a una frase real
        # self.last_removed = True si se quitó algo inventado (para mostrar "inaudible")
        if words:
            n0 = len(words)
            words = clean_hallucinated_words(words)
            text = "".join(w[0] for w in words).strip()
            self.last_removed = len(words) < n0
        else:
            clean = clean_hallucinated_text(text)
            self.last_removed = clean != text
            text = clean
        if text.lower().strip(" .!?") in {h.strip(" .!?") for h in HALLUCINATIONS}:
            self.last_removed = True
            return "", []
        return text, words

    def _early_cut(self, pw, dur):
        """¿Dónde cortar lo que se lleva dicho para mandarlo a traducir YA, aunque la
        persona siga hablando? Devuelve el segundo del corte o None.
        pw = palabras del parcial [(palabra, seguridad, inicio, fin)], dur = segundos."""
        min_s = float(self.cfg.get("commit_min_seconds", 1.5))
        if dur < min_s or len(pw) < 3:
            return None
        cut = None
        for k, (w, _, ws, we) in enumerate(pw[:-1]):
            if not (min_s * 0.6 <= we <= dur - 0.4):
                continue
            end = w.strip()
            if end.endswith((".", "?", "!")) and k >= 1:
                cut = we                                  # fin de oración
            elif (self.cfg.get("commit_on_comma", True) and end.endswith((",", ";", ":"))
                  and k >= 3):
                cut = we                                  # coma, con 4+ palabras antes
        if cut:
            return cut
        # habla de corrido sin puntuación: cortar en la pausa más larga entre palabras
        if dur >= float(self.cfg.get("commit_gap_after", 3.0)):
            best, best_gap = None, 0.18
            for a, b in zip(pw[:-1], pw[1:]):
                gap = b[2] - a[3]
                if gap > best_gap and min_s <= a[3] <= dur - 0.4:
                    best, best_gap = a[3], gap
            return best
        return None

    def _who(self, times):
        if not self.speakers or not times:
            return None
        return self.speakers.who(times[0], times[-1] + FRAME / SR)

    def _emit_final(self, frames, times):
        audio = np.concatenate(frames)
        if len(audio) < SR * 0.4:
            return
        who = self._who(times)
        t0 = time.time()
        self.last_removed = False
        lang = self._pick_lang(audio, who)
        text, words = self._transcribe(audio, final=True, lang=lang)
        lang = lang or self.last_lang
        removed = self.last_removed
        logging.info("final %.1fs audio en %.2fs [%s]", len(audio) / SR, time.time() - t0, lang)
        self.ui_q.put(("partial", None, None))
        if not text:
            if removed:                       # Whisper inventó algo: "inaudible"
                self._emit_inaudible(who, audio)
            return
        # Whisper inventa frases sobre ruido/música: seguridad promedio bajísima,
        # o la misma frase repetida con poca seguridad
        if words:
            mean = sum(w[1] for w in words) / len(words)
            norm = text.lower().strip(" .!?,")
            if mean < 0.3 or (mean < 0.5 and norm in self.recent_finals):
                logging.info("descartada (seguridad %.2f)", mean)
                self._emit_inaudible(who, audio)
                return
            self.recent_finals = (self.recent_finals + [norm])[-4:]
        self.context[lang or ""] = (self.context.get(lang or "", "") + " " + text)[-400:]

        # quién dijo cada palabra (Discord): si hablaron varios, una línea por persona
        runs = self.speakers.attribute(times[0], words) if (self.speakers and words) else None
        if not runs:
            self._emit_entry(text, who, words, audio, tail=removed, lang=lang)
            return
        logging.info("frase repartida en %d tramo(s)", len(runs))
        for k, (names, i0, i1) in enumerate(runs):
            ws = words[i0:i1]
            run_text = "".join(w[0] for w in ws).strip()
            if not run_text:
                continue
            a0 = max(0, int((ws[0][2] - 0.15) * SR))
            a1 = min(len(audio), int((ws[-1][3] + 0.25) * SR))
            self._emit_entry(run_text, names, ws, audio[a0:a1] if len(runs) > 1 else audio,
                             tail=removed and k == len(runs) - 1, lang=lang)

    def _emit_entry(self, text, who, words, audio, tail=False, lang=None):
        """tail=True: a la frase se le quitó un pedazo inventado → "(inaudible)" al final."""
        self.next_id += 1
        self.ui_q.put(("final", self.next_id, text, datetime.datetime.now(), who,
                       [(w[0], w[1]) for w in words], audio, {"tail": tail, "lang": lang}))
        self.translator.add_final(self.next_id, text, who, lang)

    def _emit_inaudible(self, who, audio):
        """Alguien dijo algo que no se entendió (Whisper solo inventó texto encima).
        Si la misma persona acaba de tener otro "inaudible", no se repite."""
        dur = len(audio) / SR
        if not self.cfg.get("show_inaudible", True):
            return
        # ruidos cortos (tos, teclado, clic) o sonido sin nadie hablando en Discord:
        # no son algo que te perdiste, se ignoran en silencio
        if dur < float(self.cfg.get("inaudible_min_seconds", 1.2)):
            logging.info("inaudible ignorado: ruido corto (%.1fs)", dur)
            return
        if who is not None and not who:
            logging.info("inaudible ignorado: nadie hablaba en Discord")
            return
        key = tuple(who or [])
        now = time.time()
        if self._last_inaudible and self._last_inaudible[0] == key \
                and now - self._last_inaudible[1] < 8:
            self._last_inaudible = (key, now)
            return
        self._last_inaudible = (key, now)
        logging.info("inaudible mostrado (%.1fs)", dur)
        self.next_id += 1
        self.ui_q.put(("final", self.next_id, "", datetime.datetime.now(), who, [], audio,
                       {"inaudible": True}))
        self.ui_q.put(("translation", self.next_id, "(inaudible)", True))

    def run(self):
        try:
            where = self._load_model()
        except Exception as e:
            logging.exception("no cargó el modelo")
            self.ui_q.put(("status", f"No pude cargar Whisper: {e}"))
            return
        self.ui_q.put(("ready", where))

        silence_end = self.cfg["silence_end"]
        max_seg = self.cfg["max_segment"]
        min_th = self.cfg["min_threshold"]

        pending = np.zeros(0, dtype=np.float32)
        pending_end_t = time.time()   # hora real del último sample de `pending`
        preroll = []           # ~0.3 s antes de que empiece a hablar: (frame, hora)
        seg, seg_rms, seg_t = [], [], []
        speaking = False
        voiced_run = 0
        silence = 0.0
        noise = min_th
        last_partial = 0.0
        partial_wait = self.partial_every

        while True:
            try:
                ts, chunk = self.audio_q.get(timeout=0.1)
            except queue.Empty:
                # WASAPI no manda nada cuando no suena nada: lo trato como silencio
                if not speaking:
                    continue
                ts, chunk = time.time(), np.zeros(int(SR * 0.1), dtype=np.float32)

            if self.paused:
                pending = np.zeros(0, dtype=np.float32)
                speaking, seg, seg_rms, seg_t, preroll = False, [], [], [], []
                continue

            pending = np.concatenate([pending, chunk])
            pending_end_t = ts
            while len(pending) >= FRAME:
                ft = pending_end_t - len(pending) / SR
                frame, pending = pending[:FRAME], pending[FRAME:]
                rms = float(np.sqrt(np.mean(frame * frame)))
                th = max(min_th, noise * 3.0)
                voiced = rms > th
                if not speaking:
                    noise = 0.98 * noise + 0.02 * min(rms, th)
                    preroll.append((frame, ft))
                    if len(preroll) > 15:
                        preroll.pop(0)
                    voiced_run = voiced_run + 1 if voiced else 0
                    if voiced_run >= 3:
                        if self.speakers:
                            # 1.er frame con voz = 3 frames atrás
                            self.speakers.calibrate(preroll[-3][1])
                        speaking = True
                        seg = [f for f, _ in preroll]
                        seg_t = [t for _, t in preroll]
                        seg_rms = [0.0] * len(seg)
                        preroll = []
                        silence = 0.0
                        last_partial = time.time()
                else:
                    seg.append(frame)
                    seg_rms.append(rms)
                    seg_t.append(ft)
                    silence = 0.0 if voiced else silence + FRAME / SR
                    dur = len(seg) * FRAME / SR
                    if silence >= silence_end:
                        self._emit_final(seg, seg_t)
                        speaking, seg, seg_rms, seg_t, voiced_run = False, [], [], [], 0
                    elif dur >= max_seg:
                        # corto en el momento más silencioso de los últimos 2 s
                        win = int(2.0 * SR / FRAME)
                        tail = seg_rms[-win:]
                        cut = len(seg) - win + int(np.argmin(tail)) + 1
                        self._emit_final(seg[:cut], seg_t[:cut])
                        seg, seg_rms, seg_t = seg[cut:], seg_rms[cut:], seg_t[cut:]
                        last_partial = time.time()

            # Discord dice que una persona terminó y otra arrancó: corto ahí
            if speaking and self.speakers and len(seg) * FRAME / SR > 1.0:
                cut_t = self.speakers.switch_time(seg_t[0], seg_t[-1])
                if cut_t:
                    cut = next((i for i, t in enumerate(seg_t) if t >= cut_t), len(seg))
                    if cut * FRAME / SR >= 0.5:
                        self._emit_final(seg[:cut], seg_t[:cut])
                        seg, seg_rms, seg_t = seg[cut:], seg_rms[cut:], seg_t[cut:]
                        last_partial = time.time()
                        if not seg:
                            speaking, voiced_run = False, 0

            # Si hay audio esperando en la cola, primero me pongo al día: si la GPU
            # está ocupada (After Effects, un juego…) cada parcial tarda segundos y,
            # hecho tras cada pedacito de 50 ms, el atraso crecía sin fin.
            if (speaking and time.time() - last_partial > partial_wait
                    and self.audio_q.qsize() <= 2
                    and len(seg) * FRAME / SR > 1.0):
                dur = len(seg) * FRAME / SR
                t0 = time.time()
                try:
                    pwho = self._who(seg_t)
                    text, pw = self._transcribe(np.concatenate(seg), final=False, words=True,
                                                lang=self._partial_lang(pwho))
                except Exception:
                    logging.exception("parcial falló")
                    text, pw = "", []
                took = time.time() - t0
                last_partial = time.time()
                # el próximo parcial espera al menos lo que tardó este
                partial_wait = max(self.partial_every, took)
                if took > 1.5:
                    logging.warning("parcial lento: %.1fs (¿GPU ocupada por otro programa?)", took)
                cut_s = self._early_cut(pw, dur)
                if cut_s:
                    cut = min(len(seg), int((cut_s + 0.12) * SR / FRAME))
                    logging.info("oración enviada a los %.1fs (sigue hablando)", cut_s)
                    self._emit_final(seg[:cut], seg_t[:cut])
                    seg, seg_rms, seg_t = seg[cut:], seg_rms[cut:], seg_t[cut:]
                    last_partial = 0.0      # mostrar enseguida lo que sigue
                    if not seg:
                        speaking, voiced_run = False, 0
                    continue
                if text:
                    self.ui_q.put(("partial", text, self._who(seg_t)))
                    self.translator.set_partial(text, self.last_lang)


# =============================================================================
# Traducción (Google Translate, el mismo servicio gratuito que usa Chrome)
# =============================================================================
class Translator(threading.Thread):
    def __init__(self, ui_q, cfg):
        super().__init__(daemon=True)
        self.ui_q = ui_q
        self.cfg = cfg
        self.finals = queue.Queue()
        self.partial = None
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.cache = {}

    def add_final(self, id_, text, who=None, lang=None):
        self.finals.put((id_, text, who, lang))
        self.event.set()

    def _translate(self, text, lang):
        """Si ya está en el idioma del usuario, se deja tal cual."""
        if same_lang(lang, self.cfg["target_lang"]):
            return text
        return self._tr(text, lang)

    def _translate_final(self, id_, text, who, lang=None):
        try:
            es = self._translate(text, lang)
        except Exception as e:
            logging.exception("Google falló")
            es = f"(no se pudo traducir: {e.__class__.__name__})"
        self.ui_q.put(("translation", id_, es, True))

    def set_partial(self, text, lang=None):
        with self.lock:
            self.partial = (text, lang)
        self.event.set()

    def _tr(self, text, lang=None):
        src = lang or self.cfg.get("source_lang") or "auto"
        src = GOOGLE_LANG.get(src, src)
        key = (text, src)
        if key in self.cache:
            return self.cache[key]
        import requests
        last = None
        for attempt in range(3):
            try:
                r = requests.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={"client": "gtx", "sl": src,
                            "tl": self.cfg["target_lang"], "dt": "t", "q": text},
                    timeout=5)
                r.raise_for_status()
                out = "".join(p[0] for p in r.json()[0] if p and p[0])
                break
            except Exception as e:
                last = e
                time.sleep(0.4 * (attempt + 1))
        else:
            raise last
        self.cache[key] = out
        if len(self.cache) > 500:
            self.cache.pop(next(iter(self.cache)))
        return out

    def run(self):
        while True:
            self.event.wait(0.5)
            self.event.clear()
            while True:
                try:
                    id_, text, who, lang = self.finals.get_nowait()
                except queue.Empty:
                    break
                self._translate_final(id_, text, who, lang)
            with self.lock:
                p, self.partial = self.partial, None
            if p:
                try:
                    self.ui_q.put(("partial_es", p[0], self._translate(p[0], p[1])))
                except Exception:
                    pass


# =============================================================================
# Ventana flotante
# =============================================================================
BG = "#111418"
BAR = "#1b2028"
FG = "#f2f4f7"
DIM = "#8b95a5"
ACC = "#5fb3ff"


class Overlay:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ui_q = queue.Queue()
        self.audio_q = queue.Queue()
        self.entries = []          # ya mostradas, en orden: [{id, en, es, t, who, words}]
        self.pending = {}          # id -> frase esperando su traducción
        self.next_show = 1         # próxima id a mostrar (el orden se respeta)
        self.audio = {}            # id -> audio de la frase (para volver a escucharla)
        self.playing = False
        self.partial_en = None
        self.partial_es = None
        self.partial_who = None
        self.mini = False          # minimizado: las traducciones salen como burbujas
        self.pill = None
        self.bubbles = []
        self.discord = ""
        self.device = "…"
        self.status = "Iniciando…"
        self.ready = False


        # identidad propia en Windows: la barra de tareas usa NUESTRO icono, no el de Python
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TraductorEnVivo.App")
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title("Traductor en vivo")
        if os.path.exists(ICON_PATH):
            try:
                self.root.iconbitmap(default=ICON_PATH)
            except Exception:
                logging.exception("icono")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", cfg["opacity"])
        self.root.geometry(cfg["geometry"])
        self.root.configure(bg=BG)
        self.root.minsize(300, 160)
        self.root.report_callback_exception = lambda *exc: logging.error(
            "error en la ventana", exc_info=exc)

        self._build()

        self.translator = Translator(self.ui_q, cfg)
        self.speakers = DiscordSpeakers(self.ui_q, cfg)
        self.engine = Engine(self.audio_q, self.ui_q, self.translator, cfg,
                             self.speakers if self.speakers.configured else None)
        self.capture = AudioCapture(self.audio_q, self.ui_q, cfg,
                                    self.speakers if self.speakers.configured else None)
        self.speakers.start()
        self.translator.start()
        self.engine.start()
        self.capture.start()

        self.root.after(50, self._poll)
        self.root.after(1500, self._keep_on_top)
        self.root.after(400, self._show_in_taskbar)
        self.tray = None
        self._start_tray()

    # ---------------------------------------------------------------- Windows
    def _show_in_taskbar(self, tries=10):
        """Una ventana sin marco (overrideredirect) no sale en la barra de tareas:
        se le pone el estilo APPWINDOW para que tenga su botón con el icono.
        Hay que hacerlo cuando Windows ya creó la ventana: si aún no, reintenta."""
        try:
            import ctypes
            u = ctypes.windll.user32
            if self.mini or not self.root.winfo_viewable():
                if tries > 0 and not self.mini:
                    self.root.after(300, lambda: self._show_in_taskbar(tries - 1))
                return
            hwnd = u.GetParent(self.root.winfo_id())
            if not hwnd or u.GetWindowLongW(hwnd, -20) & 0x40000:
                if not hwnd and tries > 0:
                    self.root.after(300, lambda: self._show_in_taskbar(tries - 1))
                return
            st = u.GetWindowLongW(hwnd, -20)
            st = (st & ~0x80) | 0x40000          # sin TOOLWINDOW, con APPWINDOW
            u.SetWindowLongW(hwnd, -20, st)
            # esconder/mostrar con la API de Windows (NO con withdraw/deiconify de Tk,
            # que recalcula los estilos y vuelve a quitar el botón)
            u.ShowWindow(hwnd, 0)
            u.ShowWindow(hwnd, 5)
        except Exception:
            logging.exception("barra de tareas")

    def _start_tray(self):
        """Icono junto al reloj (como el del micrófono): clic = mostrar;
        clic derecho = menú."""
        try:
            import pystray
            from PIL import Image
            img = Image.open(ICON_PATH) if os.path.exists(ICON_PATH) else \
                Image.new("RGB", (64, 64), ACC)
            run = lambda fn: (lambda icon, item: self.ui_q.put(("cmd", fn)))
            menu = pystray.Menu(
                pystray.MenuItem("Mostrar ventana", run(self.show_window), default=True),
                pystray.MenuItem("Modo burbujas", run(self.minimize)),
                pystray.MenuItem("Pausar / reanudar", run(self.toggle_pause)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", run(self.close)))
            self.tray = pystray.Icon("TraductorEnVivo", img, "Traductor en vivo", menu)
            self.tray.run_detached()
        except Exception:
            logging.exception("icono de la bandeja")

    def show_window(self):
        if self.mini:
            self.restore()
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.focus_force()
            self.root.after(20, self._show_in_taskbar)

    # ---------------------------------------------------------------- layout
    def _btn(self, parent, text, cmd, tip):
        b = tk.Label(parent, text=text, bg=BAR, fg=DIM, font=("Segoe UI Symbol", 11),
                     padx=7, cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: (b.config(fg=FG), self._set_hint(tip)))
        b.bind("<Leave>", lambda e: (b.config(fg=DIM), self._set_hint(None)))
        b.pack(side="right")
        return b

    def _build(self):
        bar = tk.Frame(self.root, bg=BAR, height=30)
        bar.pack(fill="x", side="top")
        self.title_lbl = tk.Label(bar, text="  Traductor en vivo", bg=BAR, fg=FG,
                                  font=("Segoe UI Semibold", 10))
        self.title_lbl.pack(side="left")
        for w in (bar, self.title_lbl):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        self._btn(bar, "✕", self.close, "Cerrar")
        self._btn(bar, "—", self.minimize,
                  "Minimizar: las traducciones salen como burbujas al costado de la pantalla")
        self._btn(bar, "🗑", self.clear, "Limpiar la pantalla")
        self._btn(bar, "🔊", self.redetect, "Volver a detectar el audio (si cambiaste de audífonos)")
        self._btn(bar, "◐", self.cycle_opacity, "Transparencia")
        self.en_btn = self._btn(bar, "EN", self.toggle_en, "Mostrar u ocultar el inglés original")
        self._btn(bar, "A+", lambda: self.font_step(1), "Letra más grande")
        self._btn(bar, "A−", lambda: self.font_step(-1), "Letra más pequeña")
        self.pause_btn = self._btn(bar, "⏸", self.toggle_pause, "Pausar / reanudar")

        self.status_lbl = tk.Label(self.root, text=self.status, bg=BG, fg=DIM, anchor="w",
                                   font=("Segoe UI", 9), padx=10)
        self.status_lbl.pack(fill="x", side="bottom")

        grip = tk.Label(self.status_lbl, text="◢", bg=BG, fg=DIM, cursor="size_nw_se",
                        font=("Segoe UI", 9))
        grip.pack(side="right")
        grip.bind("<Button-1>", self._resize_start)
        grip.bind("<B1-Motion>", self._resize_move)

        self.live = tk.Text(self.root, bg="#0c0f13", fg=FG, wrap="word", bd=0, height=3,
                            highlightthickness=0, padx=14, pady=6, cursor="arrow",
                            insertwidth=0)
        self.live.pack(fill="x", side="bottom")
        self.live.bind("<Key>", lambda e: "break")
        tk.Frame(self.root, bg="#232a35", height=1).pack(fill="x", side="bottom")

        self.text = tk.Text(self.root, bg=BG, fg=FG, wrap="word", bd=0,
                            highlightthickness=0, padx=14, pady=10, cursor="arrow",
                            spacing1=2, spacing3=2, insertwidth=0)
        self.text.pack(fill="both", expand=True)
        self.text.bind("<Key>", lambda e: "break" if e.keysym not in ("c", "C") else None)
        # bajar solo, salvo que el usuario suba a releer con la rueda
        self.follow = True
        self.text.bind("<MouseWheel>", self._on_wheel)
        self.new_btn = tk.Label(self.text, text="⬇ nuevas", bg=ACC, fg="#0b1220",
                                font=("Segoe UI Semibold", 9), padx=8, pady=2, cursor="hand2")
        self.new_btn.bind("<Button-1>", lambda e: self._go_bottom())
        self._apply_fonts()

    def _apply_fonts(self):
        fs = self.cfg["font_size"]
        for w in (self.text, self.live):
            self._tags(w, fs)
        self.text.tag_bind("btn", "<Enter>", lambda e: self.text.config(cursor="hand2"))
        self.text.tag_bind("btn", "<Leave>", lambda e: self.text.config(cursor="arrow"))
        self.en_btn.config(text="EN" if self.cfg["show_en"] else "en̶")

    def _tags(self, t, fs):
        t.tag_configure("es", font=("Segoe UI", fs), foreground=FG, spacing3=12)
        t.tag_configure("en_doubt", font=("Segoe UI Semibold", max(9, fs - 4)),
                                foreground="#ffa94d", underline=True)
        t.tag_configure("btn", font=("Segoe UI Symbol", max(9, fs - 5)),
                                foreground=ACC)
        t.tag_configure("en", font=("Segoe UI", max(8, fs - 5)), foreground=DIM,
                                spacing1=4)
        t.tag_configure("time", font=("Consolas", max(7, fs - 7)), foreground="#5c6676")
        t.tag_configure("p_en", font=("Segoe UI", max(8, fs - 5), "italic"),
                                foreground="#6f7d90", spacing1=4)
        t.tag_configure("p_es", font=("Segoe UI", fs, "italic"), foreground=ACC)
        t.tag_configure("pending", font=("Segoe UI", fs, "italic"), foreground="#56606e",
                                spacing3=12)
        t.tag_configure("hint", font=("Segoe UI", max(9, fs - 3)), foreground=DIM)
        t.tag_configure("lang", font=("Consolas", max(7, fs - 7), "bold"),
                        foreground="#0b1220", background="#8b95a5")
        t.tag_configure("lang_mine", font=("Consolas", max(7, fs - 7), "bold"),
                        foreground="#0b1220", background="#7ee787")
        t.tag_configure("inaudible", font=("Segoe UI", fs, "italic"), foreground="#6f7d90",
                        spacing3=12)
        t.tag_configure("inaudible_tail", font=("Segoe UI", max(9, fs - 3), "italic"),
                        foreground="#6f7d90")
        t.tag_configure("who_unknown", font=("Segoe UI Semibold", max(9, fs - 3)),
                                foreground="#5c6676", spacing1=6)

    # ---------------------------------------------------------------- render
    # El historial SOLO crece hacia abajo: cada frase se agrega una vez, ya traducida,
    # en el orden en que se dijo, y no se vuelve a tocar. Lo que se está diciendo
    # ahora va en la franja "en vivo" de abajo, que tiene alto fijo.
    def _on_wheel(self, e):
        self.text.yview_scroll(int(-e.delta / 120) * 2, "units")
        if e.delta > 0:                    # subió: deja de seguir
            self.follow = False
        else:                              # bajó: si llegó al final, vuelve a seguir
            self.text.update_idletasks()
            if self.text.yview()[1] >= 0.999:
                self._go_bottom()
        return "break"

    def _go_bottom(self):
        self.follow = True
        self.new_btn.place_forget()
        self.text.yview_moveto(1.0)

    def _scroll_if_following(self):
        if self.follow:
            self.text.update_idletasks()
            self.text.yview_moveto(1.0)
        else:
            self.new_btn.place(relx=1.0, rely=1.0, x=-10, y=-8, anchor="se")

    def _render(self):
        """Redibuja todo (solo al cambiar letra, EN o limpiar)."""
        t = self.text
        t.delete("1.0", "end")
        if not self.entries:
            msg = ("Cargando…" if not self.ready else
                   "Escuchando. Cuando alguien hable en inglés, la traducción aparece aquí.")
            t.insert("end", msg, "hint")
        for e in self.entries[-200:]:
            self._insert_entry(e)
        self._go_bottom()
        self.text.update_idletasks()
        self.text.yview_moveto(1.0)
        self._render_live()

    def _append(self, e):
        if not self.entries:
            self.text.delete("1.0", "end")     # quita el mensaje de "Escuchando…"
        self.entries.append(e)
        self._insert_entry(e)
        if self.mini:
            self._bubble(e)
        if len(self.entries) > 300:            # recorta lo muy viejo
            self.entries = self.entries[-200:]
            self._render()
            return
        self._scroll_if_following()

    def _insert_entry(self, e):
        t = self.text
        show_en = self.cfg["show_en"]
        words = e.get("words") or []
        flags = self._doubt_flags(words)
        inaudible = e.get("flags", {}).get("inaudible")
        lang = e.get("flags", {}).get("lang")
        mine = same_lang(lang, self.cfg["target_lang"])   # ya está en tu idioma
        en_line = (show_en or any(flags)) and not inaudible and not mine
        if e.get("who") is not None:
            self._insert_who(t, e["who"])
        t.insert("end", e["t"].strftime("%H:%M:%S") + "  ", "time")
        if lang and not inaudible:
            t.insert("end", f" {lang.upper()} ", "lang_mine" if mine else "lang")
            t.insert("end", "  ")
        if e["id"] in self.audio:
            tp, ts = f"play_{e['id']}", f"slow_{e['id']}"
            t.insert("end", "▶", ("btn", tp))
            t.insert("end", " ")
            t.insert("end", "🐢", ("btn", ts))
            t.tag_bind(tp, "<Button-1>", lambda ev, i=e["id"]: self.play(i, 1.0))
            t.tag_bind(ts, "<Button-1>", lambda ev, i=e["id"]: self.play(i, 0.75))
        t.insert("end", "\n" if not en_line else "  ")
        if en_line:
            if words:
                for (w, _), d in zip(words, flags):
                    if d:
                        if w.startswith(" "):
                            t.insert("end", " ", "en")
                        t.insert("end", w.strip(), "en_doubt")
                    else:
                        t.insert("end", w, "en")
                t.insert("end", "\n", "en")
            else:
                t.insert("end", e["en"] + "\n", "en")
        if inaudible:
            t.insert("end", "(inaudible)\n", "inaudible")
            return
        t.insert("end", e["es"] or "(sin traducción)", "es")
        if e.get("flags", {}).get("tail"):
            t.insert("end", "  (inaudible)", "inaudible_tail")
        t.insert("end", "\n", "es")

    def _render_live(self):
        """Franja de abajo: lo que alguien está diciendo en este momento."""
        lv = self.live
        lv.delete("1.0", "end")
        if not self.partial_en:
            lv.insert("end", "· · ·", "time")
            return
        if self.partial_who is not None:
            self._insert_who(lv, self.partial_who)
        if self.cfg["show_en"]:
            lv.insert("end", self.partial_en, "p_en")
        lv.insert("end", "\n")
        if self.partial_es:
            lv.insert("end", self.partial_es[1] + ("" if self.partial_es[0] == self.partial_en
                                                   else " …"), "p_es")
        lv.see("end")

    def _doubt_flags(self, words):
        th = float(self.cfg.get("doubt_threshold", 0.35))
        th1 = float(self.cfg.get("doubt_threshold_first", 0.12))
        return [p < (th1 if i == 0 else th) for i, (_, p) in enumerate(words)]

    def _insert_who(self, t, names):
        """Pone 'Juan' (o 'Juan + Ana') con el color de cada persona."""
        fs = self.cfg["font_size"]
        if not names:
            t.insert("end", "¿?  ", "who_unknown")
            return
        for i, n in enumerate(names):
            tag = "who_" + color_for(n)
            t.tag_configure(tag, font=("Segoe UI Semibold", max(9, fs - 3)),
                            foreground=color_for(n), spacing1=6)
            if i:
                t.insert("end", " + ", "time")
            t.insert("end", n, tag)
        t.insert("end", "  ")

    def _update_status(self):
        s = self.status
        if self.ready:
            s = ("⏸ En pausa" if self.engine.paused else "● Escuchando") + f"  ·  {self.device}"
            if self.discord:
                s += f"  ·  {self.discord}"
        self.status_lbl.config(text=s, fg=("#e0a84a" if self.engine.paused else DIM))

    def _set_hint(self, tip):
        if tip:
            self.status_lbl.config(text=tip, fg=FG)
        else:
            self._update_status()

    # ---------------------------------------------------------------- loop
    def _flush(self):
        """Muestra las frases traducidas en el orden en que se dijeron."""
        while self.pending:
            e = self.pending.get(self.next_show)
            if e is None:
                # esa id nunca llegó (no debería pasar): salto a la siguiente que exista
                if self.next_show < min(self.pending):
                    self.next_show = min(self.pending)
                    continue
                break
            if e["es"] is None and time.time() - e["arrived"] < 5:
                break      # todavía traduciendo; las siguientes esperan su turno
            self._append(self.pending.pop(self.next_show))
            self.next_show += 1

    def _poll(self):
        if getattr(self, "closing", False):      # ya se cerró la ventana
            return
        changed = False
        live = False
        try:
            while True:
                msg = self.ui_q.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self.status = msg[1]
                elif kind == "device":
                    self.device = msg[1]
                elif kind == "discord":
                    self.discord = msg[1]
                elif kind == "cmd":            # pedido desde el icono de la bandeja
                    msg[1]()
                elif kind == "ready":
                    self.ready = True
                    changed = True
                elif kind == "partial":
                    self.partial_en = msg[1]
                    self.partial_who = msg[2]
                    if msg[1] is None:
                        self.partial_es = None
                    live = True
                elif kind == "partial_es":
                    # solo si es de la misma frase que se está mostrando
                    if self.partial_en and self.partial_en[:12].lower() == msg[1][:12].lower():
                        self.partial_es = (msg[1], msg[2])
                        live = True
                elif kind == "final":
                    self.pending[msg[1]] = {"id": msg[1], "en": msg[2], "es": None,
                                            "t": msg[3], "who": msg[4], "words": msg[5],
                                            "arrived": time.time(),
                                            "flags": msg[7] if len(msg) > 7 else {}}
                    self.audio[msg[1]] = msg[6]
                    for old in list(self.audio)[:-100]:
                        self.audio.pop(old, None)
                    self.partial_en = self.partial_es = None
                    live = True
                elif kind == "translation":
                    final = msg[3] if len(msg) > 3 else True
                    if final and msg[1] in self.pending:
                        self.pending[msg[1]]["es"] = msg[2]
        except queue.Empty:
            pass
        self._flush()
        if changed:
            self._render()
        elif live:
            self._render_live()
        self._update_status()
        self.root.after(60, self._poll)

    def _keep_on_top(self):
        # Discord a veces se pone encima; esto lo devuelve arriba
        try:
            if self.mini:
                for w in [self.pill] + [b["win"] for b in self.bubbles]:
                    if w is not None and w.winfo_exists():
                        w.attributes("-topmost", True)
                        w.lift()
            else:
                self.root.attributes("-topmost", True)
                self.root.lift()
        except Exception:
            pass
        self.root.after(3000, self._keep_on_top)

    # ---------------------------------------------------------------- burbujas
    @staticmethod
    def _win_style(win, click_through):
        """Ventana que NO le roba el foco al juego y no sale en Alt+Tab;
        con click_through los clics pasan a través de ella."""
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = u.GetParent(win.winfo_id())
            st = u.GetWindowLongW(hwnd, -20)                   # GWL_EXSTYLE
            st |= 0x08000000 | 0x80 | 0x80000                  # NOACTIVATE|TOOLWINDOW|LAYERED
            if click_through:
                st |= 0x20                                     # TRANSPARENT
            u.SetWindowLongW(hwnd, -20, st)
        except Exception:
            logging.exception("estilo de ventana")

    def minimize(self):
        if self.mini:
            return
        self.mini = True
        self.root.withdraw()
        sw = self.root.winfo_screenwidth()
        p = tk.Toplevel(self.root)
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        p.configure(bg=ACC)
        lbl = tk.Label(p, text="💬 Traductor", bg="#111418", fg=FG, cursor="hand2",
                       font=("Segoe UI Semibold", 9), padx=10, pady=4)
        lbl.pack(padx=1, pady=1)
        lbl.bind("<Button-1>", lambda e: self.restore())
        lbl.bind("<Enter>", lambda e: lbl.config(text="⤢ Abrir ventana"))
        lbl.bind("<Leave>", lambda e: lbl.config(text="💬 Traductor"))
        p.update_idletasks()
        # justo encima de la columna de burbujas (no en la esquina: ahí están los
        # botones de minimizar/cerrar de las ventanas maximizadas)
        sh = self.root.winfo_screenheight()
        x = sw - p.winfo_reqwidth() - 16 if self.cfg["bubble_side"] == "right" else 16
        y = max(40, int(sh * float(self.cfg.get("bubble_top", 0.15))) - p.winfo_reqheight() - 10)
        p.geometry(f"+{x}+{y}")
        self._win_style(p, click_through=False)
        p.attributes("-alpha", 0.85)
        self.pill = p

    def restore(self):
        if not self.mini:
            return
        self.mini = False
        for b in self.bubbles:
            try:
                b["win"].destroy()
            except Exception:
                pass
        self.bubbles = []
        if self.pill is not None:
            self.pill.destroy()
            self.pill = None
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.after(20, self._show_in_taskbar)    # Tk le quitó el botón al mostrarla
        self._go_bottom()

    def _bubble(self, e):
        if not e.get("es"):
            return
        fs = int(self.cfg.get("bubble_font", 14))
        names = e.get("who") or []
        color = color_for(names[0]) if names else DIM
        b = tk.Toplevel(self.root)
        b.overrideredirect(True)
        b.attributes("-topmost", True)
        b.attributes("-alpha", 0.0)
        b.configure(bg=color)                       # franja de color a la izquierda
        inner = tk.Frame(b, bg="#111418", padx=12, pady=7)
        inner.pack(padx=(5, 0), pady=0, fill="both")
        if names:
            head = tk.Frame(inner, bg="#111418")
            head.pack(anchor="w")
            for i, n in enumerate(names):
                if i:
                    tk.Label(head, text=" + ", bg="#111418", fg=DIM,
                             font=("Segoe UI", fs - 4)).pack(side="left")
                tk.Label(head, text=n, bg="#111418", fg=color_for(n),
                         font=("Segoe UI Semibold", fs - 3)).pack(side="left")
        flags = e.get("flags", {})
        if flags.get("inaudible"):
            tk.Label(inner, text="(inaudible)", bg="#111418", fg="#8b95a5", justify="left",
                     font=("Segoe UI", fs, "italic")).pack(anchor="w")
        else:
            tk.Label(inner, text=e["es"] + ("  (inaudible)" if flags.get("tail") else ""),
                     bg="#111418", fg=FG, justify="left",
                     wraplength=int(fs * 26), font=("Segoe UI", fs)).pack(anchor="w")
        b.update_idletasks()
        self._win_style(b, click_through=True)
        # tiempo en pantalla: 4 s + lo que tome leerla, máximo 12 s
        secs = min(12.0, 4.0 + len(e["es"]) * 0.05)
        item = {"win": b, "h": b.winfo_reqheight(), "w": b.winfo_reqwidth(), "alpha": 0.0}
        self.bubbles.append(item)
        while len(self.bubbles) > int(self.cfg.get("bubble_max", 4)):
            old = self.bubbles.pop(0)
            try:
                old["win"].destroy()
            except Exception:
                pass
        self._layout_bubbles()
        self._fade(item, 0.93, 0.12)
        b.after(int(secs * 1000), lambda: self._fade(item, 0.0, -0.08, kill=True))

    def _layout_bubbles(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        y = int(sh * float(self.cfg.get("bubble_top", 0.15)))
        for it in self.bubbles:
            x = sw - it["w"] - 16 if self.cfg["bubble_side"] == "right" else 16
            try:
                it["win"].geometry(f"+{x}+{y}")
            except Exception:
                pass
            y += it["h"] + 8

    def _fade(self, item, target, step, kill=False):
        w = item["win"]
        try:
            if not w.winfo_exists():
                return
        except Exception:
            return
        a = item["alpha"] + step
        done = (step > 0 and a >= target) or (step < 0 and a <= target)
        item["alpha"] = target if done else a
        w.attributes("-alpha", item["alpha"])
        if not done:
            w.after(30, lambda: self._fade(item, target, step, kill))
        elif kill:
            if item in self.bubbles:
                self.bubbles.remove(item)
            w.destroy()
            self._layout_bubbles()

    # ---------------------------------------------------------------- actions
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _resize_start(self, e):
        self._rx, self._ry = e.x_root, e.y_root
        self._rw, self._rh = self.root.winfo_width(), self.root.winfo_height()

    def _resize_move(self, e):
        w = max(300, self._rw + e.x_root - self._rx)
        h = max(160, self._rh + e.y_root - self._ry)
        self.root.geometry(f"{w}x{h}")

    def play(self, id_, speed=1.0):
        """Vuelve a reproducir una frase (normalizada; speed<1 = más lenta).
        Mientras suena, el traductor no escucha para no traducirla otra vez."""
        a = self.audio.get(id_)
        if a is None or self.playing:
            return
        import io, wave, winsound

        def run():
            self.playing = True
            was = self.engine.paused
            self.engine.paused = True
            try:
                peak = float(np.max(np.abs(a))) or 1.0
                pcm = (np.clip(a / peak * 0.9, -1, 1) * 32767).astype(np.int16).tobytes()
                buf = io.BytesIO()
                with wave.open(buf, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(int(SR * speed))
                    w.writeframes(pcm)
                winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY)
                time.sleep(0.3)
            except Exception:
                logging.exception("no se pudo reproducir")
            finally:
                self.engine.paused = was
                self.playing = False

        threading.Thread(target=run, daemon=True).start()

    def toggle_pause(self):
        self.engine.paused = not self.engine.paused
        self.pause_btn.config(text="▶" if self.engine.paused else "⏸")
        if self.engine.paused:
            self.partial_en = self.partial_es = None
            self._render_live()

    def font_step(self, d):
        self.cfg["font_size"] = min(40, max(9, self.cfg["font_size"] + d))
        self._apply_fonts()
        self._render()

    def toggle_en(self):
        self.cfg["show_en"] = not self.cfg["show_en"]
        self._apply_fonts()
        self._render()

    def cycle_opacity(self):
        steps = [1.0, 0.9, 0.78, 0.65]
        cur = self.cfg["opacity"]
        i = min(range(len(steps)), key=lambda k: abs(steps[k] - cur))
        self.cfg["opacity"] = steps[(i + 1) % len(steps)]
        self.root.attributes("-alpha", self.cfg["opacity"])

    def clear(self):
        self.entries = []
        self.partial_en = self.partial_es = self.partial_who = None
        self._render()

    def redetect(self):
        self.device = "buscando…"
        self.capture.request_restart()

    def close(self):
        self.closing = True
        if not self.mini:
            self.cfg["geometry"] = self.root.geometry()
        save_config(self.cfg)
        self.capture.stop_flag = True
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        Overlay(load_config()).run()
    except Exception:
        logging.error(traceback.format_exc())
        raise
