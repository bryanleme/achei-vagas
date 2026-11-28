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
            no_titulo = card.select_one("h2 strong")
            if not no_titulo:
                continue

            ident = (card.get("id") or "").replace("job-", "")
            if not ident:
                continue

            # MEDIDO em 28/11/2026: o BNE trocou `h3` por
            # `div.info__vaga-item` (mesma ordem: área, empresa, modalidade,
            # cidade). Com o seletor antigo `campos` vinha vazio e o local
            # caía na cidade PESQUISADA — o mesmo erro da Catho, escondido.
            # Os dois seletores ficam: o que o portal servir, vale.
            itens = card.select("div.info__vaga > div.info__vaga-item") or                 card.select("div.info__vaga > h3")
            campos = [h.get_text(" ", strip=True) for h in itens]
            # Posições fixas, mas com guarda: card incompleto não pode
            # deslocar cidade pra dentro de modalidade.
            empresa = campos[1] if len(campos) > 1 else ""
            modalidade = campos[2] if len(campos) > 2 else ""
            # Sem o campo de cidade, "" — nunca a cidade pesquisada. Ver
            # _local_do_card_catho: a página de uma cidade nem sempre é dela.
            local = campos[3] if len(campos) > 3 else ""

            vagas.append(montar(
                titulo=no_titulo.get_text(" ", strip=True),
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
                no_link = card.select_one("h2.cargo a")
                if not no_link:
                    continue
                href = no_link.get("href") or ""
                no_empresa = card.select_one("span.emprVaga")
                data_el = card.select_one("span.data-publicacao")
                nome_empresa = no_empresa.get_text(strip=True) if no_empresa else ""
                vagas.append(montar(
                    titulo=no_link.get("title") or no_link.get_text(strip=True),
                    empresa=nome_empresa,
                    local=texto_sem(card.select_one("div.vaga-local"), ".tooltip-place"),
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
                no_titulo = card.select_one("h2")
                no_link = card.select_one('a[href*="/vaga-de-"]')
                if not no_titulo or not no_link:
                    continue
                href = no_link.get("href") or ""
                no_empresa = card.select_one('a[href*="/empresa-"]')
                empresa_href = (no_empresa.get("href") or "") if no_empresa else ""
                if empresa_href.startswith("/"):
                    empresa_href = self.BASE + empresa_href
                no_local = card.select_one("div.mb-8")
                vagas.append(montar(
                    titulo=no_titulo.get_text(strip=True),
                    empresa=no_empresa.get_text(strip=True) if no_empresa else "",
                    empresa_url=empresa_href.split("?")[0],
                    local=(no_local.get_text(" ", strip=True).split(",")[0]
                           if no_local else ""),
                    link=f"{self.BASE}{href}" if href.startswith("/") else href,
                    fonte="InfoJobs",
                    termo_busca=f"cidade:{cidade}",
                ))
            if len(vagas) == antes:
                break
        return vagas


def _local_do_card_catho(card) -> str:
    """O local que o PRÓPRIO card declara — nunca a cidade pesquisada.

    MEDIDO em 28/11/2026: a página /vagas/sp-itapetininga/ da Catho devolveu
    20 cards e NENHUM era de Itapetininga — São Paulo, Curitiba, Joinville,
    Salvador, João Pessoa... A Catho preenche a página da cidade com vaga do
    Brasil inteiro. Gravar `local=cidade` carimbava todas como Itapetininga
    (ou Sorocaba): 28 vagas de outras cidades viraram alerta imediato com
    +3 de "cidade da região", entre elas uma de Palmas/TO com nota 10.

    Formato do card: "1 vaga - Palmas". A cidade vem depois do hífen.
    Card sem local devolve "" — e "" não casa cidade nenhuma, que é o
    certo: sem saber onde é, não dá pra dizer que é na região.
    """
    no_local = card.select_one('p:has(span[class*="location"])')
    texto = no_local.get_text(" ", strip=True) if no_local else ""
    if " - " in texto:
        texto = texto.split(" - ", 1)[1]
    return " ".join(texto.split())


class CathoPorCidade(VarreduraPorCidade):
    """Catho por cidade.

    A página da cidade NÃO filtra por cidade (ver _local_do_card_catho). O
    que ela ainda dá é volume: os cards que de fato são da região passam
    pelo filtro de cidade normalmente; os outros são barrados por ele.
    """

    nome = "Catho (por cidade)"
    BASE = "https://www.catho.com.br/vagas"

    async def _vagas_da_cidade(self, cidade: str) -> list:
        sopa = await get_html(f"{self.BASE}/sp-{_slug(cidade)}/")
        vagas = []
        for card in sopa.select("article"):
            no_titulo = card.select_one("h2 a") or card.select_one("h2")
            if not no_titulo:
                continue
            href = no_titulo.get("href") if no_titulo.name == "a" else ""
            no_empresa = card.select_one("p.mb-2 span")
            vagas.append(montar(
                titulo=no_titulo.get_text(strip=True),
                empresa=no_empresa.get_text(strip=True) if no_empresa else "",
                local=_local_do_card_catho(card),
                link=(f"https://www.catho.com.br{href}"
                      if href and href.startswith("/") else (href or "")),
                fonte="Catho",
                termo_busca=f"cidade:{cidade}",
            ))
        return vagas


class EmpregosComPorCidade(VarreduraPorCidade):
    """Empregos.com.br por cidade — /vagas/em-<cidade>-sp.

    Ao contrário da Catho, esta página é DA cidade: MEDIDO em 28/11/2026,
    /vagas/em-itapetininga-sp disse "46 vagas de emprego em Itapetininga,
    SP". Mesmo assim o local vem do card (cards_empregos), não da URL — a
    lição da Catho é não confiar no que a página promete.
    """

    nome = "Empregos.com.br (por cidade)"
    MAX_PAGINAS = 3

    async def _vagas_da_cidade(self, cidade: str) -> list:
        from radar.fontes.portais import EMPREGOS_BASE, cards_empregos

        vagas, vistos = [], set()
        for pagina in range(1, self.MAX_PAGINAS + 1):
            sufixo = f"/{pagina}" if pagina > 1 else ""
            sopa = await get_html(f"{EMPREGOS_BASE}/vagas/em-{_slug(cidade)}-sp{sufixo}")
            novas = [v for v in cards_empregos(sopa, f"cidade:{cidade}") if v.link not in vistos]
            if not novas:
                break
            vistos.update(v.link for v in novas)
            vagas.extend(novas)
        return vagas


TODAS = [BNE, VagasComPorCidade, InfoJobsPorCidade, CathoPorCidade, EmpregosComPorCidade]
