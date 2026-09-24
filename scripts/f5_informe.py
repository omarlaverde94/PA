"""
Fase 5: textos para Telegram (alertas y resumen diario) y lectura del
registro de alertas para los resúmenes y el informe final.
"""
import gzip
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import f5_config as C

NOMBRE_REGLA = {
    "mercado": "Contra el mercado (Kambi paga más que el precio justo de Pinnacle)",
    "escalera": "Contradicción interna de Kambi: lo más fácil paga más que lo más difícil",
    "par": "Contradicción interna de Kambi: paga más que su propio más/menos",
    "modelo": "Contradicción interna de Kambi (estimada con un modelo sencillo)",
    "arbitraje": "Contradicción interna de Kambi: las opciones del mercado suman menos de 100%",
}


def recordatorio(casa="tu casa de apuestas"):
    return (f"Verifica la cuota real en {casa}: la cuota estimada puede fallar y la real puede cambiar "
            "entre este aviso y el momento de mirar.")


RECORDATORIO = recordatorio()
ORIGEN_JUSTO = {"mercado": "Pinnacle sin margen", "par": "el más/menos del propio Kambi, sin margen",
                "modelo": "modelo sencillo con la línea principal de Kambi",
                "escalera": "cuota estimada de la apuesta más difícil", "arbitraje": "lo que dejan las otras opciones"}


def hora_col(iso):
    t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return t.astimezone(C.HORA_LOCAL)


def texto_alerta(info, alertas, total):
    dep = C.DEPORTES[info["deporte"]]
    ini = hora_col(info["inicio"])
    casa = alertas[0].get("casa") or "tu casa de apuestas"
    lineas = ["PRUEBA — no apostar", "",
              f"{dep['emoji']} Partido: {info['nombre']}",
              f"Liga: {info['liga']} · empieza {ini.strftime('%d/%m %H:%M')} (hora local)"]
    for i, a in enumerate(alertas, 1):
        cota = "menos de " if a.get("cota") or a["regla"] == "escalera" else ""
        justa = f"{cota}{a['justa_rb']:.2f}" if a.get("justa_rb") else "—"
        rango80 = a.get("rango_rb") or [None, None]
        lineas += [
            "",
            f"{i}) Apuesta: {a['apuesta']}" + (" [jugador]" if a.get("jugador") else ""),
            f"• Cuota estimada en {casa}: {a['cuota_rb']:.2f}"
            + (f" (normalmente entre {rango80[0]:.2f} y {rango80[1]:.2f})" if rango80[0] else ""),
            f"• Cuota en Kambi (Paf): {a['cuota']:.2f}" + (f" · Unibet: {a['cuota_unibet']:.2f}" if a.get("cuota_unibet") else ""),
            f"• Precio justo: {justa} ({ORIGEN_JUSTO.get(a['regla'], 'estimado')})"
            + (f" · Pinnacle: {a['pin_justa']:.2f}" if a.get("pin_justa") and a["regla"] != "mercado" else ""),
            f"• Ventaja con la cuota estimada en {casa}: {a['ventaja_rb']:+.1%}" if a.get("ventaja_rb") is not None else
            f"• Ventaja con la cuota estimada en {casa}: sin dato",
            f"• Tipo de error: {NOMBRE_REGLA.get(a['regla'], a['regla'])}",
            f"• Respaldo: {a['respaldo']}",
            f"• Confianza: {a['confianza']}",
            f"• Detectado: {hora_col(a.get('avisable_desde') or a['detectado']).strftime('%d/%m %H:%M:%S')} (hora local)",
            f"• Explicación: {a['explicacion']} Con la cuota estimada en {casa} ({a['cuota_rb']:.2f}) "
            f"la apuesta sigue por encima del precio justo.",
        ]
    if total > len(alertas):
        lineas += ["", f"(Hay {total - len(alertas)} apuestas más con el mismo tipo de señal en este partido; quedan en el registro.)"]
    lineas += ["", recordatorio(casa)]
    return "\n".join(lineas)


