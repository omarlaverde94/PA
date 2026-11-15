"""
Fase 5: pruebas del emparejamiento de partidos y de la liquidación automática
(no se conecta a ninguna página: todo se simula).

Comprueba que:
1. Un partido solo se empareja con el mismo partido: hora de inicio con margen corto,
   misma competencia, misma categoría (sub-21, reservas, femenino) y nombres de los dos
   equipos. Incluye el caso real de Al Dhaid Sub-21.
2. El marcador se toma del partido de ESA fecha y hora (caso real de MLB: dos juegos de
   la misma serie en días seguidos).
3. Cada tipo de apuesta se liquida bien (ganada, perdida, nula).
4. Con una sola fuente o con fuentes que no coinciden se espera; a las 48 horas la
   alerta queda "sin resultado" en vez de abierta.
5. El tope de ventaja: más de 15 % se descarta y de 10 % a 15 % lleva aviso.

    python f5_prueba_liquidacion.py
"""
import os
import tempfile

os.environ["F5_DATA"] = tempfile.mkdtemp()
os.environ.pop("API_FOOTBALL_KEY", None)

import f5_config as C  # noqa: E402
import f5_emparejar as EM  # noqa: E402
import f5_estimacion as ES  # noqa: E402
import f5_resultados as R  # noqa: E402
from f5_registro import iso_a_ts  # noqa: E402

T0 = iso_a_ts("2026-09-24T13:25:00Z")


def caso_al_dhaid():
    k = {"local": "Al Dhaid U21", "visitante": "Shabab Al-Ahli Dubai FC U21", "_ts": T0,
         "liga": "United Arab Emirates / U21 League"}

    def otro(local, visitante, minutos=0, liga="UAE - U21 League"):
        return {"local": local, "visitante": visitante, "ts": T0 + minutos * 60, "liga": liga}

    # Otro partido de la misma liga, 5 minutos después: solo comparten "Al" y "U21" -> NO
    assert EM.mejor(k, [otro("Al Jazira U21", "Al Urooba U21", 5)], C.PIN_MARGEN_INICIO) is None
    assert EM.puntaje(k, otro("Al Jazira", "Al Urooba", 5), C.PIN_MARGEN_INICIO)[1] == "nombres"
    # El primer equipo (otra categoría) -> NO
    assert EM.puntaje(k, otro("Al Dhaid", "Shabab Al Ahli", 0, "UAE - Pro League"), C.PIN_MARGEN_INICIO)[1] == "categoria"
    # Mismo partido pero a otra hora -> NO
    assert EM.puntaje(k, otro("Al Dhaid U21", "Shabab Al Ahli U21", 25), C.PIN_MARGEN_INICIO)[1] == "hora"
    # Mismos nombres en otro país -> NO
    assert EM.puntaje(k, otro("Al Dhaid U21", "Shabab Al Ahli U21", 0, "Qatar - U21 League"),
                      C.PIN_MARGEN_INICIO)[1] == "competencia"
    # Equipos al revés (local por visitante) -> NO
    assert EM.mejor(k, [otro("Shabab Al Ahli U21", "Al Dhaid U21")], C.PIN_MARGEN_INICIO) is None
    # El partido correcto -> SÍ
    bueno = otro("Al Dhaid U21", "Shabab Al Ahli U21", 2)
    assert EM.mejor(k, [otro("Al Jazira U21", "Al Urooba U21", 5), bueno], C.PIN_MARGEN_INICIO) is bueno
    # Femenino contra masculino -> NO; femenino contra femenino -> SÍ
    f = {"local": "Inter Milan (F)", "visitante": "Juventus (F)", "_ts": T0, "liga": "Italy / Serie A (W)"}
    assert EM.puntaje(f, otro("Internazionale", "Juventus", 0, "Italy - Serie A"), C.PIN_MARGEN_INICIO)[1] == "categoria"
    assert EM.mejor(f, [otro("Internazionale", "Juventus", 0, "Italy - Serie A Women")], C.PIN_MARGEN_INICIO)
    # Selecciones con el nombre en español
    s = {"local": "España", "visitante": "Inglaterra", "_ts": T0, "liga": "UEFA Nations League"}
    assert EM.mejor(s, [otro("Spain", "England", 0, "UEFA - Nations League A")], C.PIN_MARGEN_INICIO)
    print("Emparejamiento (hora, competencia, categoría y nombres; caso Al Dhaid Sub-21): OK")


def caso_serie_mlb():
    """Dos juegos de la misma serie: se toma el de la hora de la alerta, no el del otro día."""
    ini = iso_a_ts("2026-09-25T02:10:00Z")
    info = {"local": "Seattle Mariners", "visitante": "Los Angeles Angels", "_ts": ini, "liga": "MLB", "deporte": "mlb"}
    juegos = [
        {"local": "Seattle Mariners", "visitante": "Los Angeles Angels", "ts": ini - 86400 + 1800, "full": (4, 6)},
        {"local": "Seattle Mariners", "visitante": "Los Angeles Angels", "ts": ini, "full": (5, 7)},
        {"local": "Seattle Mariners", "visitante": "Los Angeles Angels", "ts": ini + 86400, "full": (8, 4)},
    ]
    m, inv = R.buscar(info, juegos)
    assert m is juegos[1] and not inv
    assert R.buscar(info, [juegos[0], juegos[2]]) == (None, False), "tomó el juego de otro día"
    # Fuente que pone local y visitante al revés: se encuentra y se da vuelta el marcador
    m, inv = R.buscar(info, [{"local": "LA Angels", "visitante": "Seattle Mariners", "ts": ini, "full": (7, 5)}])
    assert m and inv
    print("Marcador por equipos + fecha y hora (serie de MLB): OK")


