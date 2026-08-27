"""Onde a vaga é — modalidade, cidade e escopo de mercado da vaga remota.

Este módulo concentra a classe de bug que mais custou tempo no projeto que
deu origem a este. Vale saber por quê: as fontes entregam localização como
TEXTO SOLTO, cada uma no seu formato — "En remoto", "Mostoles, Madrid",
"Itapetininga - SP", "Sorocaba, São Paulo, Brazil", "100% Remoto",
"Bloomington, IN". Não existe campo estruturado. Tudo aqui é adivinhação
sobre texto livre, e cada regra abaixo nasceu de um caso real que passou
ou foi barrado errado.

Três invariantes que o resto do projeto depende:

  1. Nada aqui lê o TÍTULO da vaga. Nunca. O bug original desta família era
     título e local concatenados num texto só: vaga americana com "Hybrid
     Remote" no título batia "remot" e passava como remota, com
     local="Bloomington, IN".

  2. Campo declarado pela fonte vence texto adivinhado. Se a fonte diz
     modalidade="Híbrido", a palavra "remoto" solta no texto de local não
     transforma a vaga em remota.

  3. Não reconhecer não é rejeitar. Texto que não resolve pra mercado
     nenhum devolve conjunto vazio, e conjunto vazio passa — "duplicar é
     aceitável, perder não".
"""

import re

from radar.texto import contem_termo, normalizar

REMOTO = "Remoto"
HIBRIDO = "Híbrido"
PRESENCIAL = "Presencial"


# ---------------------------------------------------------------------------
# MODALIDADE
# ---------------------------------------------------------------------------

# Vocabulário de trabalho remoto como aparece no CAMPO DE LOCAL das fontes.
# Inclui as formas que uma fonte brasileira escreve em vez de "Remoto" —
# cada uma delas foi vaga real rejeitada por engano no projeto original,
# porque a checagem exigia o campo `modalidade` preenchido e nem toda fonte
# preenche.
VOCABULARIO_REMOTO = [
    "remoto",
    "remota",
    "remote",
    "home office",
    "homeoffice",
    "trabalho remoto",
    "teletrabalho",
    "trabalhe de casa",
    "anywhere",
]

VOCABULARIO_HIBRIDO = ["hibrido", "hybrid"]
VOCABULARIO_PRESENCIAL = ["presencial", "on-site", "onsite", "on site"]

_REMOTO_NORM = [normalizar(v) for v in VOCABULARIO_REMOTO]
_HIBRIDO_NORM = [normalizar(v) for v in VOCABULARIO_HIBRIDO]
_PRESENCIAL_NORM = [normalizar(v) for v in VOCABULARIO_PRESENCIAL]


def classificar_modalidade(texto: str) -> str:
    """Lê o campo de modalidade da fonte e devolve a forma canônica, ou ""
    quando não dá pra dizer.

    Ordem importa: híbrido antes de remoto. "Híbrido - remoto 3x/semana" é
    híbrido, e o texto contém as duas palavras.
    """
    t = normalizar(texto)
    if not t:
        return ""
    if any(contem_termo(v, t) for v in _HIBRIDO_NORM):
        return HIBRIDO
    if any(contem_termo(v, t) for v in _REMOTO_NORM):
        return REMOTO
    if any(contem_termo(v, t) for v in _PRESENCIAL_NORM):
        return PRESENCIAL
    return ""


def e_remoto(modalidade: str, local: str) -> bool:
    """A vaga é remota?

    `modalidade` é o campo que a fonte declarou (pode vir vazio).
    `local` é o texto de localização, usado SÓ como recurso secundário.

    A regra tem duas metades, e as duas importam:

    - Modalidade preenchida DECIDE. Vaga marcada "Híbrido" ou "Presencial"
      não vira remota porque a palavra "remoto" apareceu solta no texto de
      local (acontece: "Sorocaba - presencial, remoto em exceção").

    - Modalidade vazia cai pro texto. Sem esse recurso, `local="Home
      Office"` com modalidade vazia era REJEITADA no projeto original, e
      todo o VOCABULARIO_REMOTO acima ficava inalcançável — uma lista que
      existia exatamente pra ser consultada e nunca era.
    """
    declarada = classificar_modalidade(modalidade)
    if declarada:
        return declarada == REMOTO

    local_norm = normalizar(local)
    return any(contem_termo(v, local_norm) for v in _REMOTO_NORM)


