"""
Fase 5: configuración de la prueba en papel.

Todo lo que se puede ajustar sin tocar la lógica está aquí: qué se lee,
cada cuánto, qué tan grande tiene que ser un error para avisar y dónde
se guarda. Las claves NO van aquí: se leen de variables de entorno
(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).
"""
import os
from datetime import timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("F5_DATA", RAIZ / "data" / "fase5"))

# Hora local de los avisos y del resumen diario (UTC-5).
HORA_LOCAL = timezone(timedelta(hours=-5))

# --- Cuotas de tu casa ----------------------------------------------------------------
# Prueba nueva: solo cuotas REALES de tu casa, leídas de su API pública de cuotas
# (plataforma Kambi, mercado CO). La dirección base NO está en el código: se lee del
# secreto CASA_API_URL (sin "/" al final). Sin ella el agente no lee ni avisa nada.
CASA_API_URL = os.environ.get("CASA_API_URL", "").strip().rstrip("/")
CASA_PARAMS = {"lang": "es_ES", "market": "CO", "useCombined": "true"}
# Otras casas de Kambi (solo como respaldo para marcadores en vivo si hiciera falta).
KAMBI_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/{op}/{ruta}"
KAMBI_PARAMS = {"lang": "es_ES", "market": "GB", "useCombined": "true"}
KAMBI_PRINCIPAL = "casa"      # "casa" = la API de tu casa (CASA_API_URL)
# Nombre de la casa en las explicaciones (el agente pone el nombre real al arrancar).
NOMBRE_CASA = ["tu casa"]


def casa_configurada():
    return CASA_API_URL.startswith("https://")


# Deportes excluidos de las alertas: tenis de mesa (descartado en fases anteriores).
# Se reconocen por la clave de deporte de Kambi; nunca se envía una alerta de ellos.
DEPORTES_EXCLUIDOS = {"table_tennis", "TABLE_TENNIS"}


def deporte_excluido(info):
    return (info.get("deporte") not in DEPORTES or info.get("deporte_kambi") in DEPORTES_EXCLUIDOS
            or "table tennis" in (info.get("liga") or "").lower())


# Duración de la prueba nueva y día del informe intermedio (se cuentan desde la primera tanda).
DIAS_PRUEBA = float(os.environ.get("F5_DIAS", "14"))
DIA_INFORME_INTERMEDIO = 7
# Después del día 14 se siguen liquidando las alertas que falten (sin leer cuotas nuevas)
# hasta este número de horas; luego se envía el informe final completo.
HORAS_LIQUIDAR_DESPUES = 48

# Relectura de la cuota justo antes de enviar una alerta: si está suspendida o quitada, la API
# no respondió o el partido empezó, NO se envía. Si la cuota cambió, se recalcula con la cuota
# nueva y solo se envía si sigue cumpliendo todas las reglas.
# Seguimiento: minutos después de cada alerta enviada en que se vuelve a leer la cuota
# para medir si se mantuvo (solo antes del inicio del partido).
SEGUIMIENTO_MIN = (2, 5, 15)
# Pausa mínima entre dos consultas a Kambi (segundos). Con esto el agente
# hace en promedio menos de una consulta por segundo.
KAMBI_PAUSA = float(os.environ.get("F5_KAMBI_PAUSA", "0.8"))
# Partidos por consulta de mercados completos (Kambi acepta varios a la vez).
KAMBI_LOTE = 4

# Deportes: nombre corto -> ruta de la lista de Kambi y datos de Pinnacle.
DEPORTES = {
    "nfl": {"kambi": "american_football/nfl", "pin_ligas": [889], "nombre": "NFL", "emoji": "🏈"},
    "nba": {"kambi": "basketball/nba", "pin_ligas": [487], "nombre": "NBA", "emoji": "🏀"},
    "mlb": {"kambi": "baseball/mlb", "pin_ligas": [246], "nombre": "MLB", "emoji": "⚾"},
    "futbol": {"kambi": "football", "pin_deporte": 29, "nombre": "Fútbol", "emoji": "⚽"},
}

# Cada cuánto se leen los mercados completos de un partido, según cuánto
# falta para que empiece (minutos hasta el inicio, segundos entre lecturas).
FRECUENCIA = [
    (90, 60),        # empieza en menos de 90 min: cada minuto
    (6 * 60, 240),   # en menos de 6 h: cada 4 min
    (24 * 60, 900),  # en menos de 24 h: cada 15 min
    (72 * 60, 3600), # en menos de 3 días: cada hora
]
# Fútbol: los mercados completos solo se leen dentro de estas horas antes
# del inicio (son cientos de partidos). Antes de eso se usa la lista general,
# que ya trae ganador y total de goles de todos los partidos.
FUTBOL_HORAS_COMPLETO = 6
LISTA_CADA = 120  # segundos entre lecturas de la lista general de cada deporte

# --- Pinnacle (consenso del mercado) -------------------------------------------
PIN_URL = "https://guest.api.arcadia.pinnacle.com/0.1"
PIN_PAUSA = 1.5
PIN_PARTIDOS_CADA = 1800      # lista de partidos de fútbol de Pinnacle (pesada)
PIN_PARTIDOS_EEUU_CADA = 600  # lista de partidos de NFL/NBA/MLB
PIN_CUOTAS_CERCA = 90         # cuotas cuando hay partidos en menos de 90 min
PIN_CUOTAS_LEJOS = 360
PIN_VIEJA = 300               # cuota de Pinnacle más vieja que esto no se usa
# Emparejar un partido de Kambi con uno de Pinnacle (ver f5_emparejar.py):
PIN_MARGEN_INICIO = 10 * 60   # la hora de inicio tiene que coincidir con este margen (segundos)

