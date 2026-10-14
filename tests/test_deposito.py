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
    assert deposito.vaga_conhecida(v) is False
    _guardar(v)
    assert deposito.vaga_conhecida(v) is True


def test_dedup_por_empresa_e_titulo_em_fonte_diferente():
    """A MESMA vaga aparece na Gupy e no LinkedIn com links diferentes. Sem a
    segunda chave, ela notifica duas vezes.

    A chave nasce no esquema, nunca por migração: coluna adicionada sem
    preencher o histórico fica NULL, e `WHERE chave = ?` nunca bate contra
    NULL em SQL — vaga duplicada voltaria a notificar.
    """
    primeira = vaga(link="https://gupy.io/job/1", fonte="Gupy")
    _guardar(primeira)

    republicada = vaga(link="https://linkedin.com/jobs/999", fonte="LinkedIn")
    assert republicada.id != primeira.id
    assert republicada.chave_secundaria == primeira.chave_secundaria
    assert deposito.vaga_conhecida(republicada) is True


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
    """Se todos os termos disputassem vez por igual, com ~44 termos e 10 por
    ciclo o termo que dá nome ao perfil seria buscado uma vez a cada 13
    horas. Uma vaga real, que passaria no filtro sem esforço, poderia nunca
    ser BUSCADA."""
    for _ in range(5):
        termos = ciclo.termos_desta_rodada()
        for prioritario in config.TERMOS_PRIORITARIOS:
            assert prioritario in termos


def test_g2_prioritario_nao_ocupa_vaga_do_rodizio():
    termos = ciclo.termos_desta_rodada()
    assert len(termos) == len(config.TERMOS_PRIORITARIOS) + config.TERMOS_POR_RODADA


def test_g2_nenhum_termo_repetido_no_mesmo_ciclo():
    termos = ciclo.termos_desta_rodada()
    assert len(termos) == len(set(termos))


def test_g2_rodizio_cobre_todos_os_termos_com_o_tempo():
    """O bloco avança a cada ciclo. Depois de voltas suficientes, todo termo
    da lista precisa ter sido buscado pelo menos uma vez."""
    vistos = set()
    resto = [t for t in config.TERMOS_DE_BUSCA if t not in config.TERMOS_PRIORITARIOS]
    ciclos = len(resto) // config.TERMOS_POR_RODADA + 2
    for _ in range(ciclos):
        vistos.update(ciclo.termos_desta_rodada())
    assert set(config.TERMOS_DE_BUSCA) <= vistos


def test_feedback_e_gravado_na_vaga():
    v = vaga()
    _guardar(v)
    assert deposito.registrar_feedback(v.id, "positivo") is True
    linhas = deposito.precisao_por_fonte()
    assert linhas[0][2] == 1  # um 👍


def test_vaga_confidencial_nao_engole_outra_do_mesmo_titulo():
    """MEDIDO com a InfoJobs ligada: vaga confidencial vem sem empresa, e é
    comum. Com empresa vazia a chave secundária viraria "|analista de pcp" —
    idêntica pra duas vagas diferentes de empresas diferentes com o mesmo
    título. A segunda seria descartada como duplicata e nunca notificada.
    """
    primeira = vaga(empresa="", link="https://infojobs.com.br/vaga/1")
    segunda = vaga(empresa="", link="https://infojobs.com.br/vaga/2")

    assert primeira.chave_secundaria == ""
    assert segunda.chave_secundaria == ""

    _guardar(primeira)
    assert deposito.vaga_conhecida(segunda) is False, "vaga distinta foi engolida"
    _guardar(segunda)
    assert deposito.vaga_conhecida(primeira) is True
    assert deposito.vaga_conhecida(segunda) is True


def test_com_empresa_a_segunda_chave_volta_a_valer():
    """A correção acima não pode ter desligado a dedup entre fontes."""
    a = vaga(empresa="Yazaki", link="https://gupy.io/1")
    b = vaga(empresa="Yazaki", link="https://vagas.com.br/2")
    _guardar(a)
    assert deposito.vaga_conhecida(b) is True


