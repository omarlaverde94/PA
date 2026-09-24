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


if __name__ == "__main__":
    caso_touchdown()
    caso_escalera()
    caso_sin_error()
    caso_arbitraje()
    print("Todas las pruebas pasaron.")