def caso_tipos_de_apuesta():
    info = {"local": "Seattle Mariners", "visitante": "Los Angeles Angels"}
    m = {"full": (4, 6), "f5": (4, 3), "h1": (1, 1)}

    def f(crit, tipo, ot, line=None, nsal=2, part=None):
        return {"crit": crit, "tipo": tipo, "otype": ot, "line": line, "part": part, "nsal": nsal, "ev": 1}

    casos = [
        (f("Moneyline", "Match", "OT_TWO"), 1), (f("Moneyline", "Match", "OT_ONE"), -1),
        (f("Run Line", "Handicap", "OT_TWO", -1.5), 1), (f("Run Line", "Handicap", "OT_ONE", 1.5), -1),
        (f("Run Line", "Handicap", "OT_ONE", 2.0), 0),
        (f("Total Runs", "Over/Under", "OT_OVER", 9.5), 1), (f("Total Runs", "Over/Under", "OT_UNDER", 9.5), -1),
        (f("Total Runs", "Over/Under", "OT_OVER", 10.0), 0),
        (f("Total Runs - First 5 Innings", "Over/Under", "OT_UNDER", 7.5), 1),
        (f("Handicap - First 5 Innings", "Handicap", "OT_ONE", -0.5), 1),
        (f("Total Runs by Los Angeles Angels", "Over/Under", "OT_OVER", 5.5), 1),
        (f("Total Runs by Seattle Mariners", "Over/Under", "OT_OVER", 4.5), -1),
        (f("Full Time", "Match", "OT_CROSS", nsal=3), -1), (f("Full Time", "Match", "OT_TWO", nsal=3), 1),
        (f("Half Time", "Match", "OT_CROSS", nsal=3), 1),
        (f("Total Goals - 1st Half", "Over/Under", "OT_UNDER", 2.5), 1),
        (f("Draw No Bet", "Match", "OT_ONE"), -1), (f("Double Chance", "Double Chance", "OT_CROSS_OR_TWO", nsal=3), 1),
        (f("Total Goals", "Over/Under", "OT_OVER", 9.75), 0.5), (f("Total Goals", "Over/Under", "OT_OVER", 10.25), -0.5),
    ]
    for fila, esperado in casos:
        factor, detalle = R.valor_apuesta(fila, info, m)
        assert factor == esperado, (fila["crit"], fila["otype"], fila["line"], factor, detalle)
    # Lo que no se sabe liquidar se dice, no se inventa
    assert R.valor_apuesta(f("3-Way Handicap", "3-Way Handicap", "OT_ONE", 1.0, 3), info, m)[0] is None
    assert R.valor_apuesta(f("Total Runs - 3rd Inning", "Over/Under", "OT_OVER", 0.5), info, m)[0] is None
    print("Tipos de apuesta (ganada, perdida, nula, media): OK")


