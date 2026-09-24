"""
Fase 5: lectura de cuotas.

- Kambi (Paf y Unibet): cuotas públicas, sin clave y sin cuenta de usuario.
- Pinnacle: cuotas públicas de su página (sin clave), usadas como consenso
  del mercado. Es la casa más afinada y no usa Kambi.

Las dos se consultan despacio (pausa mínima entre consultas, ver f5_config).
Aquí también se traduce cada cuota a una forma común ("proposición") para
poder comparar Kambi consigo mismo y con Pinnacle:

    grupo = (partido, a quién se refiere, estadística, periodo)
    dir   = "ge" (la estadística llega al menos a t) o "le" (como máximo t)
    t     = umbral entero

Ejemplos: "2+ pases de touchdown" -> (jugador, "touchdown passe", full), ge 2.
"Menos de 45.5 puntos" -> (partido, "points", full), le 45.
"Packers -6.5" -> (partido, "margen", full), ge 7  (margen = local - visitante).
"""
import math
import re
import threading
import time
import unicodedata

import requests

import f5_config as C

_UA = "Mozilla/5.0 (compatible; prueba en papel)"


class Limitador:
    """Asegura una pausa mínima entre consultas a un mismo servidor."""

    def __init__(self, pausa):
        self.pausa = pausa
        self.ultimo = 0.0
        self.lock = threading.Lock()

    def esperar(self):
        with self.lock:
            falta = self.ultimo + self.pausa - time.time()
            if falta > 0:
                time.sleep(falta)
            self.ultimo = time.time()


class Cliente:
    def __init__(self, pausa):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": _UA, "Accept": "application/json"})
        self.lim = Limitador(pausa)
        self.consultas = 0
        self.fallos = 0
        self.bytes = 0

    def get(self, url, params=None, timeout=45):
        for intento in range(3):
            self.lim.esperar()
            try:
                r = self.s.get(url, params=params, timeout=timeout)
                self.consultas += 1
                self.bytes += len(r.content)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:
                    return None
                if r.status_code == 429:
                    time.sleep(30 * (intento + 1))
                    continue
            except (requests.RequestException, ValueError):
                pass
            self.fallos += 1
            time.sleep(3 * (intento + 1))
        return None


KAMBI = Cliente(C.KAMBI_PAUSA)
PIN = Cliente(C.PIN_PAUSA)


# --------------------------------------------------------------------------- Kambi
def kambi(op, ruta):
    return KAMBI.get(C.KAMBI_URL.format(op=op, ruta=ruta), params=C.KAMBI_PARAMS)


def kambi_lista(deporte, op=C.KAMBI_PRINCIPAL):
    """Partidos de un deporte con sus mercados principales."""
    d = kambi(op, f"listView/{C.DEPORTES[deporte]['kambi']}.json")
    return (d or {}).get("events", [])


def kambi_mercados(ids, op=C.KAMBI_PRINCIPAL):
    """Todos los mercados de antes del inicio de uno o varios partidos."""
    d = kambi(op, "betoffer/event/" + ",".join(str(i) for i in ids) + ".json")
    if d is None:
        return None, None
    return d.get("betOffers", []), {e["id"]: e for e in d.get("events", [])}


def info_evento(ev, deporte):
    path = [p.get("englishName") for p in ev.get("path", [])]
    return {
        "id": ev["id"], "deporte": deporte, "nombre": ev.get("name"),
        "local": ev.get("homeName"), "visitante": ev.get("awayName"),
        "inicio": ev.get("start"), "liga": " / ".join(path[1:]) or ev.get("group"),
        "estado": ev.get("state"),
    }


def filas_kambi(betoffers):
    """Aplana las cuotas de Kambi: una fila por resultado posible."""
    filas = []
    for b in betoffers:
        crit = b.get("criterion", {})
        for o in b.get("outcomes", []):
            if "odds" not in o:
                continue
            filas.append({
                "k": o["id"], "ev": b["eventId"], "bo": b["id"],
                "crit": crit.get("englishLabel", ""), "crit_es": crit.get("label", ""),
                "tipo": b.get("betOfferType", {}).get("englishName", ""),
                "lbl": o.get("englishLabel", o.get("label", "")), "lbl_es": o.get("label", ""),
                "part": o.get("participant"), "pid": o.get("participantId"),
                "line": o["line"] / 1000 if "line" in o else None,
                "odds": o["odds"] / 1000, "st": o.get("status", "OPEN"),
                "otype": o.get("type", ""), "cambio": o.get("changedDate"),
                "nsal": len(b.get("outcomes", [])),
            })
    return filas


# --------------------------------------------------------------------------- Pinnacle
def pin(ruta):
    return PIN.get(C.PIN_URL + ruta)


def americana_a_decimal(p):
    return 1 + p / 100 if p > 0 else 1 + 100 / -p


