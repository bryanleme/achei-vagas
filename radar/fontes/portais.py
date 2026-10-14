"""Portais brasileiros de vaga — todos por HTML servido, sem navegador.

MEDIDO antes de escrever uma linha de scraper, portal por portal: os três
daqui devolvem a lista de vagas pronta num GET simples. Vagas.com trouxe 10
cards, InfoJobs 22, Catho 20 — com título, empresa, local e, no caso do
Vagas.com, data de publicação real.

Isso contraria a suposição comum de que scraper de vaga precisa de
Chromium headless, e a diferença é enorme: com navegador o ciclo passaria
de uma hora, o navegador seria o vetor de todo bloqueio anti-bot, e uma
única fonte travada estenderia o ciclo em vários minutos entregando zero
vaga. Nada disso é necessário para a maioria das fontes.

O que continua valendo: se um portal passar a montar a lista só no
navegador, aí sim vale um scraper de Playwright — para ele, e não para
todos por precaução.
"""

import re
import unicodedata

from radar import datas
from radar.fontes.base import (
    Cadencia,
    Fonte,
    Resultado,
    get_html,
    montar,
    texto_sem,
)


def _slug(texto: str) -> str:
    """"Analista de PCP" -> "analista-de-pcp". Vagas.com e Catho põem o termo
    no CAMINHO da URL, não em parâmetro de busca."""
    tirar_acentos = unicodedata.normalize("NFKD", texto)
    tirar_acentos = "".join(c for c in tirar_acentos if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", tirar_acentos.lower()).strip("-")


class VagasCom(Fonte):
    """Vagas.com — o portal generalista com melhor cobertura de indústria.

    É a única fonte deste módulo que informa data de publicação, em formato
    brasileiro ("13/08/2026"), que `datas.extrair_data` já entende.
    """

    nome = "Vagas.com"
    cadencia = Cadencia.TODO_CICLO
    BASE = "https://www.vagas.com.br"
    MAX_PAGINAS = 2

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        vagas = []
        vistos = set()

        for pagina in range(1, self.MAX_PAGINAS + 1):
            url = f"{self.BASE}/vagas-de-{_slug(termo)}"
            sopa = await get_html(url, params={"pagina": pagina} if pagina > 1 else None)

            cards = sopa.select("li.vaga")
            if not cards:
                break  # acabaram os resultados

            novas_da_pagina = 0
            for card in cards:
                no_link = card.select_one("h2.cargo a")
                if not no_link:
                    continue

                href = no_link.get("href") or ""
                link = f"{self.BASE}{href}" if href.startswith("/") else href
                if link in vistos:
                    continue
                vistos.add(link)
                novas_da_pagina += 1

                no_empresa = card.select_one("span.emprVaga")
                data_el = card.select_one("span.data-publicacao")
                empresa_nome = no_empresa.get_text(strip=True) if no_empresa else ""

                vaga = montar(
                    titulo=no_link.get("title") or no_link.get_text(strip=True),
                    empresa=empresa_nome,
                    # O portal publica a empresa em /vagas-de-<slug do nome>.
                    # Derivado e não lido do card: o card não traz link de
                    # empresa, e o slug do nome resolve — confirmado ao vivo.
                    empresa_url=(f"{self.BASE}/vagas-de-{_slug(empresa_nome)}"
                                 if empresa_nome else ""),
                    # texto_sem: o elemento de local traz o tooltip
                    # grudado ("Sorocaba / SP A empresa aceita candidaturas
                    # de..."), e o texto inteiro faria o filtro de cidade
                    # comparar contra um parágrafo.
                    local=texto_sem(card.select_one("div.vaga-local"), ".tooltip-place"),
                    link=link,
                    fonte=self.nome,
                    publicado_em=datas.extrair_data(
                        None, data_el.get_text(strip=True) if data_el else ""
                    ),
                    termo_busca=termo,
                )
                if vaga:
                    vagas.append(vaga)

            # Página que só repetiu o que já veio é o fim da lista — os
            # portais devolvem a última página de novo em vez de vazia.
            if novas_da_pagina == 0:
                break

        return Resultado(vagas=vagas)


class InfoJobs(Fonte):
    nome = "InfoJobs"
    cadencia = Cadencia.TODO_CICLO
    BASE = "https://www.infojobs.com.br"
    MAX_PAGINAS = 2

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        vagas = []
        vistos = set()

        for pagina in range(1, self.MAX_PAGINAS + 1):
            params = {"palabra": termo}
            if pagina > 1:
                params["Page"] = pagina
            sopa = await get_html(f"{self.BASE}/empregos.aspx", params=params)

            # MEDIDO: existe uma div externa com a mesma classe da interna
            # envolvendo cada vaga. Selecionar por classe traz o card
            # DUPLICADO; 'div[id^=vacancy]' é só a interna, uma por vaga.
            cards = sopa.select('div[id^="vacancy"]')
            if not cards:
                break

            novas_da_pagina = 0
            for card in cards:
                no_titulo = card.select_one("h2")
                no_link = card.select_one('a[href*="/vaga-de-"]')
                if not no_titulo or not no_link:
                    continue

                href = no_link.get("href") or ""
                link = f"{self.BASE}{href}" if href.startswith("/") else href
                if link in vistos:
                    continue
                vistos.add(link)
                novas_da_pagina += 1

                no_empresa = card.select_one('a[href*="/empresa-"]')
                no_local = card.select_one("div.mb-8")

                # O id numérico do fim da URL não é derivável do nome, então
                # o link precisa ser lido do card — não dá pra reconstruir
                # depois. Manter o ".aspx": sem ele o portal devolve 404.
                empresa_href = (no_empresa.get("href") or "") if no_empresa else ""
                if empresa_href.startswith("/"):
                    empresa_href = self.BASE + empresa_href

                vaga = montar(
                    titulo=no_titulo.get_text(strip=True),
                    empresa=no_empresa.get_text(strip=True) if no_empresa else "",
                    empresa_url=empresa_href.split("?")[0],
                    local=(
                        no_local.get_text(" ", strip=True).split(",")[0]
                        if no_local else ""
                    ),
                    link=link,
                    fonte=self.nome,
                    termo_busca=termo,
                )
                if vaga:
                    vagas.append(vaga)

            if novas_da_pagina == 0:
                break

        return Resultado(vagas=vagas)


class Catho(Fonte):
    """Catho — busca por slug de termo mais estado no caminho da URL.

    Cadência diária: a Catho tem rendimento baixo pro perfil e é a fonte
    mais sujeita a timeout. Custa um GET em vez de uma sessão de navegador,
    mas o rendimento baixo é motivo pra não gastar uma passada dela a cada
    três horas.
    """

    nome = "Catho"
    cadencia = Cadencia.DIARIA
    BASE = "https://www.catho.com.br/vagas"

    def __init__(self, uf: str = "sp", estado: str = "sao-paulo"):
        self.uf = uf
        self.estado = estado

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        url = f"{self.BASE}/{_slug(termo)}/{self.uf}-{self.estado}/"
        sopa = await get_html(url)

        vagas = []
        for card in sopa.select("article"):
            no_titulo = card.select_one("h2 a") or card.select_one("h2")
            if not no_titulo:
                continue

            href = no_titulo.get("href") if no_titulo.name == "a" else ""
            link = (
                f"https://www.catho.com.br{href}"
                if href and href.startswith("/") else (href or "")
            )

            no_empresa = card.select_one("p.mb-2 span")
            no_local = card.select_one('p:has(span[class*="location"])')
            local_texto = no_local.get_text(" ", strip=True) if no_local else ""
            # Formato "3 vagas - São Paulo": a cidade vem depois do hífen.
            if " - " in local_texto:
                local_texto = local_texto.split(" - ", 1)[1]

            vaga = montar(
                titulo=no_titulo.get_text(strip=True),
                empresa=no_empresa.get_text(strip=True) if no_empresa else "",
                local=local_texto,
                link=link,
                fonte=self.nome,
                termo_busca=termo,
            )
            if vaga:
                vagas.append(vaga)

        return Resultado(vagas=vagas)


TODOS = [VagasCom, InfoJobs, Catho]
