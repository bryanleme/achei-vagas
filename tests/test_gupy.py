"""Contrato da integração pública com o portal de vagas da Gupy."""

import asyncio

from radar.fontes import gupy


def _item(**alteracoes):
    item = {
        "name": "Analista de PCP",
        "careerPageName": "Empresa Teste",
        "city": "Sorocaba",
        "state": "São Paulo",
        "workplaceType": "hybrid",
        "publishedDate": "2026-11-10T08:30:00.000Z",
        "jobUrl": (
            "https://empresa-teste.gupy.io/job/"
            "eyJqb2JJZCI6MTIzLCJzb3VyY2UiOiJndXB5X3BvcnRhbCJ9"
        ),
        # O endpoint atual pode devolver este campo vazio.
        "careerPageUrl": "",
        "description": "Planejamento e controle de produção.",
    }
    item.update(alteracoes)
    return item


def test_endpoint_publico_atual_da_gupy():
    assert gupy.API == "https://portal.gupy.io/api/job-search/jobs"


def test_converter_aceita_formato_atual_sem_country_e_career_page_url():
    vaga = gupy._converter(_item(), termo_busca="analista de pcp")

    assert vaga is not None
    assert vaga.titulo == "Analista de PCP"
    assert vaga.empresa == "Empresa Teste"
    assert vaga.local == "Sorocaba, São Paulo"
    assert vaga.modalidade == "Híbrido"
    assert vaga.publicado_em == "2026-11-10"
    assert vaga.empresa_url == "https://empresa-teste.gupy.io/"


def test_busca_mantem_passada_estadual_e_nacional(monkeypatch):
    chamadas = []

    async def falso_get_json(url, params=None, headers=None):
        chamadas.append((url, dict(params or {}), dict(headers or {})))
        return {
            "data": [_item()],
            "pagination": {"total": 1, "limit": 100, "offset": 0},
        }

    monkeypatch.setattr(gupy, "get_json", falso_get_json)

    resultado = asyncio.run(gupy.Gupy(estado="São Paulo").buscar("analista de pcp"))

    assert len(resultado.vagas) == 1  # mesma vaga nas duas passadas é deduplicada
    assert len(chamadas) == 2
    assert all(url == gupy.API for url, _, _ in chamadas)
    assert chamadas[0][1]["state"] == "São Paulo"
    assert "state" not in chamadas[1][1]
    assert all(params["limit"] == 100 for _, params, _ in chamadas)
