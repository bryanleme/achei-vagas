"""Varredura por CIDADE — a cobertura exaustiva da região.

POR QUE ISTO É DIFERENTE (E MELHOR) QUE BUSCAR POR TERMO

Todo o resto do projeto pergunta aos portais "quem tem vaga de PCP?". Isso
só acha o que o texto do termo casa, e depende do rodízio chegar na vez do
termo certo — uma volta a cada ~15 horas.

Aqui a pergunta é outra: "o que existe em Itapetininga?". Para uma busca
presa a uma região, essa pergunta é estritamente melhor:

  - cobre TODA vaga da cidade, inclusive de título que nenhum termo do config
    alcançaria ("Especialista em Planejamento Industrial", "Encarregado de
    Expedição");
  - não depende do rodízio: a cidade inteira é varrida de uma vez;
  - o filtro de cargo continua sendo o que decide — a varredura só amplia o
    que chega até ele, sem afrouxar nada.

O custo é o que justifica a cadência diária: 35 cidades × 4 portais é ordem de
300 requisições. Vaga não abre de minuto em minuto, e o ciclo de 3 horas
continua fazendo a busca por termo, que é a rápida.

SÓ AS CIDADES DA REGIÃO. São Paulo e Campinas ficam de fora de propósito:
sozinhas têm mais vaga que as 35 juntas, e varrê-las por cidade traria
milhares de vagas que a faixa distante já rebaixa no ranking. Elas continuam
cobertas pela busca por termo.
"""

import re
import unicodedata

from radar import config, datas
from radar.fontes.base import (
    Cadencia,
    Fonte,
    Resultado,
    get_html,
    montar,
    texto_sem,
)


