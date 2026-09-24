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
os.environ.update({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": MI_CHAT, "F5_DATA": tmp})

import f5_agente as A  # noqa: E402
import f5_telegram as T  # noqa: E402

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
            "deporte": "nfl", "liga": "Liga", "cuota_rb": 1.93, "rango_rb": [1.91, 1.94], "justa_rb": 1.75, "ventaja_rb": 0.103}
    antes = len(enviados)
    ag.avisar(info, [dict(base, avisable=False, clave="mercado:9")])
    assert len(enviados) == antes, "se envió una alerta que no la merecía"
    info["id"] = 1028811658
    ag.est.enlace = "https://casa.example/?page=sportsbook#event/{id}"
    alerta = dict(base, avisable=True, id="al-1")
    ag.reg.alertas_abiertas[alerta["clave"]] = alerta  # como en la vida real: la alerta está en el registro
    ag.avisar(info, [alerta])
    corto = enviados[-1][1]
    import f5_tarjeta
    assert f5_tarjeta.Image is None or fotos, "con Pillow instalado la alerta debe ir como imagen"
    assert len(enviados) == antes + 1 and corto.startswith("🟢 Más de 45.5")
    assert "Casa de prueba paga 1.93 → debería pagar 1.75" in corto and "Ventaja +10.3% · Confianza media" in corto
    assert len(corto.split("\n")) == 4, "la alerta corta debe tener 4 líneas"
    boton = (botones[-1] or {}).get("inline_keyboard", [[None]])[0][0]
    assert boton and boton["url"].endswith("#event/1028811658"), "falta el botón con el enlace al partido"
    # "más" respondiendo a la alerta: explicación completa; de otra persona: nada
    mid = _n_msj[0]
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
print("Alertas cortas con imagen y botón, \"más\" solo para tu chat, solo si merecen alerta; comando rango seguro: OK")
print("Resumen y \"detalle\" listan los errores (15 más fuertes y cuántos faltan): OK")

destinos = {c for c, _ in enviados} | {c for c, _, _ in fotos}
assert destinos == {MI_CHAT}, f"se envió a un chat no autorizado: {destinos}"
assert ag.foco == {"nfl"} and ag.foco_hasta, "el pedido de enfoque no se aplicó"
respuestas = [t for _, t in enviados]
assert any("No es un pedido que yo conozca" in t for t in respuestas)
for t in respuestas:
    assert TOKEN not in t and "TELEGRAM" not in t and "F5_" not in t and tmp not in t, "una respuesta reveló configuración"
print("Solo responde a tu chat y solo con comandos fijos: OK")

impreso = "\n".join(l for l in salida.getvalue().splitlines() if not l.startswith("::add-mask::"))
for prohibido in (TOKEN, MI_CHAT, EXTRANO, "api.telegram.org", "variables de entorno", "enfócate"):
    assert prohibido not in impreso, f"los registros muestran algo que no deben: {prohibido[:12]}..."
print("Los registros no muestran token, número de chat ni mensajes: OK")

for ruta in Path(tmp).rglob("*"):
    if ruta.is_file():
        datos = ruta.read_bytes()
        if ruta.suffix == ".gz":
            datos = gzip.decompress(datos)
        for prohibido in (MI_CHAT, EXTRANO, TOKEN, "enfócate", "variables de entorno"):
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
