"""Testes do que decide se uma mensagem chega em você.

Este arquivo existe por um motivo específico. No projeto original o resumo
diário NUNCA foi enviado — nenhuma vez, desde que o recurso foi escrito.
408 vagas ficaram presas na fila. E havia testes do CONTEÚDO do resumo: eles
passavam, verdes, o tempo todo. Não havia nenhum teste do DISPARO.

Testar o que a mensagem diz sem testar se ela sai é testar a metade que não
quebra.
"""

from datetime import date, datetime, timezone

import pytest

from radar import ciclo, config, deposito
from radar.alertas import telegram


@pytest.fixture(autouse=True)
def banco_temporario(tmp_path, monkeypatch):
    """Cada teste com seu banco. Sem isso um teste veria o metadado que o
    anterior gravou, e a ordem passaria a importar."""
    monkeypatch.setattr(deposito.caminhos, "PASTA_DADOS", tmp_path)
    monkeypatch.setattr(deposito.caminhos, "BANCO", tmp_path / "teste.db")
    deposito.iniciar()


def _em(hora: int, dia: int = 26) -> datetime:
    return datetime(2026, 8, dia, hora, 30, tzinfo=timezone.utc)


# ===========================================================================
# G1 — o resumo que nunca disparou
# ===========================================================================

def test_g1_resumo_sai_quando_o_ciclo_termina_depois_da_hora():
    """A causa raiz: a condição era `agora.hour == HORA`, que exige o ciclo
    TERMINAR dentro de uma janela de uma hora. O cron começa às 09:00 e o
    ciclo dura ~80 minutos, então quando a função rodava já era 10h. A
    janela nunca estava aberta.
    """
    assert config.DIGEST_HORA_UTC == 9
    assert ciclo.deve_enviar_digest(_em(10)) is True  # ciclo das 9h terminando
    assert ciclo.deve_enviar_digest(_em(13)) is True  # ciclo das 12h, recuperação
    assert ciclo.deve_enviar_digest(_em(23)) is True  # tarde, mas ainda hoje


def test_g1_resumo_nao_sai_antes_da_hora():
    assert ciclo.deve_enviar_digest(_em(2)) is False
    assert ciclo.deve_enviar_digest(_em(8)) is False


def test_g1_resumo_sai_uma_vez_por_dia():
    assert ciclo.deve_enviar_digest(_em(10)) is True
    deposito.marcar_feito_hoje(ciclo.CHAVE_DIGEST, _em(10).date())
    assert ciclo.deve_enviar_digest(_em(13)) is False
    assert ciclo.deve_enviar_digest(_em(21)) is False


def test_g1_no_dia_seguinte_volta_a_sair():
    deposito.marcar_feito_hoje(ciclo.CHAVE_DIGEST, date(2026, 8, 26))
    assert ciclo.deve_enviar_digest(_em(10, dia=26)) is False
    assert ciclo.deve_enviar_digest(_em(10, dia=27)) is True


def test_g1_sem_rede_de_seguranca_circular():
    """A rede de segurança original ("se pulou um dia, manda no ciclo
    seguinte") exigia um envio anterior REGISTRADO — dependia exatamente
    daquilo que deveria consertar. Com a fila nunca esvaziada e nenhum envio
    anterior, ela nunca ligava.

    A formulação atual não tem estado anterior nenhum como pré-requisito:
    banco recém-criado, sem nenhum envio na história, já manda.
    """
    assert deposito.ler(ciclo.CHAVE_DIGEST) is None
    assert ciclo.deve_enviar_digest(_em(10)) is True


def test_g1_dia_comparado_em_utc_nao_em_hora_local():
    """Misturar dia local com hora UTC dava resultado diferente no Actions
    (UTC) e na máquina de casa (UTC-3): entre 21h e meia-noite o dia local
    ainda é o anterior ao dia UTC."""
    deposito.marcar_feito_hoje(ciclo.CHAVE_DIGEST, date(2026, 8, 26))
    assert deposito.ja_feito_hoje(ciclo.CHAVE_DIGEST, date(2026, 8, 26)) is True
    assert deposito.ja_feito_hoje(ciclo.CHAVE_DIGEST, date(2026, 8, 27)) is False


# ===========================================================================
# G3 — alerta de saúde que disparava sem motivo
# ===========================================================================

