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

UMA COLETA, VÁRIOS PERFIS. As fontes são consultadas uma vez, com a união
dos termos e da geografia de todos os perfis ativos. Cada vaga coletada é
avaliada contra cada perfil separadamente — filtro, nota, deduplicação,
alerta e fila são todos POR PERFIL. Ver radar/perfis.py.
"""

import asyncio
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from radar import config, deposito, perfis as perfis_mod
from radar.alertas import telegram
from radar.filtro import Regras, avaliar, barrada_so_pelo_cargo
from radar.fontes import registro
from radar.fontes.base import Resultado, com_protecao
from radar.localizacao import casa_cidade
from radar.log import logger
from radar.perfis import Perfil
from radar.relevancia import pontuar

CHAVE_RODIZIO = "rodizio_offset"
CHAVE_VOLTAS = "rodizio_voltas"
CHAVE_BATIMENTO = "batimento_ultimo_dia"
CHAVE_DIGEST = "digest_ultimo_dia"
CHAVE_FONTES_DIARIAS = "fontes_diarias_ultimo_dia"

# Quanto dura a estreia de um perfil novo: nada alerta na hora, tudo vai pro
# resumo. Ver perfil_em_estreia.
DURACAO_ESTREIA = timedelta(hours=30)


def regras_do_perfil() -> Regras:
    """As regras do perfil principal. Mantida pra quem já chamava assim."""
    return perfis_mod.perfil_principal().regras


# ---------------------------------------------------------------------------
# Rodízio de termos
# ---------------------------------------------------------------------------

def termos_desta_rodada(perfis: list[Perfil] | None = None) -> list[str]:
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

    Com vários perfis, prioritários e rodízio são a UNIÃO dos perfis: termo
    repetido entre dois perfis é buscado uma vez só.
    """
    perfis = perfis if perfis is not None else [perfis_mod.perfil_principal()]
    prioritarios, resto = perfis_mod.termos_da_coleta(perfis)

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


def perfil_em_estreia(perfil: Perfil, agora: datetime) -> bool:
    """O perfil acabou de chegar: tudo dele vai pro resumo, nada alerta.

    É a mesma lição de em_varredura_inicial, agora por pessoa. Um perfil
    convidado que entra hoje vê pela primeira vez tudo o que já estava
    publicado na cidade dele — e "novo pra ele" não é "novo no mundo".

    30 horas e não "um ciclo": a varredura por cidade e por empresa roda só
    no primeiro ciclo do dia (UTC), e o rodízio de termos leva ~18 horas pra
    dar a volta. Quem é vinculado logo depois do ciclo diário só passa por
    ele ~24 horas depois; 30 cobre isso com folga.

    O começo é marcado no vínculo do convite; perfil sem marca (o principal
    num banco novo) ganha a marca agora.
    """
    if em_varredura_inicial():
        return True
    chave = f"inicio:{perfil.id}"
    inicio = deposito.ler(chave)
    if inicio is None:
        deposito.gravar(chave, agora.isoformat(timespec="seconds"))
        return True
    return agora < datetime.fromisoformat(inicio) + DURACAO_ESTREIA


# ---------------------------------------------------------------------------
# Destino, digest e batimento — por perfil
# ---------------------------------------------------------------------------

def destino_do_perfil(perfil: Perfil) -> str | None:
    """O chat do Telegram deste perfil, ou None se ainda não há.

    O principal vem de variável de ambiente (o secret de sempre). Convidado
    vem do vínculo feito pelo convite.

    .strip() pelo mesmo motivo de config.TELEGRAM_BOT_TOKEN: um "\\r" colado
    junto com o valor do secret vira chat inexistente.
    """
    if perfil.chat_id_env:
        return (os.getenv(perfil.chat_id_env) or "").strip() or None
    return deposito.destino(perfil.id)


def dia_local(perfil: Perfil, agora: datetime) -> datetime:
    """`agora` (UTC) no fuso do perfil.

    Conta feita com offset explícito, nunca com o relógio da máquina: dá o
    mesmo resultado no GitHub Actions (UTC) e em casa (UTC-3).
    """
    return agora.astimezone(timezone.utc) + timedelta(hours=perfil.fuso_utc)


