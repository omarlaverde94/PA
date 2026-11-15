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
    "mercado": "Contra el mercado (la casa paga más que el precio justo de Pinnacle)",
    "escalera": "Contradicción interna de la casa: lo más fácil paga más que lo más difícil",
    "par": "Contradicción interna de la casa: paga más que su propio más/menos",
    "modelo": "Contradicción interna de la casa (estimada con un modelo sencillo)",
    "arbitraje": "Contradicción interna de la casa: las opciones del mercado suman menos de 100%",
}


def recordatorio(casa="tu casa de apuestas"):
    return (f"Verifica la cuota en {casa} antes de apostar: puede cambiar entre la hora de lectura "
            "y el momento de mirar.")


RECORDATORIO = recordatorio()
ORIGEN_JUSTO = {"mercado": "Pinnacle sin margen", "par": "el más/menos de la propia casa, sin margen",
                "modelo": "modelo sencillo con la línea principal de la casa",
                "escalera": "cuota de la apuesta más difícil", "arbitraje": "lo que dejan las otras opciones"}


def hora_seg(ts):
    """Hora local (Colombia) con segundos de una marca de tiempo."""
    return datetime.fromtimestamp(ts, C.HORA_LOCAL).strftime("%H:%M:%S")


ESTADO_SEG = {"igual": "igual", "subió": "subió", "bajó": "bajó", "suspendida": "suspendida",
              "quitada": "quitada"}


def texto_seguimiento(a):
    """'a los 2 min 1.85 (igual) · a los 5 min 1.80 (bajó) …' o '' si no hay relecturas."""
    seg = a.get("seguimiento") or {}
    partes = []
    for m in sorted(seg, key=int):
        r = seg[m]
        if r.get("cuota") is not None and r.get("estado") in ESTADO_SEG:
            partes.append(f"a los {m} min {r['cuota']:.2f} ({r['estado']})")
        else:
            partes.append(f"a los {m} min: {r.get('estado', '—')}")
    return " · ".join(partes)


def hora_col(iso):
    t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return t.astimezone(C.HORA_LOCAL)