def caso_reintentos_y_48_horas():
    liq = R.Liquidador()
    fila = {"k": 7, "crit": "Full Time", "tipo": "Match", "otype": "OT_ONE", "line": None, "part": "Equipo A", "ev": 55}
    fuentes = {}
    R.marcador_fuente = lambda fuente, info, apifoot=None: fuentes.get(fuente)

    def alerta(horas):
        ini = R.time.time() - horas * 3600
        return {"id": f"a-{horas}", "k": 7, "ev": 55, "deporte": "futbol", "partido": "Equipo A - Equipo B",
                "liga": "Pais / Liga", "cuota": 1.80, "cuota_rb": 1.75,
                "inicio": R.datetime.fromtimestamp(ini, R.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def probar(horas):
        liq._por_ev = {}
        return liq.liquidar_una(alerta(horas), fila)

    # Sin ninguna fuente: se espera; a las 48 h queda "sin resultado"
    assert probar(3) is None and probar(47) is None
    assert probar(49)["estado"] == "sin resultado"
    # Solo Kambi: nunca basta
    liq.kambi.datos["55"] = {"fin": True, "full": [2, 1], "segundo": True, "h1": [1, 0]}
    assert probar(3) is None and probar(49)["estado"] == "sin resultado"
    # Kambi + una fuente oficial que coinciden: confirmado enseguida
    fuentes["API-Football"] = {"fuente": "API-Football", "estado": "final", "full": (2, 1), "h1": (1, 0)}
    r = probar(3)
    assert r["estado"] == "ganada" and r["unidades"] == 0.75 and r["verificacion"] == "confirmado" and r["marcador"] == "2-1"
    # No coinciden: no se liquida; a las 48 h queda sin resultado
    fuentes["API-Football"]["full"] = (1, 1)
    assert probar(3) is None and "no coinciden" in probar(49)["detalle"]
    # Una sola fuente oficial: se espera 6 horas a que aparezca la segunda
    liq.kambi.datos.clear()
    assert probar(3) is None
    r = probar(7)
    assert r["estado"] == "perdida" and r["unidades"] == -1 and r["verificacion"] == "una fuente"
    # Dos oficiales que coinciden valen aunque Kambi diga otra cosa
    liq.kambi.datos["55"] = {"fin": True, "full": [2, 1], "segundo": True}
    fuentes["ESPN"] = {"fuente": "ESPN", "estado": "final", "full": (1, 1)}
    assert probar(3)["estado"] == "perdida"
    # Partido aplazado: nula a las 48 h
    liq.kambi.datos.clear()
    fuentes.clear()
    fuentes["ESPN"] = {"fuente": "ESPN", "estado": "aplazado", "full": None}
    assert probar(3) is None and probar(49)["estado"] == "nula"

    # El ciclo: reintenta cada 15 minutos y no antes de que el partido haya podido terminar
    fuentes.clear()
    res = {}
    alertas = [alerta(1), alerta(3)]
    assert [a["id"] for a in liq.pendientes(alertas, res)] == ["a-3"]
    assert liq.ciclo(alertas, res, lambda ks: {7: fila}) == [] and liq.estado["intentos"]["a-3"]["n"] == 1
    assert liq.ciclo(alertas, res, lambda ks: {7: fila}) == [] and liq.estado["intentos"]["a-3"]["n"] == 1
    liq.estado["intentos"]["a-3"]["ultimo"] -= C.LIQ_REINTENTO + 1
    fuentes["API-Football"] = {"fuente": "API-Football", "estado": "final", "full": (3, 0)}
    fuentes["ESPN"] = {"fuente": "ESPN", "estado": "final", "full": (3, 0)}
    assert liq.ciclo(alertas, res, lambda ks: {7: fila}) == ["a-3"] and res["a-3"]["intentos"] == 2
    liq.guardar()
    print("Reintentos, dos fuentes, y \"sin resultado\" a las 48 horas: OK")


def caso_kambi_en_vivo():
    """El marcador de 90 minutos se congela si hay prórroga; el partido termina cuando sale de la lista."""
    kv = R.KambiVivo({})
    en_vivo = {}
    import f5_fuentes
    f5_fuentes.kambi = lambda op, ruta: {"liveEvents": [{"event": {"id": 9}, "liveData": en_vivo[9]}] if 9 in en_vivo else []}

    def paso(per, minuto, h, a):
        en_vivo[9] = {"score": {"home": str(h), "away": str(a)}, "matchClock": {"periodId": per, "minute": minuto}}
        kv.ultima = 0
        kv.leer({9})

    paso("FIRST_HALF", 44, 1, 0)
    paso("SECOND_HALF", 60, 1, 1)
    assert kv.marcador(9, "futbol") is None, "no puede dar marcador de un partido que sigue en juego"
    paso("SECOND_HALF", 90, 1, 1)
    paso("OVERTIME_FIRST_HALF", 100, 2, 1)
    en_vivo.clear()
    kv.ultima = 0
    kv.leer({9})
    m = kv.marcador(9, "futbol")
    assert m["full"] == (1, 1) and m["h1"] == (1, 0), m
    print("Marcador en vivo de Kambi (90 minutos, sin prórroga): OK")


def caso_tope_de_ventaja():
    assert ES.nivel_ventaja(0.0999) == "normal" and ES.nivel_ventaja(None) == "normal"
    assert ES.nivel_ventaja(0.10) == "alta" and ES.nivel_ventaja(0.15) == "alta"
    assert ES.nivel_ventaja(0.1501) == "excesiva"
    assert ES.ventaja_pinnacle({"regla": "mercado", "ventaja": 0.9998, "cuota": 2.9}) == 0.9998
    assert ES.ventaja_pinnacle({"regla": "escalera", "cuota": 1.80, "pin_justa": 1.50}) == 0.2
    assert ES.ventaja_pinnacle({"regla": "escalera", "cuota": 1.80, "pin_justa": None}) is None
    C.RANGO_PREFERIDO[:] = list(C.RANGO_DEFECTO)
    assert C.RANGO_DEFECTO == (1.40, 2.00)
    assert ES.en_rango(1.40) and ES.en_rango(2.00) and not ES.en_rango(1.39) and not ES.en_rango(2.01)
    assert not ES.en_rango(None)
    print("Rango 1.40-2.00 (ambos incluidos) y tope de ventaja: OK")


if __name__ == "__main__":
    caso_al_dhaid()
    caso_serie_mlb()
    caso_tipos_de_apuesta()
    caso_reintentos_y_48_horas()
    caso_kambi_en_vivo()
    caso_tope_de_ventaja()
    print("Todas las pruebas de emparejamiento y liquidación pasaron.")
