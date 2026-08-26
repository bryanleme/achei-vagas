"""Catálogo de fontes ativas.

Um lugar só pra responder "de onde as vagas vêm". Ligar ou desligar uma
fonte é comentar uma linha aqui — não mexer no ciclo.

REGRA DE ENTRADA E SAÍDA, registrada pra a decisão ser por número e não por
impressão:

  ENTRA    depois de testada ao vivo, nunca antes. O endpoint é conferido
           com uma chamada real e o formato do retorno é lido de verdade —
           foi assim que a API JSON da Gupy apareceu, no lugar de mais um
           scraper de navegador.

  SAI      quando em um mês não trouxer nenhuma vaga aprovada, OU quando
           passar a ser bloqueada. As duas coisas são diferentes: fonte sem
           vaga do perfil é fonte errada pro perfil; fonte bloqueada é fonte
           boa que parou de responder e pode voltar. No segundo caso o
           código fica, só sai desta lista.
"""

from radar import config
from radar.fontes import portais, remotas
from radar.fontes.base import Cadencia, Fonte
from radar.fontes.gupy import Gupy


def fontes_ativas() -> list[Fonte]:
    return [
        # A fonte que importa. ATS da indústria brasileira, API JSON com
        # cidade/estado/modalidade/data em campo próprio.
        Gupy(estado=config.ESTADO_BUSCA),

        # Portais brasileiros generalistas, todos por HTML servido — sem
        # navegador. Cobrem a empresa que não usa Gupy.
        portais.VagasCom(),
        portais.InfoJobs(),
        portais.Catho(uf=config.UF_BUSCA.lower(), estado="sao-paulo"),

        # Agregadores de remoto internacional. Rendimento esperado baixo pro
        # perfil, custo quase zero. Ver o cabeçalho de remotas.py.
        *[classe() for classe in remotas.TODAS],
    ]


def do_ciclo(fontes: list[Fonte], e_primeiro_do_dia: bool) -> list[Fonte]:
    """Quais fontes rodam agora.

    Fonte de cadência diária só entra na primeira execução do dia. Sem essa
    separação, o custo de todo ciclo passa a ser ditado pela fonte mais cara
    da lista — no projeto original UMA fonte bloqueada estendia o ciclo em
    19 minutos entregando zero vaga, oito vezes por dia.
    """
    return [
        f for f in fontes
        if f.cadencia is Cadencia.TODO_CICLO or e_primeiro_do_dia
    ]