# ---------------------------------------------------------------------------
# UNIDADE FEDERATIVA
# ---------------------------------------------------------------------------

SIGLAS_UF = {
    "ac", "al", "ap", "am", "ba", "ce", "df", "es", "go", "ma", "mt", "ms",
    "mg", "pa", "pb", "pr", "pe", "pi", "rj", "rn", "rs", "ro", "rr", "sc",
    "sp", "se", "to",
}

# Estado por extenso -> sigla. As duas grafias, porque o LinkedIn escreve o
# local em INGLÊS mesmo pra vaga brasileira ("Sorocaba, São Paulo, Brazil",
# mas também "Brasilia, Federal District").
#
# Isto não é preciosismo. No projeto original a guarda de UF só entendia
# SIGLA de duas letras, e o formato do LinkedIn é por extenso — ou seja, a
# guarda estava furada exatamente na fonte que respondia por 1.061 das
# 1.532 vagas do banco. A mesma vaga passava ou era barrada dependendo só
# de como a fonte escrevia o estado.
NOME_DO_ESTADO = {
    "acre": "ac",
    "alagoas": "al",
    "amapa": "ap",
    "amazonas": "am",
    "bahia": "ba",
    "ceara": "ce",
    "distrito federal": "df",
    "federal district": "df",
    "espirito santo": "es",
    "goias": "go",
    "maranhao": "ma",
    "mato grosso": "mt",
    "mato grosso do sul": "ms",
    "minas gerais": "mg",
    "para": "pa",
    "paraiba": "pb",
    "parana": "pr",
    "pernambuco": "pe",
    "piaui": "pi",
    "rio de janeiro": "rj",
    "rio grande do norte": "rn",
    "rio grande do sul": "rs",
    "rondonia": "ro",
    "roraima": "rr",
    "santa catarina": "sc",
    "sao paulo": "sp",
    "sergipe": "se",
    "tocantins": "to",
}

# Ordem: nome mais longo primeiro. Sem isso "mato grosso" bate dentro de
# "mato grosso do sul" e resolve pro estado errado.
_NOMES_ESTADO_ORDENADOS = sorted(NOME_DO_ESTADO, key=len, reverse=True)


def uf_declarada(local: str) -> str | None:
    """A sigla de UF que o texto declara, ou None quando não declara nenhuma.

    Procura primeiro a SIGLA no texto original (maiúscula, borda de palavra
    — "Itapetininga - SP"), depois o nome por extenso no texto normalizado
    ("Sorocaba, São Paulo, Brazil").

    A sigla é exigida em MAIÚSCULA de propósito: é como toda fonte escreve
    UF, e exigir isso evita que a palavra "para" de uma frase qualquer vire
    o estado do Pará por acidente.
    """
    if not local:
        return None

    for token in re.findall(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])", local):
        if token.lower() in SIGLAS_UF:
            return token.lower()

    local_norm = normalizar(local)
    for nome in _NOMES_ESTADO_ORDENADOS:
        if contem_termo(nome, local_norm):
            return NOME_DO_ESTADO[nome]

    return None


def cidade_confere(local: str, uf_esperada: str) -> bool:
    """O nome da cidade bateu — mas é MESMO aquela cidade?

    Nome batido não basta. Casos reais que passavam no projeto original:

        "CAMPINA GRANDE DO SUL - PR"  aceita como Campina Grande/PB
        "Recife - SP"                 aceita como Recife/PE
        "Natal da Serra - MG"         aceita como Natal/RN

    São cidades diferentes, algumas a milhares de quilômetros. O nome aceito
    é prefixo de outra cidade real, e borda de palavra não resolve isso —
    "campina grande" tem borda válida dentro de "campina grande do sul".

    A regra: quando o texto DECLARA uma UF e ela contradiz a esperada pra
    aquela cidade, não é a cidade certa.

    No perfil atual isso importa de verdade em pelo menos três casos:
    "Piedade" é bairro de Jaboatão dos Guararapes/PE além de cidade em SP;
    "Alumínio" é nome de cidade e nome de material; "Tietê" é cidade e rio
    que atravessa meio estado.

    Limite conhecido e aceito: texto sem UF nenhuma ("Piedade" sozinho)
    continua passando. Sem sigla não há o que comparar, e barrar por
    contagem de palavras derrubaria "Sorocaba SP" e "vaga em Avaré", que são
    válidos. Preferir o falso positivo raro ao falso negativo.
    """
    declarada = uf_declarada(local)
    if declarada is None:
        return True
    return declarada == normalizar(uf_esperada)


