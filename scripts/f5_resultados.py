"""
Fase 5: ¿se habría ganado cada alerta? Resultados reales gratis de ESPN.

Para cada alerta cuyo partido ya terminó, busca el marcador (y las
estadísticas de jugadores en NFL, MLB y NBA) y decide si la apuesta se
habría ganado, a la cuota de Kambi del momento de la detección, con 1 unidad.

Tipos de apuesta que se pueden liquidar:
- Ganador, totales, hándicap y totales por equipo del partido completo.
- Jugadores: yardas, recepciones, pases de TD, touchdowns, intercepciones,
  hits, carreras, impulsadas, jonrones, ponches, puntos, rebotes, asistencias.
Lo demás queda "sin resultado" y no entra en las cuentas.

Guarda data/fase5/resultados.json.gz: {id de alerta: {gana, unidades, valor, detalle}}.
  gana = True / False, o None si la apuesta se anula (jugador que no jugó).

    python f5_resultados.py
"""
import gzip
import json
import time
from datetime import datetime, timedelta, timezone

import requests

import f5_config as C
from f5_fuentes import es_ganador, mismo_jugador, normal, parecido, proposicion_kambi
from f5_informe import leer_alertas
from f5_registro import iso_a_ts

ESPN = "https://site.web.api.espn.com/apis/site/v2/sports/"
RUTA = {"nfl": "football/nfl", "nba": "basketball/nba", "mlb": "baseball/mlb", "futbol": "soccer/all"}
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (compatible; prueba en papel)"
_cache = {}


def _get(url):
    if url in _cache:
        return _cache[url]
    for intento in range(3):
        try:
            r = S.get(url, timeout=40)
            if r.status_code == 200:
                _cache[url] = r.json()
                time.sleep(0.5)
                return _cache[url]
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (intento + 1))
    _cache[url] = None
    return None


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


def partido_espn(a):
    ini = datetime.fromtimestamp(iso_a_ts(a["inicio"]), timezone.utc)
    local, visit = a["partido"].split(" - ", 1) if " - " in a["partido"] else (a["partido"], "")
    for delta in (0, -1, 1):
        dia = (ini + timedelta(days=delta)).strftime("%Y%m%d")
        d = _get(f"{ESPN}{RUTA[a['deporte']]}/scoreboard?dates={dia}&limit=500")
        for e in (d or {}).get("events", []):
            comp = e["competitions"][0]
            eq = {c["homeAway"]: c for c in comp["competitors"]}
            if "home" not in eq or "away" not in eq:
                continue
            if parecido(eq["home"]["team"]["displayName"], local) >= 0.5 and \
                    parecido(eq["away"]["team"]["displayName"], visit) >= 0.5:
                return e
    return None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def estadisticas_jugadores(deporte, evid):
    s = _get(f"{ESPN}{RUTA[deporte]}/summary?event={evid}")
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


def liquidar_una(a, cat):
    f = cat.get(a["k"])
    if not f:
        return None
    e = partido_espn(a)
    if not e or not e["status"]["type"].get("completed"):
        return None
    comp = e["competitions"][0]
    eq = {c["homeAway"]: c for c in comp["competitors"]}
    gl, gv = _num(eq["home"]["score"]), _num(eq["away"]["score"])
    if gl is None or gv is None:
        return None
    f = dict(f, ev=a["ev"], odds=a["cuota"], st="OPEN", nsal=2 if f["tipo"] != "Match" else 0)
    info = {"local": a["partido"].split(" - ")[0], "visitante": a["partido"].split(" - ")[-1]}
    gana, valor, detalle = None, None, ""
    if es_ganador(f):
        gana = {"OT_ONE": gl > gv, "OT_TWO": gv > gl, "OT_CROSS": gl == gv}.get(f["otype"])
        detalle = f"marcador {gl:g}-{gv:g}"
    else:
        f["nsal"] = 2
        p = proposicion_kambi(f, info)
        if not p:
            return None
        (_, quien, stat, per), d, t = p
        if per != "full":
            return None
        if quien in ("partido", "local", "visitante"):
            if stat == "margen":
                valor = gl - gv
            elif stat in ("point", "run", "goal"):
                valor = {"partido": gl + gv, "local": gl, "visitante": gv}[quien]
            else:
                return None
            detalle = f"marcador {gl:g}-{gv:g}"
        elif a["deporte"] in ("nfl", "mlb", "nba"):
            jug = estadisticas_jugadores(a["deporte"], e["id"])
            nombre = next((n for n in jug if normal(n) == quien[2:] or mismo_jugador(n, quien[2:])), None)
            if nombre is None:
                return {"gana": None, "unidades": 0.0, "detalle": "el jugador no aparece en la planilla (apuesta anulada)"}
            valor = jug[nombre].get(stat)
            if valor is None:
                return None
            detalle = f"{nombre}: {stat} = {valor:g}"
        else:
            return None
        gana = valor >= t if d == "ge" else valor <= t
    if gana is None:
        return None
    return {"gana": bool(gana), "unidades": round(a["cuota"] - 1 if gana else -1.0, 3),
            "valor": valor, "detalle": detalle}


def liquidar():
    ruta = C.DATA / "resultados.json.gz"
    res = {}
    if ruta.exists():
        with gzip.open(ruta, "rt", encoding="utf-8") as fh:
            res = json.load(fh)
    alertas = [a for a in leer_alertas() if a["id"] not in res
               and time.time() - iso_a_ts(a["inicio"]) > 5 * 3600]
    if not alertas:
        return res
    cat = catalogo({a["k"] for a in alertas})
    for a in alertas:
        try:
            r = liquidar_una(a, cat)
        except Exception as e:  # un partido raro no debe frenar a los demás
            r = None
            print("no se pudo liquidar", a["id"], type(e).__name__)
        if r is not None:
            res[a["id"]] = r
        elif time.time() - iso_a_ts(a["inicio"]) > 3 * 86400:
            res[a["id"]] = {"gana": None, "unidades": 0.0, "detalle": "sin resultado disponible"}
    tmp = ruta.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(res, fh)
    tmp.replace(ruta)
    return res


if __name__ == "__main__":
    r = liquidar()
    print(len(r), "alertas con resultado o cerradas sin resultado")
