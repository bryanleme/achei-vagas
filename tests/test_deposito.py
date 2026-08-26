"""Testes da persistência — dedup, fila e a guarda de banco recriado."""

import pytest

from radar import ciclo, config, deposito
from tests.conftest import vaga


@pytest.fixture(autouse=True)
def banco_temporario(tmp_path, monkeypatch):
    monkeypatch.setattr(deposito.caminhos, "PASTA_DADOS", tmp_path)
    monkeypatch.setattr(deposito.caminhos, "BANCO", tmp_path / "teste.db")
    deposito.iniciar()


def _guardar(v, relevancia=8, digest=False):
    deposito.salvar(v, relevancia, "cargo direto", digest_pendente=digest)


# ===========================================================================
# C2 — dedup que não pegava o histórico
# ===========================================================================

def test_dedup_pelo_link():
    v = vaga()
    assert deposito.ja_vista(v) is False
    _guardar(v)
    assert deposito.ja_vista(v) is True


def test_dedup_por_empresa_e_titulo_em_fonte_diferente():
    """A MESMA vaga aparece na Gupy e no LinkedIn com links diferentes. Sem a
    segunda chave, ela notifica duas vezes.

    No projeto original a coluna dessa chave foi adicionada por migração sem
    preencher o histórico: 373 linhas ficaram NULL, e `WHERE chave = ?` nunca
    bate contra NULL em SQL — 52 vagas duplicadas voltaram a notificar.
    """
    primeira = vaga(link="https://gupy.io/job/1", fonte="Gupy")
    _guardar(primeira)

    republicada = vaga(link="https://linkedin.com/jobs/999", fonte="LinkedIn")
    assert republicada.id != primeira.id
    assert republicada.chave_secundaria == primeira.chave_secundaria
    assert deposito.ja_vista(republicada) is True


def test_chave_secundaria_nunca_e_nula():
    _guardar(vaga(empresa="", titulo="Analista de PCP"))
    with deposito._conectar() as conn:
        nulas = conn.execute(
            "SELECT COUNT(*) FROM vagas WHERE chave_secundaria IS NULL"
        ).fetchone()[0]
    assert nulas == 0


def test_link_com_barra_final_e_o_mesmo_link():
    a = vaga(link="https://exemplo.com/vaga/1")
    b = vaga(link="https://exemplo.com/vaga/1/")
    assert a.id == b.id


# ===========================================================================
# C1 — a guarda de banco recriado do zero
# ===========================================================================

def test_banco_recriado_do_zero_aborta_o_ciclo():
    """O pior estrago possível neste projeto: banco vazio faz toda vaga
    voltar a ser "nova", o Telegram recebe centenas de mensagens repetidas, o
    rodízio de termos reinicia e a fila do resumo é descartada.

    Aconteceu de verdade, por um caminho de arquivo que mudou em silêncio, e
    ninguém percebeu por dias porque nada dava erro. Melhor não rodar do que
    rodar renotificando tudo.
    """
    _guardar(vaga(link="https://exemplo.com/1"))
    _guardar(vaga(link="https://exemplo.com/2", titulo="Analista de Logística"))
    assert deposito.registrar_total() == 2

    with deposito._conectar() as conn:
        conn.execute("DELETE FROM vagas")  # simula o banco recriado

    with pytest.raises(deposito.BancoSuspeito):
        deposito.iniciar()


def test_primeira_execucao_nao_dispara_a_guarda():
    """Banco novo, sem total registrado: não há com o que comparar."""
    deposito.iniciar()  # não levanta


# ===========================================================================
# Fila do resumo
# ===========================================================================

def test_vaga_abaixo_do_limiar_entra_na_fila():
    _guardar(vaga(link="https://exemplo.com/a"), relevancia=4, digest=True)
    _guardar(vaga(link="https://exemplo.com/b", titulo="Analista de BI"), relevancia=9)

    pendentes = deposito.pendentes_do_digest()
    assert len(pendentes) == 1
    assert pendentes[0][0] == "Analista de PCP"


def test_fila_vem_ordenada_da_mais_relevante():
    for i, nota in enumerate([3, 9, 6]):
        _guardar(
            vaga(link=f"https://exemplo.com/{i}", titulo=f"Analista de Processos {i}"),
            relevancia=nota, digest=True,
        )
    notas = [linha[3] for linha in deposito.pendentes_do_digest()]
    assert notas == [9, 6, 3]


def test_fila_so_e_limpa_depois_do_envio_confirmado():
    _guardar(vaga(), relevancia=4, digest=True)
    assert len(deposito.pendentes_do_digest()) == 1
    assert deposito.limpar_fila_do_digest() == 1
    assert deposito.pendentes_do_digest() == []


# ===========================================================================
# Rodízio de termos
# ===========================================================================

def test_g2_prioritarios_entram_em_todo_ciclo():
    """No projeto original todos os termos disputavam vez por igual, em ordem
    alfabética: com 44 termos e 10 por ciclo, o termo que dava nome ao perfil
    era buscado uma vez a cada 13 horas. Uma vaga real, que passava no filtro
    sem esforço, nunca chegou a ser BUSCADA."""
    for _ in range(5):
        termos = ciclo.termos_do_ciclo()
        for prioritario in config.TERMOS_PRIORITARIOS:
            assert prioritario in termos


def test_g2_prioritario_nao_ocupa_vaga_do_rodizio():
    termos = ciclo.termos_do_ciclo()
    assert len(termos) == len(config.TERMOS_PRIORITARIOS) + config.TERMOS_POR_CICLO


def test_g2_nenhum_termo_repetido_no_mesmo_ciclo():
    termos = ciclo.termos_do_ciclo()
    assert len(termos) == len(set(termos))


def test_g2_rodizio_cobre_todos_os_termos_com_o_tempo():
    """O bloco avança a cada ciclo. Depois de voltas suficientes, todo termo
    da lista precisa ter sido buscado pelo menos uma vez."""
    vistos = set()
    resto = [t for t in config.TERMOS_BUSCA if t not in config.TERMOS_PRIORITARIOS]
    ciclos = len(resto) // config.TERMOS_POR_CICLO + 2
    for _ in range(ciclos):
        vistos.update(ciclo.termos_do_ciclo())
    assert set(config.TERMOS_BUSCA) <= vistos


def test_feedback_e_gravado_na_vaga():
    v = vaga()
    _guardar(v)
    assert deposito.registrar_feedback(v.id, "positivo") is True
    linhas = deposito.precisao_por_fonte()
    assert linhas[0][2] == 1  # um 👍
