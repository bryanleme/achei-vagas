"""Contrato das fontes, e o que todas elas compartilham.

Uma fonte é qualquer coisa que devolva `list[Vaga]` pra um termo de busca.
Não importa se por trás é uma API JSON ou um navegador raspando HTML — quem
chama não precisa saber, e é isso que permite trocar a implementação de uma
fonte sem tocar no ciclo.

CADÊNCIA é o único conceito que o ciclo precisa entender além disso. Fonte
de API custa segundos; fonte de navegador custa minutos e é o que leva
bloqueio anti-bot. Rodar as duas na mesma frequência faz o custo do ciclo
inteiro ser ditado pela mais cara — no projeto original UMA fonte bloqueada
estendia o ciclo em 19 minutos entregando zero vaga.
"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum

import requests

from radar.log import logger
from radar.modelos import Vaga

TIMEOUT_HTTP = 25

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class Cadencia(Enum):
    """Com que frequência a fonte roda."""

    TODO_CICLO = "todo_ciclo"
    # Uma vez por dia. Pra fonte cara (navegador) ou de rendimento baixo:
    # ela continua cobrindo o que só ela cobre, sem pesar em todo ciclo.
    DIARIA = "diaria"


@dataclass
class Resultado:
    """O que uma fonte devolve, com o suficiente pro ciclo diagnosticar.

    `falhou` é diferente de "veio vazia". Uma fonte que devolve zero vaga
    porque não há vaga pra aquele termo está funcionando; uma que devolve
    zero porque foi bloqueada, não. Sem essa distinção o alerta de saúde
    dispara em dia fraco e cala em dia de bloqueio — exatamente ao
    contrário do que deveria.
    """

    vagas: list[Vaga]
    falhou: bool = False
    detalhe: str = ""


class Fonte:
    """Classe-base. Toda fonte define `nome`, `cadencia` e `buscar`."""

    nome: str = "?"
    cadencia: Cadencia = Cadencia.TODO_CICLO
    usa_navegador: bool = False

    # A fonte é estrangeira o bastante pra que "remoto sem mercado
    # declarado" signifique "não é pro Brasil"?
    #
    # MEDIDO na primeira execução real: o Himalayas devolveu 60 vagas
    # aprovadas num ciclo, entre elas "Online Data Analyst - Bengali (IN)" e
    # "- English (AU)". Todas passavam legitimamente: o título bate "Data
    # Analyst", que é um cargo do perfil, e o campo de restrição vinha
    # vazio, o que o filtro lê (corretamente) como "remoto sem restrição".
    #
    # A regra geral do projeto é aceitar remoto sem escopo declarado, porque
    # num portal BRASILEIRO isso quase sempre quer dizer Brasil. Num
    # agregador que publica quase só vaga em inglês pro mercado americano e
    # asiático, a mesma ausência quer dizer o contrário. Mesma informação,
    # significado oposto — e é por isso que isso é propriedade da FONTE, e
    # não do filtro.
    exige_mercado_declarado: bool = False

    # Quantas buscas desta fonte podem estar no ar ao mesmo tempo.
    # 0 = sem limite (o padrão: API pública aguenta a rajada).
    #
    # MEDIDO na primeira execução com o LinkedIn ligado: uma busca isolada
    # respondia 200, mas os 15 termos disparados de uma vez falharam TODOS.
    # Não é o endpoint estar errado — é limite de taxa. A diferença entre os
    # dois diagnósticos é o dia inteiro de investigação que este comentário
    # economiza.
    max_simultaneas: int = 0

    # Pausa em segundos entre requisições sequenciais da mesma fonte.
    pausa: float = 0.0

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<Fonte {self.nome}>"


# Quantas vezes reenviar quando a resposta é 429 (limite de taxa) ou 5xx.
TENTATIVAS = 3

# Espera entre tentativas, dobrando a cada vez: 2s, 4s.
ESPERA_BASE = 2.0

_STATUS_QUE_VALE_REPETIR = {429, 500, 502, 503, 504}


def _requisitar(url: str, params, headers) -> requests.Response:
    """GET com reenvio no que é temporário — e só no que é temporário.

    429 quer dizer "você está indo rápido demais, espere". Tratar isso como
    falha definitiva é desistir de uma resposta que o servidor está disposto
    a dar: MEDIDO no LinkedIn, 4 dos 15 termos de um ciclo voltavam 429
    mesmo já serializados, e cada um era uma busca inteira perdida.

    404 e 403 NÃO são repetidos de propósito. 404 é endpoint errado e 403 é
    bloqueio — repetir os dois só gasta tempo e, no caso do 403, insiste
    exatamente onde já disseram não.
    """
    espera = ESPERA_BASE
    ultima: requests.Response | None = None

    for tentativa in range(TENTATIVAS):
        resposta = requests.get(url, params=params, headers=headers, timeout=TIMEOUT_HTTP)
        if resposta.status_code not in _STATUS_QUE_VALE_REPETIR:
            resposta.raise_for_status()
            return resposta

        ultima = resposta
        if tentativa < TENTATIVAS - 1:
            # `Retry-After`, quando vem, é o próprio servidor dizendo quanto
            # esperar. Ignorar isso e usar a nossa conta é adivinhar tendo a
            # resposta na mão.
            sugerido = resposta.headers.get("Retry-After")
            try:
                pausa = min(float(sugerido), 30.0) if sugerido else espera
            except ValueError:
                pausa = espera
            time.sleep(pausa)
            espera *= 2

    assert ultima is not None
    ultima.raise_for_status()
    return ultima


async def get_json(url: str, params: dict | None = None, headers: dict | None = None):
    """GET que devolve JSON, fora do event loop.

    `requests` é síncrono: chamado direto dentro de uma corrotina ele TRAVA
    o loop inteiro, e as outras fontes param de avançar enquanto esta espera
    a rede. Com dez fontes em paralelo isso transforma execução concorrente
    em execução em fila, sem nenhum aviso — o tempo total simplesmente vira
    a soma em vez do máximo.
    """
    cabecalhos = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        cabecalhos.update(headers)

    def _buscar():
        return _requisitar(url, params, cabecalhos).json()

    return await asyncio.to_thread(_buscar)


async def get_html(url: str, params: dict | None = None):
    """GET que devolve a página já parseada.

    Todos os portais brasileiros deste projeto entregam a lista de vagas no
    HTML da resposta — a página é montada no servidor. Isso foi MEDIDO,
    portal por portal, antes de qualquer linha de scraper: Vagas.com,
    InfoJobs, Catho e Trabalha Brasil devolvem os cards prontos pra um GET
    simples.

    A consequência é grande e vale registrar, porque contraria o que o
    projeto original assumia: nove das onze fontes de lá abriam Chromium
    headless, o ciclo levava mais de uma hora e o navegador era o vetor de
    todo bloqueio anti-bot. Nada disso era necessário pra maioria delas.

    Se um portal um dia passar a montar a lista só no navegador, aí sim
    vale um scraper de Playwright — pra ele, não pra todos.
    """
    from bs4 import BeautifulSoup

    cabecalhos = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}

    def _buscar():
        return BeautifulSoup(_requisitar(url, params, cabecalhos).text, "lxml")

    return await asyncio.to_thread(_buscar)


def primeiro_texto(elemento) -> str:
    """Só o primeiro nó de TEXTO do elemento, ignorando filhos.

    Os portais grudam o local da vaga com um tooltip no mesmo elemento —
    "Pirapora do Bom Jesus / SP A empresa aceita candidaturas de...". Pegar
    o texto inteiro leva a frase do tooltip junto pro campo de local, e o
    filtro de cidade passa a comparar contra um parágrafo.
    """
    if elemento is None:
        return ""
    for filho in elemento.children:
        if isinstance(filho, str) and filho.strip():
            return filho.strip()
    return elemento.get_text(" ", strip=True)


def texto_local(*partes: str | None) -> str:
    """Junta cidade/estado/país num texto de local, ignorando vazio.

    Fonte com campo estruturado é a exceção boa deste projeto — a maioria
    entrega tudo grudado num texto só. Aqui a gente REMONTA o texto num
    formato que o filtro entende bem ("Sorocaba, São Paulo, Brasil"), em vez
    de inventar um caminho paralelo pro filtro só pras fontes estruturadas.
    Um caminho só, testado uma vez.
    """
    return ", ".join(p.strip() for p in partes if p and p.strip())


async def com_protecao(fonte: "Fonte", termo: str, navegador=None) -> Resultado:
    """Roda uma fonte sem deixar que ela derrube o ciclo.

    Fonte que levanta exceção vira `falhou=True`, não interrompe as outras e
    não é confundida com fonte que legitimamente não achou nada.
    """
    try:
        return await fonte.buscar(termo, navegador)
    except requests.HTTPError as e:
        # O STATUS importa e o tipo sozinho não diz nada. 429 é limite de
        # taxa (baixar a concorrência); 403 é bloqueio (a fonte sai); 404 é
        # endpoint errado (corrigir o código). Registrar só "HTTPError"
        # transforma os três no mesmo bilhete sem informação.
        #
        # O status vem de `e.response` e não contém URL nem credencial. A
        # exceção CRUA conteria: a mensagem padrão do requests inclui a URL
        # completa que falhou, e URL de API pode carregar chave.
        status = e.response.status_code if e.response is not None else "?"
        logger.warning("[%s] falhou em '%s': HTTP %s", fonte.nome, termo, status)
        return Resultado(vagas=[], falhou=True, detalhe=f"HTTP {status}")
    except Exception as e:
        logger.warning("[%s] falhou em '%s': %s", fonte.nome, termo, type(e).__name__)
        return Resultado(vagas=[], falhou=True, detalhe=type(e).__name__)


def montar(
    titulo: str,
    empresa: str,
    local: str,
    link: str,
    fonte: str,
    modalidade: str = "",
    publicado_em: str = "",
    descricao: str = "",
    termo_busca: str = "",
) -> Vaga | None:
    """Constrói a Vaga, descartando card incompleto.

    Card sem título ou sem link é lixo de renderização, não vaga — o
    esqueleto ainda não hidratado que os portais em React deixam no DOM
    quando a leitura acontece rápido demais. Gravar isso enche o banco de
    registro fantasma que nunca mais some (o dedup passa a considerá-lo
    visto) e polui a contagem de rendimento por fonte.
    """
    titulo = (titulo or "").strip()
    link = (link or "").strip()
    if not titulo or not link:
        return None

    return Vaga(
        titulo=titulo,
        empresa=(empresa or "").strip(),
        local=(local or "").strip(),
        link=link,
        fonte=fonte,
        modalidade=modalidade or "",
        publicado_em=publicado_em or "",
        descricao=descricao or "",
        termo_busca=termo_busca,
    )
