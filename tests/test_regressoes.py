"""Cada teste aqui é um bug que já aconteceu de verdade em produção.

Não são cenários hipotéticos. Todos vêm do histórico medido do projeto que
deu origem a este — o número de vagas afetadas está no nome ou no comentário
de cada caso. Se um destes voltar a falhar, o bug voltou.

A ordem segue as sete classes do levantamento:

    A. campos misturados
    B. falha silenciosa da fonte
    C. estado que evapora
    D. o robô escrevendo código
    E. casamento por substring
    F. texto livre e locale
    G. lógica que nunca rodou
"""

from datetime import date

import pytest

from radar import caminhos, datas
from radar.filtro import avaliar, combina
from radar.localizacao import (
    bate_cidade,
    cidade_confere,
    e_remoto,
    extrair_escopo,
    uf_declarada,
)
from radar.texto import contem_termo, esqueleto, normalizar
from tests.conftest import vaga


# ===========================================================================
# CLASSE A — campos misturados
# ===========================================================================

def test_a1_titulo_com_remote_nao_torna_a_vaga_remota(regras):
    """O bug fundador desta base.

    Vaga americana com "Hybrid Remote" no TÍTULO batia a raiz "remot" e
    passava como remota, com local="Bloomington, IN". Título e local eram
    concatenados num texto só antes de comparar.
    """
    v = vaga(titulo="Data Analyst - Hybrid Remote", local="Bloomington, IN")
    assert v.remota is False
    assert combina(v, regras) is False


def test_a1_local_remoto_de_verdade_continua_passando(regras):
    """A correção acima não pode ter fechado o caminho legítimo."""
    v = vaga(titulo="Analista de PCP", local="Remoto", modalidade="Remoto")
    assert v.remota is True
    assert combina(v, regras) is True


def test_a2_todo_cargo_do_config_e_realmente_buscado():
    """As listas de cargo e de termo de busca divergiram em silêncio: metade
    dos cargos filtrados nunca chegava a ser buscada de verdade, e as vagas
    correspondentes só apareciam por acidente. Agora TERMOS_BUSCA é derivado
    de CARGOS_*, então a divergência não pode voltar a existir.
    """
    from radar import config

    for cargo in config.CARGOS_FORTES + config.CARGOS_AMBIGUOS:
        assert cargo.lower() in config.TERMOS_BUSCA, (
            f"'{cargo}' é filtrado mas nunca é buscado"
        )


def test_a2_termos_prioritarios_existem_na_lista_de_busca():
    """Prioritário que não está em TERMOS_BUSCA é linha morta: sai do rodízio
    sem entrar em lugar nenhum."""
    from radar import config

    for termo in config.TERMOS_PRIORITARIOS:
        assert termo in config.TERMOS_BUSCA


# ===========================================================================
# CLASSE C — estado que evapora
# ===========================================================================

def test_c1_banco_fica_ancorado_na_raiz_do_projeto():
    """Mover um módulo pra subpasta movia o banco junto, em silêncio: o
    histórico ficava órfão, o banco nascia vazio a cada execução e TODA vaga
    virava "nova" — renotificação a cada ciclo.

    O teste trava a relação: o banco é filho de data/ na raiz, e a raiz é a
    pasta que contém a pasta radar/.
    """
    assert caminhos.BANCO.parent == caminhos.PASTA_DADOS
    assert caminhos.PASTA_DADOS.parent == caminhos.RAIZ
    assert (caminhos.RAIZ / "radar").is_dir()
    assert caminhos.BANCO.name.endswith(".db")


# ===========================================================================
# CLASSE E — casamento por substring
# ===========================================================================

@pytest.mark.parametrize(
    "local",
    [
        "Campina Grande do Sul - PR",
        "Campina Grande do Sul, Paraná, Brazil",
    ],
)
def test_e1_cidade_homonima_em_outro_estado_nao_passa(local):
    """"Campina Grande do Sul - PR" era aceita como Campina Grande/PB. São
    cidades diferentes a 2.500 km. O nome aceito é PREFIXO de outra cidade
    real, e borda de palavra não resolve isso sozinha.

    Aqui o análogo no perfil atual: nenhuma cidade do corredor pode ser
    aceita quando o texto declara UF diferente de SP.
    """
    assert cidade_confere(local, "SP") is False


@pytest.mark.parametrize(
    "local",
    [
        "Piedade - PE",
        "Piedade, Pernambuco, Brazil",
        "Alumínio - MG",
        "Tietê, Minas Gerais, Brasil",
    ],
)
def test_e1_homonimas_reais_do_corredor_sao_barradas(local):
    """Piedade é bairro de Jaboatão/PE além de cidade em SP; Alumínio e
    Tietê batem nome fora de SP. Sem a guarda de UF, as três entram."""
    assert bate_cidade(local, ["Piedade", "Alumínio", "Tietê"], "SP") is None