# ---------------------------------------------------------------------------- registro
def leer_alertas():
    """Une los registros de abrir y cerrar de cada alerta."""
    alertas = {}
    for ruta in sorted((C.DATA / "alertas").glob("alertas_*.jsonl.gz")):
        try:
            with gzip.open(ruta, "rt", encoding="utf-8") as fh:
                for linea in fh:
                    try:
                        r = json.loads(linea)
                    except ValueError:
                        continue
                    if r.get("evento") == "abre":
                        alertas[r["id"]] = r
                    elif r.get("evento") == "cierra" and r["id"] in alertas:
                        alertas[r["id"]].update({k: v for k, v in r.items() if k != "evento"})
                        alertas[r["id"]]["cerrada"] = True
        except (OSError, EOFError):
            continue
    return list(alertas.values())


def leer_resultados():
    ruta = C.DATA / "resultados.json.gz"
    if not ruta.exists():
        return {}
    with gzip.open(ruta, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _med(xs):
    return statistics.median(xs) if xs else None


def _dur(seg):
    if seg is None:
        return "—"
    if seg < 90:
        return f"{seg:.0f} s"
    if seg < 5400:
        return f"{seg / 60:.0f} min"
    return f"{seg / 3600:.1f} h"


def resumen_diario(reg, dia):
    """Resumen corto del día (hora local): 3 o 4 líneas de totales y la lista en formato corto."""
    todas = leer_alertas()
    # Las que siguen abiertas (el archivo puede no estar volcado todavía)
    for a in reg.alertas_abiertas.values():
        if not any(x["id"] == a["id"] for x in todas):
            todas.append(a)
    del_dia = [a for a in todas if hora_col(a["detectado"]).date() == dia]
    res = leer_resultados()
    casa = next((a["casa"] for a in del_dia if a.get("casa")), "tu casa de apuestas")
    lineas = [f"📋 Resumen {dia.strftime('%d/%m')} · PRUEBA, no apostar"]
    if not del_dia:
        lineas += ["Hoy no hubo posibles errores.", ""]
    else:
        cerradas = [a for a in del_dia if a.get("cerrada")]
        durs = [a["duracion_seg"] for a in cerradas if a.get("motivo") != "empezó el partido"]
        avisables = sum(1 for a in del_dia if a.get("avisable"))
        avisadas = sum(1 for a in del_dia if a.get("avisado"))
        lineas.append(f"Posibles errores: {len(del_dia)} · merecían alerta ({C.RANGO_PREFERIDO[0]:.2f}–{C.RANGO_PREFERIDO[1]:.2f}): {avisables} · "
                      f"avisados: {avisadas}")
        lineas.append(f"Corregidos: {len(durs)} (mediana {_dur(_med(durs))}) · abiertos: "
                      f"{sum(1 for a in del_dia if not a.get('cerrada'))}")
        con_res = [a for a in del_dia if res.get(a["id"], {}).get("gana") is not None]
        if con_res:
            av = [a for a in con_res if a.get("avisable")]
            lineas.append(f"Con resultado: {len(con_res)} · ganadas {sum(1 for a in con_res if res[a['id']]['gana'])} · "
                          f"{sum(res[a['id']]['unidades'] for a in con_res):+.1f} u"
                          + (f" (las de alerta: {len(av)} · {sum(res[a['id']]['unidades'] for a in av):+.1f} u)" if av else ""))
        else:
            lineas.append("Con resultado: todavía ninguno")
        lineas += [""] + lista_errores(del_dia, res)
    lineas.append(f"Verifica siempre la cuota real en {casa}.")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------- lista de errores
MAX_LISTA = 15


def _pin(a):
    """Precio justo de Pinnacle de una alerta (las viejas solo lo tienen en las de mercado)."""
    if a.get("pin_justa"):
        return a["pin_justa"]
    return a.get("justa") if a.get("regla") == "mercado" else None


def fuerza(a):
    """Diferencia de la cuota estimada en tu casa (o la de Kambi si no hay estimación)
    contra Pinnacle (o contra el precio justo estimado)."""
    ref = _pin(a) or a.get("justa")
    cuota = a.get("cuota_rb") or a["cuota"]
    return cuota / ref - 1 if ref else (a.get("ventaja") or 0)


def orden(a):
    """Primero las que merecían alerta (estimada en el rango y sobre el justo), luego por fuerza."""
    return (bool(a.get("avisable")), fuerza(a))


def _estado(a):
    if a.get("cerrada"):
        if a.get("motivo") == "empezó el partido":
            return "no se corrigió antes del inicio"
        return f"corregido a los {_dur(a.get('duracion_seg'))}"
    if a.get("ausente_desde"):
        return "parece corregido"
    return "sigue abierto"


def _resultado(a, res):
    r = res.get(a["id"])
    if r and r.get("gana") is not None:
        return f"{'GANADA' if r['gana'] else 'perdida'} ({r['unidades']:+.2f} u)"
    if r:
        return "sin resultado (" + r.get("detalle", "no disponible") + ")"
    try:
        inicio = datetime.strptime(a["inicio"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (KeyError, ValueError):
        return "resultado pendiente"
    if time.time() < inicio:
        return "no ha empezado"
    if time.time() < inicio + 5 * 3600:
        return "en juego"
    return "resultado pendiente"


def _hora_corta(a):
    try:
        return hora_col(a["inicio"]).strftime("%d/%m %H:%M")
    except (KeyError, ValueError):
        return "—"


def _justa(a):
    """Precio justo que se muestra: el de la regla medido para tu casa; si no hay, Pinnacle o el estimado."""
    return a.get("justa_rb") or _pin(a) or a.get("justa")


def texto_corto(a, marca="🟢"):
    """El formato corto de una alerta (4 líneas)."""
    casa = a.get("casa") or "Tu casa"
    paga = a.get("cuota_rb") or a["cuota"]
    justa = _justa(a)
    ventaja = a.get("ventaja_rb") if a.get("ventaja_rb") is not None else (paga / justa - 1 if justa else None)
    return "\n".join([
        f"{marca} {a.get('apuesta', '')}",
        f"{a.get('partido', '')} · {_hora_corta(a)}",
        f"{casa} paga {paga:.2f} → debería pagar " + (f"{justa:.2f}" if justa else "—"),
        (f"Ventaja {ventaja:+.1%}" if ventaja is not None else "Ventaja —") + f" · Confianza {a.get('confianza', '—')}",
    ])


def bloque_tarjeta(a):
    """Los mismos datos del texto corto, para la imagen."""
    paga = a.get("cuota_rb") or a["cuota"]
    justa = _justa(a) or paga
    return {"apuesta": a.get("apuesta", ""), "partido": a.get("partido", ""), "hora": _hora_corta(a),
            "casa": a.get("casa") or "Tu casa", "paga": paga, "justa": justa,
            "ventaja": a.get("ventaja_rb") if a.get("ventaja_rb") is not None else paga / justa - 1,
            "confianza": a.get("confianza", "—")}


def texto_error(i, a, res):
    """Formato corto más una línea con cómo terminó."""
    return texto_corto(a, "🟢" if a.get("avisable") else "⚪") + f"\n{_estado(a)} · {_resultado(a, res)}"


def lista_errores(alertas, res):
    """Los errores ordenados del más fuerte al más débil, como máximo 15."""
    ordenadas = sorted(alertas, key=orden, reverse=True)
    lineas = [texto_error(i, a, res) + "\n" for i, a in enumerate(ordenadas[:MAX_LISTA], 1)]
    if len(ordenadas) > MAX_LISTA:
        lineas.append(f"… y {len(ordenadas) - MAX_LISTA} más (los más débiles), en el registro.")
    return lineas


def detalle_abiertos(reg):
    """Para el comando "detalle": los posibles errores que siguen abiertos ahora, en formato corto."""
    abiertos = list(reg.alertas_abiertas.values())
    casa = next((a["casa"] for a in abiertos if a.get("casa")), "tu casa de apuestas")
    lineas = [f"🔎 Abiertos ahora: {len(abiertos)} · PRUEBA, no apostar", ""]
    if abiertos:
        lineas += lista_errores(abiertos, leer_resultados())
    else:
        lineas.append("Ningún posible error abierto en este momento.\n")
    lineas.append(f"Verifica siempre la cuota real en {casa}.")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------- informe final
def _fila_resumen(nombre, grupo, res):
    con = [a for a in grupo if res.get(a["id"], {}).get("gana") is not None]
    u = [res[a["id"]]["unidades"] for a in con]
    ganadas = sum(1 for a in con if res[a["id"]]["gana"])
    cerr = [a["duracion_seg"] for a in grupo if a.get("cerrada") and a.get("motivo") != "empezó el partido"]
    alc = sum(1 for a in grupo if a.get("alcanzable"))
    if u:
        media = sum(u) / len(u)
        de = statistics.pstdev(u) if len(u) > 1 else 0
        margen = 2 * de / len(u) ** 0.5
        # peor racha de pérdidas vista
        racha = peor = 0
        for a in sorted(con, key=lambda a: a["detectado"]):
            racha = racha + 1 if not res[a["id"]]["gana"] else 0
            peor = max(peor, racha)
        conf = "nula" if len(u) < 20 else "baja" if len(u) < 100 else "media" if len(u) < 400 else "alta"
        tail = (f"{len(con)} | {ganadas} | {sum(u):+.1f} | {media:+.0%} ± {margen:.0%} | {peor} | {conf}")
    else:
        tail = "0 | — | — | — | — | nula"
    return (f"| {nombre} | {len(grupo)} | {_dur(_med(cerr))} | {alc} | " + tail + " |")


def tablas_finales():
    """Tablas del informe final (data/fase5/tablas.md)."""
    todas = sorted(leer_alertas(), key=lambda a: a["detectado"])
    res = leer_resultados()
    enc = ("| Grupo | Errores | Duración mediana | Alcanzables (2+ min) | Con resultado | Ganadas | "
           "Ganancia (u) | Rendimiento ± margen | Peor racha | Confianza |\n|---|---|---|---|---|---|---|---|---|---|")
    out = ["# Fase 5: tablas de la prueba en papel", "",
           f"Alertas registradas: {len(todas)}. Apuesta simulada: 1 unidad a la cuota ESTIMADA en tu casa de apuestas "
           "en el momento de la detección (o a la de Kambi si no había estimación).", ""]

    def bloque(titulo, clave):
        grupos = {}
        for a in todas:
            grupos.setdefault(clave(a), []).append(a)
        out.extend([f"## {titulo}", "", enc])
        for g in sorted(grupos):
            out.append(_fila_resumen(g, grupos[g], res))
        out.append(_fila_resumen("**Total**", todas, res))
        out.append("")

    bloque("Por día (hora local)", lambda a: hora_col(a["detectado"]).date().isoformat())
    bloque("Por deporte", lambda a: C.DEPORTES[a["deporte"]]["nombre"])
    bloque("Por tipo de error", lambda a: NOMBRE_REGLA.get(a["regla"], a["regla"]).split(":")[0] + f" ({a['regla']})")
    bloque("Jugadores contra el resto", lambda a: "Apuestas de jugadores" if a.get("jugador") else "Resto de apuestas")
    bloque("Por rango de cuota estimada en tu casa", lambda a: "sin estimación" if not a.get("cuota_rb") else
           "dentro del rango de alertas" if a.get("en_rango") else
           ("debajo del rango" if a["cuota_rb"] < C.RANGO_PREFERIDO[0] else "encima del rango"))
    bloque("Merecían alerta (estimada en el rango y sobre el justo)", lambda a: "merecía alerta" if a.get("avisable") else "solo registro")
    bloque("Solo las alcanzables (duraron 2 min o más)", lambda a: "alcanzable" if a.get("alcanzable") else "no alcanzable / abierta")
    bloque("Avisadas por Telegram", lambda a: "avisada" if a.get("avisado") else "solo registrada")
    ruta = C.DATA / "tablas.md"
    ruta.write_text("\n".join(out))
    return ruta


if __name__ == "__main__":
    import sys
    if "final" in sys.argv:
        print(tablas_finales())
