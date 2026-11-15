"""
Fase 5: liquidación automática de cada alerta (¿se habría ganado?).

Cuándo: el agente lo revisa solo, cada pocos minutos. Cada alerta se intenta
liquidar cuando su partido ya debió terminar (unas 2 horas en fútbol, 3 en MLB)
y se reintenta cada 15 minutos. Si a las 48 horas del inicio no hay un resultado
confiable, queda como "sin resultado" en vez de quedar abierta para siempre.

Fuentes del marcador (gratis):
- Kambi en vivo (fútbol): mientras el partido se juega, el agente lee el marcador en vivo de
  Kambi. Es el MISMO partido de la alerta (mismo número), así que no hay que
  emparejar nombres. En fútbol se guarda el marcador de los 90 minutos (sin prórroga).
- API-Football (fútbol; plan gratis de 100 consultas al día, clave en API_FOOTBALL_KEY;
  una consulta trae todos los partidos de un día).
- ESPN (NFL, NBA y MLB, y fútbol como respaldo; sin clave).
En API-Football y ESPN el partido se busca por los nombres de los dos equipos Y por la
fecha y hora de inicio (margen corto), con la misma categoría (sub-21, reservas,
femenino). Así no se toma el juego de otro día de la misma serie (error visto en MLB).

Cuándo se acepta un marcador:
- Dos fuentes con el mismo marcador: "confirmado".
- Una sola fuente oficial (API-Football o ESPN), sin ninguna que la contradiga: "una
  fuente". En fútbol se espera 6 horas a que aparezca la segunda; en NFL, NBA y MLB la
  fuente oficial es ESPN y se usa enseguida. Kambi solo nunca basta.
- Si las fuentes no coinciden, no se liquida: se reintenta y a las 48 h queda
  "sin resultado (las fuentes no coinciden)".
- Partido aplazado o cancelado: a las 48 h queda "nula".

Reglas (las mismas del recálculo aprobado de la fase 5): fútbol a 90 minutos más
reposición (sin prórroga ni penales); MLB y NFL con el marcador final oficial; las
apuestas de "primeras 5 entradas" con el marcador de esas entradas; líneas enteras que
empatan devuelven lo apostado ("nula"); 1 unidad a la cuota real de tu casa (en las enviadas, la releída al enviar).

Guarda data/fase5/resultados.json.gz: {id de alerta: {estado, gana, unidades, cuota,
marcador, fuentes, detalle}} con estado = ganada, perdida, nula o sin resultado.

    python f5_resultados.py      # liquida lo que se pueda ahora (sin editar Telegram)
"""
import gzip
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

import f5_config as C
import f5_emparejar as EM
from f5_fuentes import mismo_jugador, normal, proposicion_kambi
from f5_registro import ahora_iso, iso_a_ts

RUTA_ESPN = {"nfl": "football/nfl", "nba": "basketball/nba", "mlb": "baseball/mlb", "futbol": "soccer/all"}
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (compatible; prueba en papel)"
_cache = {}


# ----------------------------------------------------------------------------- descargas
def _get(url, params=None, headers=None, cache_seg=None):
    """GET con reintentos. Nunca se imprime la dirección ni las cabeceras (pueden llevar la clave)."""
    clave = url + json.dumps(params or {}, sort_keys=True)
    if clave in _cache and (cache_seg is None or time.time() - _cache[clave][0] < cache_seg):
        return _cache[clave][1]
    datos = None
    for intento in range(3):
        try:
            r = S.get(url, params=params, headers=headers, timeout=40)
            if r.status_code == 200:
                datos = r.json()
                break
            if r.status_code in (401, 403):
                break
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (intento + 1))
    _cache[clave] = (time.time(), datos)
    return datos


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _par(h, a):
    h, a = _num(h), _num(a)
    return None if h is None or a is None else (h, a)


