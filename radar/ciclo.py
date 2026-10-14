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
from radar.localizacao import casa_cidade
from radar.log import logger
from radar.relevancia import pontuar

CHAVE_RODIZIO = "rodizio_offset"
CHAVE_VOLTAS = "rodizio_voltas"
CHAVE_BATIMENTO = "batimento_ultimo_dia"
CHAVE_DIGEST = "digest_ultimo_dia"
CHAVE_FONTES_DIARIAS = "fontes_diarias_ultimo_dia"


def regras_do_perfil() -> Regras:
    return Regras(
        cargos_fortes=config.CARGOS_FORTES,
        cargos_ambiguos=config.CARGOS_AMBIGUOS,
        qualificadores_dominio=config.QUALIFICADORES_DOMINIO,
        ferramentas_de_cargo=config.FERRAMENTAS_DE_CARGO,
        palavras_de_cargo=config.PALAVRAS_DE_CARGO,
        cidades=config.CIDADES,
        uf_esperada=config.UF_BUSCA,
        aceita_remoto=config.ACEITA_REMOTO,
        paises_remoto_permitidos=config.PAISES_REMOTO_PERMITIDOS,
        exclusoes_titulo=config.EXCLUSOES_TITULO,
    )


# ---------------------------------------------------------------------------
# Rodízio de termos
# ---------------------------------------------------------------------------

def termos_desta_rodada() -> list[str]:
    """Os prioritários SEMPRE, mais um bloco do rodízio.

    Por que os prioritários saem do rodízio: se todos os termos disputassem
    vez por igual, com ~44 termos e 10 por ciclo uma volta completa levaria
    13 horas — e o termo que dá nome ao perfil inteiro seria buscado uma vez
    a cada 13 horas, disputando lugar com termos de ferramenta obscura. Uma
    vaga real, que passaria no filtro sem esforço, poderia nunca ser
    BUSCADA. Não seria bug de filtro nem de deduplicação: ela simplesmente
    nunca entraria numa consulta.

    Prioritário custa alguns segundos por ciclo e sobe a cobertura dos
    termos que importam de 1x a cada 13h para 8x por dia.
    """
    prioritarios = [t for t in config.TERMOS_PRIORITARIOS if t in config.TERMOS_DE_BUSCA]
    resto = [t for t in config.TERMOS_DE_BUSCA if t not in prioritarios]

    if not resto:
        return prioritarios

    salvo = deposito.ler(CHAVE_RODIZIO)
    offset = int(salvo) % len(resto) if salvo else 0
    tamanho = min(config.TERMOS_POR_RODADA, len(resto))

    bloco = [resto[(offset + i) % len(resto)] for i in range(tamanho)]

    novo_offset = (offset + tamanho) % len(resto)
    deposito.gravar(CHAVE_RODIZIO, str(novo_offset))
    # O bloco deu a volta: a lista inteira ja foi buscada pelo menos uma vez.
    if novo_offset <= offset:
        voltas = int(deposito.ler(CHAVE_VOLTAS) or 0)
        deposito.gravar(CHAVE_VOLTAS, str(voltas + 1))

    return prioritarios + bloco


def em_varredura_inicial() -> bool:
    """O rodizio ainda nao cobriu a lista de termos uma vez inteira.

    MEDIDO no SEGUNDO ciclo em producao: chegaram 15 alertas imediatos de
    uma vez. Nenhum era erro -- eram vagas reais, novas para o banco. Mas
    novas para o banco nao e o mesmo que novas no mundo: elas ja estavam
    publicadas, e so apareceram agora porque o rodizio finalmente chegou nos
    termos "transportes", "suprimentos" e "logistica".

    A guarda de estreia cobria so o primeiro ciclo, e por isso resolveu
    metade do problema. Enquanto a varredura inicial nao termina, TODO ciclo
    e uma pequena estreia: descobre um lote que sempre esteve la.

    Com 43 termos e 10 por ciclo, a varredura leva cerca de 5 ciclos --
    aproximadamente 15 horas. Ate la tudo vai pro resumo. Depois disso
    "vaga nova" passa a significar de verdade "publicada desde o ultimo
    ciclo", que e a unica coisa que merece interromper o seu dia.
    """
    return int(deposito.ler(CHAVE_VOLTAS) or 0) < 1


# ---------------------------------------------------------------------------
# Digest e batimento
# ---------------------------------------------------------------------------

