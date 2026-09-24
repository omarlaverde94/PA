"""
Fase 5: detector de errores de cuota en Kambi.

Dos tipos de error:

a) CONTRA EL MERCADO: la cuota de Kambi paga bastante más que el precio justo
   de Pinnacle (Pinnacle sin su margen). Pinnacle no usa Kambi y es la casa
   más afinada; las casas que usan Kambi o copian su precio no cuentan.

b) CONTRADICCIÓN INTERNA de Kambi (no necesita datos de afuera):
   - "escalera": algo más fácil paga más que algo más difícil del mismo
     jugador o partido (por ejemplo, "1+ pases de TD" paga más que "2+").
   - "par": una apuesta paga más que el precio justo que el propio Kambi
     marca en el más/menos de la misma línea.
   - "modelo": para estadísticas de conteo chico (touchdowns, jonrones...),
     se estima la escalera completa a partir del más/menos principal y se
     marca la apuesta que se sale mucho. Es la regla menos segura.
   - "arbitraje": las dos caras de un mismo mercado suman menos de 100%.

Cada hallazgo trae la cuota, un precio justo estimado, qué lo respalda y una
explicación en español sencillo.
"""
import math
from collections import defaultdict

import f5_config as C
from f5_fuentes import es_ganador, mismo_jugador, proposicion_kambi

# Estadísticas de conteo chico en las que se usa el modelo de Poisson.
CONTEO = {"touchdown", "touchdown passe", "interception", "home run", "rbi", "run",
          "double", "stolen base", "goal", "assist", "shot on target"}


def _poisson_ge(lam, t):
    if t <= 0:
        return 1.0
    acum, termino = 0.0, math.exp(-lam)
    for k in range(t):
        acum += termino
        termino *= lam / (k + 1)
    return max(0.0, 1.0 - acum)


def _lambda_para(p_ge, t):
    lo, hi = 1e-4, 30.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _poisson_ge(mid, t) < p_ge:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _dir_txt(dir_, t):
    return f"{t} o más" if dir_ == "ge" else f"{t} o menos"


def describir(f):
    """Nombre legible de la apuesta en español (tal como lo muestra Kambi)."""
    partes = [f.get("crit_es") or f["crit"]]
    if f.get("part") and f["part"] not in (f.get("lbl_es") or ""):
        partes.append(f["part"])
    sal = f.get("lbl_es") or f["lbl"]
    if f.get("line") is not None and (f["otype"] in ("OT_OVER", "OT_UNDER") or "Handicap" in f["tipo"]):
        sal = f"{sal} {f['line']:+g}" if "Handicap" in f["tipo"] else f"{sal} {f['line']:g}"
    partes.append(sal)
    return " — ".join(partes)


def _hallazgo(f, tipo, regla, justa, respaldo, explicacion, confianza, extra=None):
    ventaja = f["odds"] / justa - 1 if justa else None
    h = {
        "clave": f"{regla}:{f['k']}", "tipo": tipo, "regla": regla, "k": f["k"], "ev": f["ev"],
        "apuesta": describir(f), "cuota": f["odds"], "justa": round(justa, 3) if justa else None,
        "ventaja": round(ventaja, 4) if ventaja is not None else None,
        "respaldo": respaldo, "explicacion": explicacion, "confianza": confianza,
        "jugador": bool(f.get("part")) and f["tipo"] == "Player Occurrence Line",
        "en_rango": C.RANGO_PREFERIDO[0] <= f["odds"] <= C.RANGO_PREFERIDO[1],
    }
    if extra:
        h.update(extra)
    return h