def texto_alerta(info, alertas, total):
    """Explicación completa (la que se manda con "más"). Sirve también para alertas
    viejas, anteriores a la cuota estimada en tu casa."""
    dep = C.DEPORTES.get(info["deporte"], {"emoji": ""})
    ini = hora_col(info["inicio"])
    casa = alertas[0].get("casa") or "tu casa de apuestas"
    lineas = ["PRUEBA — no apostar", "",
              f"{dep['emoji']} Partido: {info['nombre']}",
              f"Liga: {info.get('liga') or '—'} · empieza {ini.strftime('%d/%m %H:%M')} (hora local)"]
    for i, a in enumerate(alertas, 1):
        cota = "menos de " if a.get("cota") or a["regla"] == "escalera" else ""
        justa_v = a.get("justa_rb") or a.get("justa")
        justa = f"{cota}{justa_v:.2f}" if justa_v else "—"
        rango80 = a.get("rango_rb") or [None, None]
        palabra = "real" if a.get("real") else "estimada"
        if a.get("real") and a.get("cuota_rb"):
            estimada = f"• Cuota real en {casa}: {a.get('cuota_envio') or a['cuota_rb']:.2f}"
            if a.get("relectura_ts"):
                estimada += f" (leída a las {hora_seg(a['relectura_ts'])} hora Colombia, justo antes de enviar)"
        elif a.get("cuota_rb"):
            estimada = (f"• Cuota estimada en {casa}: {a['cuota_rb']:.2f}"
                        + (f" (normalmente entre {rango80[0]:.2f} y {rango80[1]:.2f})" if rango80[0] else ""))
        else:
            estimada = f"• Cuota estimada en {casa}: sin estimación (alerta anterior a ese cálculo)"
        if a.get("ventaja_rb") is not None:
            ventaja = f"• Ventaja con la cuota {palabra} de {casa}: {a['ventaja_rb']:+.1%}"
        elif a.get("ventaja") is not None and not a.get("cota"):
            ventaja = f"• Ventaja con la cuota de Kambi: {a['ventaja']:+.1%}"
        else:
            ventaja = "• Ventaja: sin dato"
        explicacion = a.get("explicacion", "")
        if a.get("cuota_rb") and a.get("avisable") and not a.get("real"):
            explicacion += f" Con la cuota estimada en {casa} ({a['cuota_rb']:.2f}) la apuesta sigue por encima del precio justo."
        lineas += [
            "",
            f"{i}) Apuesta: {a['apuesta']}" + (" [jugador]" if a.get("jugador") else ""),
            estimada,
        ]
        if not a.get("real"):
            lineas.append(f"• Cuota en Kambi (Paf): {a['cuota']:.2f}"
                          + (f" · Unibet: {a['cuota_unibet']:.2f}" if a.get("cuota_unibet") else ""))
        lineas += [
            f"• Precio justo: {justa} ({ORIGEN_JUSTO.get(a['regla'], 'estimado')})"
            + (f" · Pinnacle: {a['pin_justa']:.2f}" if a.get("pin_justa") and a["regla"] != "mercado" else ""),
            ventaja,
            f"• Tipo de error: {NOMBRE_REGLA.get(a['regla'], a['regla'])}",
            f"• Respaldo: {a.get('respaldo', '—')}",
            f"• Confianza: {a.get('confianza', '—')}",
            f"• Detectado: {hora_col(a.get('avisable_desde') or a['detectado']).strftime('%d/%m %H:%M:%S')} (hora local)",
            f"• Explicación: {explicacion}",
        ]
        if a.get("ventaja_alta"):
            lineas.append(f"• {C.AVISO_VENTAJA_ALTA}")
        if a.get("pin_partido"):
            lineas.append(f"• Comparado con Pinnacle: {a['pin_partido']} ({a.get('pin_liga') or '—'})"
                          + (f" · Pinnacle leída a las {hora_col(a['pin_hora']).strftime('%H:%M:%S')}" if a.get("pin_hora") else ""))
        if a.get("recalculada"):
            lineas.append(f"• La cuota cambió de {a['cuota_detectada']:.2f} a {a.get('cuota_envio') or a['cuota']:.2f} "
                          "al releerla justo antes de enviar; todo se recalculó con la cuota nueva y seguía cumpliendo las reglas.")
        if a.get("no_enviada"):
            lineas.append(f"• No se envió: {a['no_enviada']}")
        if texto_seguimiento(a):
            lineas.append(f"• Después del aviso: {texto_seguimiento(a)}")
        if a.get("cerrada"):
            lineas.append(f"• Ahora: {_estado(a)}")
    if total > len(alertas):
        lineas += ["", f"(Hay {total - len(alertas)} apuestas más con el mismo tipo de señal en este partido; quedan en el registro.)"]
    lineas += ["", recordatorio(casa)]
    return "\n".join(lineas)


# ---------------------------------------------------------------------------- registro
def leer_alertas():
    """Une los registros de abrir y cerrar de cada alerta."""
    alertas, seguimientos = {}, {}
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
                    elif r.get("evento") == "seguimiento":
                        # Puede llegar antes que el cierre: se guarda aparte y se une al final
                        seguimientos.setdefault(r["id"], {})[str(r["min"])] = {
                            k: v for k, v in r.items() if k not in ("evento", "id")}
        except (OSError, EOFError):
            continue
    for i, seg in seguimientos.items():
        if i in alertas:
            alertas[i]["seguimiento"] = seg
    return list(alertas.values())