def deve_enviar_digest(agora: datetime | None = None) -> bool:
    """"Ainda não enviei hoje E já passou da hora" — nunca "a hora é X".

    A diferença entre as duas formulações é a diferença entre um recurso que
    funciona e um que nunca rodou uma única vez.

    Com janela exata (`agora.hour == HORA`), um ciclo que começa às 09:00 e
    dura 80 minutos termina às 10:20, quando a janela já fechou. O digest
    nunca dispara. E uma rede de segurança do tipo "se pulou um dia, manda
    no ciclo seguinte" exigiria um envio anterior registrado — ou seja,
    dependeria exatamente daquilo que deveria consertar, e nunca ligaria. A
    fila cresceria sem nunca chegar ao Telegram, sem erro nenhum.

    A formulação atual não precisa de rede de segurança: se o ciclo das
    09:00 falhar, o das 12:00 manda, porque "já passou de 9" continua
    verdadeiro o dia inteiro.
    """
    agora = agora or datetime.now(timezone.utc)
    if deposito.ja_feito_hoje(CHAVE_DIGEST, agora.date()):
        return False
    return agora.hour >= config.HORA_RESUMO_UTC


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

    if telegram.enviar_resumo(pendentes):
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



async def _buscar_termos(fonte, termos: list[str]) -> list[Resultado]:
    """Roda os termos de uma fonte respeitando o teto dela."""
    # Fonte que varre por conta própria roda UMA vez, não uma por termo.
    # Sem isso a varredura por empresa custaria 15x o mesmo resultado.
    if fonte.ignora_termos:
        return [await com_protecao(fonte, "")]

    if not fonte.max_simultaneas:
        return await asyncio.gather(*[com_protecao(fonte, t) for t in termos])

    porteiro = asyncio.Semaphore(fonte.max_simultaneas)

    async def _uma(termo: str) -> Resultado:
        async with porteiro:
            resultado = await com_protecao(fonte, termo)
            if fonte.pausa:
                # Dentro do semáforo de propósito: a pausa só vale se o
                # próximo termo esperar por ela. Fora, todos dormiriam
                # juntos e a rajada aconteceria do mesmo jeito.
                await asyncio.sleep(fonte.pausa)
            return resultado

    return await asyncio.gather(*[_uma(t) for t in termos])


# ---------------------------------------------------------------------------
# O ciclo
# ---------------------------------------------------------------------------