@pytest.mark.parametrize(
    "local",
    [
        "Itapetininga - SP",
        "Itapetininga/SP",
        "Itapetininga, SP",
        "Itapetininga, São Paulo, Brazil",
        "ITAPETININGA - SP",
        "Itapetininga",  # sem UF: não há o que comparar, passa de propósito
    ],
)
def test_e1_cidade_certa_passa_em_toda_grafia_real(local):
    assert bate_cidade(local, ["Itapetininga"], "SP") == "Itapetininga"


def test_e2_guarda_de_uf_entende_estado_por_extenso():
    """A guarda só entendia SIGLA de duas letras — e o formato do LinkedIn é
    por extenso. Ou seja: estava furada exatamente na fonte que respondia
    por 1.061 das 1.532 vagas do banco.

        "Campina Grande do Sul - PR"             -> barrada  (certo)
        "Campina Grande do Sul, Parana, Brazil"  -> PASSAVA  (errado)
    """
    assert uf_declarada("Sorocaba - SP") == "sp"
    assert uf_declarada("Sorocaba, São Paulo, Brazil") == "sp"
    assert uf_declarada("Curitiba, Paraná, Brasil") == "pr"
    assert uf_declarada("Brasília, Federal District, Brazil") == "df"
    assert uf_declarada("Brazil") is None


def test_e2_estado_composto_nao_resolve_pro_estado_curto():
    """"mato grosso" bate dentro de "mato grosso do sul". Sem ordenar do nome
    mais longo pro mais curto, MS resolvia como MT."""
    assert uf_declarada("Dourados, Mato Grosso do Sul, Brasil") == "ms"
    assert uf_declarada("Cuiabá, Mato Grosso, Brasil") == "mt"


def test_e3_sigla_curta_nao_bate_dentro_de_palavra():
    """"bi" batia dentro de "combinado" e "ambiente"; "sp" dentro de
    "esporte". A lista de qualificadores tem sigla curta de propósito, então
    `in` cru transformaria cada uma numa fonte de falso positivo."""
    assert contem_termo("bi", normalizar("Analista de BI")) is True
    assert contem_termo("bi", normalizar("Ambiente combinado")) is False
    assert contem_termo("pcp", normalizar("Analista de PCP")) is True
    assert contem_termo("pcp", normalizar("PCPX Consultoria")) is False


# ===========================================================================
# CLASSE F — texto livre e locale
# ===========================================================================

def test_f1_data_do_linkedin_em_ingles_e_reconhecida():
    """`publicado_em` estava vazio em 1.061 de 1.061 vagas do LinkedIn: os
    padrões eram em português e o card vem em inglês. Sem data, vaga de
    quatro meses ia como alerta imediato, sem aviso."""
    hoje = date(2026, 8, 26)
    assert datas.extrair_data(None, "4 months ago", hoje=hoje) == "2026-04-28"
    assert datas.extrair_data(None, "2 weeks ago", hoje=hoje) == "2026-08-12"
    assert datas.extrair_data(None, "há 3 dias", hoje=hoje) == "2026-08-23"
    assert datas.extrair_data(None, "Publicada há 1 mês", hoje=hoje) == "2026-07-27"


def test_f1_atributo_datetime_vence_o_texto():
    """A tag <time datetime="..."> é absoluta e não depende de idioma. Houve
    card cujo TEXTO não trazia idade nenhuma, só a tag — pra esse, padrão de
    texto nenhum resolveria, em idioma nenhum."""
    hoje = date(2026, 8, 26)
    assert datas.extrair_data("2026-03-26", "sem idade no texto", hoje=hoje) == "2026-03-26"
    # E o atributo vence mesmo quando o texto também traria uma data.
    assert datas.extrair_data("2026-03-26", "2 days ago", hoje=hoje) == "2026-03-26"


def test_f1_data_ausente_nao_conta_como_vaga_antiga():
    """Não saber a idade não é o mesmo que saber que é velha. Tratar como
    velha rebaixaria toda vaga de fonte que não informa data."""
    assert datas.vaga_antiga("", 30) is False
    assert datas.vaga_antiga("data ilegível", 30) is False


def test_f1_vaga_de_quatro_meses_e_antiga():
    hoje = date(2026, 8, 26)
    assert datas.vaga_antiga("2026-04-01", 30, hoje=hoje) is True
    assert datas.vaga_antiga("2026-08-20", 30, hoje=hoje) is False


@pytest.mark.parametrize(
    "local",
    ["Home Office", "100% Remoto", "Trabalhe de casa", "Teletrabalho", "Remote"],
)
def test_f2_remoto_reconhecido_quando_a_fonte_nao_declara_modalidade(local):
    """`local="Home Office"` com modalidade vazia era REJEITADA — a checagem
    exigia o campo `modalidade`, e nem toda fonte preenche. Todo o
    vocabulário de remoto ficava inalcançável."""
    assert e_remoto("", local) is True


