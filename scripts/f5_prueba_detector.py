"""
Fase 5: prueba rápida del detector con casos inventados (no consulta nada).

Reproduce el caso real: "1+ pases de touchdown" a 1.80 cuando el más/menos
de 1.5 pases de TD del mismo jugador está parejo. Tiene que salir alerta.

    python f5_prueba_detector.py
"""
from f5_detector import contra_mercado, internos

EV = {"id": 1, "local": "Green Bay Packers", "visitante": "Atlanta Falcons"}


def fila(k, bo, crit, tipo, lbl, odds, line=None, part=None, otype="OT_YES", nsal=1):
    return {"k": k, "ev": 1, "bo": bo, "crit": crit, "crit_es": crit, "tipo": tipo, "lbl": lbl,
            "lbl_es": lbl, "part": part, "pid": 9, "line": line, "odds": odds, "st": "OPEN",
            "otype": otype, "cambio": f"2026-09-24T00:00:{k:02d}Z", "nsal": nsal}


def caso_touchdown():
    crit_ou = "Total Touchdown Passes Thrown by the Player - Including Overtime"
    filas = [
        fila(1, 10, crit_ou, "Player Occurrence Line", "Over", 1.87, 1.5, "Jordan Love", "OT_OVER", 2),
        fila(2, 10, crit_ou, "Player Occurrence Line", "Under", 1.91, 1.5, "Jordan Love", "OT_UNDER", 2),
        fila(3, 11, "1+ Touchdown Passes By The Player - Including Overtime", "Player Occurrence Line",
             "Yes", 1.80, 1.0, "Jordan Love"),
        fila(4, 12, "2+ Touchdown Passes By The Player - Including Overtime", "Player Occurrence Line",
             "Yes", 1.84, 2.0, "Jordan Love"),
        fila(5, 13, "3+ Touchdown Passes By The Player - Including Overtime", "Player Occurrence Line",
             "Yes", 4.35, 3.0, "Jordan Love"),
    ]
    h = internos(filas, EV)
    reglas = sorted({x["regla"] for x in h if x["k"] == 3})
    print("Caso 1.80 (1+ pases de TD):", reglas)
    assert "par" in reglas or "modelo" in reglas, "el caso real de 1.80 no se detectó"


def caso_escalera():
    filas = [
        fila(1, 20, "2+ Touchdown Passes By The Player - Including Overtime", "Player Occurrence Line",
             "Yes", 3.10, 2.0, "Michael Penix Jr."),
        fila(2, 21, "3+ Touchdown Passes By The Player - Including Overtime", "Player Occurrence Line",
             "Yes", 2.60, 3.0, "Michael Penix Jr."),
    ]
    h = internos(filas, EV)
    print("Escalera (2+ paga más que 3+):", [(x["regla"], x["k"]) for x in h])
    assert any(x["regla"] == "escalera" and x["k"] == 1 for x in h)


def caso_sin_error():
    crit = "Total Points - Including Overtime"
    filas = [
        fila(1, 30, crit, "Over/Under", "Over", 1.90, 45.5, None, "OT_OVER", 2),
        fila(2, 30, crit, "Over/Under", "Under", 1.90, 45.5, None, "OT_UNDER", 2),
        fila(3, 31, crit, "Over/Under", "Over", 2.10, 47.5, None, "OT_OVER", 2),
        fila(4, 31, crit, "Over/Under", "Under", 1.72, 47.5, None, "OT_UNDER", 2),
    ]
    h = internos(filas, EV)
    print("Totales bien puestos:", h)
    assert not h
    # Contra Pinnacle: over 45.5 a 1.90 cuando lo justo es 1.60 -> alerta
    pin = [{"pm": 1, "quien": "partido", "stat": "point", "per": "full", "dir": "ge", "t": 46, "p": 0.625, "cuota": 1.55}]
    m = contra_mercado(filas, EV, pin)
    print("Contra Pinnacle:", [(x["k"], x["ventaja"]) for x in m])
    assert [x["k"] for x in m] == [1]


