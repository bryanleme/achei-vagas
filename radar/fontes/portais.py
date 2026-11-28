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
    get_json,
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


# ---------------------------------------------------------------------------
# Empregos.com.br
# ---------------------------------------------------------------------------
# Estava na lista de "testado e ficou de fora" (HTTP 502). Sondado de novo
# em 28/11/2026: responde 200 e serve 20 cards por página no HTML, cada um
# com cidade+UF, modalidade, data relativa e salário em texto próprio — o
# card mais completo entre os portais brasileiros do projeto.

EMPREGOS_BASE = "https://www.empregos.com.br"
_LOCAL_EMPREGOS = re.compile(r"^[^\d|]{2,60}, [A-Z]{2}$")
_MODALIDADES_EMPREGOS = {"presencial", "remoto", "híbrido", "hibrido", "home office"}


def cards_empregos(sopa, termo_busca: str) -> list:
    """Vagas dos cards de uma página de listagem do Empregos.com.br.

    Função de módulo, e não método: a busca por termo e a varredura por
    cidade (por_cidade.py) leem o MESMO card, e duas cópias do parser
    divergiriam na primeira mudança de layout.

    O local vem do texto do card ("São Roque, SP"), nunca da cidade
    pesquisada. MEDIDO: a busca por "analista de logística em Sorocaba"
    devolve vagas de São Roque, Mairinque e Itu — cidades vizinhas, que só
    passam no filtro se forem do perfil.
    """
    vagas = []
    for card in sopa.select("div#job-card"):
        for lixo in card.select('[id^="share"]'):
            lixo.decompose()
        no_link = card.select_one('a[href*="/vaga/"]')
        no_titulo = card.select_one("h2")
        if not no_link or not no_titulo:
            continue
        href = (no_link.get("href") or "").split("?")[0]
        no_empresa = card.select_one("h3")

        textos = list(card.stripped_strings)
        local = next((t for t in textos if _LOCAL_EMPREGOS.match(t)), "")
        modalidade = next((t for t in textos if t.lower() in _MODALIDADES_EMPREGOS), "")
        publicada = next((t for t in textos if t.lower().startswith("publicada")), "")

        vagas.append(montar(
            titulo=no_titulo.get_text(" ", strip=True),
            empresa=no_empresa.get_text(" ", strip=True) if no_empresa else "",
            local=local,
            link=f"{EMPREGOS_BASE}{href}" if href.startswith("/") else href,
            fonte="Empregos.com.br",
            modalidade=modalidade,
            publicado_em=datas.extrair_data(None, publicada),
            termo_busca=termo_busca,
        ))
    return [v for v in vagas if v]


class EmpregosCom(Fonte):
    """Empregos.com.br — busca por termo, Brasil inteiro, 20 cards por página.

    Nacional de propósito: é o que traz a vaga remota. A vaga presencial da
    região vem com mais garantia da varredura por cidade (por_cidade.py).
    """

    nome = "Empregos.com.br"
    cadencia = Cadencia.TODO_CICLO
    MAX_PAGINAS = 2

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        vagas, vistos = [], set()
        for pagina in range(1, self.MAX_PAGINAS + 1):
            sufixo = f"/{pagina}" if pagina > 1 else ""
            sopa = await get_html(f"{EMPREGOS_BASE}/vagas/{_slug(termo)}{sufixo}")
            achadas = cards_empregos(sopa, termo)
            novas = [v for v in achadas if v.link not in vistos]
            if not novas:
                break
            vistos.update(v.link for v in novas)
            vagas.extend(novas)
        return Resultado(vagas=vagas)


# ---------------------------------------------------------------------------
# Remotar
# ---------------------------------------------------------------------------
# Portal BRASILEIRO de vaga remota, com API JSON pública (a mesma que o site
# usa). Sondado em 28/11/2026: "analista de dados" -> 381 vagas, 50 por
# página, cada uma com `type` (remote/hybrid), cidade/UF da híbrida e data
# ISO. É a fonte que faltava entre os agregadores em inglês de remotas.py e
# os portais generalistas: remoto PRO Brasil, por definição do site.
#
# A busca da API é larga (devolve muito "analista" de outras áreas) — o
# filtro de título é quem decide, como em toda fonte.

_TIPO_REMOTAR = {"remote": "Remoto", "hybrid": "Híbrido", "onsite": "Presencial",
                 "on-site": "Presencial", "presential": "Presencial"}


def converter_remotar(item: dict, termo_busca: str = ""):
    modalidade = _TIPO_REMOTAR.get((item.get("type") or "").lower(), "")
    cidade = (item.get("city") or "").strip()
    uf = (item.get("state") or "").strip()
    if modalidade == "Remoto":
        local = "Remoto"
    else:
        local = f"{cidade} - {uf}" if cidade and uf else cidade
    empresa = item.get("companyDisplayName") or (item.get("company") or {}).get("name") or ""
    criado = str(item.get("createdAt") or "")[:10]
    return montar(
        titulo=item.get("title", ""),
        empresa=empresa,
        local=local,
        link=f"https://remotar.com.br/job/{item['id']}" if item.get("id") else "",
        fonte="Remotar",
        modalidade=modalidade,
        publicado_em=criado if re.match(r"^\d{4}-\d{2}-\d{2}$", criado) else "",
        descricao=item.get("description", "") or "",
        termo_busca=termo_busca,
    )


class Remotar(Fonte):
    nome = "Remotar"
    cadencia = Cadencia.TODO_CICLO

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        dados = await get_json("https://api.remotar.com.br/jobs", params={"search": termo})
        vagas = [converter_remotar(i, termo) for i in (dados.get("data") or [])]
        return Resultado(vagas=[v for v in vagas if v])


TODOS = [VagasCom, InfoJobs, Catho, EmpregosCom, Remotar]
