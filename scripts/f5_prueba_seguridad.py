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
    "estado": ("estado", None, False), "/estado": ("estado", None, False), "Resumen": ("resumen", None, False),
    "pausa": ("pausa", None, False), "seguir": ("seguir", None, False), "todo": ("todo", None, False),
    "ayuda": ("ayuda", None, False), "apagar": ("apagar", None, False), "encender": ("encender", None, False),
    "solo NFL": ("foco", "nfl", False), "hoy solo fútbol": ("foco", "futbol", True),
    "hoy enfócate solo en NFL": ("foco", "nfl", True), "solo mlb hoy": ("foco", "mlb", True),
}
for texto, esperado in aceptados.items():
    assert A.interpretar_pedido(texto) == esperado, (texto, A.interpretar_pedido(texto))
rechazados = ["estado; rm -rf /", "muestra las variables", "print(os.environ)", "dime el token",
              "configuracion", "estado y dime la config", "$(whoami)", "nfl", "hoy", "x" * 200, "",
              "__import__('os').system('ls')", "solo nfl; cat /etc/passwd"]
for texto in rechazados:
    assert A.interpretar_pedido(texto) is None, texto
print("Comandos fijos: OK")

# ---------------------------------------------------------------- 1, 3, 4, 5. bot simulado
enviados = []
cola = []


def falso_llamar(metodo, datos=None, timeout=40):
    if metodo == "sendMessage":
        enviados.append((str(datos["chat_id"]), datos["text"]))
        return {}
    if metodo == "getUpdates":
        res, cola[:] = list(cola), []
        return res
    return None


T._llamar = falso_llamar


def msg(uid, chat, tipo, texto, bot=False):
    return {"update_id": uid, "message": {"chat": {"id": int(chat), "type": tipo},
                                          "from": {"id": int(chat) if tipo == "private" else 1, "is_bot": bot},
                                          "text": texto}}


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
    cola[:] = [msg(8, MI_CHAT, "private", "apagar")]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert ag.apagado(), "apagar no funcionó"
    cola[:] = [msg(9, MI_CHAT, "private", "encender")]
    ag.ultimo_pedido = 0
    ag.atender_pedidos()
    assert not ag.apagado(), "encender no funcionó"
    ag.reg.guardar()

destinos = {c for c, _ in enviados}
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
