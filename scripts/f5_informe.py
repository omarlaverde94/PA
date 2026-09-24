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
RECORDATORIO = ("Verifica la cuota real en tu casa de apuestas: puede ser distinta (suele pagar un poco menos que Paf) "
                "y puede cambiar entre este aviso y el momento de mirar.")


def hora_col(iso):
    t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return t.astimezone(C.HORA_LOCAL)


def texto_alerta(info, alertas, total):
    dep = C.DEPORTES[info["deporte"]]
    ini = hora_col(info["inicio"])
    lineas = ["PRUEBA — no apostar", "",
              f"{dep['emoji']} Partido: {info['nombre']}",
              f"Liga: {info['liga']} · empieza {ini.strftime('%d/%m %H:%M')} (hora local)"]
    for i, a in enumerate(alertas, 1):
        justa = f"{a['justa']:.2f}" if a.get("justa") else "—"
        if a.get("cota"):
            justa = f"menos de {justa}"
        ventaja = f" (paga {a['ventaja']:.0%} más)" if a.get("ventaja") is not None and not a.get("cota") else ""
        rango = "sí" if a["en_rango"] else "no"
        lineas += [
            "",
            f"{i}) Apuesta: {a['apuesta']}" + (" [jugador]" if a.get("jugador") else ""),
            f"• Cuota en Kambi (Paf): {a['cuota']:.2f}" + (f" · Unibet: {a['cuota_unibet']:.2f}" if a.get("cuota_unibet") else ""),
            f"• Precio justo estimado: {justa}{ventaja}",
            f"• Tipo de error: {NOMBRE_REGLA.get(a['regla'], a['regla'])}",
            f"• Respaldo: {a['respaldo']}",
            f"• Confianza: {a['confianza']} · en rango 1.50-2.00: {rango}",
            f"• Detectado: {hora_col(a['detectado']).strftime('%d/%m %H:%M:%S')} (hora local)",
            f"• Explicación: {a['explicacion']}",
        ]
    if total > len(alertas):
        lineas += ["", f"(Hay {total - len(alertas)} apuestas más con el mismo tipo de señal en este partido; quedan en el registro.)"]
    lineas += ["", RECORDATORIO]
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
    """Resumen de las alertas detectadas en un día (hora local)."""
    todas = leer_alertas()
    # Las que siguen abiertas (el archivo puede no estar volcado todavía)
    for a in reg.alertas_abiertas.values():
        if not any(x["id"] == a["id"] for x in todas):
            todas.append(a)
    del_dia = [a for a in todas if hora_col(a["detectado"]).date() == dia]
    res = leer_resultados()
    lineas = ["PRUEBA — no apostar", "", f"Resumen del {dia.strftime('%d/%m/%Y')} (hora local)"]
    if not del_dia:
        lineas += ["", "Hoy no se detectó ningún posible error."]
    else:
        por_dep = Counter(C.DEPORTES[a["deporte"]]["nombre"] for a in del_dia)
        por_tipo = Counter("interno" if a["tipo"] == "interno" else "mercado" for a in del_dia)
        por_regla = Counter(a["regla"] for a in del_dia)
        cerradas = [a for a in del_dia if a.get("cerrada")]
        durs = [a["duracion_seg"] for a in cerradas if a.get("motivo") != "empezó el partido"]
        alcanz = sum(1 for a in cerradas if a.get("alcanzable"))
        jug = sum(1 for a in del_dia if a.get("jugador"))
        rango = sum(1 for a in del_dia if a.get("en_rango"))
        avisadas = sum(1 for a in del_dia if a.get("avisado"))
        lineas += [
            "",
            f"• Posibles errores: {len(del_dia)} (avisados por aquí: {avisadas}).",
            "• Por deporte: " + ", ".join(f"{d} {n}" for d, n in por_dep.most_common()) + ".",
            f"• Por tipo: contra el mercado {por_tipo.get('mercado', 0)}, contradicción interna {por_tipo.get('interno', 0)} "
            f"(" + ", ".join(f"{r} {n}" for r, n in por_regla.most_common()) + ").",
            f"• Apuestas de jugadores: {jug}; en cuota 1.50-2.00: {rango}.",
            f"• Ya corregidos: {len(durs)}; duración mediana antes de corregirse: {_dur(_med(durs))}.",
            f"• Duraron 2 min o más (se habrían alcanzado a mirar): {alcanz} de {len(cerradas)} cerrados.",
            f"• Siguen abiertos: {sum(1 for a in del_dia if not a.get('cerrada'))}.",
        ]
        con_res = [a for a in del_dia if res.get(a["id"], {}).get("gana") is not None]
        if con_res:
            ganancia = sum(res[a["id"]]["unidades"] for a in con_res)
            ganadas = sum(1 for a in con_res if res[a["id"]]["gana"])
            lineas.append(f"• Con resultado conocido: {len(con_res)}; se habrían ganado {ganadas}; "
                          f"ganancia apostando 1 unidad en cada una: {ganancia:+.1f} unidades.")
        else:
            lineas.append("• Resultados: todavía no hay partidos terminados con resultado para estas alertas.")
        lineas += ["", "Con tan pocos datos de un solo día, nada de esto demuestra ganancia ni pérdida.",
                   "", "Cada posible error del día, del más fuerte al más débil (con o sin aviso):", ""]
        lineas += ["\n\n".join(lista_errores(del_dia, res))]
    lineas += ["", RECORDATORIO]
    return "\n".join(lineas)


