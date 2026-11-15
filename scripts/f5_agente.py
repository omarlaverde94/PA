"""
Fase 5: agente de prueba en papel (NO se apuesta dinero real).

Lee sin parar las cuotas REALES de tu casa (su API pública de Kambi, mercado CO;
dirección en CASA_API_URL) de NFL, NBA, MLB y fútbol, las compara con Pinnacle y
consigo mismas, y avisa por Telegram cada posible error. Guarda todas las cuotas
(solo cambios) y cada alerta con cuánto duró.

Verificación 100 % automática de cada alerta:
- Justo antes de enviarla se vuelve a leer la cuota. Si cambió o ya no está
  abierta, NO se envía (queda registrada con el motivo).
- El mensaje muestra la hora exacta de esa lectura.
- A los 2, 5 y 15 minutos de enviada se vuelve a leer la cuota y se anota si se
  mantuvo. Eso va al resumen diario y al informe final.

Uso:
    python f5_agente.py                    # corre hasta que se detenga
    F5_DURACION=20700 python f5_agente.py  # corre 5 h 45 min y sale (GitHub Actions)

Variables de entorno:
    TELEGRAM_BOT_TOKEN   token del bot (obligatorio para avisar)
    TELEGRAM_CHAT_ID     chat o chats (separados por coma) que reciben avisos
    CASA_API_URL         dirección base de la API de cuotas de tu casa (obligatoria)
    API_FOOTBALL_KEY     clave gratis de API-Football para los marcadores de fútbol (opcional)
    F5_DATA              carpeta de datos (por defecto data/fase5)
    F5_GIT_CADA          si está, cada tantos segundos sube los datos con git
    F5_GIT_DIR           carpeta del repositorio de datos donde hacer git (por defecto la raíz)
                         Si un guardado falla, avisa por Telegram y reintenta cada 5 min.
"""
import os
import re
import signal
import subprocess
import time
import traceback
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import f5_config as C
import f5_emparejar as EM
import f5_fuentes as F
import f5_informe as INF
import f5_resultados as R
import f5_telegram as T
from f5_detector import agregar_pinnacle, contra_mercado, internos
from f5_estimacion import Estimador, en_rango, nivel_ventaja
from f5_tarjeta import tarjeta
from f5_registro import Registro, ahora_iso, iso_a_ts

GIT_REINTENTO_SEG = 300  # reintento del guardado cuando falla
CIERRE_GRACIA = 15 * 60  # un error que desaparece y vuelve antes de 15 min es el mismo

AYUDA = (
    "PRUEBA — no apostar\n\n"
    "Pedidos que entiendo (escríbelos tal cual):\n"
    "• más (respondiendo a una alerta): la explicación completa de esa alerta.\n"
    "• estado: cómo va el agente.\n"
    "• resumen: el resumen del día ahora mismo, con la lista de posibles errores.\n"
    "• detalle: los posibles errores que siguen abiertos en este momento.\n"
    "• solo NFL (o NBA, MLB, fútbol): avisar solo de ese deporte. \"hoy solo NFL\" o "
    "\"hoy enfócate solo en NFL\" vale hasta la medianoche.\n"
    "• todo: volver a avisar de todos los deportes.\n"
    "• rango 1.40 2.00: cambiar el rango de cuota de tu casa que genera alertas "
    "(dos números entre 1.20 y 3.00, el primero menor). \"rango\" solo muestra el vigente.\n"
    "• pausa / seguir: dejar de avisar o volver a avisar (el registro sigue).\n"
    "• apagar: APAGADO DE EMERGENCIA. Deja de leer cuotas y de avisar, también en las "
    "tandas siguientes, hasta que escribas \"encender\".\n"
    "• encender: volver a prender el agente después de \"apagar\".\n"
    "Cualquier otro mensaje se ignora."
)
NO_ENTENDI = "PRUEBA — no apostar\n\nNo es un pedido que yo conozca. Escribe \"ayuda\" para ver la lista."

_DEPORTE = {"nfl": "nfl", "futbol americano": "nfl", "nba": "nba", "baloncesto": "nba",
            "mlb": "mlb", "beisbol": "mlb", "futbol": "futbol"}
_FIJOS = {"mas": "mas", "ayuda": "ayuda", "start": "ayuda", "help": "ayuda", "estado": "estado", "resumen": "resumen",
          "detalle": "detalle",
          "pausa": "pausa", "seguir": "seguir", "todo": "todo", "todos": "todo",
          "apagar": "apagar", "encender": "encender"}
_RE_FOCO = re.compile(r"^(hoy\s+)?(?:(enfocate)\s+)?(?:(solo)\s+)?(?:en\s+)?(?:el\s+)?"
                      r"(nfl|nba|mlb|futbol americano|futbol|beisbol|baloncesto)(\s+hoy)?$")


_RE_RANGO = re.compile(r"^rango\s+(\d(?:[.,]\d{1,2})?)\s+(\d(?:[.,]\d{1,2})?)$")


def interpretar_pedido(texto):
    """
    Traduce un mensaje a uno de los comandos fijos, o None si no es ninguno.
    No se ejecuta ni se evalúa nada del texto: solo se compara con la lista.
    Devuelve (comando, deporte o None, solo_hoy).
    """
    if not isinstance(texto, str) or len(texto) > 60:
        return None
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    t = " ".join(t.strip().lstrip("/").strip(" .!?¡¿").split())
    if t in _FIJOS:
        return _FIJOS[t], None, False
    m = _RE_RANGO.match(t)
    if m:
        a, b = (float(x.replace(",", ".")) for x in m.groups())
        return ("rango", (a, b), False) if C.rango_valido(a, b) else ("rango_invalido", None, False)
    if t == "rango":
        return "rango_ver", None, False
    if t.startswith("rango"):
        return "rango_invalido", None, False
    m = _RE_FOCO.match(t)
    if m and (m.group(2) or m.group(3)):
        return "foco", _DEPORTE[m.group(4)], bool(m.group(1) or m.group(5))
    return None


def error_corto():
    """Tipo de error y dónde pasó (archivo y línea), SIN el mensaje: el mensaje
    podría traer direcciones o datos, y los registros de GitHub son públicos."""
    tipo, _, tb = __import__("sys").exc_info()
    pasos = [f"{os.path.basename(f.filename)}:{f.lineno}" for f in traceback.extract_tb(tb)[-3:]]
    return f"{tipo.__name__ if tipo else '?'} en {' > '.join(pasos)}"