def internos(filas, ev):
    """Contradicciones internas de Kambi en un partido."""
    abiertas = [f for f in filas if f["st"] == "OPEN" and f["odds"] > 1.0]
    props = []
    for f in abiertas:
        p = proposicion_kambi(f, ev)
        if p:
            props.append((f, *p))
    out = []

    # --- arbitraje: dos caras que suman menos de 100 %
    por_bo = defaultdict(list)
    for f in abiertas:
        por_bo[f["bo"]].append(f)
    for bo, fs in por_bo.items():
        if len(fs) < 2 or len(fs) != fs[0]["nsal"] or fs[0]["nsal"] > 3:
            continue
        suma = sum(1 / f["odds"] for f in fs)
        if suma < C.UMBRAL_ARBITRAJE:
            sospechosa = max(fs, key=lambda f: (f.get("cambio") or "", f["odds"]))
            otras = sum(1 / f["odds"] for f in fs if f is not sospechosa)
            justa = 1 / max(1e-6, 1 - otras) if otras < 1 else None
            if justa and sospechosa["odds"] <= C.CUOTA_MAX_ALERTA:
                out.append(_hallazgo(
                    sospechosa, "interno", "arbitraje", justa,
                    f"Las {len(fs)} opciones del mismo mercado suman {suma:.1%}: apostando a todas se ganaría siempre.",
                    "Kambi paga tanto por todas las opciones de este mercado que juntas suman menos de 100%. "
                    "Eso no puede pasar a propósito: una de las cuotas está mal, probablemente la que cambió última.",
                    "alta"))

    grupos = defaultdict(list)
    for f, g, d, t in props:
        grupos[g].append((f, d, t))

    for g, items in grupos.items():
        # --- par: precio justo del más/menos de Kambi en la misma línea
        justo_ge = {}  # t -> prob. justa de "t o más"
        for f, d, t in items:
            if f["otype"] in ("OT_OVER", "OT_ONE") and d == "ge":
                pareja = [x for x, dd, tt in items if x["bo"] == f["bo"] and x is not f and dd == "le" and tt == t - 1]
                if pareja:
                    a, b = 1 / f["odds"], 1 / pareja[0]["odds"]
                    justo_ge[t] = a / (a + b)
        for f, d, t in items:
            p = justo_ge.get(t) if d == "ge" else (1 - justo_ge[t + 1] if (t + 1) in justo_ge else None)
            if p is None or p <= 0:
                continue
            # Solo se compara contra el par de OTRO mercado (el propio par ya tiene margen)
            mismo_bo = any(x["bo"] == f["bo"] and x is not f for x, dd, tt in items
                           if (dd != d) and ((d == "ge" and tt == t - 1) or (d == "le" and tt == t + 1)))
            if mismo_bo:
                continue
            justa = 1 / p
            if f["odds"] / justa - 1 >= C.UMBRAL_PAR and f["odds"] <= C.CUOTA_MAX_ALERTA:
                out.append(_hallazgo(
                    f, "interno", "par", justa,
                    f"El más/menos de Kambi en la misma línea ({_dir_txt(d, t)}) da un precio justo de {justa:.2f}.",
                    f"Kambi ofrece esta misma apuesta en otro mercado (el de más/menos) a un precio que, sin margen, "
                    f"equivale a {justa:.2f}. Aquí paga {f['odds']:.2f}, bastante más que su propio precio justo.",
                    "media"))

        # --- escalera: lo más fácil no puede pagar más que lo más difícil
        for d in ("ge", "le"):
            por_t = defaultdict(list)
            for f, dd, t in items:
                if dd == d:
                    por_t[t].append(f)
            ts = sorted(por_t)
            if len(ts) < 2:
                continue
            # "más fácil" = umbral menor en ge, mayor en le
            orden = ts if d == "ge" else ts[::-1]
            for i, t_facil in enumerate(orden[:-1]):
                facil = max(por_t[t_facil], key=lambda f: f["odds"])
                dificiles = [max(por_t[t2], key=lambda f: f["odds"]) for t2 in orden[i + 1:]]
                dificil = min(dificiles, key=lambda f: f["odds"])
                if facil["odds"] > dificil["odds"] * (1 + C.TOLERANCIA_ESCALERA) and facil["odds"] <= C.CUOTA_MAX_ALERTA:
                    t_dif = next(t2 for t2 in orden[i + 1:] if dificil in por_t[t2])
                    out.append(_hallazgo(
                        facil, "interno", "escalera", dificil["odds"],
                        f"\"{_dir_txt(d, t_facil)}\" paga {facil['odds']:.2f} y \"{_dir_txt(d, t_dif)}\" "
                        f"(más difícil) paga {dificil['odds']:.2f}.",
                        f"Kambi paga más por \"{_dir_txt(d, t_facil)}\" que por \"{_dir_txt(d, t_dif)}\", "
                        f"que es más difícil de cumplir. Eso es imposible si los precios estuvieran bien: "
                        f"el precio justo tiene que ser menor a {dificil['odds']:.2f}.",
                        "alta", {"cota": True}))

        # --- modelo de conteo (Poisson) a partir del más/menos principal
        stat = g[2]
        if stat in CONTEO and justo_ge:
            t0 = min(justo_ge, key=lambda t: abs(justo_ge[t] - 0.5))
            if t0 <= 3 and 0.03 < justo_ge[t0] < 0.97:
                lam = _lambda_para(justo_ge[t0], t0)
                for f, d, t in items:
                    if d != "ge" or t == t0 or abs(t - t0) > 1 or f["otype"] != "OT_YES":
                        continue
                    p = _poisson_ge(lam, t)
                    if p <= 0:
                        continue
                    justa = 1 / p
                    if f["odds"] / justa - 1 >= C.UMBRAL_MODELO and f["odds"] <= C.CUOTA_MAX_ALERTA:
                        out.append(_hallazgo(
                            f, "interno", "modelo", justa,
                            f"El más/menos de Kambi en {t0} da {justo_ge[t0]:.0%} y con eso "
                            f"\"{t} o más\" debería rondar {p:.0%}.",
                            f"Con la línea principal de Kambi para esta estadística, lo esperable es que "
                            f"\"{t} o más\" pague cerca de {justa:.2f}, pero paga {f['odds']:.2f}. Es una estimación "
                            f"con un modelo sencillo, así que es menos segura que las otras reglas.",
                            "baja"))
    return out