# Separadores que as fontes usam entre cidade, estado e país. " - " exige os
# espaços: "Salto de Pirapora" não pode ser partido, "Sorocaba - SP" sim.
_PARTES_LOCAL = re.compile(r"\s*[,/|]\s*|\s+-\s+")

_PALAVRAS_PAIS = {"brasil", "brazil", "br"}


def _segmentos_de_cidade(local: str) -> list[str]:
    """Os pedaços do texto de local onde uma CIDADE pode estar.

    Descarta o segmento que declara o ESTADO e o que declara o país. É o
    conserto de um furo que abriu o estado inteiro de São Paulo:

    A Gupy entrega o local como "cidade, ESTADO, país" — "Lorena, São Paulo,
    Brasil". Enquanto a capital não estava na lista de cidades aceitas, isso
    não fazia diferença. Quando ela entrou, o nome do ESTADO passou a casar
    como se fosse o nome da CIDADE, e toda vaga do estado de São Paulo virou
    "São Paulo": Lorena, Jaguariúna, Mogi Guaçu, Taboão da Serra, São
    Bernardo do Campo. MEDIDO em produção — todas entraram com nota 8/10.

    Nome de cidade e nome de estado são iguais em São Paulo, no Rio e em
    vários outros. Substring não distingue os dois; POSIÇÃO distingue.

    Só UMA ocorrência do estado é descartada, e é a última: "São Paulo, São
    Paulo, Brasil" é a capital de verdade, e precisa continuar batendo.
    """
    partes = [p.strip() for p in _PARTES_LOCAL.split(local or "") if p.strip()]
    if len(partes) < 2:
        # Texto de um pedaço só ("Sorocaba", "São Paulo e Região") não tem
        # estado separado pra descartar.
        return partes

    uf = uf_declarada(local)
    descartar = None
    if uf:
        for i in range(len(partes) - 1, -1, -1):
            p = normalizar(partes[i])
            if p == uf or NOME_DO_ESTADO.get(p) == uf:
                descartar = i
                break

    return [
        p for i, p in enumerate(partes)
        if i != descartar and normalizar(p) not in _PALAVRAS_PAIS
    ]


def bate_cidade(local: str, cidades: list[str], uf_esperada: str) -> str | None:
    """Devolve o nome da cidade aceita que bate com o texto, ou None."""
    if not cidade_confere(local, uf_esperada):
        return None

    segmentos = [normalizar(s) for s in _segmentos_de_cidade(local)]
    for cidade in cidades:
        alvo = normalizar(cidade)
        if any(contem_termo(alvo, seg) for seg in segmentos):
            return cidade
    return None


# ---------------------------------------------------------------------------
# ESCOPO DE MERCADO DA VAGA REMOTA
# ---------------------------------------------------------------------------
# "Remoto" sozinho não diz pra quem. Uma vaga "Remote — US only" e uma
# "Remoto — Brasil" são as duas remotas, e só uma serve. Isto extrai o
# mercado que o TEXTO DECLARA, quando declara.

BRASIL = "Brasil"
ESTADOS_UNIDOS = "Estados Unidos"

_SINAIS_BRASIL = {
    "brasil", "brazil", "br", "brazilian", "brasileiro", "brasileira",
    "latam", "america latina", "latin america", "south america",
}

# Capitais e grandes centros: quando o texto de uma vaga remota traz a
# cidade em vez do país, é o país que interessa.
_CIDADES_BRASIL = {
    "sao paulo", "rio de janeiro", "belo horizonte", "brasilia", "salvador",
    "fortaleza", "recife", "porto alegre", "curitiba", "manaus", "belem",
    "goiania", "campinas", "sorocaba", "santos", "florianopolis", "vitoria",
    "natal", "joao pessoa", "maceio", "aracaju", "teresina", "cuiaba",
    "campo grande", "sao luis", "macapa", "boa vista", "palmas", "rio branco",
    "porto velho", "londrina", "ribeirao preto", "uberlandia", "joinville",
}

# Siglas de estado dos EUA. Sozinhas já denunciam mercado americano —
# "Bloomington, IN", "Austin, TX", "Miami, FL".
_SIGLAS_EUA = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy",
}

# Siglas que existem nos dois países: AL, MA, MT, MS, PA, SC. Nessas a
# sigla sozinha não decide nada — ver o desempate no fim de extrair_escopo.
_SIGLAS_AMBIGUAS = _SIGLAS_EUA & SIGLAS_UF

