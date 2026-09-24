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

# --- Kambi ------------------------------------------------------------------------
# Cuotas públicas de Kambi por medio de Paf (la referencia principal).
# Unibet se usa solo para confirmar un error antes de avisar.
KAMBI_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/{op}/{ruta}"
KAMBI_PARAMS = {"lang": "es_ES", "market": "GB", "useCombined": "true"}
KAMBI_PRINCIPAL = "paf"
KAMBI_CONFIRMA = "ub"
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
# Solo se envían ALERTAS si la cuota ESTIMADA en tu casa cae en este rango y sigue
# por encima del precio justo (ver f5_estimacion.py). Lo demás solo se registra.
RANGO_PREFERIDO = (1.50, 2.00)


# Un error que sigue abierto después de esto se da por "alcanzable".
ALCANZABLE_SEG = 120
# No repetir el mismo aviso (misma apuesta y tipo) antes de esto.
REPETIR_AVISO_SEG = 6 * 3600
# Tope de avisos por hora; los demás van solo al resumen diario.
MAX_AVISOS_HORA = 20

# --- Resumen diario y guardado -------------------------------------------------
RESUMEN_HORA_LOCAL = 23      # 11 p. m. hora local
GUARDAR_CADA = 60             # segundos entre guardados del estado
ARCHIVO_MAX_MB = 45           # los archivos de cuotas se parten antes de 50 MB
