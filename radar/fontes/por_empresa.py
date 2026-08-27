"""Varredura por EMPRESA, e não por termo de busca.

O PROBLEMA QUE ISTO RESOLVE

A busca por termo tem um limite estrutural: o rodízio roda 15 termos por
ciclo, então uma vaga só é encontrada quando o termo certo chega a vez. Com
43 termos isso é uma volta a cada ~15 horas — e a vaga ainda precisa que a
fonte case o texto do jeito que a gente escreveu.

MEDIDO em produção: "Analista de Suprimentos (Foco em Defesa) - Pleno" só
apareceu quando "analista de suprimentos" entrou no bloco do ciclo. O título
batia o perfil sem esforço; o que faltava era a busca ter perguntado por ele.

Varrer por EMPRESA elimina essa dependência. Uma empresa do corredor tem
todas as suas vagas listadas de uma vez, independente de qual termo está na
vez. Se a Smurfit de Porto Feliz abrir qualquer coisa amanhã, o próximo ciclo
vê — sem esperar o rodízio.

COMO A LISTA DE EMPRESAS APARECE

Ninguém digita lista de empresa à mão aqui. O ciclo registra sozinho toda
empresa que publicou vaga NUMA CIDADE DA REGIÃO — inclusive quando a vaga em
si foi reprovada no filtro de cargo.

Registrar pela CIDADE e não pelo cargo é a decisão que faz isto valer a pena:
uma metalúrgica de Tatuí que hoje publicou "Soldador" é exatamente a empresa
que mês que vem publica "Analista de PCP". Filtrar a descoberta por cargo
descartaria justamente as empresas certas, por causa da vaga errada.

CADA PORTAL COM SEU PRÓPRIO ENDEREÇO

Nome de empresa não é identificador universal — cada portal escreve do seu
jeito, e "Yazaki do Brasil Ltda." (do Vagas.com) não existe como empresa na
Gupy. Por isso a fonte de origem é gravada junto do nome, e cada varredura só
consulta as empresas que ela mesma descobriu.
"""

import re
import unicodedata

from radar.fontes.base import (
    Cadencia,
    Fonte,
    Resultado,
    get_html,
    get_json,
    montar,
    primeiro_texto,
)
from radar.fontes.gupy import API, CABECALHOS, _converter

# Teto de empresas por execução. A lista cresce sozinha e sem teto viraria
# centenas de requisições no dia em que a região estiver bem mapeada. As mais
# recentes primeiro — empresa que publicou aqui esta semana tem mais chance de
# publicar de novo que uma que apareceu uma vez há seis meses.
MAX_EMPRESAS = 50