# ----------------------------------------------------------------------------- API-Football
class APIFootball:
    """Partidos de fútbol de un día. Una consulta por fecha y como máximo una por hora."""

    def __init__(self):
        self.clave = os.environ.get("API_FOOTBALL_KEY", "")
        self.consultas = 0
        self.aviso = None  # motivo si la fuente no está disponible (sin detalles de la clave)

    def activa(self):
        return bool(self.clave)

    def partidos(self, fecha):
        if not self.clave:
            return []
        d = _get(f"{C.APIFOOT_URL}/fixtures", params={"date": fecha, "timezone": "UTC"},
                 headers={"x-apisports-key": self.clave}, cache_seg=C.APIFOOT_CADA)
        self.consultas += 1
        if not d:
            return []
        if d.get("errors"):
            # Por ejemplo, límite diario o plan sin esa temporada. Solo se anota el tipo de error.
            err = d["errors"]
            self.aviso = ", ".join(sorted(err.keys())) if isinstance(err, dict) else "error"
            return []
        salida = []
        for x in d.get("response", []):
            fx, eq, sc = x.get("fixture", {}), x.get("teams", {}), x.get("score", {})
            st = (fx.get("status") or {}).get("short", "")
            estado = {"FT": "final", "AET": "final", "PEN": "final", "PST": "aplazado", "CANC": "cancelado",
                      "ABD": "suspendido", "AWD": "adjudicado", "WO": "adjudicado"}.get(st, "en curso")
            ft = sc.get("fulltime") or {}
            ht = sc.get("halftime") or {}
            salida.append({"fuente": "API-Football", "ts": _num(fx.get("timestamp")),
                           "local": (eq.get("home") or {}).get("name", ""),
                           "visitante": (eq.get("away") or {}).get("name", ""),
                           "liga": (x.get("league") or {}).get("name", ""), "estado": estado,
                           # "fulltime" de API-Football es el marcador de los 90 minutos (sin prórroga)
                           "full": _par(ft.get("home"), ft.get("away")) if estado == "final" else None,
                           "h1": _par(ht.get("home"), ht.get("away")) if estado == "final" else None})
        return salida


