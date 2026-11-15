"""
Fase 5: estimación de la cuota de tu casa de apuestas a partir de la de Kambi.

Tu casa usa la misma plataforma (Kambi) pero cobra más margen, así que suele
pagar un poco menos que la cuota que lee el agente. Una tabla medida con datos
históricos (razón "cuota de tu casa ÷ cuota de Kambi" por deporte, tipo de
apuesta y rango de cuota) permite estimarla.

La tabla NO está en este código: se lee de un archivo (variable F5_AJUSTE)
que la tarea de GitHub trae del repositorio privado de datos. Sin tabla no
hay estimación y el agente no envía alertas (solo registra).

Con la estimación se decide si una señal merece alerta:
- la cuota estimada en tu casa tiene que estar dentro del rango de alertas
  (por defecto 1.40 a 2.00; se cambia con el comando "rango"), y
- tiene que seguir por encima del precio justo según la regla que la detectó, y
- la ventaja contra Pinnacle (cuota de Kambi ÷ precio justo de Pinnacle − 1, la misma
  medida de siempre) no puede pasar de 15 %: más que eso casi siempre es un error de
  emparejamiento o una noticia que el mercado ya sabe. Esas se registran como
  "descartada por ventaja excesiva". De 10 % a 15 % se envían con un aviso.
"""
import gzip
import json
import os

import f5_config as C
from f5_fuentes import es_ganador

_UMBRAL = {"mercado": C.UMBRAL_MERCADO, "par": C.UMBRAL_PAR, "modelo": C.UMBRAL_MODELO}


def rango(cuota):
    if cuota < 1.5:
        return "<1.50"
    if cuota <= 2.0:
        return "1.50-2.00"
    if cuota <= 3.0:
        return "2.00-3.00"
    if cuota <= 6.0:
        return "3.00-6.00"
    return ">6.00"


def grupo(f):
    """Tipo de apuesta con los mismos grupos de la tabla."""
    if f.get("part") and f["tipo"] == "Player Occurrence Line":
        return "jugador"
    crit = f["crit"].split(" (")[0]
    per = crit.split(" - ", 1)[1].lower() if " - " in crit else ""
    if per not in ("", "including overtime", "including extra innings", "full time", "regular time"):
        return "otros"
    if es_ganador(f):
        return "ganador"
    if f["tipo"] == "Over/Under" and f.get("nsal") == 2 and " by " not in crit:
        return "totales"
    if f["tipo"] in ("Handicap", "Asian Handicap") and f.get("nsal") == 2:
        return "handicap"
    return "otros"


def ventaja_pinnacle(h):
    """Cuánto paga Kambi por encima del precio justo de Pinnacle (la medida de siempre), o None
    si Pinnacle no tiene esa apuesta. En las de "mercado" es la misma ventaja de la detección."""
    if h.get("regla") == "mercado" and h.get("ventaja") is not None:
        return h["ventaja"]
    if h.get("pin_justa") and h.get("cuota"):
        return round(h["cuota"] / h["pin_justa"] - 1, 4)
    return None


def nivel_ventaja(v):
    """'normal' (menos de 10 %), 'alta' (10 % a 15 %) o 'excesiva' (más de 15 %)."""
    if v is None or v < C.VENTAJA_ALTA:
        return "normal"
    if v <= C.VENTAJA_MAX:
        return "alta"
    return "excesiva"


def en_rango(cuota):
    return bool(cuota) and C.RANGO_PREFERIDO[0] <= cuota <= C.RANGO_PREFERIDO[1]


class Estimador:
    """
    Prueba nueva (cuotas reales): si la API de tu casa está configurada (CASA_API_URL),
    la cuota leída YA es la real, así que no se estima nada: "cuota_rb" es la cuota real
    y no hay rango del 80 %. La tabla solo se usa si no hay API (modo viejo, ya no se usa
    para alertas). Del archivo se siguen leyendo el nombre de la casa y el enlace.
    """

    def __init__(self, ruta=None, real=None):
        ruta = ruta or os.environ.get("F5_AJUSTE") or str(C.DATA / "ajuste_cuotas.json.gz")
        self.tabla, self.casa, self.ok, self.enlace = {}, "tu casa de apuestas", False, None
        self.real = C.casa_configurada() if real is None else real
        try:
            with gzip.open(ruta, "rt", encoding="utf-8") as fh:
                d = json.load(fh)
            self.tabla = d["tabla"]
            self.casa = d.get("casa") or self.casa
            enlace = d.get("enlace")
            if isinstance(enlace, str) and enlace.startswith("https://") and "{id}" in enlace:
                self.enlace = enlace
            self.ok = bool(self.tabla)
        except (OSError, ValueError, KeyError, EOFError):
            pass
        if self.real:
            self.ok = True

    def enlace_partido(self, evid):
        """Enlace para abrir el partido en tu casa (mismo identificador de Kambi), o None."""
        if not self.enlace or not str(evid).isdigit():
            return None
        return self.enlace.replace("{id}", str(int(evid)))

    def _fila(self, f, deporte):
        r = rango(f["odds"])
        for c in (f"{deporte}|{grupo(f)}|{r}", f"{deporte}|*|{r}", f"*|*|{r}", "*|*|*"):
            if c in self.tabla:
                return self.tabla[c]
        return None

    def estimar(self, f, deporte):
        """(cuota estimada, rango donde cae el 80% de los casos) o (None, None).
        Con cuotas reales: (la cuota real, None)."""
        if self.real:
            return (round(f["odds"], 2), None) if f.get("odds") else (None, None)
        t = self._fila(f, deporte) if self.tabla else None
        if not t:
            return None, None
        return round(f["odds"] * t["razon"], 2), (round(f["odds"] * t["bajo"], 2), round(f["odds"] * t["alto"], 2))

    def evaluar(self, h, por_k, deporte):
        """Campos de la señal medidos con la cuota estimada en tu casa."""
        f = por_k.get(h["k"])
        est, rango80 = self.estimar(f, deporte) if f else (None, None)
        vp = ventaja_pinnacle(h)
        nivel = nivel_ventaja(vp)
        salida = {"casa": self.casa, "cuota_rb": est, "real": self.real, "rango_rb": rango80, "justa_rb": None,
                  "ventaja_rb": None, "avisable": False, "en_rango": en_rango(est),
                  "ventaja_pin": vp, "ventaja_alta": nivel == "alta",
                  "descartada": C.MOTIVO_VENTAJA_EXCESIVA if nivel == "excesiva" else None}
        if not est:
            return salida
        regla, justa, ok = h["regla"], h.get("justa"), False
        if regla in _UMBRAL and justa:
            ok = est / justa - 1 >= _UMBRAL[regla]
        elif regla == "escalera" and h.get("k_ref") in por_k:
            justa, _ = self.estimar(por_k[h["k_ref"]], deporte)
            ok = bool(justa) and est > justa * (1 + C.TOLERANCIA_ESCALERA)
        elif regla == "arbitraje":
            otras = [self.estimar(por_k[k], deporte)[0] for k in h.get("ks_otros", []) if k in por_k]
            if otras and all(otras):
                suma_otras = sum(1 / o for o in otras)
                justa = 1 / (1 - suma_otras) if suma_otras < 1 else None
                ok = suma_otras + 1 / est < C.UMBRAL_ARBITRAJE
        if justa:
            salida["justa_rb"] = round(justa, 3)
            salida["ventaja_rb"] = round(est / justa - 1, 4)
        salida["avisable"] = bool(ok and salida["en_rango"] and nivel != "excesiva")
        return salida