# --- Detector ------------------------------------------------------------------
# a) Contra el mercado: Kambi paga al menos esto por encima del precio justo
#    de Pinnacle (sin margen).
UMBRAL_MERCADO = float(os.environ.get("F5_UMBRAL_MERCADO", "0.06"))
# b) Contradicciones internas de Kambi.
TOLERANCIA_ESCALERA = 0.02    # "más fácil" pagando más que "más difícil"
UMBRAL_PAR = 0.10             # apuesta que paga más que el precio justo de su par más/menos
UMBRAL_MODELO = 0.30          # contra la escalera estimada de conteo (Poisson)
UMBRAL_ARBITRAJE = 0.995      # suma de probabilidades de un par menor a esto
CUOTA_MAX_ALERTA = 10.0       # más arriba casi todo es ruido (pruebas anteriores)
# Tope de ventaja contra Pinnacle (cuota de Kambi ÷ precio justo de Pinnacle − 1):
VENTAJA_ALTA = 0.10           # desde aquí la alerta lleva el aviso de "ventaja alta"
VENTAJA_MAX = 0.15            # por encima de esto no se envía: se registra como "ventaja excesiva"
AVISO_VENTAJA_ALTA = "⚠️ Ventaja alta: revisa noticias y alineaciones antes de apostar"
MOTIVO_VENTAJA_EXCESIVA = "descartada por ventaja excesiva"
# Solo se envían ALERTAS si la cuota ESTIMADA en tu casa cae en el rango vigente y sigue
# por encima del precio justo (ver f5_estimacion.py). Lo demás solo se registra.
RANGO_DEFECTO = (1.40, 2.00)
RANGO_LIMITES = (1.20, 3.00)   # el comando "rango" solo acepta valores dentro de estos límites
RANGO_PREFERIDO = list(RANGO_DEFECTO)  # rango vigente; se cambia con el comando "rango"
_ARCHIVO_RANGO = "rango_alertas.txt"   # se guarda con los datos para mantenerlo entre tandas


def rango_valido(a, b):
    return isinstance(a, float) and isinstance(b, float) and RANGO_LIMITES[0] <= a < b <= RANGO_LIMITES[1]


def texto_rango():
    return f"{RANGO_PREFERIDO[0]:.2f} y {RANGO_PREFERIDO[1]:.2f}"


def cargar_rango():
    """Lee el rango guardado; si falta o no es válido, se usa el de defecto."""
    try:
        a, b = (float(x) for x in (DATA / _ARCHIVO_RANGO).read_text().split())
    except (OSError, ValueError):
        a, b = RANGO_DEFECTO
    if not rango_valido(a, b):
        a, b = RANGO_DEFECTO
    RANGO_PREFERIDO[:] = [a, b]
    return a, b


def guardar_rango(a, b):
    if not rango_valido(a, b):
        raise ValueError("rango fuera de los límites")
    RANGO_PREFERIDO[:] = [a, b]
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / _ARCHIVO_RANGO).write_text(f"{a:.2f} {b:.2f}\n")


# Un error que sigue abierto después de esto se da por "alcanzable".
ALCANZABLE_SEG = 120
# No repetir el mismo aviso (misma apuesta y tipo) antes de esto.
REPETIR_AVISO_SEG = 6 * 3600
# Tope de avisos por hora; los demás van solo al resumen diario.
MAX_AVISOS_HORA = 20

# --- Resultados y liquidación (ver f5_liquidacion.py) ------------------------------
# API-Football (plan gratis, 100 consultas al día): la clave se lee de API_FOOTBALL_KEY
# y nunca se imprime ni se guarda. Sin clave, el fútbol se liquida con Kambi y ESPN.
APIFOOT_URL = "https://v3.football.api-sports.io"
APIFOOT_CADA = 3600           # una misma fecha no se vuelve a consultar antes de esto (segundos)
ESPN_URL = "https://site.web.api.espn.com/apis/site/v2/sports/"
# Desde cuándo se intenta liquidar (minutos después del inicio) y cada cuánto se reintenta.
LIQ_ESPERA_MIN = {"futbol": 115, "mlb": 180, "nfl": 200, "nba": 150}
LIQ_REINTENTO = 15 * 60
LIQ_UNA_FUENTE = 6 * 3600     # si a las 6 h solo hay una fuente (sin contradicción), se usa esa
LIQ_MAX_HORAS = 48            # a las 48 h sin resultado: "sin resultado"
LIQ_OLVIDAR_DIAS = 7          # alertas más viejas que esto no se intentan liquidar
# Margen de la hora de inicio al buscar el marcador. Cubre un cambio de horario de una hora
# y nunca alcanza al juego de otro día ni al segundo juego de una doble jornada de MLB.
LIQ_MARGEN_INICIO = 75 * 60
KAMBI_VIVO_CADA = 60          # lectura de marcadores en vivo de Kambi (solo si hay alertas en juego)

# --- Resumen diario y guardado -------------------------------------------------
RESUMEN_HORA_LOCAL = 23      # 11 p. m. hora local
GUARDAR_CADA = 60             # segundos entre guardados del estado
ARCHIVO_MAX_MB = 45           # los archivos de cuotas se parten antes de 50 MB