def _slug(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


class VarreduraPorEmpresa(Fonte):
    """Base das varreduras. Cada portal implementa só `_vagas_de`.

    `ignora_termos` faz o ciclo chamar esta fonte UMA vez por execução, em vez
    de uma vez por termo — repetir a mesma varredura 15 vezes custaria 15x
    pelo mesmo resultado.

    Cadência diária porque abertura de vaga não é evento de minuto: varrer 50
    empresas a cada 3 horas seriam 400 requisições por dia atrás de uma lista
    que muda devagar.
    """

    cadencia = Cadencia.DIARIA
    ignora_termos = True
    fonte_de_origem = ""      # de quem herdar a lista de empresas
    precisa_de_url = True     # o portal identifica empresa por endereço?
    max_simultaneas = 2
    pausa = 0.5

    def __init__(self, empresas=None):
        self._empresas = empresas

    def _lista(self):
        if self._empresas is not None:
            return self._empresas
        # Import tardio: `deposito` puxa `modelos`, que puxa daqui em cadeia.
        from radar import deposito

        if self.precisa_de_url:
            return deposito.empresas_com_url(self.fonte_de_origem, limite=MAX_EMPRESAS)
        return [(n, "") for n in
                deposito.empresas_do_corredor(self.fonte_de_origem, limite=MAX_EMPRESAS)]

    async def _vagas_de(self, nome: str, url: str) -> list:
        raise NotImplementedError

    async def buscar(self, termo: str = "", navegador=None) -> Resultado:
        vagas, vistos = [], set()
        for nome, url in self._lista():
            try:
                achadas = await self._vagas_de(nome, url)
            except Exception:
                # Uma empresa que some ou muda de endereço não pode derrubar
                # a varredura das outras 49.
                continue
            for v in achadas:
                if v and v.link not in vistos:
                    vistos.add(v.link)
                    vagas.append(v)
        return Resultado(vagas=vagas)


class GupyPorEmpresa(VarreduraPorEmpresa):
    """Gupy — filtra o portal central por `careerPageName`.

    Não existe endpoint por tenant: `<empresa>.gupy.io/api/...` responde 404
    em todas as variações testadas. O próprio portal central aceita o nome da
    empresa como filtro, confirmado por sonda com valor inexistente (devolve
    total=0 em vez de ignorar o parâmetro).
    """

    nome = "Gupy (por empresa)"
    fonte_de_origem = "Gupy"
    precisa_de_url = False  # identifica empresa pelo NOME, não por endereço

    async def _vagas_de(self, nome: str, url: str) -> list:
        dados = await get_json(
            API,
            params={"careerPageName": nome, "limit": 100, "offset": 0},
            headers=CABECALHOS,
        )
        return [_converter(i, termo_busca=f"empresa:{nome}")
                for i in (dados.get("data") or [])]


class VagasComPorEmpresa(VarreduraPorEmpresa):
    """Vagas.com — a página da empresa usa o MESMO card da busca.

    Confirmado ao vivo: /vagas-de-<slug do nome> devolve `li.vaga` idêntico ao
    da busca por termo, com local e data. Reaproveita o mesmo parsing.
    """

    nome = "Vagas.com (por empresa)"
    fonte_de_origem = "Vagas.com"
    BASE = "https://www.vagas.com.br"

    async def _vagas_de(self, nome: str, url: str) -> list:
        sopa = await get_html(url or f"{self.BASE}/vagas-de-{_slug(nome)}")
        vagas = []
        for card in sopa.select("li.vaga"):
            link_el = card.select_one("h2.cargo a")
            if not link_el:
                continue
            href = link_el.get("href") or ""
            vagas.append(montar(
                titulo=link_el.get("title") or link_el.get_text(strip=True),
                empresa=nome,
                local=primeiro_texto(card.select_one("div.vaga-local")),
                link=f"{self.BASE}{href}" if href.startswith("/") else href,
                fonte="Vagas.com",
                termo_busca=f"empresa:{nome}",
                empresa_url=url,
            ))
        return vagas


class InfoJobsPorEmpresa(VarreduraPorEmpresa):
    """InfoJobs — a página da empresa usa uma estrutura DIFERENTE da busca.

    Não há `div[id^=vacancy]` aqui: cada vaga é um `div.link-item` que contém
    APENAS o link e o título. Reaproveitar o parser da busca devolveria zero,
    em silêncio.

    E a cidade não está no bloco — MEDIDO, o bloco inteiro é
    `<div class="link-item"><a ...>JARDINEIRO - SHOPPING CIDADE JARDIM</a></div>`.
    Uma varredura sem cidade seria inútil: vaga sem local não bate cidade
    nenhuma nem é remota, então o filtro derruba tudo e o custo é gasto à toa.

    A cidade está no ENDEREÇO da vaga, que o portal monta como
    `/vaga-de-<titulo>-em-<cidade>__<id>.aspx`. É dado real da fonte, só que
    guardado noutro lugar.

    LIMITE ACEITO: o endereço não traz a UF, então a guarda de estado não tem
    o que comparar e a cidade passa pelo nome. É exatamente o comportamento
    já documentado em `cidade_confere` para local sem UF — preferir o falso
    positivo raro ao falso negativo — e não uma exceção aberta aqui.
    """

    nome = "InfoJobs (por empresa)"
    fonte_de_origem = "InfoJobs"
    BASE = "https://www.infojobs.com.br"

    # O ÚLTIMO "-em-" antes do id é o que separa título de cidade: um título
    # pode conter "em" ("analista em treinamento"), o sufixo do endereço não.
    _CIDADE_NA_URL = re.compile(r"^.*-em-([a-z0-9-]+?)__", re.IGNORECASE)

    @classmethod
    def _cidade_do_endereco(cls, href: str) -> str:
        m = cls._CIDADE_NA_URL.match(href.rsplit("/", 1)[-1])
        return m.group(1).replace("-", " ") if m else ""

    async def _vagas_de(self, nome: str, url: str) -> list:
        if not url:
            return []
        sopa = await get_html(url)
        vagas = []
        for bloco in sopa.select("div.link-item"):
            link_el = bloco.select_one('a[href*="/vaga-de-"]')
            if not link_el:
                continue
            href = link_el.get("href") or ""
            vagas.append(montar(
                titulo=link_el.get_text(strip=True),
                empresa=nome,
                local=self._cidade_do_endereco(href),
                link=f"{self.BASE}{href}" if href.startswith("/") else href,
                fonte="InfoJobs",
                termo_busca=f"empresa:{nome}",
                empresa_url=url,
            ))
        return vagas


TODAS = [GupyPorEmpresa, VagasComPorEmpresa, InfoJobsPorEmpresa]
