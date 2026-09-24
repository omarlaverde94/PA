"""
Fase 5: imagen tipo tarjeta para cada alerta (limpia y fácil de leer en el celular).

Muestra lo mismo que el texto corto: apuesta, partido y hora, cuánto paga tu
casa (estimado) contra cuánto debería pagar, ventaja y confianza. Sin logos
ni marcas. Si no se puede dibujar (falta Pillow o una fuente), devuelve None y
el agente manda solo el texto.
"""
import io

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # sin Pillow: solo texto
    Image = None

ANCHO = 1080
MARGEN = 64
COLOR = {"fondo": "#F6F7F9", "tarjeta": "#FFFFFF", "texto": "#111827", "suave": "#6B7280",
         "linea": "#E5E7EB", "verde": "#15803D", "verde_suave": "#DCFCE7", "gris_suave": "#F3F4F6",
         "alta": "#15803D", "media": "#B45309", "baja": "#B91C1C"}
_FUENTES = {
    False: ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"],
    True: ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
}


def _fuente(tam, negrita=False):
    for ruta in _FUENTES[negrita]:
        try:
            return ImageFont.truetype(ruta, tam)
        except OSError:
            continue
    return ImageFont.load_default(size=tam)


def _partir(dibujo, texto, fuente, ancho):
    """Parte un texto en renglones que quepan en el ancho."""
    renglones, actual = [], ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if dibujo.textlength(prueba, font=fuente) <= ancho:
            actual = prueba
        else:
            if actual:
                renglones.append(actual)
            actual = palabra
    if actual:
        renglones.append(actual)
    return renglones[:3]


def tarjeta(bloques, pie="PRUEBA · no apostar · verifica la cuota real antes de apostar"):
    """
    bloques: lista de dicts con apuesta, partido, hora, casa, paga, justa, ventaja,
    confianza. Devuelve los bytes PNG o None.
    """
    if Image is None or not bloques:
        return None
    try:
        f_titulo, f_normal = _fuente(46, True), _fuente(32)
        f_grande, f_chico = _fuente(64, True), _fuente(28)
        prueba = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        interior = ANCHO - 2 * MARGEN - 2 * 48
        # Alto de cada bloque según cuántos renglones ocupa la apuesta
        partes = []
        for b in bloques:
            titulo = _partir(prueba, b["apuesta"], f_titulo, interior - 40)
            partes.append((b, titulo, 48 + 58 * len(titulo) + 50 + 110 + 80 + 40))
        alto = MARGEN + 50 + sum(p[2] + 28 for p in partes) + 40 + MARGEN
        img = Image.new("RGB", (ANCHO, alto), COLOR["fondo"])
        d = ImageDraw.Draw(img)
        d.text((MARGEN, MARGEN), "PRUEBA · no apostar", font=f_chico, fill=COLOR["suave"])
        y = MARGEN + 50
        for b, titulo, h in partes:
            x0, x1 = MARGEN, ANCHO - MARGEN
            d.rounded_rectangle((x0, y, x1, y + h), radius=36, fill=COLOR["tarjeta"], outline=COLOR["linea"], width=2)
            x, yy = x0 + 48, y + 44
            d.ellipse((x, yy + 14, x + 26, yy + 40), fill=COLOR["verde"])
            for i, r in enumerate(titulo):
                d.text((x + 42, yy + 58 * i), r, font=f_titulo, fill=COLOR["texto"])
            yy += 58 * len(titulo) + 8
            d.text((x, yy), f"{b['partido']} · {b['hora']}", font=f_normal, fill=COLOR["suave"])
            yy += 64
            # Paga -> debería pagar
            d.rounded_rectangle((x, yy, x1 - 48, yy + 104), radius=24, fill=COLOR["verde_suave"])
            d.text((x + 28, yy + 8), f"{b['casa']} paga", font=f_chico, fill=COLOR["verde"])
            d.text((x + 28, yy + 36), f"{b['paga']:.2f}", font=f_grande, fill=COLOR["verde"])
            mitad = x + (x1 - 48 - x) // 2
            d.text((mitad - 30, yy + 34), "→", font=f_grande, fill=COLOR["suave"])
            d.text((mitad + 60, yy + 8), "debería pagar", font=f_chico, fill=COLOR["suave"])
            d.text((mitad + 60, yy + 36), f"{b['justa']:.2f}", font=f_grande, fill=COLOR["texto"])
            yy += 128
            # Ventaja y confianza
            chip1 = f"Ventaja {b['ventaja']:+.1%}"
            chip2 = f"Confianza {b['confianza']}"
            w1 = d.textlength(chip1, font=f_normal) + 48
            d.rounded_rectangle((x, yy, x + w1, yy + 56), radius=28, fill=COLOR["gris_suave"])
            d.text((x + 24, yy + 10), chip1, font=f_normal, fill=COLOR["texto"])
            w2 = d.textlength(chip2, font=f_normal) + 48
            d.rounded_rectangle((x + w1 + 16, yy, x + w1 + 16 + w2, yy + 56), radius=28, fill=COLOR["gris_suave"])
            d.text((x + w1 + 40, yy + 10), chip2, font=f_normal, fill=COLOR.get(b["confianza"], COLOR["texto"]))
            y += h + 28
        d.text((MARGEN, y + 4), pie, font=f_chico, fill=COLOR["suave"])
        salida = io.BytesIO()
        img.save(salida, "PNG", optimize=True)
        return salida.getvalue()
    except Exception:  # una imagen fallida no debe frenar el aviso
        return None
