"""
Conexión local con la app de Discord (RPC por named pipe) para saber
quién está hablando en el canal de voz en cada momento.

Necesita en config.json:  discord_client_id  y  discord_client_secret
(de una aplicación propia creada en https://discord.com/developers/applications
con el redirect http://localhost).
"""
import os, json, time, struct, uuid, threading, logging, zlib

BASE = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE, "discord_token.json")
REDIRECT = "http://localhost"
SCOPES = ["rpc", "rpc.voice.read"]
CHANNEL_EVENTS = ["SPEAKING_START", "SPEAKING_STOP",
                  "VOICE_STATE_CREATE", "VOICE_STATE_UPDATE", "VOICE_STATE_DELETE"]

PALETTE = ["#ffb86b", "#7ee787", "#ff7b9c", "#79c0ff", "#d2a8ff", "#ffd866",
           "#56d4c8", "#ff9e64", "#a5d6ff", "#f778ba"]


def color_for(name):
    return PALETTE[zlib.crc32(name.encode("utf-8")) % len(PALETTE)]


class DiscordSpeakers(threading.Thread):
    # el audio que sale por los parlantes llega un poco después del aviso de Discord
    LAG = 0.15
    # "hablando" más corto que esto = ruido de micrófono abierto, no cuenta
    MIN_TALK = 0.3

    def __init__(self, ui_q, cfg):
        super().__init__(daemon=True)
        self.ui_q = ui_q
        self.cid = str(cfg.get("discord_client_id") or "").strip()
        self.secret = str(cfg.get("discord_client_secret") or "").strip()
        self.lock = threading.Lock()
        self.pipe = None
        self.self_id = None
        self.names = {}
        self.intervals = []      # [user_id, inicio, fin|None]
        self.active = {}
        self.channel_id = None
        self.channel_name = None
        self.need_refresh = False
        self.connected = False
        # desfase medido en la llamada real (se guarda en config al cerrar)
        self.cfg = cfg
        self.LAG = float(cfg.get("discord_lag", self.LAG))
        self.lag_samples = []

    @property
    def configured(self):
        return bool(self.cid and self.secret)

    def _status(self, text):
        self.ui_q.put(("discord", text))

    # ------------------------------------------------------------------ IPC
    def _open(self):
        for i in range(10):
            try:
                return open(rf"\\?\pipe\discord-ipc-{i}", "r+b", buffering=0)
            except OSError:
                continue
        return None

    def _send(self, op, payload):
        data = json.dumps(payload).encode("utf-8")
        self.pipe.write(struct.pack("<II", op, len(data)) + data)

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.pipe.read(n - len(buf))
            if not chunk:
                raise ConnectionError("Discord cerró la conexión")
            buf += chunk
        return buf

    def _recv(self):
        while True:
            op, ln = struct.unpack("<II", self._read(8))
            data = json.loads(self._read(ln).decode("utf-8"))
            if op == 2:
                raise ConnectionError(f"Discord cerró: {data}")
            if op == 3:          # ping
                self._send(4, data)
                continue
            return data

    def _cmd(self, cmd, args=None, evt=None):
        nonce = str(uuid.uuid4())
        p = {"cmd": cmd, "args": args or {}, "nonce": nonce}
        if evt:
            p["evt"] = evt
        self._send(1, p)
        while True:
            d = self._recv()
            if d.get("nonce") == nonce:
                if d.get("evt") == "ERROR":
                    raise RuntimeError(f"{cmd}: {d.get('data', {}).get('message')}")
                return d.get("data")
            self._handle(d)

    # ------------------------------------------------------------------ OAuth
    def _token_request(self, data):
        import requests
        data = dict(data, client_id=self.cid, client_secret=self.secret)
        r = requests.post("https://discord.com/api/oauth2/token", data=data, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"token {r.status_code}: {r.text[:200]}")
        tok = r.json()
        tok["expires_at"] = time.time() + tok.get("expires_in", 0) - 60
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(tok, f)
        return tok

    def _auth(self):
        tok = None
        try:
            with open(TOKEN_PATH, encoding="utf-8") as f:
                tok = json.load(f)
        except Exception:
            pass
        if tok:
            try:
                if tok.get("expires_at", 0) < time.time():
                    tok = self._token_request({"grant_type": "refresh_token",
                                               "refresh_token": tok["refresh_token"]})
                self._cmd("AUTHENTICATE", {"access_token": tok["access_token"]})
                return
            except Exception:
                logging.exception("token guardado no sirvió, pido autorización de nuevo")
        self._status("Discord: acepta la ventana de autorización en Discord")
        code = self._cmd("AUTHORIZE", {"client_id": self.cid, "scopes": SCOPES})["code"]
        tok = self._token_request({"grant_type": "authorization_code", "code": code,
                                   "redirect_uri": REDIRECT})
        self._cmd("AUTHENTICATE", {"access_token": tok["access_token"]})

    # ------------------------------------------------------------------ canal
    def _remember(self, vs):
        u = vs.get("user") or {}
        uid = u.get("id")
        if uid:
            name = vs.get("nick") or u.get("global_name") or u.get("username") or "?"
            with self.lock:
                self.names[uid] = name

    def _refresh_channel(self):
        self.need_refresh = False
        ch = self._cmd("GET_SELECTED_VOICE_CHANNEL")
        new_id = ch["id"] if ch else None
        if new_id != self.channel_id and self.channel_id:
            for ev in CHANNEL_EVENTS:
                try:
                    self._cmd("UNSUBSCRIBE", {"channel_id": self.channel_id}, evt=ev)
                except Exception:
                    pass
        if ch:
            for vs in ch.get("voice_states", []):
                self._remember(vs)
            if new_id != self.channel_id:
                for ev in CHANNEL_EVENTS:
                    self._cmd("SUBSCRIBE", {"channel_id": new_id}, evt=ev)
        self.channel_id = new_id
        self.channel_name = ch.get("name") if ch else None
        with self.lock:
            self.active.clear()
        if ch:
            n = len(ch.get("voice_states", []))
            self._status(f"Discord: 🔈 {self.channel_name} ({n})")
        else:
            self._status("Discord: no estás en un canal de voz")

    def _handle(self, d):
        if d.get("cmd") != "DISPATCH":
            return
        ev, data = d.get("evt"), d.get("data") or {}
        now = time.time()
        if ev == "SPEAKING_START":
            uid = data.get("user_id")
            with self.lock:
                if uid and uid not in self.active:
                    iv = [uid, now, None]
                    self.intervals.append(iv)
                    self.active[uid] = iv
                cutoff = now - 300
                self.intervals = [i for i in self.intervals
                                  if i[2] is None or i[2] > cutoff]
        elif ev == "SPEAKING_STOP":
            uid = data.get("user_id")
            with self.lock:
                iv = self.active.pop(uid, None)
                if iv:
                    iv[2] = now
        elif ev in ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE"):
            self._remember(data)
        elif ev == "VOICE_CHANNEL_SELECT":
            self.need_refresh = True

    def run(self):
        if not self.configured:
            self._status("Discord: sin configurar")
            return
        while True:
            try:
                self.pipe = self._open()
                if not self.pipe:
                    self._status("Discord: no está abierto")
                    time.sleep(5)
                    continue
                self._send(0, {"v": 1, "client_id": self.cid})
                ready = self._recv()
                self.self_id = ((ready.get("data") or {}).get("user") or {}).get("id")
                self._auth()
                self._cmd("SUBSCRIBE", {}, evt="VOICE_CHANNEL_SELECT")
                self.connected = True
                self._refresh_channel()
                while True:
                    self._handle(self._recv())
                    if self.need_refresh:
                        self._refresh_channel()
            except Exception as e:
                logging.exception("Discord RPC")
                self.connected = False
                self._status(f"Discord: desconectado ({str(e)[:60]})")
                try:
                    if self.pipe:
                        self.pipe.close()
                except Exception:
                    pass
                self.pipe = None
                self.channel_id = None
                time.sleep(5)

    # ------------------------------------------------------------------ consultas
    def _by_user(self, t0, t1):
        """{uid: [primer_inicio, ultimo_fin, segundos_hablando]} en la ventana [t0, t1]."""
        t0, t1 = t0 - self.LAG, t1 - self.LAG
        now = time.time()
        out = {}
        with self.lock:
            for uid, s, e in self.intervals:
                e = now if e is None else e
                ov = min(e, t1) - max(s, t0)
                if ov <= 0 or uid == self.self_id:
                    continue
                r = out.setdefault(uid, [s, e, 0.0])
                r[0] = min(r[0], s)
                r[1] = max(r[1], e)
                r[2] += ov
        return out

    def who(self, t0, t1):
        """Lista de nombres que hablaron en la ventana, el que más habló primero."""
        if not self.connected:
            return None
        by = self._by_user(t0, t1)
        if not by:
            return []
        # todos los que hablaron de verdad (no los chispazos de micrófonos con ruido),
        # el que más habló primero; máximo 3
        ranked = sorted(by.items(), key=lambda kv: -kv[1][2])
        top = ranked[0][1][2]
        keep = [ranked[0]] + [kv for kv in ranked[1:3]
                              if kv[1][2] >= max(self.MIN_TALK, top * 0.25)]
        with self.lock:
            return [self.names.get(uid, "Alguien") for uid, _ in keep]

    def calibrate(self, onset_t):
        """onset_t = hora real en que el audio empezó a sonar con voz.
        Si justo antes UNA sola persona empezó a hablar y nadie más estaba hablando,
        la diferencia es el desfase real entre el aviso de Discord y el sonido."""
        if not self.connected:
            return
        with self.lock:
            starts = [s for u, s, e in self.intervals
                      if u != self.self_id and onset_t - 1.0 <= s <= onset_t + 0.3]
            busy = [u for u, s, e in self.intervals
                    if u != self.self_id and s < onset_t - 1.0
                    and (e is None or e > onset_t - 0.5)]
        if len(starts) != 1 or busy:
            return
        d = onset_t - starts[0]
        if not -0.3 <= d <= 0.9:
            return
        self.lag_samples = (self.lag_samples + [d])[-40:]
        if len(self.lag_samples) >= 5:
            new = min(0.6, max(0.0, float(sorted(self.lag_samples)[len(self.lag_samples) // 2])))
            if abs(new - self.LAG) > 0.02:
                logging.info("desfase Discord→audio calibrado: %.2fs (%d muestras)",
                             new, len(self.lag_samples))
            self.LAG = new
            self.cfg["discord_lag"] = round(new, 3)

    def attribute(self, t0, words):
        """Reparte las palabras de una frase entre quienes hablaban en ese instante.

        t0    = hora real (time.time) del primer sample del audio
        words = [(palabra, seguridad, inicio_s, fin_s)] con tiempos relativos al audio
        Devuelve [(nombres, i_desde, i_hasta)] — tramos consecutivos de palabras —
        o None si no hay datos de Discord.
        """
        if not self.connected or not words:
            return None
        now = time.time()
        with self.lock:
            ivs = [(uid, s + self.LAG, (now if e is None else e) + self.LAG)
                   for uid, s, e in self.intervals if uid != self.self_id]
            names = dict(self.names)
        # sin los chispazos de ruido (salvo que todavía esté hablando)
        ivs = [(u, s, e) for u, s, e in ivs if e - s >= self.MIN_TALK or e >= now]
        if not ivs:
            return None

        labels = []
        for _, _, ws, we in words:
            a, b = t0 + ws, t0 + max(we, ws + 0.08)
            cov, start, end = {}, {}, {}
            for uid, s, e in ivs:
                ov = min(e, b) - max(s, a)
                if ov > 0:
                    cov[uid] = max(cov.get(uid, 0.0), ov / (b - a))
                    start[uid], end[uid] = s, e
            if not cov:
                labels.append(None)
                continue
            strong = [u for u, c in cov.items() if c >= 0.8]
            if len(strong) >= 2:
                newest = max(strong, key=lambda u: start[u])
                others = [u for u in strong if u != newest]
                # el aviso de "dejó de hablar" de Discord llega tarde: si los demás
                # se callan justo después de que este arranca, es solo él
                if all(end[u] <= start[newest] + 0.6 and end[u] < now for u in others):
                    labels.append(frozenset([newest]))
                else:  # se están pisando de verdad
                    labels.append(frozenset(strong))
            elif strong:
                labels.append(frozenset(strong))
            else:
                labels.append(frozenset([max(cov, key=cov.get)]))

        # palabras sin dato: heredan de la anterior (o de la siguiente si es al inicio)
        for i in range(len(labels)):
            if labels[i] is None and i > 0:
                labels[i] = labels[i - 1]
        for i in range(len(labels) - 1, -1, -1):
            if labels[i] is None and i + 1 < len(labels):
                labels[i] = labels[i + 1]
        if all(l is None for l in labels):
            return None

        # tramos de palabras con la misma etiqueta
        runs = []
        for i, l in enumerate(labels):
            if runs and runs[-1][0] == l:
                runs[-1][2] = i + 1
            else:
                runs.append([l, i, i + 1])
        # un tramo corto (menos de 3 palabras o de 0,8 s) casi siempre es ruido del
        # cruce o un "yeah" encima: se une al vecino para no partir la frase en pedacitos
        def short(r):
            return (r[2] - r[1] < 3) or (words[r[2] - 1][3] - words[r[1]][2] < 0.8)

        changed = True
        while changed and len(runs) > 1:
            changed = False
            for k, r in enumerate(runs):
                if short(r):
                    if k > 0:
                        runs[k - 1][2] = r[2]          # al anterior
                    else:
                        runs[1][1] = r[1]              # el primero, al siguiente
                    runs.pop(k)
                    changed = True
                    break
        merged = runs
        # tras unir, dos tramos seguidos pueden haber quedado con la misma etiqueta
        out = []
        for r in merged:
            if out and out[-1][0] == r[0]:
                out[-1][2] = r[2]
            else:
                out.append(r)

        def label_names(l):
            # el orden: quien arrancó primero
            first = {u: min(s for uu, s, _ in ivs if uu == u) for u in l}
            return [names.get(u, "Alguien") for u in sorted(l, key=first.get)]

        return [(label_names(l), i0, i1) for l, i0, i1 in out]

    def switch_time(self, t0, t1):
        """Si dentro de la ventana alguien dejó de hablar y otra persona arrancó,
        devuelve el instante (en tiempo de audio) en que conviene cortar la frase."""
        if not self.connected:
            return None
        by = self._by_user(t0, t1)
        if len(by) < 2:
            return None
        for uid, (s, e, _) in sorted(by.items(), key=lambda kv: kv[1][0]):
            if s <= t0 - self.LAG + 0.6:
                continue
            others = [v for u, v in by.items() if u != uid and v[0] < s]
            if others and all(v[1] <= s + 0.3 for v in others):
                return s + self.LAG
        return None