def test_f2_modalidade_declarada_vence_texto_solto():
    """Vaga marcada "Híbrido" não vira remota porque a palavra "remoto"
    apareceu solta no texto de local."""
    assert e_remoto("Híbrido", "Sorocaba - remoto 2x por semana") is False
    assert e_remoto("Presencial", "Home Office eventual") is False


def test_f3_preposicao_solta_nao_vira_mercado():
    """O maior falso negativo do projeto original: 179 descartes.

    O LinkedIn em espanhol escreve "En remoto". Removida a raiz "remot",
    sobrava "en" solto como candidato a mercado, que não resolvia pra país
    nenhum e virava escopo desconhecido — e escopo desconhecido derrubava
    a vaga.
    """
    assert extrair_escopo("En remoto") == set()
    assert extrair_escopo("Em remoto") == set()


def test_f3_token_sem_letra_nao_vira_mercado():
    """`local="100% remoto"` resolvia pro escopo {"100%"} — não é dígito puro
    (tem o "%"), então sobrevivia a um filtro que só descartasse número."""
    assert extrair_escopo("100% remoto") == set()
    assert extrair_escopo("08015") == set()


def test_f3_casamento_por_segmento_nao_por_substring():
    """Substring fazia "Porto Alegre" virar Portugal e "Santiago do Cacém"
    virar Chile. O casamento é por igualdade de SEGMENTO."""
    assert "Portugal" not in extrair_escopo("Porto Alegre, RS, Brasil")
    assert extrair_escopo("Porto Alegre, RS, Brasil") == {"Brasil"}
    assert extrair_escopo("Lisboa, Portugal") == {"Portugal"}


def test_f3_dois_mercados_no_mesmo_texto(regras):
    """"Remote - Brazil/LATAM" declara DOIS mercados. Comparar um valor só
    descartava a vaga toda vez que o sobrevivente não fosse o aceito."""
    escopo = extrair_escopo("Remote - Brazil/LATAM")
    assert "Brasil" in escopo

    v = vaga(titulo="Analista de BI", local="Remote - Brazil/LATAM", modalidade="Remoto")
    assert combina(v, regras) is True


def test_f3_sigla_de_estado_americano_nao_e_confundida_com_preposicao():
    """"IN" é preposição em inglês E sigla de Indiana. Tratada como
    preposição, "Bloomington, IN" perdia o único sinal de que era vaga
    americana e passava com escopo vazio."""
    assert extrair_escopo("Bloomington, IN") == {"Estados Unidos"}
    # E o caminho oposto continua valendo: aqui "in" é preposição mesmo.
    assert extrair_escopo("Remote in Brazil") == {"Brasil"}


def test_f3_sigla_ambigua_segue_o_resto_do_texto():
    """AL, MA, MT, MS, PA e SC existem no Brasil E nos EUA. "Belém, PA" é
    brasileira; "Boston, MA" é americana; a sigla é a mesma."""
    assert extrair_escopo("Belém, PA") == {"Brasil"}
    assert extrair_escopo("Boston, MA") == {"Estados Unidos"}


def test_f3_vaga_remota_americana_e_rejeitada(regras):
    v = vaga(titulo="Analista de Dados", local="Remote - United States", modalidade="Remoto")
    resultado = avaliar(v, regras)
    assert resultado.aprovada is False
    assert resultado.escopo_rejeitado == {"Estados Unidos"}


def test_f3_remoto_sem_escopo_declarado_continua_passando(regras):
    """A maioria das vagas remotas não declara mercado nenhum. Não há base
    pra rejeitar — "duplicar é aceitável, perder não"."""
    v = vaga(titulo="Analista de Processos", local="Remoto", modalidade="Remoto")
    resultado = avaliar(v, regras)
    assert resultado.aprovada is True
    assert resultado.escopo == set()
    assert resultado.mercado_confirmado is False


# ===========================================================================
# Regras de cargo — os três níveis
# ===========================================================================

def test_cargo_forte_passa_sozinho(regras):
    assert combina(vaga(titulo="Analista de PCP"), regras) is True
    assert combina(vaga(titulo="Analista de Logística Pleno"), regras) is True


def test_cargo_ambiguo_sozinho_nao_passa(regras):
    """"Analista Administrativo" existe em qualquer área do Brasil. Sozinho
    não pode aprovar nada."""
    assert combina(vaga(titulo="Analista Administrativo"), regras) is False
    assert combina(vaga(titulo="Analista de Planejamento"), regras) is False


def test_cargo_ambiguo_com_dominio_passa(regras):
    assert combina(vaga(titulo="Analista Administrativo de Frotas"), regras) is True
    assert combina(vaga(titulo="Analista de Planejamento de Produção"), regras) is True