# ---------------------------------------------------------------------------- lista de errores
MAX_LISTA = 15


def _pin(a):
    """Precio justo de Pinnacle de una alerta (las viejas solo lo tienen en las de mercado)."""
    if a.get("pin_justa"):
        return a["pin_justa"]
    return a.get("justa") if a.get("regla") == "mercado" else None


def fuerza(a):
    """Diferencia de la cuota de Kambi contra Pinnacle (o contra el precio estimado)."""
    ref = _pin(a) or a.get("justa")
    return a["cuota"] / ref - 1 if ref else (a.get("ventaja") or 0)


def _estado(a):
    if a.get("cerrada"):
        if a.get("motivo") == "empezó el partido":
            return "no se corrigió antes del inicio del partido"
        return f"corregido a los {_dur(a.get('duracion_seg'))} ({a.get('motivo', 'cuota corregida')})"
    if a.get("ausente_desde"):
        return "parece corregido (confirmando)"
    return "sigue abierto"


def _resultado(a, res):
    r = res.get(a["id"])
    if r and r.get("gana") is not None:
        return f"se habría {'GANADO' if r['gana'] else 'perdido'} ({r['unidades']:+.2f} u)"
    if r:
        return "sin resultado (" + r.get("detalle", "no disponible") + ")"
    try:
        inicio = datetime.strptime(a["inicio"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (KeyError, ValueError):
        return "resultado pendiente"
    if time.time() < inicio:
        return "el partido no ha empezado"
    if time.time() < inicio + 5 * 3600:
        return "partido en juego o recién terminado"
    return "partido terminado, resultado pendiente"


def texto_error(i, a, res):
    dep = C.DEPORTES.get(a.get("deporte"), {})
    pin = _pin(a)
    dif = fuerza(a)
    if pin:
        precio = f"Pinnacle justo {pin:.2f} · diferencia {dif:+.1%}"
    else:
        est = a.get("justa")
        cota = "menos de " if a.get("cota") else ""
        precio = (f"Pinnacle: no tiene esta apuesta · justo estimado {cota}{est:.2f} (contradicción interna) · "
                  f"diferencia {dif:+.1%}") if est else "Pinnacle: no tiene esta apuesta"
    return "\n".join([
        f"{i}) {dep.get('emoji', '')} {a.get('partido', '')} · empieza {hora_col(a['inicio']).strftime('%d/%m %H:%M')}",
        f"   Apuesta: {a.get('apuesta', '')}" + (" [jugador]" if a.get("jugador") else ""),
        f"   Kambi {a['cuota']:.2f} · {precio}",
        f"   {_estado(a)} · {_resultado(a, res)}" + (" · avisado" if a.get("avisado") else " · sin aviso"),
    ])


def lista_errores(alertas, res):
    """Los errores ordenados del más fuerte al más débil, como máximo 15."""
    orden = sorted(alertas, key=fuerza, reverse=True)
    lineas = [texto_error(i, a, res) for i, a in enumerate(orden[:MAX_LISTA], 1)]
    if len(orden) > MAX_LISTA:
        lineas.append(f"… y {len(orden) - MAX_LISTA} más (los más débiles), que quedan en el registro.")
    return lineas


def detalle_abiertos(reg):
    """Para el comando "detalle": los posibles errores que siguen abiertos ahora."""
    abiertos = list(reg.alertas_abiertas.values())
    partes = ["PRUEBA — no apostar", f"Posibles errores abiertos ahora: {len(abiertos)}"]
    if abiertos:
        partes += ["Del más fuerte al más débil:"] + lista_errores(abiertos, leer_resultados())
    partes.append(RECORDATORIO)
    return "\n\n".join(partes)


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
           f"Alertas registradas: {len(todas)}. Apuesta simulada: 1 unidad a la cuota de Kambi (Paf) del momento "
           "de la detección. Tu casa de apuestas puede pagar un poco menos, así que la ganancia real sería algo menor.", ""]

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
    bloque("Por rango de cuota", lambda a: "1.50-2.00" if a.get("en_rango") else ("menos de 1.50" if a["cuota"] < 1.5 else "más de 2.00"))
    bloque("Solo las alcanzables (duraron 2 min o más)", lambda a: "alcanzable" if a.get("alcanzable") else "no alcanzable / abierta")
    bloque("Avisadas por Telegram", lambda a: "avisada" if a.get("avisado") else "solo registrada")
    ruta = C.DATA / "tablas.md"
    ruta.write_text("\n".join(out))
    return ruta


if __name__ == "__main__":
    import sys
    if "final" in sys.argv:
        print(tablas_finales())