# ----------------------------------------------------------------------------- ESPN
def espn_partidos(deporte, fecha):
    d = _get(f"{C.ESPN_URL}{RUTA_ESPN[deporte]}/scoreboard", params={"dates": fecha, "limit": 500},
             cache_seg=600)
    salida = []
    for e in (d or {}).get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        eq = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if "home" not in eq or "away" not in eq:
            continue
        tipo = (e.get("status") or {}).get("type") or {}
        nombre = tipo.get("name", "")
        if tipo.get("completed") and nombre not in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_ABANDONED"):
            estado = "final"
        else:
            estado = {"STATUS_POSTPONED": "aplazado", "STATUS_CANCELED": "cancelado",
                      "STATUS_ABANDONED": "suspendido"}.get(nombre, "en curso")
        # En fútbol, ESPN da el marcador final: si hubo prórroga no sirve para 90 minutos
        if deporte == "futbol" and nombre in ("STATUS_FINAL_AET", "STATUS_FINAL_PEN"):
            estado = "prorroga"
        try:
            ts = datetime.strptime(e["date"][:16], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp()
        except (KeyError, ValueError):
            continue
        lh = [_num(x.get("value")) or 0 for x in eq["home"].get("linescores", [])]
        la = [_num(x.get("value")) or 0 for x in eq["away"].get("linescores", [])]
        full = _par(eq["home"].get("score"), eq["away"].get("score")) if estado == "final" else None
        h1 = f5 = None
        if full and deporte in ("nfl", "nba") and len(lh) >= 2 and len(la) >= 2:
            h1 = (lh[0] + lh[1], la[0] + la[1])
        if full and deporte == "mlb" and len(lh) >= 5 and len(la) >= 5:
            f5 = (sum(lh[:5]), sum(la[:5]))
        salida.append({"fuente": "ESPN", "ts": ts, "id": e.get("id"),
                       "local": eq["home"]["team"].get("displayName", ""),
                       "visitante": eq["away"]["team"].get("displayName", ""),
                       # ESPN no da el nombre de la liga en esta lista; el de la temporada a veces
                       # dice la categoría ("ncaa-division-i-women")
                       "liga": ((e.get("season") or {}).get("slug") or "").replace("-", " "),
                       "estado": estado, "full": full, "h1": h1, "f5": f5})
    return salida


def estadisticas_jugadores(deporte, evid):
    s = _get(f"{C.ESPN_URL}{RUTA_ESPN[deporte]}/summary", params={"event": evid}, cache_seg=3600)
    jug = {}
    for equipo in (s or {}).get("boxscore", {}).get("players", []):
        for bloque in equipo.get("statistics", []):
            nombre_b = bloque.get("name") or bloque.get("type") or ""
            etiquetas = bloque.get("labels", [])
            for at in bloque.get("athletes", []):
                n = at["athlete"]["displayName"]
                v = dict(zip(etiquetas, at.get("stats", [])))
                d = jug.setdefault(n, {})
                if deporte == "nfl":
                    if nombre_b == "passing":
                        comp, att = (v.get("C/ATT", "0/0").split("/") + ["0"])[:2]
                        d.update({"pass completion": _num(comp), "pass attempt": _num(att),
                                  "passing yard": _num(v.get("YDS")), "touchdown passe": _num(v.get("TD")),
                                  "interception": _num(v.get("INT"))})
                    elif nombre_b == "rushing":
                        d.update({"rushing yard": _num(v.get("YDS")), "_rtd": _num(v.get("TD")),
                                  "rush attempt": _num(v.get("CAR"))})
                    elif nombre_b == "receiving":
                        d.update({"reception": _num(v.get("REC")), "receiving yard": _num(v.get("YDS")),
                                  "_retd": _num(v.get("TD"))})
                elif deporte == "mlb":
                    if nombre_b == "batting":
                        d.update({"hit": _num(v.get("H")), "run": _num(v.get("R")), "rbi": _num(v.get("RBI")),
                                  "home run": _num(v.get("HR"))})
                    elif nombre_b == "pitching":
                        ip = v.get("IP", "0")
                        ent, _, tercio = ip.partition(".")
                        d.update({"strikeout": _num(v.get("K")),
                                  "out": (_num(ent) or 0) * 3 + (_num(tercio) or 0)})
                elif deporte == "nba":
                    tres = (v.get("3PT") or "0-0").split("-")[0]
                    d.update({"point": _num(v.get("PTS")), "rebound": _num(v.get("REB")),
                              "assist": _num(v.get("AST")), "steal": _num(v.get("STL")),
                              "block": _num(v.get("BLK")), "3 point field goals made": _num(tres)})
    for d in jug.values():
        if "_rtd" in d or "_retd" in d:
            d["touchdown"] = (d.get("_rtd") or 0) + (d.get("_retd") or 0)
        if "rushing yard" in d or "receiving yard" in d:
            d["rushing receiving yard"] = (d.get("rushing yard") or 0) + (d.get("receiving yard") or 0)
        if "passing yard" in d:
            d["passing rushing yard"] = (d.get("passing yard") or 0) + (d.get("rushing yard") or 0)
        if "hit" in d:
            d["hit run rbi"] = (d.get("hit") or 0) + (d.get("run") or 0) + (d.get("rbi") or 0)
        if all(k in d for k in ("point", "rebound", "assist")):
            d["point rebound assist"] = d["point"] + d["rebound"] + d["assist"]
    return jug


# ----------------------------------------------------------------------------- buscar el partido
def _fechas(ts, deporte):
    """Fechas a consultar: el día del inicio en UTC y, para ESPN, también en hora de EE. UU."""
    utc = datetime.fromtimestamp(ts, timezone.utc)
    fechas = {utc.strftime("%Y-%m-%d")}
    fechas.add((utc - timedelta(hours=5)).strftime("%Y-%m-%d"))
    return sorted(fechas)


def buscar(info, candidatos):
    """El partido de la fuente que es el mismo de Kambi: mismos equipos (local con local),
    misma categoría y hora de inicio con margen corto. Si no aparece en el mismo orden, se
    prueba al revés (algunas fuentes invierten local y visitante en cancha neutral) y se da
    vuelta el marcador. Devuelve (partido, invertido) o (None, False)."""
    m = EM.mejor(info, candidatos, C.LIQ_MARGEN_INICIO, revisar_competencia=False)
    if m:
        return m, False
    al_reves = [dict(c, local=c["visitante"], visitante=c["local"], _orig=c) for c in candidatos]
    m = EM.mejor(info, al_reves, C.LIQ_MARGEN_INICIO, revisar_competencia=False)
    if m:
        return m["_orig"], True
    return None, False


def _girar(p):
    return (p[1], p[0]) if p else None


def marcador_fuente(fuente, info, apifoot=None):
    """Marcador de una fuente para el partido de la alerta: dict con estado, full, h1, f5, o None."""
    dep = info["deporte"]
    cands = []
    if fuente == "API-Football":
        if dep != "futbol" or not apifoot or not apifoot.activa():
            return None
        # Una consulta por día (en hora UTC) trae todos los partidos de fútbol de ese día
        cands = apifoot.partidos(datetime.fromtimestamp(info["_ts"], timezone.utc).strftime("%Y-%m-%d"))
    else:
        for f in _fechas(info["_ts"], dep):
            cands += espn_partidos(dep, f.replace("-", ""))
    vistos, unicos = set(), []
    for c in cands:
        clave = (c["local"], c["visitante"], c["ts"])
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(c)
    m, inv = buscar(info, unicos)
    if not m:
        return None
    r = {k: m.get(k) for k in ("estado", "full", "h1", "f5", "id")}
    if inv:
        for k in ("full", "h1", "f5"):
            r[k] = _girar(r[k])
    r["fuente"] = fuente
    r["partido_fuente"] = f"{m['local']} - {m['visitante']}"
    return r


# ----------------------------------------------------------------------------- Kambi en vivo
_PERIODOS_90 = {"FIRST_HALF", "HALF_TIME", "SECOND_HALF"}


class KambiVivo:
    """Marcador en vivo de Kambi de los partidos con alertas. Se guarda en el estado de la
    liquidación. En fútbol se guarda aparte el de los 90 minutos (sin prórroga) y el del
    primer tiempo."""

    def __init__(self, datos):
        self.datos = datos  # ev (texto) -> {...}
        self.ultima = 0

    def leer(self, evs_en_juego):
        """evs_en_juego: ids de Kambi de partidos que empezaron hace menos de 5 h y faltan liquidar."""
        if not evs_en_juego or time.time() - self.ultima < C.KAMBI_VIVO_CADA:
            return
        self.ultima = time.time()
        from f5_fuentes import kambi
        d = kambi(C.KAMBI_PRINCIPAL, "event/live/open.json")
        if d is None:
            return
        en_vivo = {}
        for e in d.get("liveEvents", []):
            ev = (e.get("event") or {}).get("id")
            if ev is not None:
                en_vivo[str(ev)] = e.get("liveData") or {}
        ahora = time.time()
        for ev in map(str, evs_en_juego):
            reg = self.datos.get(ev)
            ld = en_vivo.get(ev)
            if ld is None:
                # Ya no está en vivo: si se vio jugando el final, el partido terminó
                if reg and not reg.get("fin") and reg.get("final_visto"):
                    reg["fin"] = True
                    reg["fin_ts"] = ahora
                continue
            sc = ld.get("score") or {}
            par = _par(sc.get("home"), sc.get("away"))
            reloj = ld.get("matchClock") or {}
            per = reloj.get("periodId") or reloj.get("period") or ""
            minuto = _num(reloj.get("minute")) or 0
            reg = self.datos.setdefault(ev, {"visto": ahora})
            reg["ultimo_visto"] = ahora
            if not par:
                continue
            reg["marcador"] = list(par)
            reg["periodo"] = per
            if per in _PERIODOS_90 or not per:
                if not reg.get("prorroga"):
                    reg["full"] = list(par)
                if per == "FIRST_HALF" or (per == "HALF_TIME" and not reg.get("segundo")):
                    reg["h1"] = list(par)
                if per == "SECOND_HALF":
                    reg["segundo"] = True
                    if minuto >= 85:
                        reg["final_visto"] = True
            elif reg.get("segundo"):
                # Prórroga o penales: el marcador de los 90 minutos queda congelado
                reg["prorroga"] = True
                reg["final_visto"] = True

    def marcador(self, ev, deporte):
        reg = self.datos.get(str(ev))
        if not reg or not reg.get("fin"):
            return None
        # Solo se usa en fútbol; en NFL, NBA y MLB el marcador oficial es el de ESPN
        if deporte != "futbol" or not reg.get("full"):
            return None
        return {"fuente": "Kambi", "estado": "final", "full": tuple(reg["full"]),
                "h1": tuple(reg["h1"]) if reg.get("h1") and reg.get("segundo") else None, "f5": None}


# ----------------------------------------------------------------------------- liquidar una apuesta
def _resultado_linea(valor, linea, mas):
    """Unidades por cada unidad apostada a 'más' o 'menos' de una línea (sin la cuota):
    1 = gana, 0 = nula, -1 = pierde; con líneas de cuarto (2.25) se reparte en dos medias."""
    if linea is None:
        return None
    if abs(linea * 4 - round(linea * 4)) < 1e-6 and abs(linea * 2 - round(linea * 2)) > 1e-6:
        return (_resultado_linea(valor, linea - 0.25, mas) + _resultado_linea(valor, linea + 0.25, mas)) / 2
    if abs(valor - linea) < 1e-9:
        return 0
    return 1 if (valor > linea) == mas else -1


def _equipo_de(nombre, info):
    if not nombre:
        return None
    sl, sv = EM.similitud(nombre, info.get("local", "")), EM.similitud(nombre, info.get("visitante", ""))
    if max(sl, sv) < 0.6 or abs(sl - sv) < 0.1:
        return None
    return "local" if sl > sv else "visitante"


def es_de_jugador(f):
    return bool(f.get("part")) and f.get("tipo", "").startswith("Player Occurrence")


def periodo_de(crit):
    t = (crit or "").lower()
    if t.startswith("half time"):
        return "h1"
    if "first 5 innings" in t or "1st 5 innings" in t:
        return "f5"
    if "1st half" in t or "first half" in t:
        return "h1"
    resto = re.sub(r"\(.*?\)", "", t.split(" - ", 1)[1]).strip() if " - " in t else ""
    if resto in ("", "including overtime", "including extra innings", "full time", "regular time"):
        return "full"
    return None


def valor_apuesta(f, info, m, jugadores=None):
    """
    Resultado de una apuesta de Kambi con el marcador m ({full, h1, f5}).
    Devuelve (factor, detalle) donde factor es 1 (gana), -1 (pierde), 0 (nula), un valor
    intermedio (media ganada / media perdida en líneas de cuarto), o (None, motivo).
    """
    crit, tipo, ot, line = f["crit"], f["tipo"], f["otype"], f.get("line")
    per = periodo_de(crit)
    if per is None:
        return None, "tipo de apuesta que el agente no sabe liquidar"
    marc = m.get(per)
    if marc is None and not es_de_jugador(f):
        return None, "falta el marcador de ese periodo"
    base = re.sub(r"\s*\(.*?\)\s*", " ", crit.split(" - ", 1)[0]).strip()
    if base.startswith("3-Way") or base.startswith("European Handicap"):
        return None, "tipo de apuesta que el agente no sabe liquidar"
    if es_de_jugador(f):
        return _valor_jugador(f, info, jugadores)
    gl, gv = marc
    txt = f"marcador {gl:g}-{gv:g}" + ("" if per == "full" else {"h1": " (primer tiempo)", "f5": " (5 entradas)"}[per])
    # Ganador del partido
    if tipo == "Match" and base in ("Full Time", "Moneyline", "Match Odds", "Half Time", "Match Winner"):
        if ot == "OT_CROSS":
            return (1 if gl == gv else -1), txt
        if ot in ("OT_ONE", "OT_TWO"):
            if gl == gv and f.get("nsal") == 2:
                return 0, txt + " (empate: se devuelve)"
            gana = gl > gv if ot == "OT_ONE" else gv > gl
            return (1 if gana else -1), txt
    if base == "Draw No Bet" and ot in ("OT_ONE", "OT_TWO"):
        if gl == gv:
            return 0, txt + " (empate: se devuelve)"
        return (1 if (gl > gv) == (ot == "OT_ONE") else -1), txt
    if base == "Double Chance":
        ok = {"OT_ONE_OR_CROSS": gl >= gv, "OT_ONE_OR_TWO": gl != gv, "OT_CROSS_OR_TWO": gv >= gl}.get(ot)
        if ok is not None:
            return (1 if ok else -1), txt
    if base in ("Both Teams To Score", "Both Teams to Score") and ot in ("OT_YES", "OT_NO"):
        ambos = gl > 0 and gv > 0
        return (1 if ambos == (ot == "OT_YES") else -1), txt
    # Más/menos del partido o de un equipo
    if tipo == "Over/Under" and ot in ("OT_OVER", "OT_UNDER"):
        mm = re.match(r"^Total \w+ by (.+)$", base)
        if mm:
            lado = _equipo_de(mm.group(1), info)
            if not lado:
                return None, "no se reconoce el equipo del total"
            valor = gl if lado == "local" else gv
            txt += f" ({mm.group(1)} anotó {valor:g})"
        elif base.startswith("Total "):
            valor = gl + gv
        else:
            return None, "tipo de apuesta que el agente no sabe liquidar"
        return _resultado_linea(valor, line, ot == "OT_OVER"), txt
    # Hándicap de dos opciones (línea del lado apostado)
    if tipo in ("Handicap", "Asian Handicap") and ot in ("OT_ONE", "OT_TWO") and f.get("nsal") == 2 and line is not None:
        dif = (gl - gv) if ot == "OT_ONE" else (gv - gl)
        return _resultado_linea(dif + line, 0, True), txt
    return None, "tipo de apuesta que el agente no sabe liquidar"


def _valor_jugador(f, info, jugadores):
    """Apuestas de jugadores (estadísticas de ESPN): N+ o más/menos."""
    p = proposicion_kambi(dict(f, nsal=2), info)
    if not p or jugadores is None:
        return None, "apuesta de jugador sin estadísticas disponibles"
    (_, quien, stat, per_p), d, t = p
    if per_p != "full":
        return None, "apuesta de jugador de un periodo"
    nombre = next((n for n in jugadores if normal(n) == quien[2:] or mismo_jugador(n, quien[2:])), None)
    if nombre is None:
        return 0, "el jugador no aparece en la planilla (apuesta anulada)"
    valor = jugadores[nombre].get(stat)
    if valor is None:
        return None, "estadística del jugador no disponible"
    gana = valor >= t if d == "ge" else valor <= t
    return (1 if gana else -1), f"{nombre}: {stat} = {valor:g}"


def _estado_de(factor):
    return "ganada" if factor > 0 else ("perdida" if factor < 0 else "nula")


def acordar(marcadores, periodo="full", deporte="futbol"):
    """
    Junta los marcadores de las fuentes. Devuelve (marcador, fuentes que coinciden, hay_conflicto).
    - Si las fuentes oficiales (API-Football, ESPN) son dos o más y coinciden, vale aunque
      Kambi diga otra cosa (Kambi en vivo pudo no ver la última jugada).
    - En NFL, NBA y MLB, Kambi solo sirve para confirmar: nunca bloquea a ESPN.
    - En fútbol, si una fuente oficial y Kambi no coinciden, no se liquida.
    """
    vals = [(m["fuente"], tuple(m[periodo])) for m in marcadores if m.get(periodo)]
    if not vals:
        return None, [], False
    oficiales = [(f, v) for f, v in vals if f != "Kambi"]
    if len({v for _, v in oficiales}) > 1:
        return None, [f for f, _ in vals], True
    if len({v for _, v in vals}) > 1:
        if len(oficiales) >= 2 or (deporte != "futbol" and oficiales):
            v = oficiales[0][1]
            return v, [f for f, x in vals if x == v], False
        return None, [f for f, _ in vals], True
    return vals[0][1], [f for f, _ in vals], False


# ----------------------------------------------------------------------------- el liquidador
class Liquidador:
    """Liquida las alertas sin resultado y anota qué mensajes de Telegram hay que editar."""

    ARCHIVO = "liquidacion.json.gz"

    def __init__(self):
        self.apifoot = APIFootball()
        self.estado = self._cargar()
        self.kambi = KambiVivo(self.estado.setdefault("kambi", {}))
        self._por_ev = {}

    def _cargar(self):
        try:
            with gzip.open(C.DATA / self.ARCHIVO, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError, EOFError):
            return {}

    def guardar(self):
        # Se olvidan los marcadores de Kambi de hace más de 4 días
        limite = time.time() - 4 * 86400
        self.estado["kambi"] = {k: v for k, v in self.estado.get("kambi", {}).items()
                                if v.get("ultimo_visto", v.get("visto", 0)) > limite}
        self.kambi.datos = self.estado["kambi"]
        self.estado["intentos"] = {k: v for k, v in self.estado.get("intentos", {}).items()
                                   if v.get("ultimo", 0) > limite}
        C.DATA.mkdir(parents=True, exist_ok=True)
        tmp = C.DATA / (self.ARCHIVO + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(self.estado, fh, separators=(",", ":"))
        os.replace(tmp, C.DATA / self.ARCHIVO)

    # -- una alerta
    def liquidar_una(self, a, f, ahora=None):
        """Devuelve el resultado final de la alerta, o None si todavía hay que esperar."""
        ahora = ahora or time.time()
        try:
            ts = iso_a_ts(a["inicio"])
        except (KeyError, ValueError):
            return None
        dep = a["deporte"]
        horas = (ahora - ts) / 3600
        vencida = horas >= C.LIQ_MAX_HORAS
        if not f:
            return self._sin_resultado(a, "no está en el catálogo de cuotas") if vencida else None
        local, _, visit = a["partido"].partition(" - ")
        info = {"local": local, "visitante": visit, "_ts": ts, "liga": a.get("liga", ""), "deporte": dep}
        marcadores, espn_id = self._marcadores(a["ev"], info)
        estados = {m["estado"] for m in marcadores if m.get("estado")}
        finales = [m for m in marcadores if m.get("estado") == "final"]
        per = periodo_de(f["crit"]) or "full"
        es_jugador = es_de_jugador(f)
        marc, quienes, conflicto = acordar(finales, "full", dep)
        if conflicto:
            return self._sin_resultado(a, "las fuentes no coinciden: " + ", ".join(quienes)) if vencida else None
        if marc is None:
            if vencida and estados & {"aplazado", "cancelado", "suspendido"}:
                return self._final(a, 0, "partido " + sorted(estados & {"aplazado", "cancelado", "suspendido"})[0],
                                   None, [m["fuente"] for m in marcadores])
            return self._sin_resultado(a, "no apareció el marcador en las fuentes") if vencida else None
        oficiales = [q for q in quienes if q != "Kambi"]
        if len(quienes) < 2:
            # Una sola fuente: solo si es oficial y ya pasó el tiempo de espera de la segunda
            # (en NFL, NBA y MLB la fuente oficial es una sola, ESPN, y se usa enseguida)
            if not oficiales or (dep == "futbol" and (ahora - ts) < C.LIQ_UNA_FUENTE):
                return self._sin_resultado(a, "solo Kambi tiene el marcador") if vencida else None
        nivel = "confirmado" if len(quienes) >= 2 else "una fuente"
        m_usado = {"full": marc}
        if per != "full":
            mp, qp, cp = acordar(finales, per, dep)
            if cp:
                return self._sin_resultado(a, "las fuentes no coinciden en el marcador del periodo") if vencida else None
            if qp == ["Kambi"]:
                mp = None  # el marcador de un periodo con solo Kambi no basta
            m_usado[per] = mp
        jugadores = None
        if es_jugador:
            if dep not in ("nfl", "mlb", "nba") or not espn_id:
                return self._sin_resultado(a, "apuesta de jugador sin estadísticas") if vencida else None
            jugadores = estadisticas_jugadores(dep, espn_id)
        base = f["crit"].split(" - ", 1)[0]
        nsal = 3 if (dep == "futbol" and base in ("Full Time", "Half Time")) else 2
        fila = dict(f, ev=a["ev"], odds=a["cuota"], st="OPEN", nsal=nsal)
        factor, detalle = valor_apuesta(fila, info, m_usado, jugadores)
        if factor is None:
            if "no sabe liquidar" in detalle or vencida:
                return self._sin_resultado(a, detalle)
            return None
        return self._final(a, factor, detalle, marc, quienes, nivel)

    def _marcadores(self, ev, info):
        """Marcadores de todas las fuentes para un partido (se buscan una vez por ciclo)."""
        if ev in self._por_ev:
            return self._por_ev[ev]
        dep = info["deporte"]
        marcadores, espn_id = [], None
        k = self.kambi.marcador(ev, dep)
        if k:
            marcadores.append(k)
        for fu in (["API-Football", "ESPN"] if dep == "futbol" else ["ESPN"]):
            m = marcador_fuente(fu, info, self.apifoot)
            if m:
                marcadores.append(m)
                if fu == "ESPN":
                    espn_id = m.get("id")
        self._por_ev[ev] = (marcadores, espn_id)
        return self._por_ev[ev]

    def _final(self, a, factor, detalle, marc, fuentes, nivel=None):
        cuota = a.get("cuota_envio") or a.get("cuota_rb") or a["cuota"]
        estado = _estado_de(factor)
        unidades = round(factor * (cuota - 1) if factor > 0 else factor, 3)
        return {"estado": estado, "gana": (True if factor > 0 else False) if factor != 0 else None,
                "unidades": unidades, "cuota": cuota, "detalle": detalle,
                "marcador": f"{marc[0]:g}-{marc[1]:g}" if marc else None, "fuentes": fuentes,
                "verificacion": nivel, "liquidado": ahora_iso()}

    def _sin_resultado(self, a, motivo):
        return {"estado": "sin resultado", "gana": None, "unidades": 0.0, "detalle": motivo,
                "liquidado": ahora_iso()}

    # -- todas
    def pendientes(self, alertas, res, ahora=None):
        ahora = ahora or time.time()
        salida = []
        for a in alertas:
            if a["id"] in res or not a.get("inicio"):
                continue
            try:
                ts = iso_a_ts(a["inicio"])
            except ValueError:
                continue
            # Lo de hace más de una semana no se toca (pertenece a pruebas ya cerradas)
            if C.LIQ_ESPERA_MIN.get(a["deporte"], 150) * 60 <= ahora - ts <= C.LIQ_OLVIDAR_DIAS * 86400:
                salida.append(a)
        return salida

    def en_juego(self, alertas, res, ahora=None):
        """Partidos con alertas por liquidar que se están jugando (para leer Kambi en vivo)."""
        ahora = ahora or time.time()
        evs = set()
        for a in alertas:
            if a["id"] in res or not a.get("inicio"):
                continue
            try:
                ts = iso_a_ts(a["inicio"])
            except ValueError:
                continue
            if -300 <= ahora - ts <= 5 * 3600:
                evs.add(a["ev"])
        return evs

    def ciclo(self, alertas, res, catalogo_de, forzar=False):
        """
        Intenta liquidar lo pendiente. `res` se modifica en el lugar. Devuelve la lista de
        ids recién liquidados. Cada alerta se reintenta cada LIQ_REINTENTO segundos.
        """
        ahora = time.time()
        intentos = self.estado.setdefault("intentos", {})
        nuevas = []
        pend = [a for a in self.pendientes(alertas, res, ahora)
                if forzar or ahora - intentos.get(a["id"], {}).get("ultimo", 0) >= C.LIQ_REINTENTO]
        if not pend:
            return nuevas
        cat = catalogo_de({a["k"] for a in pend})
        self._por_ev = {}
        for a in pend:
            it = intentos.setdefault(a["id"], {"n": 0})
            it["n"] += 1
            it["ultimo"] = ahora
            try:
                r = self.liquidar_una(a, cat.get(a["k"]), ahora)
            except Exception as e:  # un partido raro no debe frenar a los demás
                r = None
                print("no se pudo liquidar una alerta:", type(e).__name__, flush=True)
            if r is not None:
                r["intentos"] = it["n"]
                res[a["id"]] = r
                intentos.pop(a["id"], None)
                nuevas.append(a["id"])
        return nuevas


# ----------------------------------------------------------------------------- archivos
def catalogo(ks):
    """Qué es cada cuota (se lee del catálogo guardado)."""
    salida = {}
    for ruta in sorted((C.DATA / "cuotas").glob("catalogo_*.jsonl.gz")):
        try:
            with gzip.open(ruta, "rt", encoding="utf-8") as fh:
                for linea in fh:
                    try:
                        r = json.loads(linea)
                    except ValueError:
                        continue
                    if r["k"] in ks:
                        salida[r["k"]] = r
        except (OSError, EOFError):
            continue
    return salida


def leer_resultados():
    ruta = C.DATA / "resultados.json.gz"
    try:
        with gzip.open(ruta, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, EOFError):
        return {}


def guardar_resultados(res):
    ruta = C.DATA / "resultados.json.gz"
    tmp = ruta.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(res, fh)
    tmp.replace(ruta)


def liquidar(liq=None):
    """Liquida ahora todo lo que se pueda (lo usa el resumen diario y la línea de comandos)."""
    from f5_informe import leer_alertas
    liq = liq or Liquidador()
    res = leer_resultados()
    liq.ciclo(leer_alertas(), res, catalogo, forzar=True)
    guardar_resultados(res)
    liq.guardar()
    return res


if __name__ == "__main__":
    r = liquidar()
    print(len(r), "alertas con resultado o cerradas sin resultado")