def test_ferramenta_sozinha_nao_passa(regras):
    """"Power BI" sozinho aprovaria vaga de desenvolvimento."""
    assert combina(vaga(titulo="Desenvolvedor Power BI + Python"), regras) is False
    assert combina(vaga(titulo="Power BI Specialist"), regras) is True  # tem cargo junto


def test_vaga_de_desenvolvimento_fica_de_fora(regras):
    for titulo in [
        "Desenvolvedor de Software",
        "Engenheiro de Dados",
        "Data Engineer",
        "Full Stack Developer",
    ]:
        assert combina(vaga(titulo=titulo), regras) is False, titulo


def test_conectivo_ausente_nao_derruba_o_cargo(regras):
    """Metade das vagas escreve "Analista PCP" e a outra metade "Analista de
    PCP". Exigir a frase exata perde a primeira metade."""
    assert esqueleto("Analista de PCP") == "analista pcp"
    assert combina(vaga(titulo="Analista PCP Júnior"), regras) is True
    assert combina(vaga(titulo="Analista Logística"), regras) is True


def test_cargo_parecido_mas_diferente_nao_passa(regras):
    """O esqueleto tira conectivo, mas continua exigindo FRASE contígua —
    não vira saco de palavras."""
    assert combina(vaga(titulo="Analista de Risco e Crédito"), regras) is False
    assert combina(vaga(titulo="Analista de Recursos Humanos"), regras) is False


# ===========================================================================
# Localização — a regra combinada
# ===========================================================================

def test_presencial_fora_do_corredor_nao_passa(regras):
    """São Paulo capital não está no corredor. Nem Campinas."""
    assert combina(vaga(titulo="Analista de PCP", local="São Paulo - SP"), regras) is False
    assert combina(vaga(titulo="Analista de PCP", local="Campinas - SP"), regras) is False


def test_presencial_no_corredor_passa(regras):
    for cidade in ["Sorocaba", "Botucatu", "Avaré", "Tatuí", "Itapeva"]:
        v = vaga(titulo="Analista de Logística", local=f"{cidade} - SP")
        assert combina(v, regras) is True, cidade


def test_cargo_certo_local_errado_nao_passa(regras):
    assert combina(vaga(titulo="Analista de PCP", local="Recife - PE"), regras) is False


def test_local_certo_cargo_errado_nao_passa(regras):
    assert combina(vaga(titulo="Vendedor Interno", local="Sorocaba - SP"), regras) is False


# ===========================================================================
# EXCLUSÕES — vaga que não serve, por mais que o cargo bata
# ===========================================================================

@pytest.mark.parametrize(
    "titulo",
    [
        "ANALISTA DE LOGÍSTICA JUNIOR - Vaga Exclusiva para Pessoas com Deficiência (PCD)",
        "Analista de Logística JR. (Vaga exclusiva para PCD)",
        "Analista de PCP - PCD",
        "Analista de Processos (Pessoa com Deficiência)",
        "Analista de BI — vaga para portadores de deficiência",
    ],
)
def test_vaga_reservada_a_pcd_e_descartada(titulo, regras):
    """Pedido explícito do usuário. No Brasil a marcação no TÍTULO é a
    convenção para posição reservada sob a Lei de Cotas — vaga aberta a
    todos não escreve isso no nome.

    Todas estas têm cargo forte e cidade aceita: sem a regra de exclusão,
    passariam com nota alta e virariam alerta imediato.
    """
    v = vaga(titulo=titulo, local="Sorocaba - SP")
    resultado = avaliar(v, regras)
    assert resultado.aprovada is False
    assert resultado.excluida is True


def test_exclusao_nao_conta_como_barrada_pelo_cargo(regras):
    """Vaga excluída não pode poluir o diagnóstico de "barrada só pelo
    título" — ela nem chegou a ser avaliada por cargo."""
    v = vaga(titulo="Analista de PCP (PCD)", local="Sorocaba - SP")
    resultado = avaliar(v, regras)
    assert resultado.excluida is True
    assert resultado.bate_cargo is False


def test_pcd_nao_bate_dentro_de_outra_palavra(regras):
    """"pcd" tem três letras e a busca é por palavra inteira. Se batesse por
    substring, qualquer título com essas letras seguidas seria descartado."""
    for titulo in ["Analista de PCP", "Analista de PCP Pleno", "Especialista PCPD"]:
        v = vaga(titulo=titulo, local="Sorocaba - SP")
        assert avaliar(v, regras).excluida is False, titulo


def test_vaga_normal_continua_passando(regras):
    """A exclusão não pode ter derrubado o caso comum."""
    v = vaga(titulo="Analista de Logística Junior", local="Sorocaba - SP")
    assert avaliar(v, regras).aprovada is True
