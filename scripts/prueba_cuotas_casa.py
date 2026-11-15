"""
Prueba separada (no es parte del agente): ¿se pueden leer las cuotas de
la casa desde GitHub Actions, y coinciden con lo que muestra su página?

    python prueba_cuotas_casa.py <carpeta de salida>
    python prueba_cuotas_casa.py avisar <carpeta de salida> <ok|fallo>

Las direcciones no están escritas aquí: se leen de dos variables (en GitHub,
secretos del repositorio):
- CASA_API_URL: dirección base de la API de cuotas de la casa (sin "/" al final).
- CASA_PAGINA_URL: dirección de la página de un partido, con {id} en el lugar
  del número del partido.

1. Lee de la API pública de cuotas de la casa (mercado CO) unos 10
   partidos próximos (fútbol y otros deportes) con su cuota principal
   (1X2 o ganador). Solo partidos que no han empezado.
2. Con un navegador sin ventana abre la página pública de 3 de esos
   partidos y toma captura. NUNCA inicia sesión ni usa credenciales. Si la
   página está bloqueada o pide verificación, lo anota, toma captura de eso
   y sigue, sin intentar saltarse el bloqueo.
3. Compara la cuota de la API (leída de nuevo justo antes de abrir la
   página) con los números que se ven en la página.
4. Escribe en la carpeta de salida: informe.md, resumen.json y las capturas.
   Con «avisar» (después de guardar) envía un resumen corto por Telegram.

Lo que se imprime en los registros es solo un conteo (sin equipos, sin
cuotas, sin direcciones de repositorios ni claves), porque los registros
de un repositorio público los puede ver cualquiera.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = os.environ.get("CASA_API_URL", "").strip().rstrip("/")
PAGINA = os.environ.get("CASA_PAGINA_URL", "").strip()
PARAMS = {"lang": "es_ES", "market": "CO"}
# (ruta en Kambi, nombre en el informe, cuántos partidos tomar)
DEPORTES = [("football", "Fútbol", 4), ("basketball", "Baloncesto", 1), ("tennis", "Tenis", 1),
            ("american_football", "Fútbol americano", 1), ("baseball", "Béisbol", 1),
            ("ufc_mma", "UFC", 1), ("table_tennis", "Tenis de mesa", 1)]
TOTAL = 10
CAPTURAS = 3
# Palabras que suelen aparecer si la página está bloqueada o pide verificación.
SENALES_BLOQUEO = ["no está disponible en tu país", "no disponible en tu", "not available in your",
                   "restricted", "restringid", "access denied", "acceso denegado", "bloquead",
                   "your location", "tu ubicación", "forbidden", "captcha", "verify you are human",
                   "verifica que eres", "cloudflare", "just a moment", "un momento"]

_S = requests.Session()
_S.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) prueba-cuotas"


def log(msg):
    print(msg, flush=True)


def _get(ruta):
    r = _S.get(f"{API}/{ruta}", params=PARAMS, timeout=30)
    r.raise_for_status()
    return r.json()


def _oferta_principal(ofertas):
    """La cuota principal: 1X2 (fútbol) o ganador del partido (los demás)."""
    for o in ofertas or []:
        if (o.get("betOfferType") or {}).get("id") == 2 and "MAIN" in (o.get("tags") or []):
            return o
    for o in ofertas or []:
        if (o.get("betOfferType") or {}).get("id") == 2:
            return o
    return None


def _cuotas(oferta):
    """Lista de (etiqueta, cuota) solo si todas las opciones están abiertas y con cuota."""
    if not oferta:
        return None
    sal = []
    for x in oferta.get("outcomes", []):
        if x.get("status") != "OPEN" or not x.get("odds"):
            return None
        sal.append((x.get("participant") or x.get("label") or "?", round(x["odds"] / 1000, 2)))
    return sal if len(sal) >= 2 else None


def leer_api():
    """Devuelve (partidos, notas). Cada partido: id, deporte, liga, nombre, inicio, mercado, cuotas."""
    partidos, notas = [], []
    # Que falten al menos 20 minutos para el inicio, para que no empiece durante la prueba.
    desde = datetime.fromtimestamp(time.time() + 20 * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for ruta, nombre, cuantos in DEPORTES:
        try:
            d = _get(f"listView/{ruta}/all/all/all/matches.json")
        except (requests.RequestException, ValueError) as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            notas.append(f"{nombre}: no se pudo leer ({type(e).__name__}{f', código {code}' if code else ''})")
            continue
        candidatos = []
        for item in d.get("events", []):
            ev = item.get("event", {})
            if ev.get("state") != "NOT_STARTED":
                continue  # solo antes del inicio: nada en vivo
            if any("esports" in (x.get("termKey") or "") for x in ev.get("path", [])):
                continue  # sin deportes virtuales (e-fútbol, etc.)
            if (ev.get("start") or "") < desde:
                continue
            oferta = _oferta_principal(item.get("betOffers"))
            cuotas = _cuotas(oferta)
            if not cuotas:
                continue
            candidatos.append({"id": ev.get("id"), "deporte": nombre, "liga": ev.get("group", ""),
                               "partido": ev.get("name", ""), "inicio": ev.get("start", ""),
                               "mercado": (oferta.get("criterion") or {}).get("label", ""), "cuotas": cuotas})
        candidatos.sort(key=lambda p: p["inicio"])
        partidos.append((nombre, cuantos, candidatos))
        notas.append(f"{nombre}: {len(candidatos)} partidos próximos con cuota principal abierta")
    # Primero lo pedido por deporte; si faltan, se completa con lo que sobre (por fecha).
    elegidos, sobrantes = [], []
    for _, cuantos, cand in partidos:
        elegidos += cand[:cuantos]
        sobrantes += cand[cuantos:]
    sobrantes.sort(key=lambda p: p["inicio"])
    elegidos += sobrantes[:max(0, TOTAL - len(elegidos))]
    return elegidos[:TOTAL], notas


def cuotas_actuales(evento_id):
    """Vuelve a leer la cuota principal de un partido justo antes de abrir su página."""
    try:
        return _cuotas(_oferta_principal(_get(f"betoffer/event/{evento_id}.json").get("betOffers")))
    except (requests.RequestException, ValueError):
        return None


def _formas(cuota):
    """Cómo puede aparecer una cuota escrita en la página (1.85 / 1,85 / 14.00 / 14)."""
    f = {f"{cuota:.2f}", f"{cuota:.2f}".replace(".", ",")}
    if cuota >= 10 and float(cuota).is_integer():
        f.add(str(int(cuota)))
    t = f"{cuota:.2f}".rstrip("0").rstrip(".")
    f.update({t, t.replace(".", ",")})
    return f


def comparar(cuotas, texto, mercado):
    """Cuántas cuotas de la API aparecen tal cual en la página.

    Se busca primero en el bloque del mercado principal (desde su título, p. ej.
    «Resultado Final», unos 600 caracteres); si el título no aparece, en toda la
    página (comparación más floja). Devuelve (zona, [(opción, cuota, ¿aparece?)]).
    """
    i = texto.lower().find(mercado.lower()) if mercado else -1
    zona, trozo = ("bloque del mercado", texto[i:i + 600]) if i >= 0 else ("toda la página", texto)
    numeros = set(re.findall(r"(?<![\d.,])\d{1,3}(?:[.,]\d{1,2})?(?![\d.,])", trozo))
    return zona, [(etq, c, bool(_formas(c) & numeros)) for etq, c in cuotas]


def capturar(elegidos, carpeta):
    """Abre la página de 3 partidos (sin sesión) y toma captura. Devuelve la lista de resultados."""
    from playwright.sync_api import sync_playwright

    # Uno de fútbol y luego otros deportes distintos, si hay.
    orden, vistos = [], set()
    for p in elegidos:
        if p["deporte"] not in vistos:
            orden.append(p)
            vistos.add(p["deporte"])
    orden += [p for p in elegidos if p not in orden]
    resultados = []
    with sync_playwright() as pw:
        # PW_EXE solo sirve para probar en otra máquina con un Chromium ya instalado.
        nav = pw.chromium.launch(**({"executable_path": os.environ["PW_EXE"]} if os.environ.get("PW_EXE") else {}))
        # Contexto nuevo y vacío: sin cuenta, sin cookies, sin credenciales.
        ctx = nav.new_context(locale="es-CO", timezone_id="America/Bogota",
                              viewport={"width": 1366, "height": 900})
        for i, p in enumerate(orden[:CAPTURAS], 1):
            res = {"id": p["id"], "partido": p["partido"], "deporte": p["deporte"],
                   "url": PAGINA.format(id=p["id"]), "captura": None, "codigo_http": None,
                   "titulo": "", "bloqueo": [], "error": None, "comparacion": None}
            res["cuotas_api"] = cuotas_actuales(p["id"]) or p["cuotas"]
            pagina = ctx.new_page()
            try:
                resp = pagina.goto(res["url"], wait_until="domcontentloaded", timeout=60000)
                res["codigo_http"] = resp.status if resp else None
                try:
                    pagina.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass  # páginas que siguen cargando cosas: se sigue igual
                pagina.wait_for_timeout(10000)
                res["titulo"] = pagina.title()
                texto = pagina.inner_text("body")
                bajo = texto.lower()
                res["bloqueo"] = [w for w in SENALES_BLOQUEO if w in bajo]
                res["zona"], res["comparacion"] = comparar(res["cuotas_api"], texto, p["mercado"])
                res["nombre_visible"] = all(n.lower() in bajo for n in p["partido"].split(" - ")[:2])
                res["texto_inicio"] = " ".join(texto.split())[:400]
            except Exception as e:
                res["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            try:
                nombre = f"captura-{i}.png"
                pagina.screenshot(path=str(Path(carpeta) / nombre))
                res["captura"] = nombre
            except Exception as e:
                res["error"] = (res["error"] or "") + f" | sin captura: {type(e).__name__}"
            pagina.close()
            resultados.append(res)
        nav.close()
    return resultados


def informe(elegidos, notas, error_api, capturas, error_nav, momento):
    L = ["# Prueba de cuotas de la casa desde GitHub Actions", "",
         f"Fecha (UTC): {momento}", "",
         "Prueba separada del agente. Solo cuotas de antes del inicio. Sin iniciar sesión.", "",
         "## 1. API de cuotas de la casa (mercado CO)", ""]
    if error_api:
        L += [f"- No se pudo leer: {error_api}", ""]
    L += [f"- {n}" for n in notas] + ["", f"Partidos elegidos: {len(elegidos)}", "",
                                     "| Deporte | Liga | Partido | Inicio (UTC) | Mercado | Cuotas |",
                                     "|---|---|---|---|---|---|"]
    for p in elegidos:
        cu = " / ".join(f"{e}: {c:.2f}" for e, c in p["cuotas"])
        L.append(f"| {p['deporte']} | {p['liga']} | {p['partido']} | {p['inicio']} | {p['mercado']} | {cu} |")
    L += ["", "## 2. Página pública de la casa (navegador sin ventana)", "",
          "El servidor de GitHub está fuera de Colombia (normalmente en EE. UU.); "
          "si la página está bloqueada por región, eso es lo esperable y no se intentó saltar.", ""]
    if error_nav:
        L += [f"- No se pudo usar el navegador: {error_nav}", ""]
    for r in capturas:
        L += [f"### {r['deporte']}: {r['partido']}", "", f"- Dirección: {r['url']}",
              f"- Código HTTP: {r['codigo_http']}", f"- Título de la página: {r['titulo'] or '-'}",
              f"- Señales de bloqueo o verificación: {', '.join(r['bloqueo']) if r['bloqueo'] else 'ninguna'}",
              f"- Captura: {r['captura'] or 'no se pudo'}"]
        if r["error"]:
            L.append(f"- Error: {r['error']}")
        if r.get("texto_inicio"):
            L.append(f"- Lo primero que se lee en la página: «{r['texto_inicio']}»")
        if "nombre_visible" in r:
            L.append(f"- ¿Se ven los nombres del partido en la página?: {'sí' if r['nombre_visible'] else 'no'}")
        if r["comparacion"]:
            L.append(f"- Dónde se buscaron las cuotas: {r['zona']}")
            L += ["", "| Opción | Cuota API (leída justo antes) | ¿Aparece en la página? |", "|---|---|---|"]
            L += [f"| {e} | {c:.2f} | {'sí' if ok else 'no'} |" for e, c, ok in r["comparacion"]]
        L.append("")
    L += ["## 3. Cómo leer esto", "",
          "- «Aparece en la página» quiere decir que ese mismo número se ve escrito en la página. "
          "Si la página está bloqueada o no carga las cuotas, sale «no» aunque la API esté bien.",
          "- La cuota puede cambiar entre la lectura de la API y la carga de la página (segundos).",
          "- Recuerda verificar siempre la cuota real en la casa antes de apostar.", ""]
    return "\n".join(L)


def main(carpeta):
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    if not API.startswith("https://") or not PAGINA.startswith("https://") or "{id}" not in PAGINA:
        # No se imprime el valor: solo qué falta.
        log("FALLÓ: faltan o están mal los secretos CASA_API_URL o CASA_PAGINA_URL "
            "(deben empezar por https:// y CASA_PAGINA_URL debe llevar {id})")
        return 1
    momento = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    elegidos, notas, error_api = [], [], None
    try:
        elegidos, notas = leer_api()
    except Exception as e:
        error_api = type(e).__name__
    api_ok = bool(elegidos)
    log(f"API: {'leída' if api_ok else 'NO leída'}; partidos elegidos: {len(elegidos)}")

    capturas, error_nav = [], None
    if elegidos:
        try:
            capturas = capturar(elegidos, carpeta)
        except Exception as e:
            error_nav = f"{type(e).__name__}: {str(e)[:200]}"
    n_cap = sum(1 for r in capturas if r["captura"])
    n_bloq = sum(1 for r in capturas if r["bloqueo"])
    comparadas = [r for r in capturas if r["comparacion"]]
    total_c = sum(len(r["comparacion"]) for r in comparadas)
    iguales = sum(ok for r in comparadas for _, _, ok in r["comparacion"])
    log(f"Capturas: {n_cap} de {min(CAPTURAS, len(elegidos))}; con señales de bloqueo: {n_bloq}; "
        f"cuotas que coinciden: {iguales} de {total_c}")

    (carpeta / "informe.md").write_text(informe(elegidos, notas, error_api, capturas, error_nav, momento),
                                        encoding="utf-8")
    (carpeta / "resumen.json").write_text(json.dumps(
        {"momento_utc": momento, "api_ok": api_ok, "partidos": elegidos, "notas": notas,
         "capturas": capturas, "error_navegador": error_nav}, ensure_ascii=False, indent=1), encoding="utf-8")

    (carpeta / "conteo.json").write_text(json.dumps(
        {"api_ok": api_ok, "partidos": len(elegidos), "capturas": n_cap, "capturas_pedidas": min(CAPTURAS, len(elegidos)),
         "bloqueo": n_bloq, "cuotas_iguales": iguales, "cuotas_comparadas": total_c}), encoding="utf-8")
    return 0 if api_ok else 1


def avisar(carpeta, guardado):
    """Resumen corto por Telegram (sin enlaces ni nombres de repositorios)."""
    import f5_telegram as T
    try:
        c = json.loads((Path(carpeta) / "conteo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        c = None
    if c is None:
        texto = "PRUEBA — no apostar\n\nPrueba cuotas casa: la prueba falló antes de terminar. Revisa el registro en Actions."
    else:
        if c["cuotas_comparadas"] == 0:
            coinciden = "no se pudo comparar (la página no mostró las cuotas)"
        elif c["cuotas_iguales"] == c["cuotas_comparadas"]:
            coinciden = f"sí, {c['cuotas_iguales']} de {c['cuotas_comparadas']}"
        else:
            coinciden = f"en parte, {c['cuotas_iguales']} de {c['cuotas_comparadas']}"
        texto = ("PRUEBA — no apostar\n\nPrueba cuotas casa desde GitHub Actions\n\n"
                 f"• ¿Se leyó la API? {'sí' if c['api_ok'] else 'no'}\n"
                 f"• Partidos leídos: {c['partidos']}\n"
                 f"• Capturas: {c['capturas']} de {c['capturas_pedidas']}"
                 + (f" ({c['bloqueo']} con señales de bloqueo o verificación)" if c["bloqueo"] else "") + "\n"
                 f"• ¿Coinciden las cuotas? {coinciden}\n\n"
                 + ("Informe y capturas guardados en el repositorio privado."
                    if guardado == "ok" else "OJO: no se pudo guardar el informe en el repositorio privado."))
    log(f"Telegram: {'enviado' if T.enviar(texto) else 'no enviado'}")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "avisar":
        avisar(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 2:
        sys.exit(main(sys.argv[1]))
    else:
        print(__doc__)
        sys.exit(2)