def test_varredura_inicial_segura_o_alerta_imediato():
    """MEDIDO no SEGUNDO ciclo em produção: 15 alertas imediatos de uma vez.
    Nenhum era erro — eram vagas reais, novas para o banco. Mas "nova para o
    banco" não é "nova no mundo": elas já estavam publicadas, e só apareceram
    porque o rodízio chegou nos termos "transportes" e "suprimentos".

    A guarda de estreia cobria só o primeiro ciclo e resolveu metade do
    problema. Enquanto a varredura inicial não termina, todo ciclo é uma
    pequena estreia.
    """
    assert ciclo.em_varredura_inicial() is True

    resto = [t for t in config.TERMOS_DE_BUSCA if t not in config.TERMOS_PRIORITARIOS]
    ciclos = -(-len(resto) // config.TERMOS_POR_RODADA)
    for _ in range(ciclos):
        ciclo.termos_desta_rodada()

    assert ciclo.em_varredura_inicial() is False, (
        "depois de cobrir todos os termos, o alerta imediato precisa ligar"
    )


# ===========================================================================
# Descoberta de empresas do corredor
# ===========================================================================

def test_nome_de_empresa_e_amarrado_a_fonte_que_o_produziu():
    """Nome de empresa NÃO é identificador universal — cada portal escreve
    do seu jeito.

    MEDIDO: a varredura por empresa da Gupy consulta por `careerPageName`.
    "Grupo Caio" (colhido da Gupy) devolve 44 vagas; "Yazaki do Brasil
    Ltda." (colhido do Vagas.com) devolve ZERO, porque na Gupy a empresa não
    se chama assim. Consultar um portal com o vocabulário de outro falha em
    SILÊNCIO — parece "não há vaga" e é "perguntei errado".
    """
    deposito.registrar_empresa("Grupo Caio", "Gupy", "Botucatu")
    deposito.registrar_empresa("Yazaki do Brasil Ltda.", "Vagas.com", "Tatuí")

    da_gupy = deposito.empresas_do_corredor("Gupy")
    assert da_gupy == ["Grupo Caio"]
    assert "Yazaki do Brasil Ltda." not in da_gupy
    assert deposito.empresas_do_corredor("Vagas.com") == ["Yazaki do Brasil Ltda."]


def test_empresa_repetida_nao_duplica_e_conta_aparicoes():
    for _ in range(3):
        deposito.registrar_empresa("Smurfit Westrock", "Gupy", "Porto Feliz")
    assert deposito.empresas_do_corredor("Gupy") == ["Smurfit Westrock"]
    with deposito._conectar() as conn:
        n = conn.execute(
            "SELECT aparicoes FROM empresas WHERE nome = ?", ("Smurfit Westrock",)
        ).fetchone()[0]
    assert n == 3


def test_empresa_sem_nome_e_ignorada():
    deposito.registrar_empresa("", "Gupy", "Sorocaba")
    deposito.registrar_empresa("   ", "Gupy", "Sorocaba")
    assert deposito.empresas_do_corredor("Gupy") == []


# ===========================================================================
# Varredura por empresa
# ===========================================================================

def test_cidade_extraida_do_endereco_da_vaga_no_infojobs():
    """A página de empresa do InfoJobs traz SÓ o título — MEDIDO, o bloco
    inteiro é `<div class="link-item"><a>TÍTULO</a></div>`. Varredura sem
    cidade seria inútil: vaga sem local não bate cidade nem é remota, o filtro
    derruba tudo e o custo é gasto à toa.

    A cidade está no endereço, que o portal monta como
    `/vaga-de-<titulo>-em-<cidade>__<id>.aspx`.
    """
    from radar.fontes.por_empresa import InfoJobsPorEmpresa as F

    assert F._cidade_do_endereco(
        "https://www.infojobs.com.br/vaga-de-jardineiro-shopping-cidade-jardim"
        "-em-sao-paulo__11957366.aspx") == "sao paulo"
    assert F._cidade_do_endereco("/vaga-de-analista-de-pcp-em-sorocaba__123.aspx") == "sorocaba"
    assert F._cidade_do_endereco(
        "/vaga-de-analista-de-logistica-em-salto-de-pirapora__9.aspx") == "salto de pirapora"


def test_titulo_com_em_no_meio_nao_confunde_a_cidade():
    """Um título pode conter "em" ("analista em treinamento"); o sufixo do
    endereço não. Por isso vale o ÚLTIMO "-em-" antes do id."""
    from radar.fontes.por_empresa import InfoJobsPorEmpresa as F

    assert F._cidade_do_endereco(
        "/vaga-de-analista-em-treinamento-em-tatui__77.aspx") == "tatui"


def test_endereco_sem_cidade_devolve_vazio():
    """Vaga sem cidade no endereço não pode virar cidade inventada — devolve
    vazio e o filtro decide."""
    from radar.fontes.por_empresa import InfoJobsPorEmpresa as F

    assert F._cidade_do_endereco("/vaga-de-analista-de-pcp__123.aspx") == ""
    assert F._cidade_do_endereco("") == ""


def test_cidade_extraida_bate_com_o_filtro():
    """A extração só serve se o formato que ela produz for entendido pelo
    filtro de cidade — que normaliza acento e caixa, mas não hífen."""
    from radar.fontes.por_empresa import InfoJobsPorEmpresa as F
    from radar.localizacao import casa_cidade

    cidade = F._cidade_do_endereco("/vaga-de-analista-em-salto-de-pirapora__9.aspx")
    assert casa_cidade(cidade, ["Salto de Pirapora"], "SP") == "Salto de Pirapora"


def test_url_de_empresa_e_preservada_quando_um_card_vem_sem_ela():
    """Card sem link de empresa não pode APAGAR o link que outro card da
    mesma empresa já trouxe — sem isso a varredura por empresa perderia o
    endereço no primeiro card incompleto."""
    deposito.registrar_empresa("Yazaki", "Vagas.com", "Tatuí",
                               "https://www.vagas.com.br/vagas-de-yazaki")
    deposito.registrar_empresa("Yazaki", "Vagas.com", "Tatuí", "")
    assert deposito.empresas_com_url("Vagas.com") == [
        ("Yazaki", "https://www.vagas.com.br/vagas-de-yazaki")
    ]
