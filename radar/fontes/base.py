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

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<Fonte {self.nome}>"


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
        resposta = requests.get(
            url, params=params, headers=cabecalhos, timeout=TIMEOUT_HTTP
        )
        resposta.raise_for_status()
        return resposta.json()

    return await asyncio.to_thread(_buscar)


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
    except Exception as e:
        # Só o tipo e uma mensagem curta: mensagem de erro de rede costuma
        # trazer a URL inteira, e URL de API pode conter chave.
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
