"""Um ciclo: buscar, filtrar, pontuar, deduplicar, avisar, gravar.

A ordem das duas últimas etapas é a decisão mais importante deste arquivo, e
é contraintuitiva: AVISA PRIMEIRO, GRAVA DEPOIS.

Gravar antes de confirmar o envio parece mais seguro e é o contrário. Se a
notificação falhar (rede caiu, Telegram fora do ar, token expirado) e a vaga
já estiver marcada como vista, o ciclo seguinte a pula na deduplicação e ela
se perde para sempre — encontrada, nunca entregue, e sem nenhum registro de
que isso aconteceu. Gravando depois, a falha custa no máximo uma repetição.

O princípio que decide todo empate deste projeto: duplicar é aceitável,
perder não.
"""

import asyncio
from collections import Counter
from datetime import datetime, timezone

from radar import config, deposito
from radar.alertas import telegram
from radar.filtro import Regras, avaliar, barrada_so_pelo_cargo
from radar.fontes import registro
from radar.fontes.base import Resultado, com_protecao
from radar.log import logger
from radar.relevancia import pontuar

CHAVE_RODIZIO = "rodizio_offset"
CHAVE_BATIMENTO = "batimento_ultimo_dia"
CHAVE_DIGEST = "digest_ultimo_dia"
CHAVE_FONTES_DIARIAS = "fontes_diarias_ultimo_dia"


def regras_do_perfil() -> Regras:
    return Regras(
        cargos_fortes=config.CARGOS_FORTES,
        cargos_ambiguos=config.CARGOS_AMBIGUOS,
        qualificadores_dominio=config.QUALIFICADORES_DOMINIO,
        ferramentas_titulo=config.FERRAMENTAS_TITULO,
        qualificadores_cargo=config.QUALIFICADORES_CARGO,
        cidades=config.CIDADES,
        uf_esperada=config.UF_BUSCA,
        aceita_remoto=config.ACEITA_REMOTO,
        mercados_remoto_aceitos=config.MERCADOS_REMOTO_ACEITOS,
    )


# ---------------------------------------------------------------------------
# Rodízio de termos
# ---------------------------------------------------------------------------

def termos_do_ciclo() -> list[str]:
    """Os prioritários SEMPRE, mais um bloco do rodízio.

    Por que os prioritários saem do rodízio: no projeto original todos os
    termos disputavam vez por igual, em ordem alfabética. Com 44 termos e 10
    por ciclo, uma volta completa levava 13 horas — e o termo que dava nome
    ao perfil inteiro era buscado uma vez a cada 13 horas, disputando lugar
    com termos de ferramenta obscura. Uma vaga real, que passava no filtro
    sem esforço, nunca chegou a ser BUSCADA. Não foi bug de filtro nem de
    deduplicação: ela simplesmente nunca entrou numa consulta.

    Prioritário custa alguns segundos por ciclo e sobe a cobertura dos
    termos que importam de 1x a cada 13h para 8x por dia.
    """
    prioritarios = [t for t in config.TERMOS_PRIORITARIOS if t in config.TERMOS_BUSCA]
    resto = [t for t in config.TERMOS_BUSCA if t not in prioritarios]

    if not resto:
        return prioritarios

    salvo = deposito.ler(CHAVE_RODIZIO)
    offset = int(salvo) % len(resto) if salvo else 0
    tamanho = min(config.TERMOS_POR_CICLO, len(resto))

    bloco = [resto[(offset + i) % len(resto)] for i in range(tamanho)]
    deposito.gravar(CHAVE_RODIZIO, str((offset + tamanho) % len(resto)))

    return prioritarios + bloco


# ---------------------------------------------------------------------------
# Digest e batimento
# ---------------------------------------------------------------------------

def deve_enviar_digest(agora: datetime | None = None) -> bool:
    """"Ainda não enviei hoje E já passou da hora" — nunca "a hora é X".

    A diferença entre as duas formulações é a diferença entre um recurso que
    funciona e um que nunca rodou uma única vez.

    Com janela exata (`agora.hour == HORA`), um ciclo que começa às 09:00 e
    dura 80 minutos termina às 10:20, quando a janela já fechou. O digest
    nunca dispara. E a rede de segurança que existia pra esse caso ("se
    pulou um dia, manda no ciclo seguinte") exigia um envio anterior
    registrado — ou seja, dependia exatamente daquilo que deveria consertar,
    e nunca ligou. Foi assim que 408 vagas ficaram presas na fila, sem nunca
    chegar ao Telegram, e ninguém notou porque não havia erro nenhum.

    A formulação atual não precisa de rede de segurança: se o ciclo das
    09:00 falhar, o das 12:00 manda, porque "já passou de 9" continua
    verdadeiro o dia inteiro.
    """
    agora = agora or datetime.now(timezone.utc)
    if deposito.ja_feito_hoje(CHAVE_DIGEST, agora.date()):
        return False
    return agora.hour >= config.DIGEST_HORA_UTC


