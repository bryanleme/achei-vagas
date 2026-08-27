"""Gupy — a fonte mais importante do perfil, e a única com dado estruturado.

A Gupy é o ATS que a indústria brasileira usa. Pra um perfil de PCP,
logística e melhoria contínua no interior de São Paulo, é onde as vagas
estão de verdade — mais que LinkedIn, muito mais que os agregadores de
vaga remota em inglês.

DESCOBERTA QUE MUDA O PROJETO: existe API JSON pública por trás do portal.

    https://employability-portal.gupy.io/api/v1/jobs?jobName=<termo>

Encontrada inspecionando as requisições reais do portal (o HTML servido não
traz vaga nenhuma — a página monta no navegador). Isso importa muito mais
que "uma fonte a menos no Playwright":

  - `city`, `state` e `country` vêm em CAMPOS SEPARADOS. A classe de bug que
    mais custou tempo no projeto original existe porque as fontes entregam
    local como texto solto e é preciso adivinhar o que é cidade, estado e
    país. Aqui não há o que adivinhar.

  - `workplaceType` é "remote" / "hybrid" / "on-site". Modalidade declarada
    pela fonte, não deduzida de palavra solta no texto.

  - `publishedDate` é ISO de verdade. No projeto original a data estava
    vazia em 1.061 de 1.061 vagas do LinkedIn, e sem data toda vaga velha
    ia como alerta imediato.

  - `pagination.total` diz quantas vagas existem. Não é preciso descobrir o
    fim da paginação por tentativa e erro — que foi o que gerou quinze
    alarmes falsos de "vaga pode ter ficado de fora" por ciclo.

Custo: uma requisição HTTP de milissegundos, sem navegador.
"""

from urllib.parse import urlparse

from radar.fontes.base import Cadencia, Fonte, Resultado, get_json, montar, texto_local

API = "https://employability-portal.gupy.io/api/v1/jobs"

# O portal exige Origin/Referer coerentes. Sem eles a API responde, mas não
# vale arriscar: é o comportamento que o navegador real produz.
CABECALHOS = {
    "Origin": "https://portal.gupy.io",
    "Referer": "https://portal.gupy.io/",
}

# 12 é o tamanho de página do próprio portal; 100 é aceito e reduz o número
# de requisições por termo.
POR_PAGINA = 100

# Teto de páginas por passada. Com 100 por página, 3 páginas são 300 vagas
# pra um único termo — muito acima do que qualquer termo do perfil devolve
# (o mais concorrido testado deu 82). O teto existe pra um termo
# inesperadamente genérico não virar centenas de requisições.
MAX_PAGINAS = 3

_MODALIDADE = {
    "remote": "Remoto",
    "hybrid": "Híbrido",
    "on-site": "Presencial",
    "onsite": "Presencial",
}


def _pagina_de_carreira(bruto: str) -> str:
    """"https://tenant.gupy.io/eyJzb3VyY2Ui..." -> "https://tenant.gupy.io/".

    O sufixo é um payload em base64 que identifica de onde veio o clique, não
    a página. Guardar a URL inteira daria um link que funciona hoje e envelhece
    mal; o domínio é a página de carreira da empresa, estável.
    """
    dom = urlparse(bruto or "").netloc
    return f"https://{dom}/" if dom.endswith(".gupy.io") else ""


def _converter(item: dict, termo_busca: str = ""):
    return montar(
        titulo=item.get("name", ""),
        empresa=item.get("careerPageName", ""),
        # Remonta o texto no formato que o filtro entende, em vez de criar
        # um caminho paralelo só pra esta fonte. País incluído porque é o
        # que faz o escopo resolver pra Brasil numa vaga remota.
        local=texto_local(item.get("city"), item.get("state"), item.get("country")),
        link=item.get("jobUrl", ""),
        fonte="Gupy",
        modalidade=_MODALIDADE.get((item.get("workplaceType") or "").lower(), ""),
        # A API manda "2026-08-26T17:04:33.492Z"; o projeto guarda só a data.
        publicado_em=(item.get("publishedDate") or "")[:10],
        # A descrição vem na MESMA resposta. Não usamos pra filtrar (o
        # filtro lê só o título, de propósito), mas descartar seria jogar
        # fora o único texto de requisito que o projeto tem de graça.
        descricao=item.get("description", "") or "",
        termo_busca=termo_busca,
        empresa_url=_pagina_de_carreira(item.get("careerPageUrl")),
    )


class Gupy(Fonte):
    nome = "Gupy"
    cadencia = Cadencia.TODO_CICLO
    usa_navegador = False

    def __init__(self, estado: str | None = None):
        # Duas passadas por termo, e as duas são necessárias:
        #
        #   - com `state`: garante alcançar vaga de cidade pequena do
        #     corredor, que num resultado nacional fica soterrada por SP
        #     capital e Campinas.
        #   - sem `state`: é a única que traz vaga REMOTA, que por definição
        #     não tem estado.
        #
        # Sem a segunda, todo remoto sumiria; sem a primeira, o interior
        # sumiria. Foi exatamente esse buraco (busca nacional não alcança
        # cidade pequena) que fez uma vaga real nunca ser notificada no
        # projeto original.
        self.estado = estado

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        vagas = []
        vistos = set()

        for filtro_estado in ([self.estado, None] if self.estado else [None]):
            for pagina in range(MAX_PAGINAS):
                params = {
                    "jobName": termo,
                    "limit": POR_PAGINA,
                    "offset": pagina * POR_PAGINA,
                }
                if filtro_estado:
                    params["state"] = filtro_estado

                dados = await get_json(API, params=params, headers=CABECALHOS)
                itens = dados.get("data") or []

                for item in itens:
                    vaga = _converter(item, termo_busca=termo)
                    if vaga and vaga.link not in vistos:
                        vistos.add(vaga.link)
                        vagas.append(vaga)

                # A API DIZ o total. Nada de deduzir fim de paginação pelo
                # tamanho da página, que é como nasce o alarme falso de
                # "pode ter ficado vaga de fora".
                total = (dados.get("pagination") or {}).get("total", 0)
                if (pagina + 1) * POR_PAGINA >= total:
                    break

        return Resultado(vagas=vagas)
