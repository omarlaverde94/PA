"""
Fase 5: emparejar un partido de Kambi con el mismo partido en otra fuente
(Pinnacle para comparar cuotas; API-Football y ESPN para los marcadores).

Para que dos partidos se den por iguales tienen que coincidir TODAS estas cosas:
1. La hora de inicio, con un margen corto (unos minutos, no horas).
2. La competencia: el mismo país (o los dos son torneos internacionales).
3. La categoría: primer equipo, reservas/sub-xx o femenino. Un equipo sub-21
   nunca se empareja con el primer equipo, ni uno femenino con uno masculino.
4. Los nombres de los DOS equipos, cada uno en su lado (local con local).
   Las palabras genéricas ("Al", "FC", "Club", "United", "U21"...) no cuentan:
   tiene que coincidir una palabra propia del equipo.

Lección de la fase 5: "Al Dhaid U21 - Shabab Al-Ahli U21" se emparejó con otro
partido de la misma liga y a la misma hora porque el emparejador viejo daba por
buenos dos equipos que solo compartían la palabra "Al".
"""
import difflib
import re
import unicodedata

# Palabras que no identifican a un equipo
GENERICAS = {
    "al", "el", "la", "le", "les", "los", "las", "de", "del", "da", "do", "di", "du", "der", "the", "and", "y",
    "fc", "cf", "sc", "ac", "afc", "cfc", "cd", "ca", "club", "sv", "vv", "fk", "nk", "hnk", "if", "ik", "bk",
    "sk", "ks", "us", "ud", "sd", "ss", "as", "asd", "ad", "rc", "rkvv", "fbc", "sp", "sad", "jk", "ff", "cs",
    "csd", "mfk", "ofk", "fco", "pfc", "tsv", "fsv", "ssv", "spvgg", "calcio", "esporte", "clube", "futebol",
    "football", "soccer", "sport", "sports", "sporting", "deportivo", "atletico", "athletic", "real",
    "united", "utd", "city", "town", "fem", "jr", "sr", "saudi", "uae", "dubai", "san", "santa", "st", "saint",
    "ce", "cp", "ue", "rcd", "rsd", "ssd", "mfc", "fcd", "kfc", "sdad", "union",
}
# Palabras que dicen la categoría del equipo o de la liga
_JUVENIL = re.compile(r"\b(u ?1[5-9]|u ?2[0-3]|sub ?1[5-9]|sub ?2[0-3]|youth|primavera|juvenil|"
                      r"academy|reserve|reserves|reserva|reservas|suplentes|filial|"
                      r"professional development|development|premier league 2|promesas|next gen)\b")
# Solo en el nombre del equipo (en el de la liga, "Liga II" es segunda división, no reservas)
_RESERVA_EQUIPO = re.compile(r"\b(ii|iii|b|c|jong)\b")
_FEMENINO = re.compile(r"\b(w|f|women|womens|woman|femenino|femenina|feminino|femenil|ladies|frauen|damen|"
                       r"dames|damer|dam|kvinner|naiset|wsl|nwsl|damallsvenskan|toppserien|kvindeliga|liga f|d1 arkema)\b")
_ESPORTS = re.compile(r"esport|cyber|e-?football")

