"""
Fase 5: agente de prueba en papel (NO se apuesta dinero real).

Lee sin parar las cuotas de Kambi (Paf, con Unibet para confirmar) de NFL,
NBA, MLB y fútbol, las compara con Pinnacle y consigo mismas, y avisa por
Telegram cada posible error. Guarda todas las cuotas (solo cambios) y cada
alerta con cuánto duró.

Uso:
    python f5_agente.py                    # corre hasta que se detenga
    F5_DURACION=20700 python f5_agente.py  # corre 5 h 45 min y sale (GitHub Actions)

Variables de entorno:
    TELEGRAM_BOT_TOKEN   token del bot (obligatorio para avisar)
    TELEGRAM_CHAT_ID     chat o chats (separados por coma) que reciben avisos
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
import f5_fuentes as F
import f5_informe as INF
import f5_telegram as T
from f5_detector import agregar_pinnacle, contra_mercado, internos
from f5_estimacion import Estimador
from f5_registro import Registro, ahora_iso, iso_a_ts

GIT_REINTENTO_SEG = 300  # reintento del guardado cuando falla
CIERRE_GRACIA = 15 * 60  # un error que desaparece y vuelve antes de 15 min es el mismo

AYUDA = (
    "PRUEBA — no apostar\n\n"
    "Pedidos que entiendo (escríbelos tal cual):\n"
    "• estado: cómo va el agente.\n"
    "• resumen: el resumen del día ahora mismo, con la lista de posibles errores.\n"
    "• detalle: los posibles errores que siguen abiertos en este momento.\n"
    "• solo NFL (o NBA, MLB, fútbol): avisar solo de ese deporte. \"hoy solo NFL\" o "
    "\"hoy enfócate solo en NFL\" vale hasta la medianoche.\n"
    "• todo: volver a avisar de todos los deportes.\n"
    "• pausa / seguir: dejar de avisar o volver a avisar (el registro sigue).\n"
    "• apagar: APAGADO DE EMERGENCIA. Deja de leer cuotas y de avisar, también en las "
    "tandas siguientes, hasta que escribas \"encender\".\n"
    "• encender: volver a prender el agente después de \"apagar\".\n"
    "Cualquier otro mensaje se ignora."
)
NO_ENTENDI = "PRUEBA — no apostar\n\nNo es un pedido que yo conozca. Escribe \"ayuda\" para ver la lista."

_DEPORTE = {"nfl": "nfl", "futbol americano": "nfl", "nba": "nba", "baloncesto": "nba",
            "mlb": "mlb", "beisbol": "mlb", "futbol": "futbol"}
_FIJOS = {"ayuda": "ayuda", "start": "ayuda", "help": "ayuda", "estado": "estado", "resumen": "resumen",
          "detalle": "detalle",
          "pausa": "pausa", "seguir": "seguir", "todo": "todo", "todos": "todo",
          "apagar": "apagar", "encender": "encender"}
_RE_FOCO = re.compile(r"^(hoy\s+)?(?:(enfocate)\s+)?(?:(solo)\s+)?(?:en\s+)?(?:el\s+)?"
                      r"(nfl|nba|mlb|futbol americano|futbol|beisbol|baloncesto)(\s+hoy)?$")


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
    valores = [os.environ.get("TELEGRAM_BOT_TOKEN", "")] + T.chats()
    for v in valores:
        if v:
            print(f"::add-mask::{v}", flush=True)


def col(ts=None):
    return datetime.fromtimestamp(ts or time.time(), C.HORA_LOCAL)


class Agente:
    def __init__(self):
        self.reg = Registro()
        self.est = Estimador()
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
        """Busca el partido de Pinnacle que corresponde a uno de Kambi."""
        if evid in self.pareja:
            return self.pareja[evid]
        info = self.reg.eventos.get(evid)
        if not info:
            return None
        dep = info["deporte"]
        if dep == "futbol":
            candidatos = self.pin_futbol
        else:
            candidatos = [m for liga in C.DEPORTES[dep]["pin_ligas"] for m in self.pin_mu.get(liga, [])
                          if m.get("type") == "matchup" and not m.get("parentId")]
        mejor, puntos = None, 0
        for m in candidatos:
            try:
                ini = iso_a_ts(m["startTime"].replace("+00:00", "Z"))
            except (KeyError, ValueError):
                continue
            if abs(ini - info["_ts"]) > 1800:
                continue
            h = next((p["name"] for p in m["participants"] if p["alignment"] == "home"), "")
            a = next((p["name"] for p in m["participants"] if p["alignment"] == "away"), "")
            if "(" in h or "(" in a:
                continue
            ph, pa = F.parecido(h, info["local"]), F.parecido(a, info["visitante"])
            if ph >= 0.5 and pa >= 0.5 and ph + pa > puntos:
                mejor, puntos = m, ph + pa
        if mejor:
            self.pareja[evid] = (mejor["league"]["id"], mejor["id"])
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
    def detectar(self, evid):
        info = self.reg.eventos.get(evid)
        filas = list(self.filas.get(evid, {}).values())
        if not info or not filas or self.reg.empezo(evid):
            return
        props, hora_pin = self.props_pinnacle(evid)
        hallazgos = agregar_pinnacle(internos(filas, info) + contra_mercado(filas, info, props),
                                     filas, info, props)
        vistos = {h["clave"] for h in hallazgos}
        ahora = time.time()
        por_k = {f["k"]: f for f in filas}
        # Cuota estimada en tu casa y si la señal merece alerta (rango 1.50-2.00 y sobre el justo)
        for h in hallazgos:
            h.update(self.est.evaluar(h, por_k, info["deporte"]))

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
                                                  "en_rango", "avisable")})
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
        # Confirmación rápida con Unibet (otra casa de Kambi): la cuota sigue ahí
        confirmados = self.confirmar(evid, nuevos)
        para_avisar = []
        for h in confirmados:
            extra = {
                "id": f"{int(ahora)}-{h['k']}-{h['regla']}", "deporte": info["deporte"],
                "partido": info["nombre"], "liga": info["liga"], "inicio": info["inicio"],
                "detectado": ahora_iso(ahora), "detectado_ts": ahora,
                "min_al_inicio": round((info["_ts"] - ahora) / 60),
                "cuota_max": h["cuota"], "pin_hora": ahora_iso(hora_pin) if hora_pin else None,
                "avisado": False,
            }
            self.reg.alerta_abre(h, extra)
            para_avisar.append(self.reg.alertas_abiertas[h["clave"]])
        self.avisar(info, para_avisar)

    def confirmar(self, evid, nuevos):
        bos, _ = F.kambi_mercados([evid], op=C.KAMBI_CONFIRMA)
        if bos is None:
            return nuevos  # si Unibet no responde, se confía en Paf
        ub = {f["k"]: f for f in F.filas_kambi(bos)}
        salida = []
        for h in nuevos:
            f = ub.get(h["k"])
            if f and f["st"] == "OPEN" and f["odds"] >= h["cuota"] * 0.97:
                h["cuota_unibet"] = f["odds"]
                salida.append(h)
        return salida

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
        if self.pausa or (self.foco and info["deporte"] not in self.foco):
            return
        # Solo alertas con la cuota ESTIMADA en tu casa entre 1.50 y 2.00 y por encima del justo.
        # Todo lo demás queda registrado para el resumen diario y el informe final.
        enviar = [a for a in alertas if a.get("avisable")
                  and ahora - self.reg.avisados.get(a["clave"], 0) > C.REPETIR_AVISO_SEG]
        if not enviar or len(self.avisos_hora) >= C.MAX_AVISOS_HORA:
            return
        enviar.sort(key=lambda a: -(a.get("ventaja_rb") or 0))
        texto = INF.texto_alerta(info, enviar[:5], len(enviar))
        if T.enviar(texto):
            self.avisos_hora.append(ahora)
            for a in enviar:
                a["avisado"] = True
                a["avisado_ts"] = ahora
                self.reg.avisados[a["clave"]] = ahora
            self.log(f"aviso enviado: {info['nombre']} ({len(enviar)} apuestas)")

    # ------------------------------------------------------------------ pedidos
    def atender_pedidos(self):
        if time.time() - self.ultimo_pedido < 10 or not T.activo():
            return
        self.ultimo_pedido = time.time()
        for chat, texto in self.pedidos.nuevos():
            pedido = interpretar_pedido(texto)
            if pedido is None:
                T.enviar(NO_ENTENDI, chat=chat)
                continue
            cmd, deporte, solo_hoy = pedido
            self.log("pedido recibido:", cmd)  # solo el nombre del comando, nunca el texto
            if cmd == "ayuda":
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

    def resumen_si_toca(self):
        ahora = col()
        hoy = ahora.date().isoformat()
        if ahora.hour >= C.RESUMEN_HORA_LOCAL and self.resumen_hecho != hoy:
            self.resumen_hecho = hoy
            (C.DATA / "ultimo_resumen.txt").write_text(hoy)
            try:
                import f5_resultados
                f5_resultados.liquidar()
            except Exception:  # los resultados no deben tumbar al agente
                self.log("no se pudieron bajar resultados:", error_corto())
            T.enviar(INF.resumen_diario(self.reg, ahora.date()))
            self.log("resumen diario enviado")

    # ------------------------------------------------------------------ ciclo
    def prueba_terminada(self):
        """La prueba dura F5_DIAS días (7) desde la primera vez que corrió."""
        ruta = C.DATA / "inicio_prueba.txt"
        if not ruta.exists():
            ruta.write_text(ahora_iso())
        inicio = iso_a_ts(ruta.read_text().strip())
        return time.time() - inicio >= float(os.environ.get("F5_DIAS", "7")) * 86400

    def correr(self):
        ocultar_en_registros()
        if self.prueba_terminada():
            self.log("la prueba de 7 días ya terminó; no se hace nada")
            if not (C.DATA / "fin_avisado.txt").exists():
                (C.DATA / "fin_avisado.txt").write_text(ahora_iso())
                T.enviar("PRUEBA — no apostar\n\nTerminaron los 7 días de la prueba en papel. "
                         "El agente dejó de vigilar. Pide el informe final cuando quieras.")
                self.guardar(forzar=True)
            return
        self.log("agente iniciado; Telegram", "activo" if T.activo() else "SIN TOKEN",
                 "; chats autorizados:", len(T.chats()))
        if T.activo() and not T.chats():
            self.log("AVISO: falta TELEGRAM_CHAT_ID; no se envían avisos ni se obedecen pedidos")
        self.probar_guardado()
        if self.est.ok:
            self.log("estimación de la cuota de tu casa: tabla cargada")
        else:
            self.log("AVISO: falta la tabla para estimar la cuota de tu casa; se registra todo pero no se envían alertas")
            T.enviar("PRUEBA — no apostar\n\nNo encontré la tabla para estimar la cuota de tu casa de apuestas. "
                     "Sigo leyendo y registrando todo, pero NO envío alertas hasta tenerla.")
        if self.apagado():
            self.log("el agente está APAGADO (apagado de emergencia); solo escucha \"encender\"")
        if T.activo() and T.chats() and not os.environ.get("F5_SIN_SALUDO"):
            T.enviar("PRUEBA — no apostar\n\nEl agente de la prueba en papel está corriendo. "
                     "Escribe \"estado\" para ver cómo va o \"ayuda\" para ver los pedidos.")
        while not self.salir:
            if (self.fin and time.time() >= self.fin) or self.prueba_terminada():
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
                self.revisar_cierres()
                self.resumen_si_toca()
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
