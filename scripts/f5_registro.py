"""
Fase 5: registro de cuotas y alertas.

Cuotas (data/fase5/cuotas/):
- catalogo_<día>_<parte>.jsonl.gz: una línea por cuota nueva con qué es
  (partido, mercado, jugador, línea, casa). Se escribe una sola vez.
- cambios_<día>_<parte>.jsonl.gz: una línea por CAMBIO, nunca fotos repetidas:
    [hora, k, cuota, estado]
  estado: OPEN (abierta), SUSPENDED (suspendida), QUITADA (Kambi la sacó)
  y ULTIMA (la última vez que se vio antes del inicio; se escribe al cerrar).
  La primera vez que se vio es su primer cambio.
- Solo cuotas de antes del inicio: al empezar el partido se deja de guardar.
- Los archivos se parten antes de 50 MB.

Partidos: data/fase5/eventos.jsonl.gz
Alertas: data/fase5/alertas.jsonl.gz, con un registro al abrir y otro al cerrar.
Estado (para reanudar sin repetir cuotas): data/fase5/estado.json.gz
"""
import gzip
import json
import os
import time
from datetime import datetime, timezone

import f5_config as C


def ahora_iso(t=None):
    return datetime.fromtimestamp(t or time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_a_ts(iso):
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


class ArchivoPartido:
    """Archivo .jsonl.gz que se agrega por tandas y se parte antes de 50 MB."""

    def __init__(self, carpeta, prefijo):
        self.carpeta, self.prefijo = carpeta, prefijo
        self.buffer = []

    def _ruta(self):
        dia = time.strftime("%Y%m%d", time.gmtime())
        parte = 1
        while True:
            ruta = self.carpeta / f"{self.prefijo}_{dia}_{parte:02d}.jsonl.gz"
            if not ruta.exists() or ruta.stat().st_size < C.ARCHIVO_MAX_MB * 1e6:
                return ruta
            parte += 1

    def agregar(self, obj):
        self.buffer.append(obj)

    def volcar(self):
        if not self.buffer:
            return
        self.carpeta.mkdir(parents=True, exist_ok=True)
        with gzip.open(self._ruta(), "at", encoding="utf-8") as fh:
            for o in self.buffer:
                fh.write(json.dumps(o, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.buffer = []


class Registro:
    def __init__(self):
        self.dir = C.DATA
        self.dir.mkdir(parents=True, exist_ok=True)
        self.catalogo = ArchivoPartido(self.dir / "cuotas", "catalogo")
        self.cambios = ArchivoPartido(self.dir / "cuotas", "cambios")
        self.eventos_f = ArchivoPartido(self.dir, "eventos")
        self.alertas_f = ArchivoPartido(self.dir / "alertas", "alertas")
        # k -> [cuota, estado, primera vez, última vez, id del partido]
        self.cuotas = {}
        self.eventos = {}        # id -> info
        self.cerrados = set()    # partidos que ya empezaron
        self.alertas_abiertas = {}
        self.avisados = {}       # clave -> hora del último aviso
        self.contador = {"cambios": 0, "nuevas": 0}
        self._cargar()

    # ------------------------------------------------------------------ estado
    def _cargar(self):
        ruta = self.dir / "estado.json.gz"
        if not ruta.exists():
            return
        with gzip.open(ruta, "rt", encoding="utf-8") as fh:
            e = json.load(fh)
        self.cuotas = {int(k): v for k, v in e.get("cuotas", {}).items()}
        self.eventos = {int(k): v for k, v in e.get("eventos", {}).items()}
        self.cerrados = set(e.get("cerrados", []))
        self.alertas_abiertas = e.get("alertas_abiertas", {})
        self.avisados = e.get("avisados", {})

    def guardar(self):
        for a in (self.catalogo, self.cambios, self.eventos_f, self.alertas_f):
            a.volcar()
        # Se olvidan partidos cerrados hace más de 2 días
        limite = time.time() - 2 * 86400
        for evid in [e for e, i in self.eventos.items() if i.get("_ts", 0) < limite]:
            self.eventos.pop(evid, None)
            self.cerrados.discard(evid)
        e = {"cuotas": self.cuotas, "eventos": self.eventos, "cerrados": sorted(self.cerrados),
             "alertas_abiertas": self.alertas_abiertas,
             "avisados": {k: v for k, v in self.avisados.items() if v > time.time() - 86400},
             "guardado": ahora_iso()}
        tmp = self.dir / "estado.json.gz.tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(e, fh, separators=(",", ":"))
        os.replace(tmp, self.dir / "estado.json.gz")

    # ------------------------------------------------------------------ partidos
    def evento(self, info):
        evid = info["id"]
        info = dict(info, _ts=iso_a_ts(info["inicio"]))
        viejo = self.eventos.get(evid)
        if not viejo or viejo.get("inicio") != info["inicio"]:
            self.eventos_f.agregar({**info, "visto": ahora_iso()})
        self.eventos[evid] = info

    def empezo(self, evid):
        info = self.eventos.get(evid)
        return evid in self.cerrados or (info and time.time() >= info["_ts"])

    def cerrar_evento(self, evid):
        """El partido empezó: se anota la última vez que se vio cada cuota."""
        if evid in self.cerrados:
            return
        self.cerrados.add(evid)
        for k in [k for k, v in self.cuotas.items() if v[4] == evid]:
            v = self.cuotas.pop(k)
            self.cambios.agregar([ahora_iso(v[3]), k, v[0], "ULTIMA"])

    # ------------------------------------------------------------------ cuotas
    def cuotas_leidas(self, filas, op, completo, ids_eventos=None):
        """
        Registra una lectura. Si `completo` es True, la lectura trae TODOS los
        mercados de esos partidos, así que lo que falte se marca como QUITADA.
        """
        t = time.time()
        vistas = set()
        for f in filas:
            evid = f["ev"]
            if self.empezo(evid):
                continue
            k = f["k"]
            vistas.add(k)
            v = self.cuotas.get(k)
            if v is None:
                self.catalogo.agregar({
                    "k": k, "op": op, "ev": evid, "bo": f["bo"], "crit": f["crit"], "crit_es": f["crit_es"],
                    "tipo": f["tipo"], "lbl": f["lbl"], "lbl_es": f["lbl_es"], "part": f["part"],
                    "pid": f["pid"], "line": f["line"], "otype": f["otype"]})
                self.cuotas[k] = [f["odds"], f["st"], t, t, evid]
                self.cambios.agregar([ahora_iso(t), k, f["odds"], f["st"]])
                self.contador["nuevas"] += 1
            else:
                if v[0] != f["odds"] or v[1] != f["st"]:
                    self.cambios.agregar([ahora_iso(t), k, f["odds"], f["st"]])
                    self.contador["cambios"] += 1
                    v[0], v[1] = f["odds"], f["st"]
                v[3] = t
        if completo and ids_eventos:
            ids = set(ids_eventos)
            for k, v in list(self.cuotas.items()):
                if v[4] in ids and k not in vistas and v[1] != "QUITADA":
                    self.cambios.agregar([ahora_iso(t), k, v[0], "QUITADA"])
                    v[1] = "QUITADA"

    # ------------------------------------------------------------------ alertas
    def alerta_abre(self, h, extra):
        reg = {**h, **extra, "evento": "abre"}
        self.alertas_abiertas[h["clave"]] = reg
        self.alertas_f.agregar(reg)

    def alerta_cierra(self, clave, motivo, cuota_final=None, fin=None):
        a = self.alertas_abiertas.pop(clave, None)
        if not a:
            return None
        fin = fin or time.time()
        dur = fin - a["detectado_ts"]
        reg = {"evento": "cierra", "clave": clave, "id": a["id"], "fin": ahora_iso(fin),
               "duracion_seg": round(dur), "motivo": motivo, "cuota_final": cuota_final,
               "alcanzable": dur >= C.ALCANZABLE_SEG}
        # Lo que cambió mientras estuvo abierta (aviso enviado, cuota estimada, etc.)
        for campo in ("avisado", "avisado_ts", "avisable", "avisable_desde", "cuota_max", "cuota_rb",
                      "rango_rb", "justa_rb", "ventaja_rb", "en_rango"):
            if campo in a:
                reg[campo] = a[campo]
        self.alertas_f.agregar(reg)
        return reg