# Nombres en español (Kambi en es_ES) a inglés, y países con varios nombres
ALIAS = {
    "inglaterra": "england", "escocia": "scotland", "gales": "wales", "irlanda del norte": "northern ireland",
    "irlanda": "ireland", "republica de irlanda": "ireland", "republic of ireland": "ireland",
    "republica checa": "czech republic", "chequia": "czech republic", "czechia": "czech republic",
    "macedonia del norte": "north macedonia", "islas feroe": "faroe islands", "islas faroe": "faroe islands",
    "alemania": "germany", "espana": "spain", "francia": "france", "italia": "italy", "paises bajos": "netherlands",
    "holanda": "netherlands", "belgica": "belgium", "suiza": "switzerland", "suecia": "sweden",
    "noruega": "norway", "dinamarca": "denmark", "finlandia": "finland", "polonia": "poland",
    "hungria": "hungary", "rumania": "romania", "grecia": "greece", "turquia": "turkey", "turkiye": "turkey",
    "rusia": "russia", "ucrania": "ukraine", "croacia": "croatia", "eslovenia": "slovenia",
    "eslovaquia": "slovakia", "serbia": "serbia", "bosnia y herzegovina": "bosnia and herzegovina",
    "bosnia herzegovina": "bosnia and herzegovina", "argelia": "algeria", "tunez": "tunisia",
    "marruecos": "morocco", "egipto": "egypt", "sudafrica": "south africa", "japon": "japan",
    "corea del sur": "south korea", "korea republic": "south korea", "republic of korea": "south korea",
    "china pr": "china", "estados unidos": "usa", "united states": "usa", "mexico": "mexico",
    "brasil": "brazil", "peru": "peru", "united arab emirates": "uae", "emiratos arabes unidos": "uae",
    "arabia saudita": "saudi arabia", "arabia saudi": "saudi arabia", "catar": "qatar",
    "nueva zelanda": "new zealand", "islandia": "iceland", "austria": "austria", "chipre": "cyprus",
}
INTERNACIONAL = {"international", "internacional", "world", "uefa", "conmebol", "concacaf", "caf", "afc", "ofc",
                 "fifa", "club friendly matches", "international friendly matches", "international youth tournaments",
                 "international clubs", "europe", "south america", "africa", "asia"}


def _base(txt):
    txt = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode().lower()
    # "F.C." -> "fc"; "L'Escala" -> "l escala"
    txt = txt.replace("&", " and ").replace(".", "").replace("'", " ").replace("`", " ").replace("´", " ")
    for a in sorted(ALIAS, key=len, reverse=True):
        txt = re.sub(rf"\b{re.escape(a)}\b", ALIAS[a], txt)
    return txt


def categoria(local, visitante, liga=""):
    """Categoría de un partido: 'juvenil' (incluye reservas y sub-xx), 'femenino' o 'esports'.
    Vacío = primer equipo masculino. Se mira el nombre de los dos equipos y el de la liga."""
    cat = set()
    for t, es_equipo in ((local, True), (visitante, True), (liga, False)):
        b = re.sub(r"[()\[\]|]", " ", _base(t)).replace("-", " ")
        if _ESPORTS.search(b):
            cat.add("esports")
        if _FEMENINO.search(b):
            cat.add("femenino")
        if _JUVENIL.search(b) or (es_equipo and _RESERVA_EQUIPO.search(b)):
            cat.add("juvenil")
    return cat


def palabras(nombre):
    """Palabras propias del nombre de un equipo (sin genéricas ni categoría)."""
    b = re.sub(r"\(.*?\)", " ", _base(nombre))
    b = re.sub(r"[^a-z0-9 ]", " ", b.replace("-", " ").replace(".", " ").replace("/", " "))
    b = _JUVENIL.sub(" ", b)
    b = _RESERVA_EQUIPO.sub(" ", b)
    b = _FEMENINO.sub(" ", b)
    todas = [t for t in b.split() if len(t) > 1]
    # Si el nombre es solo de palabras genéricas ("Sporting", "Real Club"), se usan todas
    return [t for t in todas if t not in GENERICAS] or todas


def _igual(x, y):
    """Misma palabra, o una es abreviatura de la otra ("Man" y "Manchester", "Dep" y "Deportes")."""
    corta, larga = sorted((x, y), key=len)
    return x == y or (len(corta) >= 3 and len(larga) >= 4 and larga.startswith(corta))


def similitud(a, b):
    """0 a 1: qué tanto se parecen dos nombres de equipo. 0 si no comparten ninguna palabra propia."""
    ta, tb = palabras(a), palabras(b)
    if not ta or not tb:
        return 0.0
    ha = sum(1 for x in ta if any(_igual(x, y) for y in tb))
    hb = sum(1 for y in tb if any(_igual(x, y) for x in ta))
    if not ha:
        return 0.0
    por_palabra = (ha / len(ta) + hb / len(tb)) / 2
    return max(por_palabra, difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio())