async def rodar(so_teste: bool = False) -> dict:
    """Roda um ciclo completo. `so_teste` não envia nada nem grava."""
    agora = datetime.now(timezone.utc)
    regras = regras_do_perfil()
    cidades_distantes = set(config.CIDADES_DISTANTES)

    deposito.iniciar()
    telegram.zerar_placar()

    # PRIMEIRA EXECUÇÃO: nada alerta na hora, tudo vai pro resumo.
    #
    # Com o banco vazio, TODA vaga que existe hoje nos portais é "nova" — o
    # ciclo de estreia encontra dezenas de uma vez. Sem esta guarda, todas as
    # que passam do limiar viram alerta imediato e chegam juntas: vinte e
    # tantas mensagens seguidas, num dia em que nenhuma delas é urgente (elas
    # já estavam lá antes de o robô existir).
    #
    # O custo de errar aqui não é só incômodo. Alerta imediato que chega em
    # rajada ensina a ignorar alerta imediato, e aí ele para de funcionar
    # justamente quando passar a significar alguma coisa.
    #
    # Um resumo único, ranqueado, entrega a mesma informação melhor. Do
    # segundo ciclo em diante o comportamento normal volta sozinho.
    estreia = em_varredura_inicial()
    if estreia:
        logger.info(
            "Varredura inicial em andamento: nada alerta na hora, tudo entra "
            "no resumo. Alerta imediato liga quando o rodízio cobrir todos os "
            "termos uma vez."
        )

    if not so_teste:
        processados = telegram.processar_feedback(deposito)
        if processados:
            logger.info("%d resposta(s) de 👍/👎 registrada(s).", processados)

    termos = termos_desta_rodada()
    todas = registro.fontes_ativas()
    fontes = registro.do_ciclo(todas, _primeiro_ciclo_do_dia(agora))

    logger.info(
        "Ciclo iniciado: %d termo(s) × %d fonte(s).", len(termos), len(fontes)
    )

    brutas = 0
    aprovadas = 0
    novas = 0
    com_problema: list[str] = []
    rejeitadas_por_mercado: Counter = Counter()
    barradas_pelo_titulo = 0
    sem_mercado_declarado = 0
    excluidas = 0
    ja_notificadas: set[str] = set()

    for fonte in fontes:
        # Termos em paralelo — o gargalo é rede, não CPU, e esperar uma
        # resposta de cada vez multiplicaria o ciclo pelo número de termos.
        #
        # Com teto por fonte, e o teto não é excesso de zelo. MEDIDO: uma
        # busca isolada no LinkedIn responde 200; os 15 termos disparados de
        # uma vez falharam TODOS por limite de taxa. Fonte que precisa de
        # calma declara `max_simultaneas` e `pausa` — ver fontes/base.py.
        resultados: list[Resultado] = await _buscar_termos(fonte, termos)

        falhas = sum(1 for r in resultados if r.falhou)
        vagas_da_fonte = [v for r in resultados for v in r.vagas]
        brutas += len(vagas_da_fonte)

        # Fonte que falhou em TODOS os termos está com problema. Falhar em
        # um termo e responder nos outros é ruído de rede, não bloqueio.
        if falhas == len(termos) and termos:
            com_problema.append(fonte.nome)

        aprovadas_da_fonte = 0
        ineditas_da_fonte = 0

        for vaga in vagas_da_fonte:
            # Descoberta de empresa acontece ANTES do filtro e independe
            # dele: registra toda empresa que publicou numa cidade do
            # corredor, mesmo que ESTA vaga não sirva. Uma metalúrgica de
            # Tatuí que hoje publicou "Soldador" é exatamente a empresa que
            # mês que vem publica "Analista de PCP" — filtrar a descoberta
            # por cargo descartaria as empresas certas pela vaga errada.
            cidade_da_vaga = casa_cidade(vaga.local, config.CIDADES, config.UF_BUSCA)
            if cidade_da_vaga and vaga.empresa:
                deposito.registrar_empresa(
                    vaga.empresa, vaga.fonte, cidade_da_vaga, vaga.empresa_url
                )

            resultado = avaliar(vaga, regras)

            if resultado.escopo_rejeitado:
                rejeitadas_por_mercado.update(resultado.escopo_rejeitado)

            if resultado.excluida:
                excluidas += 1
                continue

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
                and not resultado.mercado_declarado
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
            chave = vaga.chave_secundaria
            if vaga.id in ja_notificadas or (chave and chave in ja_notificadas):
                continue
            ja_notificadas.add(vaga.id)
            if chave:
                ja_notificadas.add(chave)

            if deposito.vaga_conhecida(vaga):
                continue

            nota = pontuar(
                vaga, regras, resultado,
                niveis_alvo=config.NIVEIS_ALVO,
                niveis_acima=config.NIVEIS_ACIMA_DO_ALVO,
                dias_vaga_antiga=config.DIAS_PARA_VAGA_ANTIGA,
                cidades_distantes=cidades_distantes,
            )
            antiga = vaga.antiga(config.DIAS_PARA_VAGA_ANTIGA)
            # Cidade distante NUNCA alerta na hora — vai pro resumo,
            # independente da nota.
            #
            # Isto é política, não ajuste de peso. Dava pra chegar no mesmo
            # efeito baixando PESO_CIDADE_DISTANTE até a conta não alcançar o
            # limiar, e seria frágil: qualquer mexida futura em outro peso
            # reabriria o caminho sem ninguém perceber. A regra que se quer é
            # "vaga a duas horas de casa não interrompe seu dia" — então ela
            # está escrita assim, e continua valendo mesmo que a escala mude.
            longe = resultado.cidade in cidades_distantes
            imediata = (
                nota.total >= config.LIMIAR_ALERTA_IMEDIATO
                and not antiga
                and not estreia
                and not longe
            )

            if so_teste:
                logger.info(
                    "  [teste] %s (%d/10) %s — %s | %s",
                    "AGORA " if imediata else "resumo",
                    nota.total, vaga.titulo, vaga.empresa, vaga.local,
                )
                novas += 1
                ineditas_da_fonte += 1
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
            ineditas_da_fonte += 1

        logger.info(
            "[%s] %d brutas → %d aprovadas → %d novas%s",
            fonte.nome, len(vagas_da_fonte), aprovadas_da_fonte, ineditas_da_fonte,
            f" ({falhas} termo(s) com falha)" if falhas else "",
        )

    logger.info(
        "Ciclo concluído: %d brutas → %d aprovadas → %d novas.", brutas, aprovadas, novas
    )

    # Diagnóstico que só existe se for medido. Escopo mal reconhecido barra
    # a vaga do jeito certo e some sem rastro — um falso negativo em massa
    # passaria despercebido sem esta linha.
    if rejeitadas_por_mercado:
        detalhe = "; ".join(f"{m} ({n})" for m, n in rejeitadas_por_mercado.most_common())
        logger.info("Descartadas por mercado: %s", detalhe)
    if excluidas:
        logger.info(
            "%d vaga(s) descartadas por exclusão no título (ver "
            "config.EXCLUSOES_TITULO).",
            excluidas,
        )
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

    placar = telegram.placar_de_entrega()
    if placar["tentativas"] and placar["falhas"] == placar["tentativas"]:
        # TODAS as mensagens falharam. Isso não é rede instável — é
        # configuração quebrada (token errado, chat_id errado, bot
        # bloqueado). O ciclo inteiro rodou e nada chegou em ninguém.
        logger.error(
            "NENHUMA das %d mensagens foi entregue. O ciclo rodou e você não "
            "recebeu nada — confira TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.",
            placar["tentativas"],
        )
    elif placar["falhas"]:
        logger.warning(
            "%d de %d mensagens falharam. As vagas afetadas não foram gravadas "
            "e voltam no próximo ciclo.",
            placar["falhas"], placar["tentativas"],
        )

    return {
        "brutas": brutas,
        "aprovadas": aprovadas,
        "novas": novas,
        "fontes_com_problema": com_problema,
        "entregas": placar,
    }