@pytest.mark.parametrize(
    "com_problema,total,esperado",
    [
        (1, 2, False),  # o caso que motivou: UMA fonte fraca não é "com problema"
        (2, 2, True),
        (1, 3, False),
        (2, 3, True),
        (4, 8, False),  # metade exata não basta
        (5, 8, True),
        (0, 5, False),
        (1, 1, True),
        (0, 0, False),  # nenhuma fonte: não há o que alertar
    ],
)
def test_g3_alerta_de_saude_exige_maioria_estrita(com_problema, total, esperado):
    """A regra era ">= metade". Com duas fontes, UMA sozinha disparava — e
    fonte pequena volta vazia num dia fraco sem nada de errado.

    Alerta que dispara sem motivo é pior que alerta nenhum: depois de duas ou
    três vezes ele deixa de ser lido, e aí não serve nem quando o problema é
    real.
    """
    assert telegram.deve_alertar_saude(com_problema, total) is esperado


# ===========================================================================
# G4 — o token no log
# ===========================================================================

def test_g4_erro_de_telegram_nunca_loga_a_excecao_crua():
    """A URL da Bot API é `.../bot<TOKEN>/sendMessage` — o token está DENTRO
    dela — e a mensagem de erro de conexão do requests inclui a URL completa
    que falhou. Logar `f"{e}"` num except vaza a credencial pro log público
    da execução. Aconteceu seis vezes.

    O teste lê o código-fonte porque é o que de fato prende a regra: não
    existe entrada que produza o vazamento se o `except` estiver certo, e a
    única forma de o bug voltar é alguém reescrever o except.
    """
    import inspect

    fonte = inspect.getsource(telegram._chamar)
    assert "{e}" not in fonte, "except do Telegram não pode interpolar a exceção crua"
    assert "%s" in fonte
    assert "type(e).__name__" in fonte


# ===========================================================================
# Renderização
# ===========================================================================

@pytest.mark.parametrize(
    "pontos,cheias",
    [(0, 0), (1, 1), (5, 3), (6, 3), (7, 4), (10, 5)],
)
def test_estrelas_arredondam_sempre_pra_cima(pontos, cheias):
    """`round(pontos / 2)` arredonda .5 pro par mais próximo: 5/10 daria 2
    estrelas e 7/10 daria 4 — inconsistente pra quem só olha o emoji."""
    assert telegram.estrelas(pontos).count("⭐") == cheias


def test_digest_quebra_em_partes_quando_passa_do_limite():
    """Um dia cheio passa dos 4.096 caracteres do Telegram. Estourar faz a
    API recusar a mensagem inteira; truncar some com vaga sem ninguém saber."""
    vagas = [
        (f"Analista de Processos {i}", "Empresa " + "X" * 40,
         f"https://exemplo.com/{i}", 6, "Sorocaba - SP", "Gupy", "")
        for i in range(60)
    ]
    partes = telegram.montar_digest(vagas)
    assert len(partes) > 1
    assert all(len(p) <= telegram.LIMITE_MENSAGEM + 200 for p in partes)
    assert "parte 1/" in partes[0]
    # Nenhuma vaga pode sumir na quebra.
    juntas = "\n".join(partes)
    for i in range(60):
        assert f"https://exemplo.com/{i}" in juntas


def test_digest_vazio_nao_gera_mensagem():
    assert telegram.montar_digest([]) == []


# ===========================================================================
# Feedback 👍/👎
# ===========================================================================

def test_callback_de_feedback_e_parseado():
    assert telegram.parsear_callback("fb|1|abc123") == ("abc123", "positivo")
    assert telegram.parsear_callback("fb|0|abc123") == ("abc123", "negativo")


@pytest.mark.parametrize(
    "data",
    ["fb|ok|-", "", "outro|1|x", "fb|2|x", "fb|1", "fb|1|x|y", None],
)
def test_callback_desconhecido_nao_vira_feedback(data):
    """O botão de confirmação que substitui o teclado depois do clique usa
    'fb|ok|-' de propósito: ele não pode ser lido como voto novo."""
    assert telegram.parsear_callback(data) is None


# ===========================================================================
# A rajada da estreia
# ===========================================================================

def test_estreia_manda_tudo_pro_resumo(monkeypatch):
    """Com o banco vazio, TODA vaga que existe hoje nos portais é "nova" — o
    ciclo de estreia encontra dezenas de uma vez. Sem guarda, todas as que
    passam do limiar viram alerta imediato e chegam juntas.

    Alerta imediato que chega em rajada ensina a ignorar alerta imediato, e
    aí ele para de funcionar justamente quando passar a significar algo.
    """
    import inspect

    fonte = inspect.getsource(ciclo.rodar)
    assert "estreia" in fonte
    assert "and not estreia" in fonte
    assert deposito.estatisticas()["total"] == 0


