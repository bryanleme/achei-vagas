"""Leitura das fontes novas (28/11/2026) a partir de HTML/JSON reais.

Os trechos abaixo foram copiados das respostas ao vivo e enxugados. O que
cada teste prende é a regra que já produziu bug neste projeto: o local da
vaga sai do PRÓPRIO anúncio, nunca da cidade pesquisada.
"""

import asyncio

from bs4 import BeautifulSoup

from radar.fontes import por_cidade
from radar.fontes.portais import cards_empregos, converter_remotar

EMPREGOS_HTML = """
<div id="job-card" role="button">
  <div><h2><span>Analista de Logistica</span></h2>
  <h3><a href="https://empregos.com.br/empresa/simpar/e-447928">Simpar</a></h3>
  <div id="share-modal-mobile"><p>Que tal compartilhar esta vaga?</p></div></div>
  <p>São Roque, SP</p><p>São Roque, SP</p><span>Presencial</span>
  <p>1 vaga</p><p>À combinar</p><p>Publicada há 12 dias</p>
  <a href="/vaga/12059917/analista-de-logistica-em-sao-roque-sp?x=1">Mais detalhes</a>
</div>
<div id="job-card"><h2><span>Sem link</span></h2></div>
"""


def test_empregos_le_local_modalidade_e_data_do_card():
    vagas = cards_empregos(BeautifulSoup(EMPREGOS_HTML, "lxml"), "cidade:Sorocaba")
    assert len(vagas) == 1  # card sem link é descartado
    v = vagas[0]
    assert v.titulo == "Analista de Logistica"
    assert v.empresa == "Simpar"
    # Pesquisado em Sorocaba, a vaga é de São Roque — e assim fica.
    assert v.local == "São Roque, SP"
    assert v.modalidade == "Presencial"
    assert v.publicado_em  # "há 12 dias" virou data ISO
    assert v.link == "https://www.empregos.com.br/vaga/12059917/analista-de-logistica-em-sao-roque-sp"


def test_remotar_remota_e_hibrida():
    remota = converter_remotar({
        "id": 174174, "title": "Analista de Business Intelligence", "type": "remote",
        "city": "", "state": "", "createdAt": "2026-11-26T05:57:34.839-03:00",
        "companyDisplayName": None, "company": {"name": "Acme"},
    })
    assert remota.modalidade == "Remoto" and remota.local == "Remoto"
    assert remota.empresa == "Acme"
    assert remota.publicado_em == "2026-11-26"
    assert remota.link == "https://remotar.com.br/job/174174"

    hibrida = converter_remotar({
        "id": 1, "title": "Analista de Dados", "type": "hybrid",
        "city": "São Paulo", "state": "SP", "createdAt": "lixo",
    })
    assert hibrida.modalidade == "Híbrido"
    assert hibrida.local == "São Paulo - SP"  # híbrida precisa casar cidade do perfil
    assert hibrida.publicado_em == ""


BNE_HTML = """
<section class="job__card__container" id="job-6268329">
  <h2><strong><a class="card__link" href="#">Vaga De Analista De Dados</a></strong></h2>
  <div class="info__vaga">
    <div class="info__vaga-item">Dados</div>
    <div class="info__vaga-item">Empresa X</div>
    <div class="info__vaga-item">Presencial</div>
    <div class="info__vaga-item">Itapetininga / SP</div>
  </div>
</section>
<section class="job__card__container" id="job-2">
  <h2><strong><a class="card__link" href="#">Vaga De Analista De BI</a></strong></h2>
</section>
"""


def test_bne_le_o_layout_novo_e_nao_inventa_cidade(monkeypatch):
    async def falso(url, params=None):
        return BeautifulSoup(BNE_HTML, "lxml")

    monkeypatch.setattr(por_cidade, "get_html", falso)
    vagas = asyncio.run(por_cidade.BNE()._vagas_da_cidade("Itapetininga"))
    assert vagas[0].local == "Itapetininga / SP"
    assert vagas[0].modalidade == "Presencial"
    assert vagas[0].empresa == "Empresa X"
    # Card sem campos: local vazio, e não "Itapetininga" por palpite.
    assert vagas[1].local == ""