_PAISES_ESTRANGEIROS = {
    "estados unidos": ESTADOS_UNIDOS,
    "united states": ESTADOS_UNIDOS,
    "usa": ESTADOS_UNIDOS,
    "eua": ESTADOS_UNIDOS,
    "canada": "Canadá",
    "mexico": "México",
    "argentina": "Argentina",
    "chile": "Chile",
    "colombia": "Colômbia",
    "peru": "Peru",
    "uruguay": "Uruguai",
    "uruguai": "Uruguai",
    "portugal": "Portugal",
    "espanha": "Espanha",
    "espana": "Espanha",
    "spain": "Espanha",
    "france": "França",
    "franca": "França",
    "germany": "Alemanha",
    "alemanha": "Alemanha",
    "deutschland": "Alemanha",
    "united kingdom": "Reino Unido",
    "reino unido": "Reino Unido",
    "england": "Reino Unido",
    "ireland": "Irlanda",
    "netherlands": "Holanda",
    "poland": "Polônia",
    "india": "Índia",
    "philippines": "Filipinas",
    "nigeria": "Nigéria",
    "australia": "Austrália",
    "singapore": "Singapura",
    "emea": "EMEA",
    "apac": "APAC",
}

_CIDADES_ESTRANGEIRAS = {
    "new york": ESTADOS_UNIDOS,
    "new york city": ESTADOS_UNIDOS,
    "san francisco": ESTADOS_UNIDOS,
    "los angeles": ESTADOS_UNIDOS,
    "chicago": ESTADOS_UNIDOS,
    "austin": ESTADOS_UNIDOS,
    "seattle": ESTADOS_UNIDOS,
    "boston": ESTADOS_UNIDOS,
    "miami": ESTADOS_UNIDOS,
    "houston": ESTADOS_UNIDOS,
    "dallas": ESTADOS_UNIDOS,
    "toronto": "Canadá",
    "vancouver": "Canadá",
    "lisboa": "Portugal",
    "lisbon": "Portugal",
    "madrid": "Espanha",
    "barcelona": "Espanha",
    "buenos aires": "Argentina",
    "santiago": "Chile",
    "bogota": "Colômbia",
    "ciudad de mexico": "México",
    "mexico city": "México",
    "london": "Reino Unido",
    "berlin": "Alemanha",
    "amsterdam": "Holanda",
    "dublin": "Irlanda",
}

# Mercado que não restringe nada — "Remote (Anywhere)" é remoto sem escopo,
# não um país chamado "Anywhere".
_SEM_RESTRICAO = {"anywhere", "worldwide", "global"}

# Preposição, artigo e conectivo. Sozinhos NÃO são nome de lugar.
#
# Este é o item mais caro da lista. O LinkedIn em espanhol escreve
# "En remoto" e "España (En remoto)" — depois de remover a raiz "remot",
# sobrava "en" solto como candidato a mercado, que não resolvia pra país
# nenhum e virava escopo desconhecido. Só isso foram 179 descartes no
# projeto original, o segundo maior motivo de rejeição do sistema inteiro,
# e todas eram vagas de um mercado aceito.
#
# ATENÇÃO ao mexer aqui: "in" e "at" são preposição em inglês E sigla de
# estado americano (Indiana, e nenhuma pra "at"). O desempate não está
# nesta lista, está em _classificar_segmento: sigla é testada ANTES de
# palavra vazia, então "Bloomington, IN" resolve pra Indiana e "Remote in
# Brazil" resolve pro Brasil.
_PALAVRAS_VAZIAS = {
    "en", "em", "no", "na", "de", "do", "da", "the", "in", "at", "only",
    "apenas", "somente", "based", "e", "y", "and", "or", "ou", "a", "o",
    "para", "for", "trabalho", "vaga", "job", "position", "regiao", "area",
    "cidade", "estado", "pais", "country",
}

# Tira a raiz "remot*" e o vocabulário de modalidade antes de partir o
# texto — o que sobra é candidato a nome de lugar.
_RUIDO = re.compile(
    r"\b(remot\w*|home\s*office|teletrabalho|hibrido|hybrid|presencial|"
    r"on-?\s?site|full\s*time|part\s*time|clt|pj)\b"
)

_SEPARADORES = re.compile(r"[,;/|()\[\]–—\-]+")

_BRASIL = "br"
_ESTRANGEIRO = "ext"
_AMBIGUO = "amb"