def conteo_seguimiento(alertas):
    """Por cada minuto de relectura: (medidas, se mantuvo, bajó, suspendida o quitada, no medibles)."""
    salida = {}
    for m in C.SEGUIMIENTO_MIN:
        regs = [a["seguimiento"][str(m)] for a in alertas if str(m) in (a.get("seguimiento") or {})]
        medidas = [r for r in regs if r.get("se_mantuvo") is not None]
        salida[m] = {"medidas": len(medidas), "mantuvo": sum(1 for r in medidas if r["se_mantuvo"]),
                     "bajo": sum(1 for r in medidas if r.get("estado") == "bajó"),
                     "cerrada": sum(1 for r in medidas if r.get("estado") in ("suspendida", "quitada")),
                     "sin_medir": len(regs) - len(medidas)}
    return salida


def linea_seguimiento(alertas):
    """Una línea corta: 'Cuota mantenida tras el aviso: 2 min 3 de 4 · 5 min 2 de 4 · 15 min 1 de 3'."""
    c = conteo_seguimiento(alertas)
    if not any(v["medidas"] or v["sin_medir"] for v in c.values()):
        return None
    return "Cuota mantenida tras el aviso: " + " · ".join(
        f"{m} min {v['mantuvo']} de {v['medidas']}" for m, v in c.items())


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
        avisadas = [a for a in del_dia if a.get("avisado")]
        cambiaron = sum(1 for a in del_dia if not a.get("avisado") and "cambió al releerla" in (a.get("no_enviada") or ""))
        recalc = sum(1 for a in avisadas if a.get("recalculada"))
        lineas.append(f"Posibles errores: {len(del_dia)} · merecían alerta ({C.RANGO_PREFERIDO[0]:.2f}–{C.RANGO_PREFERIDO[1]:.2f}): {avisables} · "
                      f"avisados: {len(avisadas)}" + (f" ({recalc} con cuota recalculada)" if recalc else "")
                      + (f" · no enviados porque la cuota cambió y ya no cumplía: {cambiaron}" if cambiaron else ""))
        ls = linea_seguimiento(avisadas)
        if ls:
            lineas.append(ls)
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
    if a.get("descartada"):
        return a["descartada"]
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
    if r and r.get("estado") == "nula":
        return "nula (se devuelve lo apostado)"
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
    lineas = [
        f"{marca} {a.get('apuesta', '')}",
        f"{a.get('partido', '')} · {_hora_corta(a)}",
        f"{casa} paga {paga:.2f} → debería pagar " + (f"{justa:.2f}" if justa else "—"),
        (f"Ventaja {ventaja:+.1%}" if ventaja is not None else "Ventaja —") + f" · Confianza {a.get('confianza', '—')}",
    ]
    if a.get("ventaja_alta"):
        lineas.append(C.AVISO_VENTAJA_ALTA)
    if a.get("relectura_ts") and not a.get("no_enviada"):
        lineas.append(f"🕒 Cuota leída a las {hora_seg(a['relectura_ts'])} (hora Colombia)"
                      + (f" · actualizada al releer (antes {a['cuota_detectada']:.2f})" if a.get("recalculada") else ""))
    return "\n".join(lineas)


def texto_mensaje(alertas, resto=0, res=None):
    """El texto del mensaje de alerta (hasta 5 apuestas del mismo partido). Con `res`, debajo
    de cada apuesta ya liquidada va su resultado: así se edita el mensaje original."""
    bloques = []
    for a in alertas:
        t = texto_corto(a)
        r = (res or {}).get(a["id"])
        if r:
            t += "\n" + linea_resultado(r)
        bloques.append(t)
    texto = "\n\n".join(bloques)
    if resto > 0:
        texto += f"\n\n(+{resto} más en este partido)"
    return texto


def linea_resultado(r):
    """"✅ Ganada", "❌ Perdida", "↩️ Nula" o "Sin resultado", con el marcador si lo hay."""
    estado = r.get("estado") or ("sin resultado" if r.get("gana") is None else ("ganada" if r["gana"] else "perdida"))
    marca = {"ganada": "✅ Ganada", "perdida": "❌ Perdida", "nula": "↩️ Nula"}.get(estado, "⏳ Sin resultado")
    if estado == "sin resultado":
        return f"{marca} ({r.get('detalle') or 'no disponible'})"
    return marca + (f" · {r['detalle']}" if r.get("detalle") else "")


