"""
Fase 5: envío de alertas y lectura de pedidos por Telegram.

- El token del bot se lee de TELEGRAM_BOT_TOKEN y nunca se imprime ni se guarda.
- Nunca se imprime ni se guarda el número de chat ni el texto de los mensajes.
- Los únicos chats autorizados están en TELEGRAM_CHAT_ID (uno o varios,
  separados por coma). Solo a ellos se envían avisos y solo sus pedidos se
  obedecen. A cualquier otra persona que le escriba al bot no se le responde.
- Si TELEGRAM_CHAT_ID no está puesto, el agente no envía ni obedece nada.
- Para averiguar el número de chat una sola vez:
    python f5_telegram.py mi_chat 10   (espera 10 minutos un "hola" al bot)
"""
import json
import os
import time

import requests

_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_API = "https://api.telegram.org/bot{token}/{metodo}"
_S = requests.Session()


def activo():
    return bool(_TOKEN)


def _llamar(metodo, datos=None, timeout=40, archivos=None):
    if not _TOKEN:
        return None
    for intento in range(3):
        try:
            if archivos:
                # Envío de imagen: los datos van como formulario (reply_markup en JSON)
                form = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in (datos or {}).items()}
                r = _S.post(_API.format(token=_TOKEN, metodo=metodo), data=form, files=archivos, timeout=timeout)
            else:
                r = _S.post(_API.format(token=_TOKEN, metodo=metodo), json=datos or {}, timeout=timeout)
            d = r.json()
            if d.get("ok"):
                return d["result"]
            if r.status_code == 429:
                time.sleep(d.get("parameters", {}).get("retry_after", 5) + 1)
                continue
            # Solo el método y el código: nunca la dirección (lleva el token),
            # ni el número de chat, ni la descripción de Telegram.
            print(f"[telegram] {metodo} falló (código {r.status_code})", flush=True)
            return None
        except (requests.RequestException, ValueError) as e:
            print(f"[telegram] {metodo} error de conexión: {type(e).__name__}", flush=True)
            time.sleep(3 * (intento + 1))
    return None


def chats():
    """Los únicos chats autorizados: los de TELEGRAM_CHAT_ID. Sin esa variable, ninguno."""
    return [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]


def enviar(texto, chat=None):
    """Envía solo a chats autorizados; cualquier otro destino se descarta."""
    autorizados = chats()
    destinos = [chat] if chat else autorizados
    ok = False
    for c in destinos:
        if str(c) not in autorizados:
            continue
        # Telegram corta en 4096 caracteres
        for i in range(0, len(texto), 4000):
            r = _llamar("sendMessage", {"chat_id": c, "text": texto[i:i + 4000],
                                        "disable_web_page_preview": True})
            ok = ok or r is not None
    return ok


def enlace_seguro(url):
    """Solo se aceptan enlaces https (el del botón para abrir el partido)."""
    return isinstance(url, str) and url.startswith("https://") and len(url) < 300 and " " not in url


def enviar_alerta(texto, imagen=None, boton=None):
    """
    Envía una alerta a los chats autorizados: la imagen (si hay) con el texto
    corto debajo, y un botón con enlace (si hay). Si la imagen falla, manda
    solo el texto. Devuelve los números de los mensajes enviados (para "más").
    """
    teclado = None
    if boton and enlace_seguro(boton[1]):
        teclado = {"inline_keyboard": [[{"text": boton[0], "url": boton[1]}]]}
    ids = []
    for c in chats():
        r = None
        if imagen:
            datos = {"chat_id": c, "caption": texto[:1024]}
            if teclado:
                datos["reply_markup"] = teclado
            r = _llamar("sendPhoto", datos, timeout=60, archivos={"photo": ("alerta.png", imagen, "image/png")})
        if r is None:
            datos = {"chat_id": c, "text": texto[:4000], "disable_web_page_preview": True}
            if teclado:
                datos["reply_markup"] = teclado
            r = _llamar("sendMessage", datos)
        if r and "message_id" in r:
            ids.append(r["message_id"])
    return ids


def _es_autorizado(m, autorizados):
    """Mensaje privado, escrito por una persona, desde un chat autorizado."""
    chat, remitente = m.get("chat", {}), m.get("from", {})
    cid = str(chat.get("id", ""))
    return (chat.get("type") == "private" and not remitente.get("is_bot")
            and str(remitente.get("id", "")) == cid and cid in autorizados)


class Pedidos:
    """Lee los mensajes nuevos que le escriben al bot. Solo devuelve los del
    chat autorizado; los de cualquier otra persona se descartan sin responder."""

    def __init__(self):
        self.offset = None
        self.ignorados = 0

    def nuevos(self):
        autorizados = chats()
        if not autorizados:
            return []  # sin TELEGRAM_CHAT_ID no se lee ni se obedece nada
        res = _llamar("getUpdates", {"timeout": 0, "offset": self.offset,
                                     "allowed_updates": ["message"]}) or []
        salida = []
        for u in res:
            self.offset = u["update_id"] + 1
            m = u.get("message") or {}
            texto = (m.get("text") or "").strip()
            if not _es_autorizado(m, autorizados):
                self.ignorados += 1
                continue
            if texto:
                # A qué mensaje responde (para "más"); solo el número, nunca el texto
                resp = (m.get("reply_to_message") or {}).get("message_id")
                salida.append((str(m["chat"]["id"]), texto, resp))
        return salida


def mi_chat(minutos=10):
    """
    Ayuda de una sola vez para saber el número de chat: espera unos minutos a
    que alguien le escriba al bot en privado, le contesta SOLO al primero con su
    propio número (no da acceso a nada ni obedece pedidos) y se apaga. Se usa solo mientras no exista
    TELEGRAM_CHAT_ID; con esa variable puesta no hace nada.
    """
    if chats():
        print("TELEGRAM_CHAT_ID ya está puesto: no hace falta.")
        return
    offset, fin = None, time.time() + minutos * 60
    print(f"Esperando mensajes al bot durante {minutos} min...", flush=True)
    while time.time() < fin:
        res = _llamar("getUpdates", {"timeout": 20, "offset": offset,
                                     "allowed_updates": ["message"]}, timeout=40) or []
        for u in res:
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            chat, remitente = m.get("chat", {}), m.get("from", {})
            if chat.get("type") != "private" or remitente.get("is_bot"):
                continue
            _llamar("sendMessage", {"chat_id": chat["id"], "text":
                    "PRUEBA — no apostar\n\nTu número de chat es: " + str(chat["id"]) +
                    "\n\nGuárdalo en GitHub como secreto TELEGRAM_CHAT_ID. Desde ese momento el "
                    "bot solo le responde y obedece a este chat."})
            print("Respondí al primer chat privado con su número; la ayuda termina aquí.", flush=True)
            # Se confirma la lectura para que el mensaje no quede pendiente y se sale:
            # solo se atiende a UN chat, así nadie más recibe respuesta.
            _llamar("getUpdates", {"timeout": 0, "offset": offset})
            return
        if not res:
            time.sleep(2)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "mi_chat":
        mi_chat(float(sys.argv[2]) if len(sys.argv) > 2 else 10)