def deve_enviar_digest(perfil: Perfil, agora: datetime | None = None) -> bool:
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

    Hora e dia no fuso do PERFIL: cada pessoa recebe o resumo da manhã dela.
    """
    agora = agora or datetime.now(timezone.utc)
    local = dia_local(perfil, agora)
    if deposito.ja_feito_hoje(f"{CHAVE_DIGEST}:{perfil.id}", local.date()):
        return False
    return local.hour >= perfil.hora_resumo


def enviar_digest_se_for_hora(perfil: Perfil, chat_id: str, agora: datetime | None = None) -> int:
    agora = agora or datetime.now(timezone.utc)
    if not deve_enviar_digest(perfil, agora):
        return 0

    chave = f"{CHAVE_DIGEST}:{perfil.id}"
    hoje: date = dia_local(perfil, agora).date()
    pendentes = deposito.pendentes_do_digest(perfil.id)
    if not pendentes:
        # Marca mesmo sem vaga nenhuma. Sem isso a condição fica verdadeira
        # em todo ciclo seguinte do dia, e o log enche de tentativa vazia.
        deposito.marcar_feito_hoje(chave, hoje)
        return 0

    if telegram.enviar_resumo(pendentes, chat_id, perfil.max_resumo):
        deposito.limpar_fila_do_digest(perfil.id)
        deposito.marcar_feito_hoje(chave, hoje)
        logger.info("[%s] Resumo diário enviado: %d vaga(s).", perfil.id, len(pendentes))
        return len(pendentes)

    # Não marca nem limpa: tenta de novo no próximo ciclo. Repetir uma
    # mensagem é barato; perder a fila inteira não é.
    logger.warning(
        "[%s] Falha ao enviar o resumo (%d vaga(s) pendentes) — tenta no próximo ciclo.",
        perfil.id, len(pendentes),
    )
    return 0


def enviar_batimento_se_for_o_primeiro(
    perfil: Perfil, chat_id: str, novas: int, com_problema: list[str],
    total_fontes: int, agora: datetime | None = None, perfis_ativos: int = 1,
) -> bool:
    agora = agora or datetime.now(timezone.utc)
    local = dia_local(perfil, agora)
    chave = f"{CHAVE_BATIMENTO}:{perfil.id}"
    if deposito.ja_feito_hoje(chave, local.date()):
        return False
    hora = f"{local:%H:%M} (UTC{perfil.fuso_utc:+d})"
    if telegram.batimento(chat_id, novas, com_problema, total_fontes, hora, perfis_ativos):
        deposito.marcar_feito_hoje(chave, local.date())
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
# Uma vaga, um perfil
# ---------------------------------------------------------------------------

@dataclass
class EstadoDoPerfil:
    """O que o ciclo acumula pra um perfil enquanto percorre as fontes."""

    perfil: Perfil
    chat_id: str | None
    estreia: bool
    aprovadas: int = 0
    novas: int = 0
    alertas: int = 0
    excluidas: int = 0
    barradas_pelo_titulo: int = 0
    sem_mercado_declarado: int = 0
    rejeitadas_por_mercado: Counter = field(default_factory=Counter)
    # Dedup dentro do próprio ciclo, por perfil.
    ja_notificadas: set[str] = field(default_factory=set)


def processar_vaga(vaga, fonte, est: EstadoDoPerfil, so_teste: bool) -> bool:
    """Filtra, pontua, deduplica, avisa e grava UMA vaga pra UM perfil.

    Devolve True se a vaga foi nova pra este perfil.
    """
    perfil = est.perfil
    regras = perfil.regras
    resultado = avaliar(vaga, regras)

    if resultado.escopo_rejeitado:
        est.rejeitadas_por_mercado.update(resultado.escopo_rejeitado)

    if resultado.excluida:
        est.excluidas += 1
        return False

    if not resultado.aprovada:
        if barrada_so_pelo_cargo(vaga, regras):
            est.barradas_pelo_titulo += 1
        return False

    # Agregador estrangeiro precisa DECLARAR o mercado. Ver
    # `exige_mercado_declarado` em fontes/base.py: num portal brasileiro,
    # remoto sem escopo quer dizer Brasil; num agregador de vaga em inglês,
    # quer dizer o contrário.
    if fonte.exige_mercado_declarado and resultado.remota and not resultado.mercado_declarado:
        est.sem_mercado_declarado += 1
        return False

    est.aprovadas += 1

    # Dedup dentro do próprio ciclo, antes de consultar o banco: a mesma vaga
    # costuma vir por dois termos diferentes da mesma fonte, e sem isso ela
    # seria notificada duas vezes na mesma execução (o banco só a conheceria
    # depois da primeira gravação).
    #
    # As DUAS chaves, não só o link. MEDIDO na primeira execução real:
    # "Analista de PCP (Planejamento e Controle de Produção)" da mesma
    # empresa apareceu duas vezes no mesmo ciclo, com links diferentes — a
    # empresa publicou a vaga duas vezes. O link sozinho não pega isso;
    # empresa+título pega.
    chave = vaga.chave_secundaria
    if vaga.id in est.ja_notificadas or (chave and chave in est.ja_notificadas):
        return False
    est.ja_notificadas.add(vaga.id)
    if chave:
        est.ja_notificadas.add(chave)

    if deposito.vaga_conhecida(vaga, perfil.id):
        return False

    nota = pontuar(
        vaga, regras, resultado,
        niveis_alvo=set(perfil.niveis_alvo),
        niveis_acima=set(perfil.niveis_acima),
        dias_vaga_antiga=perfil.dias_vaga_antiga,
        cidades_distantes=set(perfil.cidades_distantes),
    )
    antiga = vaga.antiga(perfil.dias_vaga_antiga)
    # Cidade distante NUNCA alerta na hora — vai pro resumo, independente da
    # nota.
    #
    # Isto é política, não ajuste de peso. Dava pra chegar no mesmo efeito
    # baixando PESO_CIDADE_DISTANTE até a conta não alcançar o limiar, e
    # seria frágil: qualquer mexida futura em outro peso reabriria o caminho
    # sem ninguém perceber. A regra que se quer é "vaga a duas horas de casa
    # não interrompe seu dia" — então ela está escrita assim, e continua
    # valendo mesmo que a escala mude.
    longe = resultado.cidade in perfil.cidades_distantes
    estreia = est.estreia
    imediata = (
        perfil.alerta_imediato
        and nota.total >= perfil.limiar_imediato
        and not antiga
        and not estreia
        and not longe
    )

    if so_teste:
        logger.info(
            "  [teste:%s] %s (%d/10) %s — %s | %s",
            perfil.id, "AGORA " if imediata else "resumo",
            nota.total, vaga.titulo, vaga.empresa, vaga.local,
        )
        est.novas += 1
        return True

    if imediata:
        enviou = telegram.alerta_vaga(
            vaga, est.chat_id, nota.total, str(nota), perfil.dias_vaga_antiga
        )
        if not enviou:
            # NÃO grava. A vaga volta no próximo ciclo — ver o cabeçalho
            # deste arquivo.
            logger.warning(
                "[%s] Falha ao alertar '%s' — não gravada, volta no próximo ciclo.",
                perfil.id, vaga.titulo,
            )
            return False
        est.alertas += 1
        logger.info("[%s] Alerta enviado (%d/10): %s", perfil.id, nota.total, vaga.titulo)

    deposito.salvar(vaga, perfil.id, nota.total, str(nota), digest_pendente=not imediata)
    est.novas += 1
    return True


def _cidade_de_algum_perfil(vaga, perfis: list[Perfil]) -> str | None:
    for p in perfis:
        cidade = casa_cidade(vaga.local, p.regras.cidades, p.uf)
        if cidade:
            return cidade
    return None


def _diagnostico(est: EstadoDoPerfil) -> None:
    """Diagnóstico que só existe se for medido. Escopo mal reconhecido barra
    a vaga do jeito certo e some sem rastro — um falso negativo em massa
    passaria despercebido sem estas linhas."""
    pid = est.perfil.id
    logger.info(
        "[%s] %d aprovadas → %d novas (%d alerta(s) na hora)%s",
        pid, est.aprovadas, est.novas, est.alertas, " — em estreia" if est.estreia else "",
    )
    if est.rejeitadas_por_mercado:
        detalhe = "; ".join(f"{m} ({n})" for m, n in est.rejeitadas_por_mercado.most_common())
        logger.info("[%s] Descartadas por mercado: %s", pid, detalhe)
    if est.excluidas:
        logger.info("[%s] %d vaga(s) descartadas por exclusão no título.", pid, est.excluidas)
    if est.sem_mercado_declarado:
        logger.info(
            "[%s] %d vaga(s) de agregador estrangeiro descartadas por não declarar "
            "mercado — ver exige_mercado_declarado em fontes/base.py.",
            pid, est.sem_mercado_declarado,
        )
    if est.barradas_pelo_titulo:
        logger.info(
            "[%s] %d vaga(s) em local aceito foram barradas só pelo título "
            "(preço da decisão de ler só o título — se este número crescer "
            "com vaga boa, vale reconsiderar).",
            pid, est.barradas_pelo_titulo,
        )


# ---------------------------------------------------------------------------
# O ciclo
# ---------------------------------------------------------------------------

async def rodar(so_teste: bool = False, perfis: list[Perfil] | None = None) -> dict:
    """Roda um ciclo completo.

    `so_teste` não envia nada e não consome o que chegou do Telegram. Quem
    chama (main.py --teste) também aponta o banco pra uma cópia descartável,
    o que garante que nada é gravado no banco real — sem depender de cada
    função lembrar de não gravar.
    """
    agora = datetime.now(timezone.utc)
    deposito.iniciar()
    telegram.zerar_placar()

    todos = [p for p in (perfis if perfis is not None else perfis_mod.todos()) if p.ativo]

    chats: dict[str, Perfil] = {}
    for p in todos:
        chat = destino_do_perfil(p)
        if chat:
            chats[chat] = p

    if not so_teste:
        recebido = telegram.processar_atualizacoes(deposito, todos, chats)
        if recebido["votos"] or recebido["comandos"]:
            logger.info(
                "Telegram: %d voto(s) 👍/👎 e %d comando(s) processados.",
                recebido["votos"], recebido["comandos"],
            )

    estados: list[EstadoDoPerfil] = []
    for p in todos:
        chat = destino_do_perfil(p)
        if not chat and not so_teste:
            logger.info("[%s] sem chat vinculado ainda — fora deste ciclo.", p.id)
            continue
        if deposito.ler(f"pausado:{p.id}") == "1":
            logger.info("[%s] pausado pela própria pessoa — fora deste ciclo.", p.id)
            continue
        estados.append(EstadoDoPerfil(perfil=p, chat_id=chat, estreia=perfil_em_estreia(p, agora)))

    if not estados:
        logger.warning(
            "Nenhum perfil com destino. Confira TELEGRAM_CHAT_ID (perfil principal) "
            "ou os convites dos perfis em perfis/."
        )
        return {"brutas": 0, "aprovadas": 0, "novas": 0, "fontes_com_problema": [],
                "entregas": telegram.placar_de_entrega(), "por_perfil": {}}

    ativos = [e.perfil for e in estados]

    if not so_teste:
        for est in estados:
            descartadas = deposito.descartar_pendentes_fora_do_perfil(est.perfil.id, est.perfil.regras)
            if descartadas:
                logger.info(
                    "[%s] %d vaga(s) retirada(s) do resumo por não atender mais ao perfil.",
                    est.perfil.id, descartadas,
                )

    for est in estados:
        if est.estreia:
            logger.info(
                "[%s] Em estreia: nada alerta na hora, tudo entra no resumo.", est.perfil.id
            )

    termos = termos_desta_rodada(ativos)
    todas = registro.fontes_ativas(ativos)
    fontes = registro.do_ciclo(todas, _primeiro_ciclo_do_dia(agora))

    logger.info(
        "Ciclo iniciado: %d termo(s) × %d fonte(s) × %d perfil(is).",
        len(termos), len(fontes), len(estados),
    )

    brutas = 0
    com_problema: list[str] = []

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
        if falhas == len(resultados) and resultados:
            com_problema.append(fonte.nome)

        novas_da_fonte = 0
        for vaga in vagas_da_fonte:
            # Descoberta de empresa acontece ANTES do filtro e independe
            # dele: registra toda empresa que publicou numa cidade de algum
            # perfil, mesmo que ESTA vaga não sirva. Uma metalúrgica de Tatuí
            # que hoje publicou "Soldador" é exatamente a empresa que mês que
            # vem publica "Analista de PCP" — filtrar a descoberta por cargo
            # descartaria as empresas certas pela vaga errada.
            cidade_da_vaga = _cidade_de_algum_perfil(vaga, ativos)
            if cidade_da_vaga and vaga.empresa:
                deposito.registrar_empresa(
                    vaga.empresa, vaga.fonte, cidade_da_vaga, vaga.empresa_url
                )

            for est in estados:
                if processar_vaga(vaga, fonte, est, so_teste):
                    novas_da_fonte += 1

        logger.info(
            "[%s] %d brutas → %d nova(s) somando os perfis%s",
            fonte.nome, len(vagas_da_fonte), novas_da_fonte,
            f" ({falhas} termo(s) com falha)" if falhas else "",
        )

    for est in estados:
        _diagnostico(est)

    aprovadas = sum(e.aprovadas for e in estados)
    novas = sum(e.novas for e in estados)
    logger.info(
        "Ciclo concluído: %d brutas → %d aprovadas → %d novas (somando %d perfil(is)).",
        brutas, aprovadas, novas, len(estados),
    )

    if not so_teste:
        admins = [e for e in estados if e.perfil.admin and e.chat_id]
        if telegram.deve_alertar_saude(len(com_problema), len(fontes)):
            for est in admins:
                telegram.alerta_saude(est.chat_id, com_problema, len(fontes))
        for est in admins:
            enviar_batimento_se_for_o_primeiro(
                est.perfil, est.chat_id, novas, com_problema, len(fontes), agora,
                perfis_ativos=len(estados),
            )
        for est in estados:
            enviar_digest_se_for_hora(est.perfil, est.chat_id, agora)
        total = deposito.registrar_total()
        logger.info("Banco: %d vaga(s) no catálogo.", total)

    placar = telegram.placar_de_entrega()
    if placar["tentativas"] and placar["falhas"] == placar["tentativas"]:
        # TODAS as mensagens falharam. Isso não é rede instável — é
        # configuração quebrada (token errado, chat_id errado, bot
        # bloqueado). O ciclo inteiro rodou e nada chegou em ninguém.
        logger.error(
            "NENHUMA das %d mensagens foi entregue. O ciclo rodou e ninguém "
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
        "por_perfil": {e.perfil.id: e.novas for e in estados},
    }
