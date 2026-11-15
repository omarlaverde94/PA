"""
Fase 5: pruebas de seguridad del bot y de los registros (no se conecta a
Telegram ni a GitHub: todo se simula).

Comprueba que:
1. Solo se aceptan pedidos del chat autorizado (TELEGRAM_CHAT_ID), en privado
   y escritos por una persona; a cualquier otro no se le responde.
2. Solo se entienden los comandos fijos; cualquier otro texto se ignora y
   nunca se ejecuta nada ni se muestra configuración.
3. Lo que se imprime (los registros públicos de GitHub) no trae el token, el
   número de chat ni el texto de los mensajes.
4. Los archivos de datos que se suben no traen el número de chat ni mensajes.
5. "apagar" detiene la lectura de cuotas y "encender" la reanuda.

    python f5_prueba_seguridad.py
"""
import contextlib
import gzip
import io
import os
import tempfile
from pathlib import Path

TOKEN = "TOKEN-FALSO-DE-PRUEBA-no-es-real"
MI_CHAT = "987654321"
EXTRANO = "555000111"
tmp = tempfile.mkdtemp()
CASA_URL = "https://api-casa.example/offering/prueba"
os.environ.update({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": MI_CHAT, "F5_DATA": tmp, "CASA_API_URL": CASA_URL})

import f5_agente as A  # noqa: E402
import f5_fuentes as F  # noqa: E402
import f5_telegram as T  # noqa: E402

# La API de cuotas de la casa, simulada: lo que devuelve cada relectura.
api_casa = {"odds": 1.95, "st": "OPEN", "estado_partido": "NOT_STARTED", "falla": False, "lecturas": 0}


def falsa_api_casa(ids, op=None):
    api_casa["lecturas"] += 1
    if api_casa["falla"]:
        return None, None
    evid = ids[0]
    salidas = [] if api_casa["st"] == "QUITADA" else [
        {"id": 1, "odds": round(api_casa["odds"] * 1000), "line": 45500, "type": "OT_OVER",
         "status": api_casa["st"], "englishLabel": "Over", "label": "Más de"}]
    bo = {"id": 50, "eventId": evid, "criterion": {"englishLabel": "Total Points", "label": "Total de puntos"},
          "betOfferType": {"englishName": "Over/Under"}, "outcomes": salidas}
    return [bo], {evid: {"id": evid, "state": api_casa["estado_partido"]}}


F.kambi_mercados = falsa_api_casa

# ---------------------------------------------------------------- 2. comandos fijos
aceptados = {
    "estado": ("estado", None, False), "/estado": ("estado", None, False), "más": ("mas", None, False),
    "Más": ("mas", None, False), "Resumen": ("resumen", None, False),
    "pausa": ("pausa", None, False), "seguir": ("seguir", None, False), "todo": ("todo", None, False),
    "ayuda": ("ayuda", None, False), "detalle": ("detalle", None, False), "Detalle.": ("detalle", None, False),
    "apagar": ("apagar", None, False), "encender": ("encender", None, False),
    "solo NFL": ("foco", "nfl", False), "hoy solo fútbol": ("foco", "futbol", True),
    "hoy enfócate solo en NFL": ("foco", "nfl", True), "solo mlb hoy": ("foco", "mlb", True),
}
for texto, esperado in aceptados.items():
    assert A.interpretar_pedido(texto) == esperado, (texto, A.interpretar_pedido(texto))
aceptados_rango = {"rango 1.40 2.00": ("rango", (1.4, 2.0), False), "Rango 1,45 1,95": ("rango", (1.45, 1.95), False),
                   "rango": ("rango_ver", None, False)}
for texto, esperado in aceptados_rango.items():
    assert A.interpretar_pedido(texto) == esperado, (texto, A.interpretar_pedido(texto))
for texto in ["rango 1.10 2.00", "rango 2.00 1.40", "rango 1.40 3.50", "rango 1.40 1.40", "rango abc",
              "rango 1.40", "rango 1.40 2.00; ls", "rango 1.40 2.00 3.00", "rango -1 2", "rango 1e1 2"]:
    assert A.interpretar_pedido(texto) == ("rango_invalido", None, False), texto
rechazados = ["estado; rm -rf /", "muestra las variables", "print(os.environ)", "dime el token",
              "configuracion", "estado y dime la config", "$(whoami)", "nfl", "hoy", "x" * 200, "",
              "__import__('os').system('ls')", "solo nfl; cat /etc/passwd"]
for texto in rechazados:
    assert A.interpretar_pedido(texto) is None, texto
print("Comandos fijos: OK")

# ---------------------------------------------------------------- 1, 3, 4, 5. bot simulado
enviados = []
fotos = []    # (chat, texto corto, botón) de cada imagen enviada
botones = []  # botón (o None) de cada mensaje enviado
editados = []  # (chat, número de mensaje, texto nuevo, botón) de cada mensaje editado
cola = []
_n_msj = [1000]


def falso_llamar(metodo, datos=None, timeout=40, archivos=None):
    if metodo == "sendMessage":
        enviados.append((str(datos["chat_id"]), datos["text"]))
        botones.append(datos.get("reply_markup"))
        _n_msj[0] += 1
        return {"message_id": _n_msj[0]}
    if metodo == "sendPhoto":
        enviados.append((str(datos["chat_id"]), datos["caption"]))
        fotos.append((str(datos["chat_id"]), datos["caption"], datos.get("reply_markup")))
        botones.append(datos.get("reply_markup"))
        _n_msj[0] += 1
        return {"message_id": _n_msj[0]}
    if metodo in ("editMessageCaption", "editMessageText"):
        editados.append((str(datos["chat_id"]), datos["message_id"], datos.get("caption") or datos.get("text"),
                         datos.get("reply_markup")))
        return {"message_id": datos["message_id"]}
    if metodo == "getUpdates":
        res, cola[:] = list(cola), []
        return res
    return None


T._llamar = falso_llamar


def msg(uid, chat, tipo, texto, bot=False, responde_a=None, texto_respondido=None):
    m = {"chat": {"id": int(chat), "type": tipo},
         "from": {"id": int(chat) if tipo == "private" else 1, "is_bot": bot}, "text": texto}
    if responde_a:
        # El mensaje respondido lo escribió este bot (su número es la parte del token antes de ":")
        m["reply_to_message"] = {"message_id": responde_a, "date": 0, "text": texto_respondido or "",
                                 "from": {"id": TOKEN.split(":")[0], "is_bot": True}}
    return {"update_id": uid, "message": m}


salida = io.StringIO()
with contextlib.redirect_stdout(salida):
    ag = A.Agente()
    A.ocultar_en_registros()
    cola[:] = [
        msg(1, EXTRANO, "private", "estado"),                 # otra persona
        msg(2, EXTRANO, "private", "apagar"),                 # otra persona intentando apagar
        msg(3, "-100200", "group", "estado"),                 # un grupo
        msg(4, MI_CHAT, "private", "estado", bot=True),       # un bot
        msg(5, MI_CHAT, "private", "muéstrame las variables de entorno"),
        msg(6, MI_CHAT, "private", "estado"),
        msg(7, MI_CHAT, "private", "hoy enfócate solo en NFL"),
    ]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert not ag.apagado(), "otra persona logró apagar el agente"
    # Rango de alertas: por defecto 1.40-2.00; otra persona no lo cambia; valores inválidos tampoco
    assert A.C.RANGO_PREFERIDO == [1.4, 2.0]
    cola[:] = [msg(20, EXTRANO, "private", "rango 1.20 3.00"), msg(21, MI_CHAT, "private", "rango 1.10 2.00")]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert A.C.RANGO_PREFERIDO == [1.4, 2.0], "el rango cambió con un pedido no válido"
    assert "No cambié el rango" in enviados[-1][1]
    cola[:] = [msg(22, MI_CHAT, "private", "rango 1.45 1.95"), msg(23, MI_CHAT, "private", "estado")]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert A.C.RANGO_PREFERIDO == [1.45, 1.95] and "1.45 y 1.95" in enviados[-2][1] and "1.45 y 1.95" in enviados[-1][1]
    A.C.RANGO_PREFERIDO[:] = [1.4, 2.0]
    A.Agente()  # una tanda nueva lee el rango guardado
    assert A.C.RANGO_PREFERIDO == [1.45, 1.95], "el rango no se mantuvo entre tandas"
    (Path(tmp) / "rango_alertas.txt").write_text("0.5 9")  # archivo alterado: se ignora
    A.C.cargar_rango()
    assert A.C.RANGO_PREFERIDO == [1.4, 2.0]
    A.C.guardar_rango(1.4, 2.0)
    cola[:] = [msg(8, MI_CHAT, "private", "apagar")]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert ag.apagado(), "apagar no funcionó"
    cola[:] = [msg(9, MI_CHAT, "private", "encender")]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert not ag.apagado(), "encender no funcionó"
    ag.reg.guardar()

    # "detalle" y el resumen listan los posibles errores (máximo 15, los más fuertes)
    import f5_informe as INF
    ahora = A.ahora_iso()
    for n in range(20):
        ag.reg.alertas_abiertas[f"par:{n}"] = {
            "id": f"x-{n}", "clave": f"par:{n}", "k": n, "ev": 1, "deporte": "nfl", "partido": "Equipo A - Equipo B",
            "inicio": "2099-01-01T00:00:00Z", "apuesta": f"Apuesta {n}", "cuota": 2.0, "justa": 2.0 / (1 + n / 100),
            "pin_justa": (2.0 / (1 + n / 100)) if n % 2 else None, "regla": "par", "tipo": "interno",
            "detectado": ahora, "detectado_ts": 0, "ventaja": n / 100, "avisado": n < 3}
    cola[:] = [msg(11, MI_CHAT, "private", "detalle")]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    det = enviados[-1][1]
    assert "Abiertos ahora: 20" in det and det.count("⚪ Apuesta") == 15, "tope de 15 incorrecto"
    assert "y 5 más" in det and det.index("Apuesta 19") < det.index("Apuesta 18"), "orden incorrecto"
    assert "paga 2.00 → debería pagar" in det and "Ventaja" in det and "Confianza" in det
    res = INF.resumen_diario(ag.reg, A.col().date())
    totales = res.split("\n\n")[0].split("\n")
    assert len(totales) <= 5 and "Posibles errores: 20" in res and "y 5 más" in res, "resumen no es corto"
    ag.reg.alertas_abiertas.clear()
    # Solo se envían ALERTAS de las señales que lo merecen (estimada en el rango y sobre el justo)
    info = {"deporte": "nfl", "nombre": "Equipo A - Equipo B", "liga": "Liga", "inicio": "2099-01-01T00:00:00Z"}
    base = {"clave": "mercado:1", "k": 1, "apuesta": "Más de 45.5", "cuota": 1.95, "justa": 1.75, "regla": "mercado",
            "tipo": "mercado", "respaldo": "r", "explicacion": "e", "confianza": "media", "detectado": ahora,
            "casa": "Casa de prueba", "partido": "Equipo A - Equipo B", "inicio": "2099-01-01T00:00:00Z",
            "deporte": "nfl", "liga": "Liga", "cuota_rb": 1.95, "rango_rb": None, "justa_rb": 1.75, "ventaja_rb": 0.114,
            "real": True, "detectado_ts": 0}
    antes = len(enviados)
    ag.avisar(info, [dict(base, avisable=False, clave="mercado:9")])
    assert len(enviados) == antes, "se envió una alerta que no la merecía"
    info["id"] = 1028811658
    ag.est.enlace = "https://casa.example/?page=sportsbook#event/{id}"
    alerta = dict(base, avisable=True, id="al-1", ev=1028811658)
    ag.reg.alertas_abiertas[alerta["clave"]] = alerta  # como en la vida real: la alerta está en el registro
    # Relectura justo antes de enviar: se vuelve a leer la cuota REAL en la API de la casa
    assert ag.est.real and ag.est.ok, "con CASA_API_URL la cuota debe ser la real, sin estimar"
    # Recálculo simulado de la señal con la cuota nueva: Pinnacle dice que lo justo es justa_prueba[0]
    justa_prueba = [1.75]

    def falsos_hallazgos(evid, filas, info_):
        f = filas[0]
        h = {"clave": "mercado:1", "k": 1, "ev": evid, "regla": "mercado", "tipo": "mercado", "cuota": f["odds"],
             "justa": justa_prueba[0], "ventaja": round(f["odds"] / justa_prueba[0] - 1, 4), "apuesta": "Más de 45.5",
             "respaldo": "r", "explicacion": "e2", "confianza": "media"}
        if h["ventaja"] < A.C.UMBRAL_MERCADO:
            return [], None  # sin ventaja mínima el detector ni siquiera la marca
        h.update(ag.est.evaluar(h, {1: f}, info_["deporte"]))
        return [h], None
    ag.hallazgos = falsos_hallazgos
    for cambio, motivo in [({"odds": 1.80}, "la cuota cambió al releerla justo antes de enviar (1.95 → 1.80) "
                                            "y con la cuota nueva ya no tiene ventaja suficiente"),
                           ({"odds": 2.05}, "la cuota cambió al releerla justo antes de enviar (1.95 → 2.05) "
                                            "y con la cuota nueva la ventaja pasa del tope del 15 %"),
                           ({"st": "SUSPENDED"}, "la apuesta ya no estaba abierta"),
                           ({"st": "QUITADA"}, "la apuesta ya no estaba abierta"),
                           ({"falla": True}, "no se pudo releer la cuota"),
                           ({"estado_partido": "STARTED"}, "no se pudo releer la cuota")]:
        api_casa.update({"odds": 1.95, "st": "OPEN", "estado_partido": "NOT_STARTED", "falla": False}, **cambio)
        ag.reg.cerrados.discard(1028811658)
        n_lect = api_casa["lecturas"]
        ag.avisar(info, [alerta])
        assert api_casa["lecturas"] == n_lect + 1, "no se releyó la cuota antes de enviar"
        assert len(enviados) == antes and alerta.get("no_enviada", "").startswith(motivo), (cambio, alerta.get("no_enviada"))
    api_casa.update({"odds": 1.95, "st": "OPEN", "estado_partido": "NOT_STARTED", "falla": False})
    ag.reg.cerrados.discard(1028811658)
    ag.reg.alertas_abiertas[alerta["clave"]] = alerta  # el caso "ya empezó" la cerró: se vuelve a abrir
    # Cambió y con la cuota nueva queda fuera del rango (ventaja dentro del tope): no se envía
    justa_prueba[0], api_casa["odds"] = 1.95, 2.10
    fuera = dict(alerta, id="al-f")
    ag.avisar(info, [fuera])
    assert len(enviados) == antes and fuera["no_enviada"].endswith("queda fuera del rango 1.40–2.00"), fuera["no_enviada"]
    # Cambió pero con la cuota nueva sigue cumpliendo todo: se envía con la cuota nueva, recalculada
    justa_prueba[0], api_casa["odds"] = 1.75, 1.90
    recalc = dict(alerta, id="al-r")
    ag.avisar(info, [recalc])
    assert len(enviados) == antes + 1, "no se envió una alerta que con la cuota nueva seguía cumpliendo"
    assert recalc["recalculada"] and recalc["cuota_detectada"] == 1.95 and recalc["cuota_envio"] == 1.90
    assert recalc["cuota_rb"] == 1.90 and recalc["ventaja_rb"] == round(1.90 / 1.75 - 1, 4) and "no_enviada" not in recalc
    assert "Casa de prueba paga 1.90" in enviados[-1][1] and "actualizada al releer (antes 1.95)" in enviados[-1][1]
    assert INF.texto_alerta(info, [recalc], 1).count("La cuota cambió de 1.95 a 1.90") == 1
    assert [x["cuota"] for x in ag.reg.seguimientos if x["id"] == "al-r"] == [1.90], "el seguimiento debe usar la cuota nueva"
    rec_av = dict(recalc, avisado=True)
    assert "1 con cuota recalculada" in "\n".join(INF.tabla_seguimiento([rec_av, dict(alerta, avisado=True)]))
    enviados.pop(), botones.pop(), fotos and fotos.pop()
    ag.reg.envios.clear(), ag.avisos_hora.clear(), ag.reg.avisados.clear()
    ag.reg.seguimientos[:] = [x for x in ag.reg.seguimientos if x["id"] != "al-r"]
    api_casa["odds"] = 1.95
    # Tenis de mesa está excluido: nunca se envía una alerta (ni se lee la cuota)
    for info_tt in (dict(info, deporte_kambi="TABLE_TENNIS"), dict(info, deporte_kambi="table_tennis"),
                    dict(info, deporte="tenis_mesa"), dict(info, liga="Table Tennis / Czech Liga Pro")):
        tt = dict(alerta, id="al-tt")
        n_lect = api_casa["lecturas"]
        ag.avisar(info_tt, [tt])
        assert len(enviados) == antes and api_casa["lecturas"] == n_lect, f"se envió una alerta de tenis de mesa: {info_tt}"
        assert tt["no_enviada"] == "deporte excluido de las alertas"
    assert not A.C.deporte_excluido(dict(info, deporte_kambi="AMERICAN_FOOTBALL"))
    # Una cuota que ya salió del rango al releer tampoco se envía
    api_casa["odds"] = 2.30
    ag.avisar(info, [dict(alerta, cuota=2.30)])
    assert len(enviados) == antes, "se envió una alerta cuya cuota ya había salido del rango"
    api_casa["odds"] = 1.95
    # Tope de ventaja: más de 15 % contra Pinnacle no se envía y queda anotado el motivo
    excesiva = dict(alerta, clave="mercado:8", id="al-8", ventaja_pin=0.2)
    ag.avisar(info, [excesiva])
    assert len(enviados) == antes and excesiva["descartada"] == "descartada por ventaja excesiva"
    # De 10 % a 15 %: se envía con el aviso de ventaja alta
    alta = dict(alerta, clave="mercado:7", id="al-7", ventaja_pin=0.12, ventaja_alta=True)
    ag.avisar(info, [alta])
    assert len(enviados) == antes + 1 and enviados[-1][1].split("\n")[4] == \
        "⚠️ Ventaja alta: revisa noticias y alineaciones antes de apostar"
    ag.avisos_hora.clear()
    enviados.pop(), botones.pop(), fotos and fotos.pop(), ag.reg.envios.clear()
    ag.avisar(info, [alerta])
    corto = enviados[-1][1]
    import f5_tarjeta
    assert f5_tarjeta.Image is None or fotos, "con Pillow instalado la alerta debe ir como imagen"
    assert len(enviados) == antes + 1 and corto.startswith("🟢 Más de 45.5")
    assert "Casa de prueba paga 1.95 → debería pagar 1.75" in corto and "Ventaja +11.4% · Confianza media" in corto
    assert len(corto.split("\n")) == 5, "la alerta corta debe tener 4 líneas más la hora de lectura"
    assert corto.split("\n")[4].startswith("🕒 Cuota leída a las ") and "(hora Colombia)" in corto
    assert alerta["cuota_envio"] == 1.95 and alerta.get("relectura") and "no_enviada" not in alerta
    # Seguimiento: a los 2, 5 y 15 minutos se vuelve a leer la cuota y se anota si se mantuvo
    seg = [x for x in ag.reg.seguimientos if x["id"] == "al-1"]
    assert len(seg) == 1 and seg[0]["faltan"] == [2, 5, 15] and seg[0]["cuota"] == 1.95
    t_envio = seg[0]["enviado_ts"]
    reloj = [t_envio + 30]
    real_time = A.time.time
    A.time.time = lambda: reloj[0]
    try:
        n_lect = api_casa["lecturas"]
        ag.seguimiento()
        assert api_casa["lecturas"] == n_lect, "se releyó antes de tiempo"
        for minuto, cambio in ((2, {"odds": 1.95}), (5, {"odds": 1.80}), (15, {"st": "SUSPENDED"})):
            reloj[0] = t_envio + minuto * 60 + 1
            api_casa.update(cambio)
            ag.seguimiento()
    finally:
        A.time.time = real_time
    api_casa.update({"odds": 1.95, "st": "OPEN"})
    assert not [x for x in ag.reg.seguimientos if x["id"] == "al-1"], "quedaron relecturas pendientes"
    ag.reg.guardar()
    import json as _json
    segs = []
    for ruta in (Path(tmp) / "alertas").glob("*.gz"):
        with gzip.open(ruta, "rt") as fh:
            segs += [_json.loads(linea) for linea in fh if '"seguimiento"' in linea]
    por_min = {r["min"]: r for r in segs if r["id"] == "al-1"}
    assert por_min[2]["estado"] == "igual" and por_min[2]["se_mantuvo"] is True
    assert por_min[5]["estado"] == "bajó" and por_min[5]["cuota"] == 1.80 and por_min[5]["se_mantuvo"] is False
    assert por_min[15]["estado"] == "suspendida" and por_min[15]["se_mantuvo"] is False
    al = dict(alerta, avisado=True, seguimiento={str(m): r for m, r in por_min.items()})
    assert INF.linea_seguimiento([al]) == "Cuota mantenida tras el aviso: 2 min 1 de 1 · 5 min 0 de 1 · 15 min 0 de 1"
    assert "a los 5 min 1.80 (bajó)" in INF.texto_seguimiento(al)
    assert "| 2 min | 1 | 1 (100%) |" in "\n".join(INF.tabla_seguimiento([al]))
    # Si el partido ya empezó, no se leen cuotas en vivo: se anota y no se mide
    ag.reg.seguimientos.append({"id": "al-x", "k": 1, "ev": 1028811658, "cuota": 1.95,
                                "enviado_ts": 0, "faltan": [2, 5, 15]})
    ag.reg.cerrados.add(1028811658)
    n_lect = api_casa["lecturas"]
    ag.seguimiento()
    assert api_casa["lecturas"] == n_lect, "se leyó una cuota en vivo"
    assert not [x for x in ag.reg.seguimientos if x["id"] == "al-x"]
    ag.reg.cerrados.discard(1028811658)
    boton = (botones[-1] or {}).get("inline_keyboard", [[None]])[0][0]
    assert boton and boton["url"].endswith("#event/1028811658"), "falta el botón con el enlace al partido"
    # Al liquidar, se edita el mensaje original con el resultado (y se conserva el botón)
    mid = _n_msj[0]
    assert ag.reg.envios and ag.reg.envios[-1]["id"] == mid and "chat" in ag.reg.envios[-1]
    assert MI_CHAT not in str(ag.reg.envios), "se guardó el número de chat"
    ag.liq_alertas = [alerta]
    for estado, marca in (("ganada", "✅ Ganada"), ("perdida", "❌ Perdida"), ("nula", "↩️ Nula")):
        ag.reg.envios[:] = [dict(ag.reg.envios[-1] if ag.reg.envios else envio_0, editadas=[])]
        envio_0 = dict(ag.reg.envios[-1])
        ag.liq_res = {"al-1": {"estado": estado, "gana": {"ganada": True, "perdida": False}.get(estado),
                               "unidades": 0.0, "detalle": "marcador 30-20"}}
        ag.editar_mensajes()
        chat_e, mid_e, texto_e, boton_e = editados[-1]
        assert chat_e == MI_CHAT and mid_e == mid and texto_e.startswith(corto), "no se editó el mensaje original"
        assert texto_e.split("\n")[5].startswith(marca) and boton_e, (estado, texto_e)
    assert not ag.reg.envios, "un mensaje ya editado no debe quedar pendiente"
    n_edit = len(editados)
    ag.editar_mensajes()
    assert len(editados) == n_edit
    # "más" respondiendo a la alerta: explicación completa; de otra persona: nada
    cola[:] = [msg(30, EXTRANO, "private", "más", responde_a=mid), msg(31, MI_CHAT, "private", "más", responde_a=mid)]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert "Explicación: e" in enviados[-1][1] and enviados[-1][0] == MI_CHAT
    # Alerta vieja (sin número de mensaje guardado): se reconoce por el partido y la apuesta del texto
    ag.reg.mensajes.clear()
    viejo = "PRUEBA — no apostar\n\nPartido: Equipo A - Equipo B\n\n1) Apuesta: Más de 45.5\n• Cuota: 1.95"
    cola[:] = [msg(32, MI_CHAT, "private", "más", responde_a=77, texto_respondido=viejo)]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert "Explicación: e" in enviados[-1][1], "más no encontró una alerta vieja por su texto"
    # "más" solo, sin números guardados: la última alerta avisada del registro
    ag.reg.alertas_abiertas["mercado:1"]["avisado"] = True
    cola[:] = [msg(33, MI_CHAT, "private", "más")]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert "Explicación: e" in enviados[-1][1], "más solo no encontró la última alerta"
    # Respuesta a un mensaje que no es una alerta: no inventa otra
    cola[:] = [msg(34, MI_CHAT, "private", "más", responde_a=78, texto_respondido="hola")]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert "No encontré esa alerta" in enviados[-1][1]
    # un enlace que no sea https no se usa
    assert T.enviar_alerta("x", None, ("b", "javascript:alert(1)")) and botones[-1] is None
    ag.reg.alertas_abiertas.clear()

    # Prueba de 14 días: informe intermedio el día 7 y final el día 14, por Telegram
    assert A.C.DIAS_PRUEBA == 14 and A.C.DIA_INFORME_INTERMEDIO == 7
    inicio = Path(tmp) / "inicio_prueba.txt"
    liquidadas = {}

    def falso_liquidar(forzar=False):
        ag.liq_res = liquidadas
    ag.liquidar = falso_liquidar
    inicio.write_text(A.ahora_iso(A.time.time() - 6.9 * 86400))
    n_env = len(enviados)
    ag.informe_intermedio_si_toca()
    assert len(enviados) == n_env and not ag.prueba_terminada(), "informe intermedio antes del día 7"
    inicio.write_text(A.ahora_iso(A.time.time() - 7.01 * 86400))
    ag.informe_intermedio_si_toca()
    ag.informe_intermedio_si_toca()
    assert len(enviados) == n_env + 1 and "Informe intermedio (día 7 de 14)" in enviados[-1][1], "falta el informe del día 7"
    assert (Path(tmp) / "tablas_dia7.md").exists() and not ag.prueba_terminada()
    # Día 14 con una alerta enviada sin resultado: informe final, aviso y, cuando llega el resultado, el completo
    ag.reg.alerta_abre({"clave": "mercado:99", "k": 99, "ev": 5, "regla": "mercado", "cuota": 1.9, "apuesta": "x"},
                       {"id": "al-99", "deporte": "nfl", "partido": "P", "inicio": "2099-01-01T00:00:00Z",
                        "detectado": A.ahora_iso(), "detectado_ts": A.time.time(), "avisado": True})
    ag.reg.alertas_f.volcar()
    inicio.write_text(A.ahora_iso(A.time.time() - 14.01 * 86400))
    assert ag.prueba_terminada()
    real_sleep = A.time.sleep
    A.time.sleep = lambda s_: liquidadas.update({"al-99": {"estado": "ganada", "gana": True, "unidades": 0.9}})
    try:
        ag.cierre_de_prueba()
    finally:
        A.time.sleep = real_sleep
    textos = [t for _, t in enviados[n_env + 1:]]
    assert "Informe final (día 14)" in textos[0] and "Faltan resultados de 1" in textos[0], textos
    assert "Terminaron los 14 días" in textos[1]
    assert "Informe final completo" in textos[2] and "1 ganadas" in textos[2], textos[2]
    n_env = len(enviados)
    ag.cierre_de_prueba()
    assert len(enviados) == n_env, "el informe final se envió dos veces"
print("Prueba de 14 días con informe intermedio el día 7 y final el día 14 por Telegram: OK")
print("Relectura de la cuota real justo antes de enviar (si cambió, se recalcula y solo se envía si cumple) y hora de lectura: OK")
print("Tenis de mesa excluido: no se envía ninguna alerta de ese deporte: OK")
print("Seguimiento a los 2, 5 y 15 minutos (sin cuotas en vivo) en el registro, el resumen y el informe: OK")
print("Revisión antes de enviar, tope de ventaja y edición del mensaje con el resultado: OK")
print("Alertas cortas con imagen y botón, \"más\" solo para tu chat, solo si merecen alerta; comando rango seguro: OK")
print("Resumen y \"detalle\" listan los errores (15 más fuertes y cuántos faltan): OK")

destinos = {c for c, _ in enviados} | {c for c, _, _ in fotos} | {c for c, _, _, _ in editados}
assert destinos == {MI_CHAT}, f"se envió a un chat no autorizado: {destinos}"
assert ag.foco == {"nfl"} and ag.foco_hasta, "el pedido de enfoque no se aplicó"
respuestas = [t for _, t in enviados]
assert any("No es un pedido que yo conozca" in t for t in respuestas)
for t in respuestas:
    assert TOKEN not in t and "TELEGRAM" not in t and "F5_" not in t and tmp not in t and CASA_URL not in t, \
        "una respuesta reveló configuración"
print("Solo responde a tu chat y solo con comandos fijos: OK")

impreso = "\n".join(l for l in salida.getvalue().splitlines() if not l.startswith("::add-mask::"))
for prohibido in (TOKEN, MI_CHAT, EXTRANO, "api.telegram.org", "variables de entorno", "enfócate", CASA_URL):
    assert prohibido not in impreso, f"los registros muestran algo que no deben: {prohibido[:12]}..."
print("Los registros no muestran token, número de chat ni mensajes: OK")

for ruta in Path(tmp).rglob("*"):
    if ruta.is_file():
        datos = ruta.read_bytes()
        if ruta.suffix == ".gz":
            datos = gzip.decompress(datos)
        for prohibido in (MI_CHAT, EXTRANO, TOKEN, "enfócate", "variables de entorno", CASA_URL):
            assert prohibido.encode() not in datos, f"{ruta.name} guarda algo que no debe"
print("Los datos guardados no traen número de chat ni mensajes: OK")

# Sin TELEGRAM_CHAT_ID no se obedece ni se envía nada
del os.environ["TELEGRAM_CHAT_ID"]
enviados.clear()
cola[:] = [msg(10, MI_CHAT, "private", "estado")]
assert T.Pedidos().nuevos() == [] and not T.enviar("x") and not enviados
print("Sin TELEGRAM_CHAT_ID no hace nada: OK")
# ---------------------------------------------------------------- 6. guardado en el repositorio privado
import f5_probar_guardado as G  # noqa: E402


class _Resp:
    def __init__(self, codigo, privado):
        self.status_code, self._p = codigo, privado

    def json(self):
        return {"private": self._p}


os.environ.update({"DATA_REPO": "dueno/repo-de-datos-secreto", "PRIVATE_REPO_TOKEN": "PAT-FALSO-no-es-real"})
for codigo, privado, debe_pasar in [(200, True, True), (200, False, False), (404, None, False), (401, None, False)]:
    G.requests.get = lambda *a, _c=codigo, _p=privado, **k: _Resp(_c, _p)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            G.verificar()
            paso = True
        except SystemExit:
            paso = False
    assert paso == debe_pasar, (codigo, privado)
    assert "PAT-FALSO" not in out.getvalue() and "repo-de-datos-secreto" not in out.getvalue()
print("Los datos solo van a un repositorio privado y no se muestra el token: OK")
print("Todas las pruebas de seguridad pasaron.")
