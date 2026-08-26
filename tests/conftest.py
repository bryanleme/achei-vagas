import pytest

from radar import config
from radar.filtro import Regras
from radar.modelos import Vaga


@pytest.fixture
def regras() -> Regras:
    """As regras reais do perfil, montadas do config.

    De propósito não é uma regra inventada pro teste: se alguém editar o
    config e quebrar o perfil, é aqui que aparece.
    """
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


def vaga(
    titulo: str = "Analista de PCP",
    local: str = "Itapetininga - SP",
    modalidade: str = "",
    empresa: str = "Empresa Teste",
    link: str = "https://exemplo.com/vaga/1",
    fonte: str = "Teste",
    publicado_em: str = "",
) -> Vaga:
    return Vaga(
        titulo=titulo,
        empresa=empresa,
        local=local,
        link=link,
        fonte=fonte,
        modalidade=modalidade,
        publicado_em=publicado_em,
    )


@pytest.fixture
def nova_vaga():
    return vaga