# --------------------------------------------------------------------------- nombres
_SOBRA = re.compile(r"\b(fc|cf|sc|ac|afc|cd|ca|club|de|the|jr|sr|ii|iii|if|fk|bk|sk|ss|us|ud|sd)\b")


def normal(txt):
    txt = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode().lower()
    txt = re.sub(r"[^a-z0-9 ]", " ", txt.replace("&", " "))
    return " ".join(_SOBRA.sub(" ", txt).split())


def parecido(a, b):
    """0 a 1: qué tanto se parecen dos nombres de equipo o jugador."""
    a, b = normal(a), normal(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    comun = len(ta & tb) / max(1, min(len(ta), len(tb)))
    if a in b or b in a:
        comun = max(comun, 0.9)
    return comun


def mismo_jugador(a, b):
    a, b = normal(a).split(), normal(b).split()
    if not a or not b:
        return False
    if a == b:
        return True
    # "Chris Brooks" y "Christopher Brooks": mismo apellido y misma inicial.
    return a[-1] == b[-1] and a[0][0] == b[0][0]


# --------------------------------------------------------------------------- proposiciones
_QUITAR_STAT = re.compile(r"\b(total|thrown|recorded|to be scored|by the player|by the|the player|player)\b")


def estadistica(txt):
    """Nombre común de una estadística, igual para Kambi y Pinnacle."""
    txt = _QUITAR_STAT.sub(" ", (txt or "").lower())
    txt = normal(txt)
    palabras = []
    for p in txt.split():
        if p.endswith("s") and len(p) > 3 and not p.endswith("ss"):
            p = p[:-1]
        palabras.append(p)
    return " ".join(palabras)


def periodo(txt):
    t = (txt or "").lower()
    t = re.sub(r"\(.*?\)", "", t).strip(" -")
    if t in ("", "including overtime", "including extra innings", "full time", "regular time"):
        return "full"
    if t in ("first half", "1st half", "1st half - including overtime"):
        return "h1"
    if "first 5 innings" in t:
        return "f5"
    return t


def _dividir_crit(crit):
    crit = re.sub(r"\s*\(.*?\)\s*", " ", crit).strip()
    if " - " in crit:
        base, per = crit.split(" - ", 1)
    else:
        base, per = crit, ""
    return base.strip(), periodo(per)


def _umbral(line, dir_):
    """Umbral entero de una línea x.5. Las líneas enteras o de cuarto se saltan
    (pueden devolver el dinero y no encajan en la escalera)."""
    if line is None or abs(line * 2 - round(line * 2)) > 1e-6 or abs(line - round(line)) < 1e-6:
        return None
    return math.ceil(line) if dir_ == "ge" else math.floor(line)


def proposicion_kambi(f, ev):
    """Traduce una fila de Kambi a (grupo, dir, t) o None si no aplica."""
    crit, tipo, lbl, part = f["crit"], f["tipo"], f["lbl"], f["part"]
    base, per = _dividir_crit(crit)
    evid = f["ev"]

    # Jugadores: escalera "N+ algo by the Player"
    m = re.match(r"^(\d+)\+ (.+)$", base)
    if m and part and f["otype"] == "OT_YES":
        return (evid, "j:" + normal(part), estadistica(m.group(2)), per), "ge", int(m.group(1))
    m = re.match(r"^Player to hit (\d+) or more (.+)$", base, re.I)
    if m and part and f["otype"] == "OT_YES":
        return (evid, "j:" + normal(part), estadistica(m.group(2)), per), "ge", int(m.group(1))
    if part and f["otype"] == "OT_YES" and (base == "Touchdown Scorer" or base.startswith("Player to score a Touchdown")):
        return (evid, "j:" + normal(part), "touchdown", per), "ge", 1
    if part and f["otype"] == "OT_YES" and base.startswith("Player to Hit a Home Run"):
        return (evid, "j:" + normal(part), "home run", per), "ge", 1
    # Jugadores: más/menos
    if tipo == "Player Occurrence Line" and part and f["otype"] in ("OT_OVER", "OT_UNDER"):
        dir_ = "ge" if f["otype"] == "OT_OVER" else "le"
        t = _umbral(f["line"], dir_)
        if t is None:
            return None
        return (evid, "j:" + normal(part), estadistica(base), per), dir_, t

    # Partido: más/menos (totales del partido o de un equipo)
    if tipo == "Over/Under" and f["otype"] in ("OT_OVER", "OT_UNDER") and f["nsal"] == 2:
        dir_ = "ge" if f["otype"] == "OT_OVER" else "le"
        t = _umbral(f["line"], dir_)
        if t is None:
            return None
        quien = "partido"
        m = re.match(r"^Total (\w+) by (.+)$", base)
        if m:
            eq = m.group(2)
            if parecido(eq, ev.get("local")) >= 0.9:
                quien = "local"
            elif parecido(eq, ev.get("visitante")) >= 0.9:
                quien = "visitante"
            else:
                return None
            base = "Total " + m.group(1)
        return (evid, quien, estadistica(base), per), dir_, t

    # Partido: hándicap de dos opciones (margen = local - visitante)
    if tipo in ("Handicap", "Asian Handicap") and f["nsal"] == 2 and f["otype"] in ("OT_ONE", "OT_TWO"):
        line = f["line"]
        if line is None or abs(line - round(line)) < 1e-6 or abs(line * 2 - round(line * 2)) > 1e-6:
            return None
        if f["otype"] == "OT_ONE":
            return (evid, "partido", "margen", per), "ge", math.ceil(-line)
        return (evid, "partido", "margen", per), "le", math.floor(line)
    return None


def es_ganador(f):
    """Mercado de ganador del partido (1X2 o de dos opciones)."""
    base, per = _dividir_crit(f["crit"])
    return f["tipo"] == "Match" and base in ("Full Time", "Moneyline", "Match Odds") and per == "full"


def pinnacle_proposiciones(matchups, mercados):
    """
    Traduce las cuotas de Pinnacle a precios justos (sin margen) por
    proposición, con la misma forma que las de Kambi, más el ganador.

    Devuelve lista de dicts: {pm (id del partido de Pinnacle), quien, stat, per,
    dir, t, p (probabilidad justa), cuota (de Pinnacle), hora}.
    """
    por_id = {m["id"]: m for m in matchups}
    salida = []
    for mk in mercados:
        if mk.get("status") != "open" or not mk.get("prices"):
            continue
        mu = por_id.get(mk["matchupId"])
        if not mu:
            continue
        precios = mk["prices"]
        dec = [americana_a_decimal(p["price"]) for p in precios]
        inv = [1 / d for d in dec]
        tot = sum(inv)
        justas = [x / tot for x in inv]  # margen quitado en proporción
        tipo, per_n = mk["type"], mk.get("period", 0)
        deporte_pin = mu["league"]["sport"]["id"]
        per = {0: "full", 1: "f5" if deporte_pin == 3 else "h1"}.get(per_n, f"p{per_n}")

        if mu["type"] == "special":
            # Apuestas de jugadores: "Zay Flowers Total Receptions"
            if mu.get("special", {}).get("category") != "Player Props" or tipo != "total":
                continue
            unidades = mu.get("units") or ""
            desc = mu["special"]["description"]
            nombre = desc.split(" Total ")[0] if " Total " in desc else desc.replace(unidades, "").strip()
            padre = mu.get("parentId")
            for p, pj, d in zip(precios, justas, dec):
                nomsal = next((x["name"] for x in mu["participants"] if x["id"] == p["participantId"]), "")
                dir_ = "ge" if nomsal == "Over" else "le"
                t = _umbral(p.get("points"), dir_)
                if t is None:
                    continue
                salida.append({"pm": padre, "quien": "j:" + normal(nombre), "jugador": nombre,
                               "stat": estadistica(unidades), "per": per, "dir": dir_, "t": t,
                               "p": pj, "cuota": d})
            continue
        if mu["type"] != "matchup":
            continue
        if tipo == "moneyline":
            for p, pj, d in zip(precios, justas, dec):
                salida.append({"pm": mu["id"], "quien": "partido", "stat": "ganador", "per": per,
                               "dir": p["designation"], "t": None, "p": pj, "cuota": d})
        elif tipo == "total":
            stat = {29: "goal", 3: "run"}.get(deporte_pin, "point")
            for p, pj, d in zip(precios, justas, dec):
                dir_ = "ge" if p["designation"] == "over" else "le"
                t = _umbral(p.get("points"), dir_)
                if t is not None:
                    salida.append({"pm": mu["id"], "quien": "partido", "stat": stat, "per": per,
                                   "dir": dir_, "t": t, "p": pj, "cuota": d})
        elif tipo == "team_total":
            stat = {29: "goal", 3: "run"}.get(deporte_pin, "point")
            lado = mk["key"].split(";")[-1]
            for p, pj, d in zip(precios, justas, dec):
                dir_ = "ge" if p["designation"] == "over" else "le"
                t = _umbral(p.get("points"), dir_)
                if t is not None:
                    salida.append({"pm": mu["id"], "quien": "local" if lado == "home" else "visitante",
                                   "stat": stat, "per": per, "dir": dir_, "t": t, "p": pj, "cuota": d})
        elif tipo == "spread":
            for p, pj, d in zip(precios, justas, dec):
                line = p.get("points")
                if line is None or abs(line - round(line)) < 1e-6 or abs(line * 2 - round(line * 2)) > 1e-6:
                    continue
                if p["designation"] == "home":
                    salida.append({"pm": mu["id"], "quien": "partido", "stat": "margen", "per": per,
                                   "dir": "ge", "t": math.ceil(-line), "p": pj, "cuota": d})
                else:
                    salida.append({"pm": mu["id"], "quien": "partido", "stat": "margen", "per": per,
                                   "dir": "le", "t": math.floor(line), "p": pj, "cuota": d})
    return salida