def _indice_pinnacle(pin_props):
    indice = defaultdict(list)
    for p in pin_props or []:
        indice[(p["stat"], p["per"], p["dir"], p["t"])].append(p)
    return indice


def _pinnacle_de(f, ev, pin_props, indice):
    """La cuota de Pinnacle equivalente a una fila de Kambi, o None."""
    if es_ganador(f):
        lado = {"OT_ONE": "home", "OT_CROSS": "draw", "OT_TWO": "away"}.get(f["otype"])
        # En deportes de EE. UU. Kambi a veces da ganador de 3 opciones: no se compara con 2 opciones
        n_pin = len({p["dir"] for p in pin_props if p["stat"] == "ganador" and p["per"] == "full"})
        cand = indice.get(("ganador", "full", lado, None), []) if n_pin == f["nsal"] else []
    else:
        prop = proposicion_kambi(f, ev)
        if not prop:
            return None
        (evid, quien, stat, per), d, t = prop
        cand = [p for p in indice.get((stat, per, d, t), [])
                if p["quien"] == quien or (quien.startswith("j:") and p["quien"].startswith("j:")
                                           and mismo_jugador(quien[2:], p["quien"][2:]))]
    return cand[0] if cand else None


def agregar_pinnacle(hallazgos, filas, ev, pin_props):
    """Anota en cada hallazgo el precio justo de Pinnacle (si tiene esa apuesta)."""
    indice = _indice_pinnacle(pin_props)
    por_k = {f["k"]: f for f in filas}
    for h in hallazgos:
        f = por_k.get(h["k"])
        p = _pinnacle_de(f, ev, pin_props, indice) if (f and pin_props) else None
        h["pin_justa"] = round(1 / p["p"], 3) if p else None
    return hallazgos


def contra_mercado(filas, ev, pin_props):
    """Kambi contra el precio justo de Pinnacle."""
    if not pin_props:
        return []
    indice = _indice_pinnacle(pin_props)
    out = []
    for f in filas:
        if f["st"] != "OPEN" or f["odds"] > C.CUOTA_MAX_ALERTA:
            continue
        p = _pinnacle_de(f, ev, pin_props, indice)
        cand = [p] if p else None
        if not cand:
            continue
        p = cand[0]
        justa = 1 / p["p"]
        ventaja = f["odds"] / justa - 1
        if ventaja < C.UMBRAL_MERCADO:
            continue
        confianza = "media"
        aviso = ""
        if ventaja >= 0.5:
            confianza = "baja"
            aviso = (" La diferencia es tan grande que puede ser un error de emparejamiento "
                     "(otra línea u otro jugador): revísalo con cuidado.")
        out.append(_hallazgo(
            f, "mercado", "mercado", justa,
            f"Pinnacle (sin margen) da {justa:.2f}; su cuota publicada es {p['cuota']:.2f}.",
            f"Kambi paga {f['odds']:.2f} y el precio justo según Pinnacle, la casa más afinada del mercado "
            f"y que no usa Kambi, es {justa:.2f}. Kambi paga {ventaja:.0%} más de lo que vale la apuesta." + aviso,
            confianza, {"pin_cuota": round(p["cuota"], 3)}))
    return out