def bloque_tarjeta(a):
    """Los mismos datos del texto corto, para la imagen."""
    paga = a.get("cuota_rb") or a["cuota"]
    justa = _justa(a) or paga
    return {"apuesta": a.get("apuesta", ""), "partido": a.get("partido", ""), "hora": _hora_corta(a),
            "leida": hora_seg(a["relectura_ts"]) if a.get("relectura_ts") else None,
            "casa": a.get("casa") or "Tu casa", "paga": paga, "justa": justa,
            "ventaja": a.get("ventaja_rb") if a.get("ventaja_rb") is not None else paga / justa - 1,
            "confianza": a.get("confianza", "—")}


def texto_error(i, a, res):
    """Formato corto más una línea con cómo terminó."""
    t = texto_corto(a, "🟢" if a.get("avisable") else "⚪") + f"\n{_estado(a)} · {_resultado(a, res)}"
    if a.get("avisado") and texto_seguimiento(a):
        t += f"\nDespués del aviso: {texto_seguimiento(a)}"
    elif a.get("no_enviada"):
        t += f"\nNo enviada: {a['no_enviada']}"
    return t


def lista_errores(alertas, res):
    """Los errores ordenados del más fuerte al más débil, como máximo 15."""
    ordenadas = sorted(alertas, key=orden, reverse=True)
    lineas = [texto_error(i, a, res) + "\n" for i, a in enumerate(ordenadas[:MAX_LISTA], 1)]
    if len(ordenadas) > MAX_LISTA:
        lineas.append(f"… y {len(ordenadas) - MAX_LISTA} más (los más débiles), en el registro.")
    return lineas


def _hora_aviso(a):
    return a.get("avisado_ts") or a.get("detectado_ts") or 0


def _grupo_de(mejor, candidatas):
    """Las alertas del mismo partido enviadas en el mismo mensaje (a menos de 2 min)."""
    ref, vistas, grupo = _hora_aviso(mejor), set(), []
    for a in sorted(candidatas, key=lambda x: -(x.get("ventaja_rb") or x.get("ventaja") or 0)):
        if a["partido"] == mejor["partido"] and abs(_hora_aviso(a) - ref) < 120 and a["id"] not in vistas:
            vistas.add(a["id"])
            grupo.append(a)
    return grupo


def buscar_por_texto(alertas, texto, fecha=None):
    """
    Encuentra las alertas de un mensaje del bot a partir de su texto (sirve para
    alertas enviadas antes de que se guardara el número de cada mensaje): el
    partido y la apuesta aparecen tal cual en el texto, en el formato viejo y en
    el corto. Si hay varias, la avisada más cercana a la fecha del mensaje.
    """
    if not texto:
        return []
    cand = [a for a in alertas if a.get("partido") and a.get("apuesta")
            and a["partido"] in texto and a["apuesta"] in texto]
    if not cand:
        return []
    mejor = min(cand, key=lambda a: (not a.get("avisado"), abs((fecha or _hora_aviso(a)) - _hora_aviso(a))))
    return _grupo_de(mejor, cand)


def ultimas_avisadas(alertas):
    """Las alertas del último aviso enviado."""
    avisadas = [a for a in alertas if a.get("avisado") and a.get("partido")]
    if not avisadas:
        return []
    return _grupo_de(max(avisadas, key=_hora_aviso), avisadas)


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