def caso_arbitraje():
    crit = "Point Spread - Including Overtime"
    filas = [
        fila(1, 40, crit, "Handicap", "Green Bay Packers", 2.30, -6.5, "Green Bay Packers", "OT_ONE", 2),
        fila(2, 40, crit, "Handicap", "Atlanta Falcons", 1.95, 6.5, "Atlanta Falcons", "OT_TWO", 2),
    ]
    h = internos(filas, EV)
    print("Arbitraje:", [(x["regla"], x["k"]) for x in h])
    assert any(x["regla"] == "arbitraje" for x in h)


def caso_estimacion():
    """Cuota estimada en tu casa: solo merecen alerta las del rango vigente (por defecto 1.40-2.00)
    que siguen sobre el justo."""
    import gzip
    import json
    import tempfile
    from f5_estimacion import Estimador
    tabla = {"*|*|*": {"razon": 0.99, "bajo": 0.97, "alto": 1.0, "n": 100},
             "futbol|ganador|>6.00": {"razon": 0.9375, "bajo": 0.9, "alto": 1.0, "n": 100},
             "nfl|totales|1.50-2.00": {"razon": 0.988, "bajo": 0.98, "alto": 0.995, "n": 100}}
    ruta = tempfile.mktemp(suffix=".json.gz")
    with gzip.open(ruta, "wt") as fh:
        json.dump({"casa": "Casa de prueba", "tabla": tabla}, fh)
    est = Estimador(ruta)
    crit = "Total Points - Including Overtime"
    over = fila(1, 30, crit, "Over/Under", "Over", 1.95, 45.5, None, "OT_OVER", 2)
    justo_175 = {"k": 1, "regla": "mercado", "justa": 1.75}
    r = est.evaluar(justo_175, {1: over}, "nfl")
    print("NFL 1.95 contra justo 1.75:", r["cuota_rb"], r["ventaja_rb"], r["avisable"])
    assert r["cuota_rb"] == 1.93 and r["avisable"] and r["casa"] == "Casa de prueba"
    # Kambi 1.86 pasa el 6% (6.3%), pero en tu casa (1.84) ya no: no merece alerta
    over2 = dict(over, odds=1.86)
    r = est.evaluar(justo_175, {1: over2}, "nfl")
    print("NFL 1.86 contra justo 1.75:", r["cuota_rb"], r["ventaja_rb"], r["avisable"])
    assert not r["avisable"]
    # Caso real Austria - Israel: Kambi 8.00, Pinnacle 6.98 -> tu casa ~7.50: fuera del rango
    israel = fila(2, 40, "Full Time", "Match", "2", 8.0, None, "Israel", "OT_TWO", 3)
    r = est.evaluar({"k": 2, "regla": "mercado", "justa": 6.98}, {2: israel}, "futbol")
    print("Austria - Israel:", r["cuota_rb"], r["en_rango"], r["avisable"])
    assert r["cuota_rb"] == 7.5 and not r["en_rango"] and not r["avisable"]
    # 1.45 estimada: dentro del rango por defecto (1.40-2.00)
    bajo = dict(over, odds=1.46)
    r = est.evaluar({"k": 1, "regla": "mercado", "justa": 1.30}, {1: bajo}, "nfl")
    print("NFL 1.46 contra justo 1.30:", r["cuota_rb"], r["avisable"])
    assert r["cuota_rb"] == 1.45 and r["avisable"]
    # Sin tabla no hay estimación ni alertas
    sin = Estimador("/no/existe.json.gz")
    assert not sin.ok and not sin.evaluar(justo_175, {1: over}, "nfl")["avisable"]


if __name__ == "__main__":
    caso_touchdown()
    caso_escalera()
    caso_sin_error()
    caso_arbitraje()
    caso_estimacion()
    print("Todas las pruebas pasaron.")