def pais(liga, sep):
    """País de una liga ('United Arab Emirates / U21 League' con sep='/', 'UAE - U21 League' con sep='-').
    Devuelve 'internacional' para torneos internacionales y None si no se sabe."""
    if not liga:
        return None
    primero = _base(liga.split(f" {sep} ", 1)[0] if f" {sep} " in liga else liga).strip(" |")
    if not primero:
        return None
    if primero in INTERNACIONAL or any(primero.startswith(x + " ") for x in INTERNACIONAL) \
            or "friendl" in primero or "international" in primero:
        return "internacional"
    return primero if f" {sep} " in liga else None


def misma_competencia(liga_kambi, liga_otra, sep_otra="-"):
    """Mismo país (o los dos internacionales). Si una de las dos no dice el país, no se bloquea."""
    pk, po = pais(liga_kambi, "/"), pais(liga_otra, sep_otra)
    if pk is None or po is None:
        return True
    return pk == po


def misma_categoria(cat_a, cat_b):
    return set(cat_a) == set(cat_b)


UMBRAL_NOMBRE = 0.5      # cada equipo por separado
UMBRAL_PROMEDIO = 0.6    # los dos equipos juntos


def puntaje(kambi, otro, margen_seg, liga_otra=None, sep_otra="-", revisar_competencia=True):
    """
    Puntaje de que `otro` sea el mismo partido que `kambi`, o None si no lo es.
    kambi: {local, visitante, _ts, liga}; otro: {local, visitante, ts, liga (opcional)}.
    Devuelve (puntaje, motivo) donde motivo explica por qué no, si puntaje es None.
    """
    if otro.get("ts") is None or abs(otro["ts"] - kambi["_ts"]) > margen_seg:
        return None, "hora"
    liga_otra = liga_otra if liga_otra is not None else otro.get("liga", "")
    if revisar_competencia and not misma_competencia(kambi.get("liga", ""), liga_otra, sep_otra):
        return None, "competencia"
    cat_k = categoria(kambi.get("local", ""), kambi.get("visitante", ""), kambi.get("liga", ""))
    cat_o = categoria(otro.get("local", ""), otro.get("visitante", ""), liga_otra)
    if not misma_categoria(cat_k, cat_o):
        return None, "categoria"
    sl, sv = similitud(kambi.get("local", ""), otro["local"]), similitud(kambi.get("visitante", ""), otro["visitante"])
    if min(sl, sv) < UMBRAL_NOMBRE or (sl + sv) / 2 < UMBRAL_PROMEDIO:
        return None, "nombres"
    # A igualdad de nombres, gana el más cercano en hora
    return sl + sv - abs(otro["ts"] - kambi["_ts"]) / 36000, ""


def mejor(kambi, candidatos, margen_seg, sep_otra="-", revisar_competencia=True):
    """El candidato con mejor puntaje, o None. Si dos candidatos empatan casi en todo
    (no se puede saber cuál es), no se elige ninguno."""
    puntos = []
    for c in candidatos:
        p, _ = puntaje(kambi, c, margen_seg, sep_otra=sep_otra, revisar_competencia=revisar_competencia)
        if p is not None:
            puntos.append((p, c))
    if not puntos:
        return None
    puntos.sort(key=lambda x: -x[0])
    primero = puntos[0][1]

    def mismo(c):  # el mismo partido repetido en la lista no cuenta como otro
        return (c["local"], c["visitante"], c["ts"]) == (primero["local"], primero["visitante"], primero["ts"])
    otros = [p for p, c in puntos[1:] if not mismo(c)]
    if otros and puntos[0][0] - otros[0] < 0.05:
        return None  # dos partidos distintos igual de parecidos: mejor no comparar
    return primero