def ocultar_en_registros():
    """En GitHub Actions, pide que el token y el número de chat se tapen con ***
    si alguna vez aparecieran en los registros (GitHub ya lo hace con los secretos)."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    valores = [os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("API_FOOTBALL_KEY", ""), C.CASA_API_URL] + T.chats()
    for v in valores:
        if v:
            print(f"::add-mask::{v}", flush=True)


def col(ts=None):
    return datetime.fromtimestamp(ts or time.time(), C.HORA_LOCAL)


class Agente:
    def __init__(self):
        self.reg = Registro()
        C.cargar_rango()  # el rango elegido con el comando "rango" se mantiene entre tandas
        self.est = Estimador()
        C.NOMBRE_CASA[0] = self.est.casa if self.est.casa != "tu casa de apuestas" else "tu casa"
        self.inicio = time.time()
        self.fin = self.inicio + float(os.environ["F5_DURACION"]) if os.environ.get("F5_DURACION") else None
        self.git_cada = float(os.environ.get("F5_GIT_CADA", "0"))
        self.git_dir = os.environ.get("F5_GIT_DIR", str(C.RAIZ))
        self.ultimo_git = time.time()
        self.git_fallos = 0
        self.ultimo_aviso_git = 0
        self.proxima = {}                 # id de partido -> hora de la próxima lectura completa
        self.ultima_lectura = {}          # id -> hora de la última lectura completa
        self.filas = {}                   # id -> {k: fila} (última lectura)
        self.lista_cuando = {}            # deporte -> hora de la última lista
        # Pinnacle
        self.pin_mu = {}                  # id de liga -> lista de partidos
        self.pin_mu_cuando = {}
        self.pin_futbol = []              # partidos de fútbol (todas las ligas)
        self.pin_futbol_cuando = 0
        self.pin_mk = {}                  # id de liga -> (hora, proposiciones)
        self.pareja = {}                  # id de Kambi -> (liga de Pinnacle, id de partido)
        self.pin_nombre = {}              # id de Kambi -> partido de Pinnacle con que se comparó
        # Pedidos
        self.pedidos = T.Pedidos()
        self.ultimo_pedido = 0
        self.foco = None
        self.foco_hasta = None
        self.pausa = False
        self.avisos_hora = []
        self.ultimo_guardado = time.time()
        self.ultimo_log = time.time()
        self.errores = Counter()
        self.resumen_hecho = self._cargar_resumen_hecho()
        # Liquidación automática
        self.liq = R.Liquidador()
        self.liq_alertas, self.liq_res = [], {}
        self.liq_alertas_cuando = 0
        self.liq_cuando = 0
        self.salir = False
        signal.signal(signal.SIGTERM, self._senal)
        signal.signal(signal.SIGINT, self._senal)

    def _senal(self, *_):
        self.salir = True

    def _cargar_resumen_hecho(self):
        try:
            return (C.DATA / "ultimo_resumen.txt").read_text().strip()
        except OSError:
            return ""

    def log(self, *a):
        print(col().strftime("%m-%d %H:%M:%S"), *a, flush=True)

    # ------------------------------------------------------------------ listas
    def leer_listas(self):
        for dep in C.DEPORTES:
            if time.time() - self.lista_cuando.get(dep, 0) < C.LISTA_CADA:
                continue
            self.lista_cuando[dep] = time.time()
            evs = F.kambi_lista(dep)
            filas = []
            for e in evs:
                ev = e["event"]
                info = F.info_evento(ev, dep)
                if info["estado"] != "NOT_STARTED" or not info["inicio"]:
                    if ev["id"] in self.reg.eventos:
                        self.partido_empezo(ev["id"])
                    continue
                if C.deporte_excluido(info):
                    continue  # tenis de mesa u otro deporte excluido: ni se vigila
                self.reg.evento(info)
                if ev["id"] not in self.proxima:
                    self.proxima[ev["id"]] = time.time()
                bos = [dict(b, eventId=ev["id"]) for b in e.get("betOffers", [])]
                filas += F.filas_kambi(bos)
            self.reg.cuotas_leidas(filas, C.KAMBI_PRINCIPAL, completo=False)
            por_ev = defaultdict(list)
            for f in filas:
                por_ev[f["ev"]].append(f)
            for evid, fs in por_ev.items():
                actual = self.filas.setdefault(evid, {})
                for f in fs:
                    actual[f["k"]] = f
                self.detectar(evid)

    # ------------------------------------------------------------------ Pinnacle
    def refrescar_pinnacle(self):
        ahora = time.time()
        for dep, d in C.DEPORTES.items():
            for liga in d.get("pin_ligas", []):
                if ahora - self.pin_mu_cuando.get(liga, 0) >= C.PIN_PARTIDOS_EEUU_CADA:
                    mu = F.pin(f"/leagues/{liga}/matchups")
                    if mu is not None:
                        self.pin_mu[liga] = mu
                    self.pin_mu_cuando[liga] = ahora
        if ahora - self.pin_futbol_cuando >= C.PIN_PARTIDOS_CADA:
            mu = F.pin("/sports/29/matchups")
            if mu is not None:
                self.pin_futbol = [m for m in mu if m.get("type") == "matchup" and not m.get("parentId")]
                por_liga = defaultdict(list)
                for m in mu:
                    por_liga[m["league"]["id"]].append(m)
                for liga, ms in por_liga.items():
                    self.pin_mu[liga] = ms
                    self.pin_mu_cuando[liga] = ahora
            self.pin_futbol_cuando = ahora

        # Cuotas por liga, más seguido si hay partidos por empezar
        ligas_cerca, ligas_lejos = set(), set()
        for evid, (liga, _) in self.pareja.items():
            info = self.reg.eventos.get(evid)
            if not info or self.reg.empezo(evid):
                continue
            falta = info["_ts"] - ahora
            (ligas_cerca if falta < 5400 else ligas_lejos).add(liga)
        for liga in ligas_cerca | ligas_lejos:
            cada = C.PIN_CUOTAS_CERCA if liga in ligas_cerca else C.PIN_CUOTAS_LEJOS
            hora, _ = self.pin_mk.get(liga, (0, None))
            if ahora - hora < cada:
                continue
            mk = F.pin(f"/leagues/{liga}/markets/straight")
            if mk is None:
                self.errores["pinnacle"] += 1
                continue
            self.pin_mk[liga] = (time.time(), F.pinnacle_proposiciones(self.pin_mu.get(liga, []), mk))

    def emparejar(self, evid):
        """
        Busca el partido de Pinnacle que corresponde a uno de Kambi. Tienen que coincidir
        la hora de inicio (margen corto), la competencia, la categoría (sub-21, reservas,
        femenino) y los nombres de los dos equipos (ver f5_emparejar.py).
        """
        if evid in self.pareja:
            return self.pareja[evid]
        info = self.reg.eventos.get(evid)
        if not info:
            return None
        dep = info["deporte"]
        if dep == "futbol":
            partidos = self.pin_futbol
        else:
            partidos = [m for liga in C.DEPORTES[dep]["pin_ligas"] for m in self.pin_mu.get(liga, [])
                        if m.get("type") == "matchup" and not m.get("parentId")]
        candidatos = []
        for m in partidos:
            try:
                ini = iso_a_ts(m["startTime"].replace("+00:00", "Z"))
            except (KeyError, ValueError):
                continue
            h = next((p["name"] for p in m["participants"] if p["alignment"] == "home"), "")
            a = next((p["name"] for p in m["participants"] if p["alignment"] == "away"), "")
            if "(" in h or "(" in a:
                continue
            candidatos.append({"local": h, "visitante": a, "ts": ini, "liga": m["league"].get("name", ""), "m": m})
        # En NFL, NBA y MLB la liga de Pinnacle ya es la correcta: no hace falta revisar la competencia
        elegido = EM.mejor(info, candidatos, C.PIN_MARGEN_INICIO, revisar_competencia=(dep == "futbol"))
        if elegido:
            mejor = elegido["m"]
            self.pareja[evid] = (mejor["league"]["id"], mejor["id"])
            # Se anota con qué partido de Pinnacle se comparó (para poder revisarlo después)
            self.pin_nombre[evid] = {"pin_partido": f"{elegido['local']} - {elegido['visitante']}",
                                     "pin_liga": elegido["liga"], "pin_inicio": ahora_iso(elegido["ts"]),
                                     "pin_id": mejor["id"]}
            return self.pareja[evid]
        return None

    def props_pinnacle(self, evid):
        par = self.emparejar(evid)
        if not par:
            return [], None
        liga, pm = par
        hora, props = self.pin_mk.get(liga, (0, None))
        if not props or time.time() - hora > C.PIN_VIEJA:
            return [], None
        return [p for p in props if p["pm"] == pm], hora

    # ------------------------------------------------------------------ lecturas completas
    def cada_cuanto(self, info):
        falta_min = (info["_ts"] - time.time()) / 60
        if info["deporte"] == "futbol" and falta_min > C.FUTBOL_HORAS_COMPLETO * 60:
            return None
        for limite, seg in C.FRECUENCIA:
            if falta_min <= limite:
                return seg
        return None

    def lecturas_completas(self, tope_seg=20):
        ahora = time.time()
        pendientes = []
        for evid, cuando in self.proxima.items():
            info = self.reg.eventos.get(evid)
            if not info or self.reg.empezo(evid):
                continue
            cada = self.cada_cuanto(info)
            if cada is None or cuando > ahora:
                continue
            if self.foco and info["deporte"] not in self.foco:
                cada *= 3  # con foco, lo demás se lee menos seguido
            pendientes.append((info["_ts"], evid, cada))
        pendientes.sort()
        t0 = time.time()
        for i in range(0, len(pendientes), C.KAMBI_LOTE):
            if time.time() - t0 > tope_seg or self.salir:
                break
            lote = pendientes[i:i + C.KAMBI_LOTE]
            ids = [evid for _, evid, _ in lote]
            bos, eventos = F.kambi_mercados(ids)
            if bos is None:
                self.errores["kambi"] += 1
                for _, evid, cada in lote:
                    self.proxima[evid] = time.time() + 60
                continue
            filas = F.filas_kambi(bos)
            # Partidos que ya no vienen o ya empezaron
            for evid in ids:
                ev = (eventos or {}).get(evid)
                if ev and ev.get("state") != "NOT_STARTED":
                    self.partido_empezo(evid)
            self.reg.cuotas_leidas(filas, C.KAMBI_PRINCIPAL, completo=True,
                                   ids_eventos=[e for e in ids if e in (eventos or {})])
            por_ev = defaultdict(dict)
            for f in filas:
                por_ev[f["ev"]][f["k"]] = f
            for _, evid, cada in lote:
                self.proxima[evid] = time.time() + cada
                if evid in (eventos or {}) and not self.reg.empezo(evid):
                    self.filas[evid] = por_ev.get(evid, {})
                    self.ultima_lectura[evid] = time.time()
                    self.detectar(evid)

    def partido_empezo(self, evid):
        if evid in self.reg.cerrados:
            return
        for clave, a in list(self.reg.alertas_abiertas.items()):
            if a["ev"] == evid:
                self.cerrar_alerta(clave, "empezó el partido")
        self.reg.cerrar_evento(evid)
        self.filas.pop(evid, None)
        self.proxima.pop(evid, None)

    # ------------------------------------------------------------------ detección
    def hallazgos(self, evid, filas, info):
        """Todas las señales de un partido con sus cuotas actuales, ya evaluadas con la cuota de tu
        casa (si merecen alerta: dentro del rango, sobre el justo y sin pasar el tope de ventaja)."""
        props, hora_pin = self.props_pinnacle(evid)
        hallazgos = agregar_pinnacle(internos(filas, info) + contra_mercado(filas, info, props),
                                     filas, info, props)
        por_k = {f["k"]: f for f in filas}
        for h in hallazgos:
            h.update(self.est.evaluar(h, por_k, info["deporte"]))
        return hallazgos, hora_pin

    def detectar(self, evid):
        info = self.reg.eventos.get(evid)
        filas = list(self.filas.get(evid, {}).values())
        if not info or not filas or self.reg.empezo(evid):
            return
        hallazgos, hora_pin = self.hallazgos(evid, filas, info)
        vistos = {h["clave"] for h in hallazgos}
        ahora = time.time()
        por_k = {f["k"]: f for f in filas}

        # Errores abiertos que ya no están: se corrigieron (o se quitaron)
        for clave, a in list(self.reg.alertas_abiertas.items()):
            if a["ev"] != evid:
                continue
            if clave in vistos:
                a.pop("ausente_desde", None)
                f = por_k.get(a["k"])
                if f:
                    a["cuota_max"] = max(a.get("cuota_max", 0), f["odds"])
                h = next(x for x in hallazgos if x["clave"] == clave)
                if h["avisable"] and not a.get("avisable"):
                    # La señal pasó a merecer alerta (por ejemplo, la cuota entró al rango)
                    a.update({k: h[k] for k in ("cuota", "cuota_rb", "rango_rb", "justa_rb", "ventaja_rb",
                                                  "en_rango", "avisable", "ventaja", "ventaja_pin", "ventaja_alta",
                                                  "descartada")})
                    a["avisable_desde"] = ahora_iso(ahora)
                    self.avisar(info, [a])
                continue
            if "ausente_desde" not in a:
                f = por_k.get(a["k"])
                a["ausente_desde"] = ahora
                if f is None:
                    a["motivo_cierre"] = "Kambi quitó la apuesta"
                elif f["st"] != "OPEN":
                    a["motivo_cierre"] = "Kambi suspendió la apuesta"
                else:
                    a["motivo_cierre"] = "cuota corregida"
                a["cuota_final"] = f["odds"] if f else None

        nuevos = [h for h in hallazgos if h["clave"] not in self.reg.alertas_abiertas]
        if not nuevos:
            return
        # Las cuotas ya son las reales de tu casa: no hace falta confirmar con otra casa.
        # La verificación se hace al enviar (relectura) y después (seguimiento).
        para_avisar = []
        for h in nuevos:
            extra = {
                "id": f"{int(ahora)}-{h['k']}-{h['regla']}", "deporte": info["deporte"],
                "partido": info["nombre"], "liga": info["liga"], "inicio": info["inicio"],
                "detectado": ahora_iso(ahora), "detectado_ts": ahora,
                "min_al_inicio": round((info["_ts"] - ahora) / 60),
                "cuota_max": h["cuota"], "pin_hora": ahora_iso(hora_pin) if hora_pin else None,
                "avisado": False,
            }
            if h.get("regla") == "mercado" or h.get("pin_justa"):
                extra.update(self.pin_nombre.get(evid, {}))
            self.reg.alerta_abre(h, extra)
            para_avisar.append(self.reg.alertas_abiertas[h["clave"]])
        self.avisar(info, para_avisar)

    def cerrar_alerta(self, clave, motivo=None):
        a = self.reg.alertas_abiertas.get(clave)
        if not a:
            return
        # La duración se mide hasta que el error desapareció por primera vez
        fin = a.get("ausente_desde")
        if motivo is None or fin:
            motivo = a.get("motivo_cierre", motivo or "cuota corregida")
        self.reg.alerta_cierra(clave, motivo, a.get("cuota_final"), fin=fin)

    def revisar_cierres(self):
        ahora = time.time()
        for clave, a in list(self.reg.alertas_abiertas.items()):
            if "ausente_desde" in a and ahora - a["ausente_desde"] >= CIERRE_GRACIA:
                self.cerrar_alerta(clave)
            elif self.reg.empezo(a["ev"]):
                self.cerrar_alerta(clave, "empezó el partido")

    # ------------------------------------------------------------------ avisos
    def avisar(self, info, alertas):
        if not alertas:
            return
        ahora = time.time()
        self.avisos_hora = [t for t in self.avisos_hora if t > ahora - 3600]
        if self.foco_hasta and ahora > self.foco_hasta:
            self.foco, self.foco_hasta = None, None
        if C.deporte_excluido(info):
            # Tenis de mesa (y cualquier deporte excluido): nunca se envía una alerta
            for a in alertas:
                a["no_enviada"] = "deporte excluido de las alertas"
            return
        if self.pausa or (self.foco and info["deporte"] not in self.foco):
            return
        # Solo alertas con la cuota REAL de tu casa dentro del rango vigente y por encima del justo.
        # Todo lo demás queda registrado para el resumen diario y el informe final.
        candidatas = [a for a in alertas if a.get("avisable")
                      and ahora - self.reg.avisados.get(a["clave"], 0) > C.REPETIR_AVISO_SEG]
        if not candidatas or len(self.avisos_hora) >= C.MAX_AVISOS_HORA:
            return
        # Relectura de la cuota justo antes de enviar (una sola consulta para el partido)
        fresca, hora_lectura = self.releer(info["id"])
        recalculo = None
        if fresca is not None and any((fresca.get(a["k"]) or {}).get("odds") not in (None, a["cuota"])
                                      for a in candidatas):
            # Alguna cuota cambió: se vuelven a calcular las señales del partido con las cuotas nuevas
            recalculo = {h["clave"]: h for h in self.hallazgos(info["id"], list(fresca.values()), info)[0]}
        enviar = [a for a in candidatas if self.revisar_antes_de_enviar(info, a, fresca, hora_lectura, recalculo)]
        if not enviar:
            self.log(f"alerta no enviada (no pasó la relectura): {len(candidatas)} apuestas")
            return
        enviar.sort(key=lambda a: -(a.get("ventaja_rb") or 0))
        texto = INF.texto_mensaje(enviar[:5], len(enviar) - 5)
        imagen = tarjeta([INF.bloque_tarjeta(a) for a in enviar[:5]])
        url = self.est.enlace_partido(info.get("id"))
        boton = (f"Abrir en {self.est.casa}", url) if url else None
        envios = T.enviar_alerta(texto, imagen, boton)
        ids = [e["id"] for e in envios]
        if ids:
            for e in envios:
                # Para editar el mensaje cuando se liquide: qué alertas lleva y cómo se envió
                self.reg.envios.append({**e, "alertas": [a["id"] for a in enviar[:5]],
                                        "resto": len(enviar) - 5 if len(enviar) > 5 else 0,
                                        "ev": info.get("id"), "ts": ahora})
            for mid in ids:
                self.reg.mensajes[str(mid)] = [a["id"] for a in enviar]
            self.reg.mensajes["ultimo"] = [a["id"] for a in enviar]
            self.avisos_hora.append(ahora)
            enviado_ts = time.time()
            for a in enviar:
                a["avisado"] = True
                a["avisado_ts"] = enviado_ts
                self.reg.avisados[a["clave"]] = enviado_ts
                self.programar_seguimiento(a, info, enviado_ts)
            self.log(f"aviso enviado: {info['nombre']} ({len(enviar)} apuestas)")
            # Guardar y subir los datos enseguida: así ninguna alerta avisada se pierde si la tanda se corta
            self.ultimo_guardado = 0
            self.ultimo_git = 0

    def releer(self, evid):
        """
        Lee otra vez, en ese mismo instante, todas las cuotas de un partido en la API de tu casa.
        Devuelve ({k: fila} o None si no se pudo leer o el partido ya empezó, hora de la lectura).
        La lectura también queda en el registro de cuotas.
        """
        if self.reg.empezo(evid):
            return None, time.time()  # nunca se leen cuotas en vivo
        bos, eventos = F.kambi_mercados([evid])
        hora = time.time()
        if bos is None:
            self.errores["relectura"] += 1
            return None, hora
        ev = (eventos or {}).get(evid)
        if not ev or ev.get("state") != "NOT_STARTED" or self.reg.empezo(evid):
            if ev and ev.get("state") != "NOT_STARTED":
                self.partido_empezo(evid)
            return None, hora
        filas = F.filas_kambi(bos)
        self.reg.cuotas_leidas(filas, C.KAMBI_PRINCIPAL, completo=True, ids_eventos=[evid])
        fresca = {f["k"]: f for f in filas}
        self.filas[evid] = fresca
        self.ultima_lectura[evid] = hora
        return fresca, hora

    def revisar_antes_de_enviar(self, info, a, fresca, hora_lectura, recalculo=None):
        """
        Última revisión con la cuota RELEÍDA justo antes de enviar:
        - Si la apuesta está suspendida o quitada, la API no respondió o el partido empezó: no se envía.
        - Si la cuota cambió: se recalcula la señal con la cuota nueva y se envía con esa cuota solo
          si sigue cumpliendo todas las reglas (rango, ventaja mínima y tope del 15 %).
        - Si no cambió: se revisan otra vez el rango y el tope.
        Lo que no se envía queda registrado con el motivo.
        """
        a["relectura_ts"] = hora_lectura
        a["relectura"] = ahora_iso(hora_lectura)
        if fresca is None:
            a["no_enviada"] = "no se pudo releer la cuota justo antes de enviar (o el partido ya empezó)"
            return False
        f = fresca.get(a["k"])
        if not f or f["st"] != "OPEN":
            a["no_enviada"] = "la apuesta ya no estaba abierta al releerla justo antes de enviar"
            a["cuota_relectura"] = f["odds"] if f else None
            return False
        a["cuota_relectura"] = f["odds"]
        if abs(f["odds"] - a["cuota"]) > 1e-9:
            vieja = a["cuota"]
            cambio = f"la cuota cambió al releerla justo antes de enviar ({vieja:.2f} → {f['odds']:.2f})"
            h = (recalculo or {}).get(a["clave"])
            nivel = nivel_ventaja(h.get("ventaja_pin")) if h else None
            if not h:
                motivo = "ya no tiene ventaja suficiente"
            elif nivel == "excesiva":
                motivo = "la ventaja pasa del tope del 15 %"
            elif not en_rango(h.get("cuota_rb")):
                motivo = f"queda fuera del rango {C.texto_rango().replace(' y ', '–')}"
            elif not h.get("avisable"):
                motivo = "ya no tiene ventaja suficiente"
            else:
                motivo = None
            if motivo:
                a["no_enviada"] = f"{cambio} y con la cuota nueva {motivo}"
                if nivel == "excesiva":
                    a["descartada"] = C.MOTIVO_VENTAJA_EXCESIVA
                return False
            # Sigue cumpliendo todo: se envía con la cuota nueva y los valores recalculados
            nuevos = {k: v for k, v in h.items() if k not in ("clave", "k", "ev", "casa")}
            a.update(nuevos)
            a["cuota_detectada"] = vieja
            a["recalculada"] = True
        est, _ = self.est.estimar(f, info["deporte"])
        if not en_rango(est) or not en_rango(a.get("cuota_rb")):
            a["no_enviada"] = "la cuota salió del rango justo antes de enviar"
            return False
        if nivel_ventaja(a.get("ventaja_pin")) == "excesiva":
            a["descartada"] = C.MOTIVO_VENTAJA_EXCESIVA
            return False
        a.pop("no_enviada", None)
        a["cuota_envio"] = f["odds"]
        return True

    # ------------------------------------------------------------------ seguimiento después del aviso
    def programar_seguimiento(self, a, info, enviado_ts):
        """Anota que hay que volver a leer esta cuota a los 2, 5 y 15 minutos del aviso."""
        self.reg.seguimientos.append({
            "id": a["id"], "k": a["k"], "ev": info["id"], "cuota": a.get("cuota_envio") or a["cuota"],
            "enviado_ts": enviado_ts, "faltan": list(C.SEGUIMIENTO_MIN)})

    def seguimiento(self):
        """
        Vuelve a leer la cuota de cada alerta enviada cuando le toca (2, 5 y 15 min después)
        y registra si se mantuvo: abierta y pagando lo mismo o más que en el aviso.
        Nunca se leen cuotas en vivo: si el partido ya empezó, se anota y no se mide.
        """
        ahora = time.time()
        tocan = defaultdict(list)
        for s in self.reg.seguimientos:
            if s["faltan"] and ahora >= s["enviado_ts"] + s["faltan"][0] * 60:
                tocan[s["ev"]].append(s)
        for evid, lista in tocan.items():
            if self.reg.empezo(evid):
                fresca, hora = None, ahora
                razon = "empezó el partido (no se leen cuotas en vivo)"
            else:
                fresca, hora = self.releer(evid)
                razon = "empezó el partido (no se leen cuotas en vivo)" if self.reg.empezo(evid) else "no se pudo leer"
            for s in lista:
                minuto = s["faltan"].pop(0)
                f = (fresca or {}).get(s["k"])
                if fresca is None:
                    estado, cuota, mantuvo = razon, None, None
                    if razon.startswith("empezó"):
                        s["faltan"] = []  # los siguientes tampoco se pueden medir
                elif not f:
                    estado, cuota, mantuvo = "quitada", None, False
                elif f["st"] != "OPEN":
                    estado, cuota, mantuvo = "suspendida", f["odds"], False
                else:
                    cuota = f["odds"]
                    estado = "igual" if abs(cuota - s["cuota"]) < 1e-9 else ("subió" if cuota > s["cuota"] else "bajó")
                    mantuvo = cuota >= s["cuota"] - 1e-9
                self.reg.alertas_f.agregar({
                    "evento": "seguimiento", "id": s["id"], "min": minuto, "hora": ahora_iso(hora),
                    "seg_desde_aviso": round(hora - s["enviado_ts"]), "cuota_aviso": s["cuota"],
                    "cuota": cuota, "estado": estado, "se_mantuvo": mantuvo})
        antes = len(self.reg.seguimientos)
        self.reg.seguimientos = [s for s in self.reg.seguimientos if s["faltan"]]
        if tocan or len(self.reg.seguimientos) != antes:
            self.ultimo_guardado = 0

    # ------------------------------------------------------------------ pedidos
    def atender_pedidos(self):
        if time.time() - self.ultimo_pedido < 10 or not T.activo():
            return
        self.ultimo_pedido = time.time()
        for chat, texto, responde_a in self.pedidos.nuevos():
            pedido = interpretar_pedido(texto)
            if pedido is None:
                T.enviar(NO_ENTENDI, chat=chat)
                continue
            cmd, deporte, solo_hoy = pedido
            self.log("pedido recibido:", cmd)  # solo el nombre del comando, nunca el texto
            if cmd == "mas":
                T.enviar(self.texto_mas(responde_a), chat=chat)
            elif cmd == "ayuda":
                T.enviar(AYUDA, chat=chat)
            elif cmd == "estado":
                T.enviar(self.texto_estado(), chat=chat)
            elif cmd == "apagar":
                self.apagar(chat)
            elif cmd == "encender":
                self.encender(chat)
            elif self.apagado():
                T.enviar("PRUEBA — no apostar\n\nEl agente está APAGADO. Escribe \"encender\" para prenderlo.", chat=chat)
            elif cmd == "resumen":
                T.enviar(INF.resumen_diario(self.reg, col().date()), chat=chat)
            elif cmd == "detalle":
                T.enviar(INF.detalle_abiertos(self.reg), chat=chat)
            elif cmd == "pausa":
                self.pausa = True
                T.enviar("PRUEBA — no apostar\nListo: dejo de avisar (sigo registrando). Escribe \"seguir\" para volver.", chat=chat)
            elif cmd == "seguir":
                self.pausa = False
                T.enviar("PRUEBA — no apostar\nListo: vuelvo a avisar.", chat=chat)
            elif cmd == "rango_ver":
                T.enviar(f"PRUEBA — no apostar\n\nRango de alertas vigente: cuota real de tu casa entre "
                         f"{C.texto_rango()}. Para cambiarlo escribe, por ejemplo, \"rango 1.40 2.00\".", chat=chat)
            elif cmd == "rango_invalido":
                T.enviar("PRUEBA — no apostar\n\nNo cambié el rango. Escribe \"rango\" y dos números entre "
                         f"{C.RANGO_LIMITES[0]:.2f} y {C.RANGO_LIMITES[1]:.2f}, el primero menor que el segundo. "
                         f"Por ejemplo: \"rango 1.40 2.00\". Rango vigente: {C.texto_rango()}.", chat=chat)
            elif cmd == "rango":
                C.guardar_rango(*deporte)
                self.log("rango de alertas cambiado a", C.texto_rango())
                self.guardar(forzar=True)
                T.enviar(f"PRUEBA — no apostar\n\nListo: desde ahora solo te aviso si la cuota real de tu casa "
                         f"está entre {C.texto_rango()} y sigue por encima del precio justo. Este rango se mantiene "
                         "en las tandas siguientes. Todo lo demás se sigue registrando para el resumen y el informe.",
                         chat=chat)
            elif cmd == "todo":
                self.foco, self.foco_hasta = None, None
                T.enviar("PRUEBA — no apostar\nListo: aviso de todos los deportes.", chat=chat)
            elif cmd == "foco":
                self.foco = {deporte}
                nombre = C.DEPORTES[deporte]["nombre"]
                if solo_hoy:
                    manana = (col() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                    self.foco_hasta = manana.timestamp()
                    T.enviar(f"PRUEBA — no apostar\nListo: hoy (hasta la medianoche) solo aviso de {nombre}. "
                             "Sigo registrando todo lo demás.", chat=chat)
                else:
                    self.foco_hasta = None
                    T.enviar(f"PRUEBA — no apostar\nListo: solo aviso de {nombre} hasta que me escribas \"todo\".",
                             chat=chat)

    def texto_mas(self, responde_a):
        """
        La explicación completa de la alerta a la que se responde (o de la última).
        Busca primero por el número del mensaje; si la alerta es anterior a que se
        guardaran esos números, la reconoce por el partido y la apuesta del texto.
        """
        self.reg.alertas_f.volcar()  # que el archivo tenga también lo más reciente
        todas = {a["id"]: a for a in INF.leer_alertas()}
        todas.update({a["id"]: a for a in self.reg.alertas_abiertas.values()})
        alertas = []
        if responde_a:
            ids = self.reg.mensajes.get(str(responde_a.get("id"))) or []
            alertas = [todas[i] for i in ids if i in todas]
            if not alertas:
                alertas = INF.buscar_por_texto(list(todas.values()), responde_a.get("texto"), responde_a.get("fecha"))
            if not alertas:
                return ("PRUEBA — no apostar\n\nNo encontré esa alerta en el registro. Puede ser de una tanda "
                        "cuyos datos no alcanzaron a guardarse. Escribe \"más\" solo para ver la última alerta.")
        else:
            ids = self.reg.mensajes.get("ultimo") or []
            alertas = [todas[i] for i in ids if i in todas] or INF.ultimas_avisadas(list(todas.values()))
            if not alertas:
                return "PRUEBA — no apostar\n\nTodavía no hay alertas enviadas en el registro."
        a0 = alertas[0]
        info = {"deporte": a0["deporte"], "nombre": a0["partido"], "liga": a0.get("liga", ""), "inicio": a0["inicio"]}
        return INF.texto_alerta(info, alertas, len(alertas))

    # ------------------------------------------------------------------ apagado de emergencia
    def apagado(self):
        return (C.DATA / "apagado.txt").exists()

    def apagar(self, chat):
        (C.DATA / "apagado.txt").write_text(ahora_iso())
        self.log("APAGADO DE EMERGENCIA pedido por Telegram")
        T.enviar("PRUEBA — no apostar\n\nAPAGADO. Dejé de leer cuotas y de avisar, y seguiré apagado en las "
                 "tandas siguientes. Escribe \"encender\" para volver a prenderlo.", chat=chat)
        self.guardar(forzar=True)

    def encender(self, chat):
        if self.apagado():
            (C.DATA / "apagado.txt").unlink()
            self.log("agente encendido de nuevo por Telegram")
            self.guardar(forzar=True)
        T.enviar("PRUEBA — no apostar\n\nEncendido: vuelvo a leer cuotas y a avisar.", chat=chat)

    def texto_estado(self):
        vig = [e for e in self.reg.eventos if not self.reg.empezo(e)]
        dep = Counter(self.reg.eventos[e]["deporte"] for e in vig)
        horas = (time.time() - self.inicio) / 3600
        foco = ", ".join(sorted(self.foco)) if self.foco else "todos"
        por_dep = ", ".join(C.DEPORTES[d]["nombre"] + " " + str(n) for d, n in dep.items())
        return ("PRUEBA — no apostar\n\nEstado del agente\n"
                f"• Corriendo hace {horas:.1f} h (hora local: {col().strftime('%H:%M')}).\n"
                f"• Partidos vigilados: {len(vig)} ({por_dep}).\n"
                f"• Lecturas de cuotas: {F.KAMBI.consultas + F.PIN.consultas} (fallidas {F.KAMBI.fallos + F.PIN.fallos}).\n"
                f"• Cuotas nuevas registradas: {self.reg.contador['nuevas']}; cambios: {self.reg.contador['cambios']}.\n"
                f"• Errores abiertos ahora: {len(self.reg.alertas_abiertas)}.\n"
                f"• Rango de alertas: cuota real de tu casa entre {C.texto_rango()}.\n"
                f"• Cuotas: reales de {self.est.casa} (API) · relecturas pendientes: {len(self.reg.seguimientos)}.\n"
                f"• Resultados: Kambi en vivo y ESPN; API-Football "
                f"{('con aviso: ' + self.liq.apifoot.aviso) if self.liq.apifoot.aviso else ('activa' if self.liq.apifoot.activa() else 'sin clave')}"
                f" · mensajes por editar: {len(self.reg.envios)}.\n"
                f"• Deportes con aviso: {foco}{' (en pausa)' if self.pausa else ''}"
                f"{' — APAGADO (escribe encender)' if self.apagado() else ''}.")

    # ------------------------------------------------------------------ guardado
    def guardar(self, forzar=False):
        if not forzar and time.time() - self.ultimo_guardado < C.GUARDAR_CADA:
            return
        self.ultimo_guardado = time.time()
        self.reg.guardar()
        # Si el último envío falló, se reintenta cada 5 minutos en vez de cada 30.
        cada = GIT_REINTENTO_SEG if self.git_fallos else self.git_cada
        if self.git_cada and (forzar or time.time() - self.ultimo_git >= cada):
            self.ultimo_git = time.time()
            self.subir_git()

    def _git(self, *args):
        """Corre git sin mostrar nunca su salida: podría traer la dirección del
        repositorio de datos. Solo se usa el código de salida."""
        return subprocess.run(["git", "-C", self.git_dir, *args], capture_output=True).returncode == 0

    def probar_guardado(self):
        """Al arrancar: comprueba que se puede escribir en el repositorio de datos."""
        if not self.git_cada:
            return
        if self._git("push", "--dry-run", "-q", "origin", "HEAD"):
            self.log("guardado de datos: acceso comprobado")
        else:
            self.git_fallos += 1
            self.log("guardado de datos: SIN acceso de escritura (se reintenta)")
            self.avisar_fallo_guardado()

    def avisar_fallo_guardado(self):
        # Como máximo un aviso por hora; nunca con detalles de la conexión.
        if time.time() - self.ultimo_aviso_git < 3600:
            return
        self.ultimo_aviso_git = time.time()
        T.enviar("PRUEBA — no apostar\n\nNo pude guardar los datos en el repositorio de datos "
                 f"(intentos fallidos seguidos: {self.git_fallos}). Los datos siguen guardados en la tanda "
                 "actual y reintento cada 5 minutos. Si sigue fallando, revisa que el token del repositorio "
                 "privado no haya vencido.")

    def subir_git(self):
        try:
            ruta = os.path.relpath(C.DATA, self.git_dir)
            self._git("add", "-A", ruta)
            hubo_commit = self._git("commit", "-q", "-m", f"Fase 5: datos de la prueba en papel hasta {ahora_iso()}")
            if not hubo_commit and not self.git_fallos:
                return  # nada nuevo y nada pendiente
            for intento in range(4):
                if self._git("push", "-q", "origin", "HEAD"):
                    if self.git_fallos:
                        T.enviar("PRUEBA — no apostar\n\nListo: el guardado de datos volvió a funcionar.")
                    self.git_fallos = 0
                    self.log("datos subidos")
                    return
                time.sleep(2 ** (intento + 1))
            self.git_fallos += 1
            self.log(f"no se pudieron subir los datos (fallos seguidos: {self.git_fallos}); reintento en 5 min")
            self.avisar_fallo_guardado()
        except OSError as e:
            self.log("git falló:", type(e).__name__)

    # ------------------------------------------------------------------ liquidación
    def liquidar(self, forzar=False):
        """
        Liquida sola cada alerta cuando su partido termina (con reintentos; a las 48 h queda
        "sin resultado") y edita el mensaje original de Telegram con el resultado.
        Mientras hay partidos con alertas en juego, lee el marcador en vivo de Kambi.
        """
        ahora = time.time()
        if forzar or ahora - self.liq_alertas_cuando >= 300:
            self.liq_alertas_cuando = ahora
            self.reg.alertas_f.volcar()
            self.liq_alertas = INF.leer_alertas()
            self.liq_res = R.leer_resultados()
        self.liq.kambi.leer(self.liq.en_juego(self.liq_alertas, self.liq_res))
        if not forzar and ahora - self.liq_cuando < 120:
            return
        self.liq_cuando = ahora
        nuevas = self.liq.ciclo(self.liq_alertas, self.liq_res, R.catalogo, forzar=forzar)
        if nuevas:
            R.guardar_resultados(self.liq_res)
            self.log(f"alertas liquidadas: {len(nuevas)}")
            self.ultimo_guardado = 0
        self.liq.guardar()
        self.editar_mensajes()

    def editar_mensajes(self):
        """Agrega ✅ Ganada / ❌ Perdida / ↩️ Nula al mensaje original de cada alerta liquidada."""
        if not self.reg.envios:
            return
        por_id = {a["id"]: a for a in self.liq_alertas}
        quedan = []
        for e in self.reg.envios:
            hechas = e.setdefault("editadas", [])
            listas = [i for i in e["alertas"] if i in self.liq_res and i not in hechas]
            if not listas or not all(i in por_id for i in e["alertas"]):
                # Nada nuevo. Se deja de esperar a los 4 días.
                if time.time() - e.get("ts", 0) < 4 * 86400:
                    quedan.append(e)
                continue
            texto = INF.texto_mensaje([por_id[i] for i in e["alertas"]], e.get("resto", 0), self.liq_res)
            url = self.est.enlace_partido(e.get("ev"))
            if T.editar_alerta(e, texto, (f"Abrir en {self.est.casa}", url) if url else None):
                hechas += listas
                e.pop("fallos", None)
            else:
                e["fallos"] = e.get("fallos", 0) + 1
            pendientes = [i for i in e["alertas"] if i not in hechas]
            if pendientes and e.get("fallos", 0) < 5:
                quedan.append(e)
            elif e.get("fallos", 0) >= 5:
                self.log("no se pudo editar un mensaje de alerta (se deja de intentar)")
        self.reg.envios = quedan

    def resumen_si_toca(self):
        ahora = col()
        hoy = ahora.date().isoformat()
        if ahora.hour >= C.RESUMEN_HORA_LOCAL and self.resumen_hecho != hoy:
            self.resumen_hecho = hoy
            (C.DATA / "ultimo_resumen.txt").write_text(hoy)
            try:
                self.liquidar(forzar=True)
            except Exception:  # los resultados no deben tumbar al agente
                self.log("no se pudieron bajar resultados:", error_corto())
            T.enviar(INF.resumen_diario(self.reg, ahora.date()))
            self.log("resumen diario enviado")

    # ------------------------------------------------------------------ ciclo
    def dias_de_prueba(self):
        """Días desde la primera tanda de la prueba (esa tanda es el día 1)."""
        ruta = C.DATA / "inicio_prueba.txt"
        if not ruta.exists():
            ruta.write_text(ahora_iso())
        return (time.time() - iso_a_ts(ruta.read_text().strip())) / 86400

    def prueba_terminada(self):
        """La prueba dura C.DIAS_PRUEBA días (14) desde la primera vez que corrió."""
        return self.dias_de_prueba() >= C.DIAS_PRUEBA

    def _informe(self, titulo, archivo_tablas):
        """Liquida lo que se pueda, guarda las tablas completas y envía el informe corto por Telegram."""
        try:
            self.liquidar(forzar=True)
        except Exception:  # los resultados no deben impedir el informe
            self.log("no se pudieron bajar resultados:", error_corto())
        alertas = INF.leer_alertas()
        INF.tablas_finales(archivo_tablas)
        res = {**R.leer_resultados(), **(self.liq_res or {})}  # lo guardado más lo recién liquidado
        T.enviar(INF.informe_telegram(titulo, alertas, res, min(self.dias_de_prueba(), C.DIAS_PRUEBA)))
        self.log(f"informe enviado: {titulo}")
        return [a for a in alertas if a.get("avisado") and a["id"] not in self.liq_res]

    def informe_intermedio_si_toca(self):
        """Día 7: informe intermedio automático por Telegram (una sola vez)."""
        marca = C.DATA / "informe_dia7.txt"
        if marca.exists() or self.dias_de_prueba() < C.DIA_INFORME_INTERMEDIO:
            return
        marca.write_text(ahora_iso())
        self._informe(f"Informe intermedio (día {C.DIA_INFORME_INTERMEDIO} de {C.DIAS_PRUEBA:.0f})", "tablas_dia7.md")
        self.guardar(forzar=True)

    def cierre_de_prueba(self):
        """
        Día 14: se deja de leer cuotas y de avisar, y se envía el informe final. Si faltan
        resultados de alertas enviadas, las tandas siguientes solo liquidan (sin leer cuotas
        nuevas) hasta tenerlos o hasta 48 horas, y entonces envían el informe final completo.
        """
        fin = C.DATA / "fin_avisado.txt"
        completo = C.DATA / "informe_final_completo.txt"
        if not fin.exists():
            fin.write_text(ahora_iso())
            pendientes = self._informe(f"Informe final (día {C.DIAS_PRUEBA:.0f})", "tablas.md")
            if pendientes:
                T.enviar(f"PRUEBA — no apostar\n\nTerminaron los {C.DIAS_PRUEBA:.0f} días: dejé de vigilar cuotas. "
                         f"Faltan resultados de {len(pendientes)} alertas enviadas; te mando el informe final "
                         f"completo cuando estén (como máximo en {C.HORAS_LIQUIDAR_DESPUES} horas).")
            else:
                completo.write_text(ahora_iso())
            self.guardar(forzar=True)
        if completo.exists():
            self.log(f"la prueba de {C.DIAS_PRUEBA:.0f} días ya terminó; no se hace nada")
            return
        # Solo liquidación, sin leer cuotas nuevas
        self.log("prueba terminada: solo se liquidan las alertas que faltan")
        while not self.salir and not (self.fin and time.time() >= self.fin):
            try:
                self.atender_pedidos()
                self.liquidar()  # respeta las pausas entre reintentos (API-Football tiene tope diario)
                self.reg.alertas_f.volcar()
                faltan = [a for a in INF.leer_alertas() if a.get("avisado") and a["id"] not in self.liq_res]
                horas = (time.time() - iso_a_ts(fin.read_text().strip())) / 3600
                if not faltan or horas >= C.HORAS_LIQUIDAR_DESPUES:
                    completo.write_text(ahora_iso())
                    self._informe(f"Informe final completo ({C.DIAS_PRUEBA:.0f} días, con todos los resultados)",
                                  "tablas.md")
                    break
                self.guardar()
            except Exception:
                self.log("error al liquidar:", error_corto())
            time.sleep(60)
        self.guardar(forzar=True)

    def correr(self):
        ocultar_en_registros()
        if self.prueba_terminada():
            self.cierre_de_prueba()
            return
        self.log("agente iniciado; Telegram", "activo" if T.activo() else "SIN TOKEN",
                 "; chats autorizados:", len(T.chats()))
        if T.activo() and not T.chats():
            self.log("AVISO: falta TELEGRAM_CHAT_ID; no se envían avisos ni se obedecen pedidos")
        self.probar_guardado()
        if not C.casa_configurada():
            # Prueba nueva: solo cuotas reales. Sin la dirección de la API no hay nada que leer.
            self.log("FALTA el secreto CASA_API_URL (API de cuotas de tu casa); el agente no corre")
            T.enviar("PRUEBA — no apostar\n\nNo encontré la dirección de la API de cuotas de tu casa "
                     "(secreto CASA_API_URL). Sin ella no leo cuotas ni envío alertas.")
            return
        if F.kambi(C.KAMBI_PRINCIPAL, f"listView/{C.DEPORTES['nfl']['kambi']}.json") is None:
            # Solo el aviso, nunca la dirección. Se sigue intentando: puede ser una falla pasajera.
            self.log("AVISO: la API de cuotas de tu casa no respondió al arrancar (se sigue intentando)")
            T.enviar("PRUEBA — no apostar\n\nLa API de cuotas de tu casa no respondió al arrancar. "
                     "Sigo intentando; mientras no responda no hay alertas.")
        else:
            self.log("cuotas: API real de tu casa configurada y respondiendo")
        if self.apagado():
            self.log("el agente está APAGADO (apagado de emergencia); solo escucha \"encender\"")
        if T.activo() and T.chats() and not os.environ.get("F5_SIN_SALUDO"):
            T.enviar("PRUEBA — no apostar\n\nEl agente de la prueba en papel está corriendo. "
                     "Escribe \"estado\" para ver cómo va o \"ayuda\" para ver los pedidos.")
        while not self.salir:
            if self.prueba_terminada():
                self.guardar(forzar=True)
                self.cierre_de_prueba()
                return
            if self.fin and time.time() >= self.fin:
                break
            try:
                self.atender_pedidos()
                if self.apagado():
                    # Apagado de emergencia: solo se escucha "encender" / "estado".
                    time.sleep(5)
                    continue
                self.refrescar_pinnacle()
                self.leer_listas()
                self.lecturas_completas()
                self.seguimiento()
                self.revisar_cierres()
                self.liquidar()
                self.resumen_si_toca()
                self.informe_intermedio_si_toca()
                self.guardar()
                if time.time() - self.ultimo_log >= 600:
                    self.ultimo_log = time.time()
                    self.log(self.texto_estado().split("Estado del agente")[1].replace("\n", " "))
            except Exception:
                self.errores["ciclo"] += 1
                self.log("error en el ciclo:", error_corto())
                time.sleep(10)
            time.sleep(1)
        self.log("guardando y saliendo")
        self.guardar(forzar=True)


if __name__ == "__main__":
    Agente().correr()