def tabla_seguimiento(todas):
    """Verificación automática de cada alerta: relectura antes de enviar y si la cuota se mantuvo después."""
    merecian = [a for a in todas if a.get("avisable") or a.get("avisado") or a.get("no_enviada")]
    avisadas = [a for a in todas if a.get("avisado")]
    no_env = Counter(a["no_enviada"].split(" (")[0] for a in merecian if a.get("no_enviada") and not a.get("avisado"))
    recalc = [a for a in avisadas if a.get("recalculada")]
    out = ["## Verificación automática de las alertas", "",
           f"- Merecían alerta: {len(merecian)} · enviadas: {len(avisadas)}, todas con la cuota releída justo antes "
           f"de enviar: {len(avisadas) - len(recalc)} con la misma cuota y {len(recalc)} con cuota recalculada "
           "(cambió al releer y seguía cumpliendo todas las reglas).", ""]
    if no_env:
        out += ["No enviadas al releer la cuota justo antes de enviar:", ""]
        out += [f"- {motivo}: {n}" for motivo, n in no_env.most_common()]
        out.append("")
    out += ["¿Se mantuvo la cuota después del aviso? (abierta y pagando lo mismo o más)", "",
            "| Relectura | Medidas | Se mantuvo | Bajó | Suspendida o quitada | Sin medir (empezó el partido o falló la lectura) |",
            "|---|---|---|---|---|---|"]
    for m, v in conteo_seguimiento(avisadas).items():
        pct = f" ({v['mantuvo'] / v['medidas']:.0%})" if v["medidas"] else ""
        out.append(f"| {m} min | {v['medidas']} | {v['mantuvo']}{pct} | {v['bajo']} | {v['cerrada']} | {v['sin_medir']} |")
    if recalc:
        out += ["", "Solo las enviadas con cuota recalculada:", "",
                "| Relectura | Medidas | Se mantuvo |", "|---|---|---|"]
        for m, v in conteo_seguimiento(recalc).items():
            out.append(f"| {m} min | {v['medidas']} | {v['mantuvo']} |")
    out += ["", f"Muestra: {len(avisadas)} alertas enviadas. Con menos de 20 la confianza es nula.", ""]
    return out


def tablas_finales(nombre="tablas.md"):
    """Tablas del informe (data/fase5/<nombre>): tablas.md el día 14, tablas_dia7.md el día 7."""
    todas = sorted(leer_alertas(), key=lambda a: a["detectado"])
    res = leer_resultados()
    enc = ("| Grupo | Errores | Duración mediana | Alcanzables (2+ min) | Con resultado | Ganadas | "
           "Ganancia (u) | Rendimiento ± margen | Peor racha | Confianza |\n|---|---|---|---|---|---|---|---|---|---|")
    out = ["# Fase 5: tablas de la prueba en papel", "",
           f"Alertas registradas: {len(todas)}. Apuesta simulada: 1 unidad a la cuota REAL de tu casa de apuestas "
           "(leída de su API; en las enviadas, la releída justo antes de enviar).", ""]
    out += tabla_seguimiento(todas)

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
    bloque("Por rango de cuota de tu casa", lambda a: "sin cuota" if not a.get("cuota_rb") else
           "dentro del rango de alertas" if a.get("en_rango") else
           ("debajo del rango" if a["cuota_rb"] < C.RANGO_PREFERIDO[0] else "encima del rango"))
    bloque("Merecían alerta (cuota en el rango y sobre el justo)", lambda a: "merecía alerta" if a.get("avisable") else "solo registro")
    bloque("Solo las alcanzables (duraron 2 min o más)", lambda a: "alcanzable" if a.get("alcanzable") else "no alcanzable / abierta")
    bloque("Avisadas por Telegram", lambda a: "avisada" if a.get("avisado") else "solo registrada")
    ruta = C.DATA / nombre
    ruta.write_text("\n".join(out))
    return ruta