def enviar_digest_se_for_hora(agora: datetime | None = None) -> int:
    agora = agora or datetime.now(timezone.utc)
    if not deve_enviar_digest(agora):
        return 0

    pendentes = deposito.pendentes_do_digest()
    if not pendentes:
        # Marca mesmo sem vaga nenhuma. Sem isso a condição fica verdadeira
        # em todo ciclo seguinte do dia, e o log enche de tentativa vazia.
        deposito.marcar_feito_hoje(CHAVE_DIGEST, agora.date())
        return 0

    if telegram.enviar_digest(pendentes):
        deposito.limpar_fila_do_digest()
        deposito.marcar_feito_hoje(CHAVE_DIGEST, agora.date())
        logger.info("Resumo diário enviado: %d vaga(s).", len(pendentes))
        return len(pendentes)

    # Não marca nem limpa: tenta de novo no próximo ciclo. Repetir uma
    # mensagem é barato; perder a fila inteira não é.
    logger.warning(
        "Falha ao enviar o resumo (%d vaga(s) pendentes) — tenta no próximo ciclo.",
        len(pendentes),
    )
    return 0


def enviar_batimento_se_for_o_primeiro(
    novas: int, com_problema: list[str], total_fontes: int, agora: datetime | None = None
) -> bool:
    agora = agora or datetime.now(timezone.utc)
    if deposito.ja_feito_hoje(CHAVE_BATIMENTO, agora.date()):
        return False
    if telegram.batimento(novas, com_problema, total_fontes, agora.strftime("%H:%M UTC")):
        deposito.marcar_feito_hoje(CHAVE_BATIMENTO, agora.date())
        return True
    return False


def _primeiro_ciclo_do_dia(agora: datetime) -> bool:
    ja = deposito.ja_feito_hoje(CHAVE_FONTES_DIARIAS, agora.date())
    if not ja:
        deposito.marcar_feito_hoje(CHAVE_FONTES_DIARIAS, agora.date())
    return not ja


# ---------------------------------------------------------------------------
# O ciclo
# ---------------------------------------------------------------------------