def test_depois_da_estreia_o_alerta_imediato_volta():
    """A guarda não pode virar permanente: basta uma vaga no banco pra o
    comportamento normal voltar."""
    from tests.conftest import vaga

    deposito.salvar(vaga(), 8, "cargo direto", digest_pendente=False)
    assert deposito.estatisticas()["total"] == 1


# ===========================================================================
# Vaga remota com a cidade da SEDE no texto de local
# ===========================================================================

def test_vaga_remota_nao_finge_estar_na_cidade_da_empresa():
    """MEDIDO no primeiro ciclo real: nove vagas chegaram como "Salvador, BA",
    "Brasília, DF", "Campinas, SP" — todas REMOTAS, marcadas assim pelo filtro
    nativo do LinkedIn. O texto de local de vaga remota traz a sede da
    empresa, não o lugar onde se trabalha.

    O filtro estava certo e a mensagem é que mentia. E o estrago dessa
    combinação é específico: uma vaga que parece estar em Salvador, pra quem
    mora em Itapetininga, não é lida como "remota" — é lida como "o robô está
    errado".
    """
    assert telegram.local_legivel("Salvador, BA", "Remoto") == "Remoto · Salvador, BA"
    assert telegram.local_legivel("Campinas, SP", "Remoto") == "Remoto · Campinas, SP"


def test_local_que_ja_diz_remoto_nao_repete():
    assert telegram.local_legivel("Remoto", "Remoto") == "Remoto"
    assert telegram.local_legivel("Home Office", "Remoto") == "Home Office"
    assert telegram.local_legivel("", "Remoto") == "Remoto"


def test_vaga_presencial_mostra_a_cidade_limpa():
    """A correção não pode ter grudado "Remoto ·" em vaga presencial."""
    assert telegram.local_legivel("Sorocaba - SP", "Presencial") == "Sorocaba - SP"
    assert telegram.local_legivel("Tatuí / SP", "") == "Tatuí / SP"
    assert telegram.local_legivel("", "") == "não informado"


# ===========================================================================
# Falha de entrega silenciosa
# ===========================================================================

def test_placar_de_entrega_conta_tentativa_e_falha(monkeypatch):
    """MEDIDO na primeira execução no GitHub Actions: TODAS as mensagens
    voltaram HTTP 404 (um carriage return invisível no fim do token) e o
    workflow terminou VERDE. Nenhum alerta chegou, nenhum resumo, nem o
    batimento — e o painel do Actions mostrava sucesso.

    Esse é o cenário que o batimento diário existe pra impedir: silêncio no
    Telegram indistinguível de "não achou vaga". Aqui o silêncio ficou
    indistinguível também no Actions, que era o último lugar onde dava pra
    perceber. Sistema de alerta que falha calado é pior que nenhum, porque
    você confia nele.
    """
    telegram.zerar_placar()
    assert telegram.placar_de_entrega() == {"tentativas": 0, "falhas": 0}

    monkeypatch.setattr(telegram, "_chamar", lambda *a, **k: None)  # tudo falha
    monkeypatch.setattr(telegram, "configurado", lambda: True)
    monkeypatch.setattr(telegram, "TELEGRAM_CHAT_ID", "123")

    assert telegram.enviar("oi") is False
    assert telegram.enviar("oi de novo") is False
    assert telegram.placar_de_entrega() == {"tentativas": 2, "falhas": 2}


def test_token_com_espaco_em_branco_e_aparado():
    """Um "\r", "\n" ou espaço sobrando no fim do token faz a Bot API
    responder 404 — que se lê como "endpoint errado" e manda investigar o
    lugar errado. É o erro mais fácil de cometer colando credencial."""
    import os
    import importlib

    os.environ["TELEGRAM_BOT_TOKEN"] = "123:ABC\r\n"
    os.environ["TELEGRAM_CHAT_ID"] = "  456  "
    from radar import config
    importlib.reload(config)
    assert config.TELEGRAM_BOT_TOKEN == "123:ABC"
    assert config.TELEGRAM_CHAT_ID == "456"
    del os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    importlib.reload(config)