# ---------------------------------------------------------------------------- informes por Telegram
def _numeros(grupo, res):
    """Ganadas, perdidas, nulas, sin resultado, unidades, rendimiento ± margen y peor racha."""
    con = [a for a in grupo if res.get(a["id"], {}).get("gana") is not None]
    u = [res[a["id"]]["unidades"] for a in con]
    racha = peor = 0
    for a in sorted(con, key=lambda a: a["detectado"]):
        racha = racha + 1 if not res[a["id"]]["gana"] else 0
        peor = max(peor, racha)
    media = sum(u) / len(u) if u else None
    margen = 2 * statistics.pstdev(u) / len(u) ** 0.5 if len(u) > 1 else None
    return {"n": len(grupo), "con": len(con), "ganadas": sum(1 for a in con if res[a["id"]]["gana"]),
            "perdidas": sum(1 for a in con if not res[a["id"]]["gana"]),
            "nulas": sum(1 for a in grupo if res.get(a["id"], {}).get("estado") == "nula"),
            "pendientes": sum(1 for a in grupo if a["id"] not in res),
            "unidades": sum(u), "media": media, "margen": margen, "peor": peor,
            "confianza": "nula" if len(u) < 20 else "baja" if len(u) < 100 else "media" if len(u) < 400 else "alta"}


def informe_telegram(titulo, alertas, res, dias):
    """Informe corto para Telegram (día 7 y día 14). Las tablas completas quedan en el repositorio privado."""
    casa = next((a["casa"] for a in alertas if a.get("casa")), "tu casa de apuestas")
    avisadas = [a for a in alertas if a.get("avisado")]
    merecian = [a for a in alertas if a.get("avisable") or a.get("avisado") or a.get("no_enviada")]
    no_env = Counter(("la cuota cambió y ya no cumplía" if "cambió al releerla" in a["no_enviada"]
                      else a["no_enviada"].split(" (")[0])
                     for a in merecian if a.get("no_enviada") and not a.get("avisado"))
    n = _numeros(avisadas, res)
    lineas = [f"📊 {titulo} · PRUEBA, no apostar", "",
              f"Días de prueba: {dias:.0f} · posibles errores registrados: {len(alertas)} · merecían alerta: {len(merecian)}",
              f"Alertas enviadas: {n['n']} ({sum(1 for a in avisadas if a.get('recalculada'))} con cuota recalculada al releer)"]
    if no_env:
        lineas.append("No enviadas al releer: " + " · ".join(f"{m}: {c}" for m, c in no_env.most_common(4)))
    if n["con"]:
        lineas.append(f"Resultado de las enviadas: {n['ganadas']} ganadas, {n['perdidas']} perdidas, {n['nulas']} nulas · "
                      f"{n['unidades']:+.1f} u ({n['media']:+.0%} por apuesta"
                      + (f" ± {n['margen']:.0%}" if n["margen"] is not None else "") + ")")
        lineas.append(f"Peor racha de pérdidas: {n['peor']} · confianza: {n['confianza']} (muestra de {n['con']})")
    else:
        lineas.append("Resultado de las enviadas: todavía ninguna con resultado")
    if n["pendientes"]:
        lineas.append(f"Faltan resultados de {n['pendientes']} alertas enviadas.")
    ls = linea_seguimiento(avisadas)
    if ls:
        lineas.append(ls)
    por_dep = defaultdict(list)
    for a in avisadas:
        por_dep[C.DEPORTES.get(a["deporte"], {}).get("nombre", a["deporte"])].append(a)
    if por_dep:
        lineas.append("Por deporte: " + " · ".join(
            f"{d} {len(g)} ({_numeros(g, res)['unidades']:+.1f} u)" for d, g in sorted(por_dep.items())))
    lineas += ["", "Con menos de 20 alertas con resultado la confianza es nula: no saques conclusiones todavía."
               if n["con"] < 20 else "Ningún resultado de la prueba hace segura una apuesta.",
               "Tablas completas guardadas en el repositorio privado.",
               f"Verifica siempre la cuota real en {casa} antes de apostar."]
    return "\n".join(lineas)


if __name__ == "__main__":
    import sys
    if "final" in sys.argv:
        print(tablas_finales())