async def rodar(so_teste: bool = False) -> dict:
    """Roda um ciclo completo. `so_teste` não envia nada nem grava."""
    agora = datetime.now(timezone.utc)
    regras = regras_do_perfil()

    deposito.iniciar()
    if not so_teste:
        processados = telegram.processar_feedback(deposito)
        if processados:
            logger.info("%d resposta(s) de 👍/👎 registrada(s).", processados)

    termos = termos_do_ciclo()
    todas = registro.fontes_ativas()
    fontes = registro.do_ciclo(todas, _primeiro_ciclo_do_dia(agora))

    logger.info(
        "Ciclo iniciado: %d termo(s) × %d fonte(s).", len(termos), len(fontes)
    )

    brutas = 0
    aprovadas = 0
    novas = 0
    com_problema: list[str] = []
    descartes_escopo: Counter = Counter()
    barradas_pelo_titulo = 0
    sem_mercado_declarado = 0
    ja_notificadas: set[str] = set()

    for fonte in fontes:
        # Todos os termos da fonte em paralelo. O gargalo aqui é rede, não
        # CPU — esperar uma resposta de cada vez multiplicaria o tempo do
        # ciclo pelo número de termos sem nenhum ganho.
        resultados: list[Resultado] = await asyncio.gather(
            *[com_protecao(fonte, termo) for termo in termos]
        )

        falhas = sum(1 for r in resultados if r.falhou)
        vagas_da_fonte = [v for r in resultados for v in r.vagas]
        brutas += len(vagas_da_fonte)

        # Fonte que falhou em TODOS os termos está com problema. Falhar em
        # um termo e responder nos outros é ruído de rede, não bloqueio.
        if falhas == len(termos) and termos:
            com_problema.append(fonte.nome)

        aprovadas_da_fonte = 0
        novas_da_fonte = 0

        for vaga in vagas_da_fonte:
            resultado = avaliar(vaga, regras)

            if resultado.escopo_rejeitado:
                descartes_escopo.update(resultado.escopo_rejeitado)

            if not resultado.aprovada:
                if barrada_so_pelo_cargo(vaga, regras):
                    barradas_pelo_titulo += 1
                continue

            # Agregador estrangeiro precisa DECLARAR o mercado. Ver
            # `exige_mercado_declarado` em fontes/base.py: num portal
            # brasileiro, remoto sem escopo quer dizer Brasil; num
            # agregador de vaga em inglês, quer dizer o contrário.
            if (
                fonte.exige_mercado_declarado
                and resultado.remota
                and not resultado.mercado_confirmado
            ):
                sem_mercado_declarado += 1
                continue

            aprovadas_da_fonte += 1
            aprovadas += 1

            # Dedup dentro do próprio ciclo, antes de consultar o banco: a
            # mesma vaga costuma vir por dois termos diferentes da mesma
            # fonte, e sem isso ela seria notificada duas vezes na mesma
            # execução (o banco só a conheceria depois da primeira gravação).
            #
            # As DUAS chaves, não só o link. MEDIDO na primeira execução
            # real: "Analista de PCP (Planejamento e Controle de Produção)"
            # da mesma empresa apareceu duas vezes no mesmo ciclo, com links
            # diferentes — a empresa publicou a vaga duas vezes. O link
            # sozinho não pega isso; empresa+título pega.
            if vaga.id in ja_notificadas or vaga.chave_secundaria in ja_notificadas:
                continue
            ja_notificadas.add(vaga.id)
            ja_notificadas.add(vaga.chave_secundaria)

            if deposito.ja_vista(vaga):
                continue

            nota = pontuar(
                vaga, regras, resultado,
                niveis_alvo=config.NIVEIS_ALVO,
                niveis_acima=config.NIVEIS_ACIMA_DO_ALVO,
                dias_vaga_antiga=config.DIAS_PARA_VAGA_ANTIGA,
            )
            antiga = vaga.antiga(config.DIAS_PARA_VAGA_ANTIGA)
            imediata = nota.total >= config.LIMIAR_ALERTA_IMEDIATO and not antiga

            if so_teste:
                logger.info(
                    "  [teste] %s (%d/10) %s — %s | %s",
                    "AGORA " if imediata else "resumo",
                    nota.total, vaga.titulo, vaga.empresa, vaga.local,
                )
                novas += 1
                novas_da_fonte += 1
                continue

            if imediata:
                enviou = telegram.alerta_vaga(
                    vaga, nota.total, str(nota), config.DIAS_PARA_VAGA_ANTIGA
                )
                if not enviou:
                    # NÃO grava. A vaga volta no próximo ciclo — ver o
                    # cabeçalho deste arquivo.
                    logger.warning(
                        "Falha ao alertar '%s' — não gravada, volta no próximo ciclo.",
                        vaga.titulo,
                    )
                    continue
                logger.info("Alerta enviado (%d/10): %s", nota.total, vaga.titulo)

            deposito.salvar(
                vaga, nota.total, str(nota), digest_pendente=not imediata
            )
            novas += 1
            novas_da_fonte += 1

        logger.info(
            "[%s] %d brutas → %d aprovadas → %d novas%s",
            fonte.nome, len(vagas_da_fonte), aprovadas_da_fonte, novas_da_fonte,
            f" ({falhas} termo(s) com falha)" if falhas else "",
        )

    logger.info(
        "Ciclo concluído: %d brutas → %d aprovadas → %d novas.", brutas, aprovadas, novas
    )

    # Diagnóstico que só existe se for medido. Escopo mal reconhecido barra
    # a vaga do jeito certo e some sem rastro — foi assim que um falso
    # negativo de 179 vagas passou despercebido no projeto original.
    if descartes_escopo:
        detalhe = "; ".join(f"{m} ({n})" for m, n in descartes_escopo.most_common())
        logger.info("Descartadas por mercado: %s", detalhe)
    if sem_mercado_declarado:
        logger.info(
            "%d vaga(s) de agregador estrangeiro descartadas por não declarar "
            "mercado — ver exige_mercado_declarado em fontes/base.py.",
            sem_mercado_declarado,
        )
    if barradas_pelo_titulo:
        logger.info(
            "%d vaga(s) em local aceito foram barradas só pelo título "
            "(preço da decisão de ler só o título — se este número crescer "
            "com vaga boa, vale reconsiderar).",
            barradas_pelo_titulo,
        )

    if not so_teste:
        if telegram.deve_alertar_saude(len(com_problema), len(fontes)):
            telegram.alerta_saude(com_problema, len(fontes))
        enviar_batimento_se_for_o_primeiro(novas, com_problema, len(fontes), agora)
        enviar_digest_se_for_hora(agora)
        total = deposito.registrar_total()
        logger.info("Banco: %d vaga(s) no histórico.", total)

    return {
        "brutas": brutas,
        "aprovadas": aprovadas,
        "novas": novas,
        "fontes_com_problema": com_problema,
    }
