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
  (1.50 a 2.00), y
- tiene que seguir por encima del precio justo según la regla que la detectó.
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


class Estimador:
    def __init__(self, ruta=None):
        ruta = ruta or os.environ.get("F5_AJUSTE") or str(C.DATA / "ajuste_cuotas.json.gz")
        self.tabla, self.casa, self.ok = {}, "tu casa de apuestas", False
        try:
            with gzip.open(ruta, "rt", encoding="utf-8") as fh:
                d = json.load(fh)
            self.tabla = d["tabla"]
            self.casa = d.get("casa") or self.casa
            self.ok = bool(self.tabla)
        except (OSError, ValueError, KeyError, EOFError):
            pass

    def _fila(self, f, deporte):
        r = rango(f["odds"])
        for c in (f"{deporte}|{grupo(f)}|{r}", f"{deporte}|*|{r}", f"*|*|{r}", "*|*|*"):
            if c in self.tabla:
                return self.tabla[c]
        return None

    def estimar(self, f, deporte):
        """(cuota estimada, rango donde cae el 80% de los casos) o (None, None)."""
        t = self._fila(f, deporte) if self.ok else None
        if not t:
            return None, None
        return round(f["odds"] * t["razon"], 2), (round(f["odds"] * t["bajo"], 2), round(f["odds"] * t["alto"], 2))

    def evaluar(self, h, por_k, deporte):
        """Campos de la señal medidos con la cuota estimada en tu casa."""
        f = por_k.get(h["k"])
        est, rango80 = self.estimar(f, deporte) if f else (None, None)
        salida = {"casa": self.casa, "cuota_rb": est, "rango_rb": rango80, "justa_rb": None,
                  "ventaja_rb": None, "avisable": False,
                  "en_rango": bool(est and C.RANGO_PREFERIDO[0] <= est <= C.RANGO_PREFERIDO[1])}
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
        salida["avisable"] = bool(ok and salida["en_rango"])
        return salida