def _slug(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


class VarreduraPorCidade(Fonte):
    """Base. Cada portal implementa só `_vagas_da_cidade`."""

    cadencia = Cadencia.DIARIA
    ignora_termos = True
    max_simultaneas = 3
    pausa = 0.3

    def __init__(self, cidades: list[str] | None = None):
        self.cidades = cidades if cidades is not None else config.CIDADES_REGIAO

    async def _vagas_da_cidade(self, cidade: str) -> list:
        raise NotImplementedError

    async def buscar(self, termo: str = "", navegador=None) -> Resultado:
        vagas, vistos = [], set()
        for cidade in self.cidades:
            try:
                achadas = await self._vagas_da_cidade(cidade)
            except Exception:
                # Uma cidade sem página no portal não pode derrubar as 34
                # restantes. Cidade pequena costuma nem existir na URL.
                continue
            for v in achadas:
                if v and v.link not in vistos:
                    vistos.add(v.link)
                    vagas.append(v)
        return Resultado(vagas=vagas)


class BNE(VarreduraPorCidade):
    """Banco Nacional de Empregos — a fonte mais bem estruturada das novas.

    Só existe busca por cidade aqui, e é justamente o que interessa. Cada card
    entrega quatro campos separados, em `div.info__vaga > h3`, nesta ordem:

        área · empresa · MODALIDADE · cidade

    MEDIDO ao vivo em Sorocaba: 20 cards, com "Presencial" e "Sorocaba / SP"
    em campos próprios. Modalidade declarada pela fonte é rara neste projeto
    e vale muito — é o que evita ter de adivinhar remoto pelo texto.

    O card não tem link: o endereço se monta do id da `<section>`
    (`id="job-6088435"` -> /vagas/6088435), confirmado ao vivo. Testei
    /vaga/{id} e /vaga-de-emprego/{id} antes: os dois dão 404.
    """

    nome = "BNE"
    BASE = "https://www.bne.com.br"

    async def _vagas_da_cidade(self, cidade: str) -> list:
        sopa = await get_html(f"{self.BASE}/vagas-de-emprego/{_slug(cidade)}-sp")
        vagas = []
        for card in sopa.select("section.job__card__container"):
            titulo_el = card.select_one("h2 strong")
            if not titulo_el:
                continue

            ident = (card.get("id") or "").replace("job-", "")
            if not ident:
                continue

            campos = [h.get_text(" ", strip=True)
                      for h in card.select("div.info__vaga > h3")]
            # Posições fixas, mas com guarda: card incompleto não pode
            # deslocar cidade pra dentro de modalidade.
            empresa = campos[1] if len(campos) > 1 else ""
            modalidade = campos[2] if len(campos) > 2 else ""
            local = campos[3] if len(campos) > 3 else cidade

            vagas.append(montar(
                titulo=titulo_el.get_text(" ", strip=True),
                empresa="" if empresa.lower() == "confidencial" else empresa,
                local=local,
                link=f"{self.BASE}/vagas/{ident}",
                fonte=self.nome,
                modalidade=modalidade,
                termo_busca=f"cidade:{cidade}",
            ))
        return vagas


class VagasComPorCidade(VarreduraPorCidade):
    """Vagas.com por cidade — mesmo card da busca por termo."""

    nome = "Vagas.com (por cidade)"
    BASE = "https://www.vagas.com.br"
    MAX_PAGINAS = 3

    async def _vagas_da_cidade(self, cidade: str) -> list:
        vagas = []
        for pagina in range(1, self.MAX_PAGINAS + 1):
            sopa = await get_html(
                f"{self.BASE}/vagas-em-{_slug(cidade)}-sp",
                params={"pagina": pagina} if pagina > 1 else None,
            )
            cards = sopa.select("li.vaga")
            if not cards:
                break
            antes = len(vagas)
            for card in cards:
                link_el = card.select_one("h2.cargo a")
                if not link_el:
                    continue
                href = link_el.get("href") or ""
                empresa_el = card.select_one("span.emprVaga")
                data_el = card.select_one("span.data-publicacao")
                nome_empresa = empresa_el.get_text(strip=True) if empresa_el else ""
                vagas.append(montar(
                    titulo=link_el.get("title") or link_el.get_text(strip=True),
                    empresa=nome_empresa,
                    local=texto_sem(card.select_one("div.vaga-local"), ".tooltip-place") or cidade,
                    link=f"{self.BASE}{href}" if href.startswith("/") else href,
                    fonte="Vagas.com",
                    publicado_em=datas.extrair_data(
                        None, data_el.get_text(strip=True) if data_el else ""),
                    termo_busca=f"cidade:{cidade}",
                    empresa_url=(f"{self.BASE}/vagas-de-{_slug(nome_empresa)}"
                                 if nome_empresa else ""),
                ))
            if len(vagas) == antes:
                break
        return vagas


class InfoJobsPorCidade(VarreduraPorCidade):
    """InfoJobs por cidade — mesmo card da busca por termo."""

    nome = "InfoJobs (por cidade)"
    BASE = "https://www.infojobs.com.br"
    MAX_PAGINAS = 3

    async def _vagas_da_cidade(self, cidade: str) -> list:
        vagas = []
        for pagina in range(1, self.MAX_PAGINAS + 1):
            sopa = await get_html(
                f"{self.BASE}/empregos-em-{_slug(cidade)},-sp.aspx",
                params={"Page": pagina} if pagina > 1 else None,
            )
            cards = sopa.select('div[id^="vacancy"]')
            if not cards:
                break
            antes = len(vagas)
            for card in cards:
                titulo_el = card.select_one("h2")
                link_el = card.select_one('a[href*="/vaga-de-"]')
                if not titulo_el or not link_el:
                    continue
                href = link_el.get("href") or ""
                empresa_el = card.select_one('a[href*="/empresa-"]')
                empresa_href = (empresa_el.get("href") or "") if empresa_el else ""
                if empresa_href.startswith("/"):
                    empresa_href = self.BASE + empresa_href
                local_el = card.select_one("div.mb-8")
                vagas.append(montar(
                    titulo=titulo_el.get_text(strip=True),
                    empresa=empresa_el.get_text(strip=True) if empresa_el else "",
                    empresa_url=empresa_href.split("?")[0],
                    local=(local_el.get_text(" ", strip=True).split(",")[0]
                           if local_el else cidade),
                    link=f"{self.BASE}{href}" if href.startswith("/") else href,
                    fonte="InfoJobs",
                    termo_busca=f"cidade:{cidade}",
                ))
            if len(vagas) == antes:
                break
        return vagas


class CathoPorCidade(VarreduraPorCidade):
    """Catho por cidade."""

    nome = "Catho (por cidade)"
    BASE = "https://www.catho.com.br/vagas"

    async def _vagas_da_cidade(self, cidade: str) -> list:
        sopa = await get_html(f"{self.BASE}/sp-{_slug(cidade)}/")
        vagas = []
        for card in sopa.select("article"):
            titulo_el = card.select_one("h2 a") or card.select_one("h2")
            if not titulo_el:
                continue
            href = titulo_el.get("href") if titulo_el.name == "a" else ""
            empresa_el = card.select_one("p.mb-2 span")
            vagas.append(montar(
                titulo=titulo_el.get_text(strip=True),
                empresa=empresa_el.get_text(strip=True) if empresa_el else "",
                local=cidade,
                link=(f"https://www.catho.com.br{href}"
                      if href and href.startswith("/") else (href or "")),
                fonte="Catho",
                termo_busca=f"cidade:{cidade}",
            ))
        return vagas


TODAS = [BNE, VagasComPorCidade, InfoJobsPorCidade, CathoPorCidade]