def _classificar_segmento(bruto: str) -> tuple[str, str] | None:
    """Classifica um segmento de texto como sinal de mercado.

    Devolve (tipo, mercado) ou None. Tipo é _BRASIL, _ESTRANGEIRO ou
    _AMBIGUO — o último só pra sigla que existe nos dois países.

    A ordem dos testes é a regra: SIGLA é resolvida antes de a limpeza de
    palavra vazia acontecer. Sem isso "Bloomington, IN" perdia o "IN" como
    se fosse preposição e a vaga americana passava com escopo vazio.
    """
    seg = bruto.strip()

    # Precisa ter pelo menos uma LETRA. Sem isso, "100% remoto" resolvia
    # pro escopo {"100%"} — não é dígito puro (tem o "%"), então sobrevivia
    # a um filtro que só descartasse número. Também mata CEP ("08015") e
    # pontuação solta.
    if not re.search(r"[a-z]", seg):
        return None

    # Sigla de duas letras, com ou sem espaço no meio ("S. L. P." vira
    # "s l p" na normalização; "S. P." vira "s p").
    compacto = seg.replace(" ", "").replace(".", "")
    if len(compacto) == 2:
        if compacto in _SIGLAS_AMBIGUAS:
            return (_AMBIGUO, ESTADOS_UNIDOS)
        if compacto in SIGLAS_UF:
            return (_BRASIL, BRASIL)
        if compacto in _SIGLAS_EUA:
            return (_ESTRANGEIRO, ESTADOS_UNIDOS)
        return None

    palavras = [p for p in seg.split() if p not in _PALAVRAS_VAZIAS]
    limpo = " ".join(palavras)
    if not limpo or limpo in _SEM_RESTRICAO:
        return None

    # Casamento por IGUALDADE de segmento, não substring. Substring faz
    # "Porto Alegre" virar Portugal e "Santiago do Cacém" virar Chile.
    if limpo in _SINAIS_BRASIL or limpo in _CIDADES_BRASIL or limpo in NOME_DO_ESTADO:
        return (_BRASIL, BRASIL)
    if limpo in _PAISES_ESTRANGEIROS:
        return (_ESTRANGEIRO, _PAISES_ESTRANGEIROS[limpo])
    if limpo in _CIDADES_ESTRANGEIRAS:
        return (_ESTRANGEIRO, _CIDADES_ESTRANGEIRAS[limpo])
    return None


def extrair_escopo(local: str) -> set[str]:
    """Mercados que o texto de localização DECLARA.

    Conjunto vazio significa "não declarou" — que é o caso da maioria das
    vagas remotas e passa sem restrição. Nunca significa "rejeitar".

    Devolve conjunto e não string única porque um texto pode declarar mais
    de um mercado de verdade: "Remote - Brazil/LATAM" e "Remote - LATAM +
    Brazil" declaram dois. Comparar só um valor descartava a vaga sempre
    que o sobrevivente não fosse o aceito, mesmo com o outro sendo.
    """
    if not local:
        return set()

    texto = _RUIDO.sub(" ", normalizar(local))

    encontrados: set[str] = set()
    tem_br = False
    tem_estrangeiro = False
    ambiguos: set[str] = set()

    for bruto in _SEPARADORES.split(texto):
        resultado = _classificar_segmento(bruto)
        if resultado is None:
            continue
        tipo, mercado = resultado
        if tipo == _AMBIGUO:
            ambiguos.add(mercado)
            continue
        encontrados.add(mercado)
        if tipo == _BRASIL:
            tem_br = True
        else:
            tem_estrangeiro = True

    # Desempate da sigla que existe nos dois países (AL, MA, MT, MS, PA, SC).
    #
    # "Belém, PA" e "Cuiabá, MT" são brasileiras; "Philadelphia, PA" e
    # "Boston, MA" são americanas — e a sigla é idêntica. Quando o resto do
    # texto já resolveu de que lado estamos, ela segue o resto. Quando nada
    # mais resolve, ela conta como BRASIL: as fontes deste projeto são
    # portais brasileiros, então essa é de longe a leitura mais provável, e
    # errar pro lado de aceitar é o erro barato ("duplicar é aceitável,
    # perder não").
    if ambiguos and not tem_br and not tem_estrangeiro:
        encontrados.add(BRASIL)
    elif ambiguos and tem_estrangeiro and not tem_br:
        encontrados |= ambiguos

    return encontrados
