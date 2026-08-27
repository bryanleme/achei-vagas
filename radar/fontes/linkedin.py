"""LinkedIn — endpoint público de convidado, sem login e sem navegador.

    /jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=...&geoId=...

É o mesmo endpoint que a página usa pra carregar mais resultados sem você
estar logado. Devolve HTML de cards, não JSON, mas responde a um GET simples.

DUAS ARMADILHAS QUE JÁ CUSTARAM CARO NESTE ENDPOINT, as duas resolvidas aqui:

1. `location=<texto>` NÃO FUNCIONA, E FALHA EM SILÊNCIO. Não devolve erro nem
   lista vazia: devolve um resultado genérico dos Estados Unidos. Do lado de
   quem chama é indistinguível de sucesso.

   Medido no projeto original: das 910 vagas que o LinkedIn trouxe pro perfil
   brasileiro, 281 eram dos EUA e 19 eram do Brasil. A passada nacional nunca
   trouxe uma vaga brasileira, por meses, sem um único erro no log.

   `geoId` é o identificador interno que a busca de verdade usa por trás.
   106057199 é o Brasil, confirmado ao vivo: os resultados vêm de Belo
   Horizonte, Barueri, Ponta Grossa, Caetanos.

2. O FILTRO NATIVO DE REMOTO (f_WT=2) NÃO FUNCIONA AQUI, e confiar nele
   custou caro. Esta fonte rodava uma segunda passada com f_WT=2 e marcava
   TUDO que voltava como modalidade="Remoto", no entendimento de que "o
   próprio LinkedIn já garante isso".

   Não garante. MEDIDO em três termos: a busca com f_WT=2 devolve o MESMO
   número de vagas que a busca sem ele, em cidades físicas espalhadas pelo
   país — Cambé/PR, Nerópolis/GO, São Luís/MA, Macaé/RJ — para termos como
   "analista de logística", que quase nunca é remoto. Para "analista de
   logística" as 10 vagas eram idênticas às da busca normal.

   O estrago era exatamente o oposto do propósito: marcada como remota, a
   vaga PULA o filtro de cidade, que é a regra mais importante deste
   projeto. 113 vagas presenciais em Aracaju, Araquari e Jaboatão entraram
   como "remotas" na primeira semana — entre elas galpão da Amazon e chão de
   fábrica de ferramentaria.

   A passada foi removida: ela não filtrava nada, custava o dobro das
   requisições e só produzia rótulo errado. E o card NÃO EXPÕE modalidade em
   atributo nenhum (conferido no HTML cru), então esta fonte não declara
   modalidade — deixa vazia e o texto de local decide, como em qualquer
   fonte que não informa.

   O que se perde: vaga genuinamente remota do LinkedIn deixa de ser
   reconhecida como tal. Mas ela nunca chegou a ser reconhecida — era
   rotulada junto com as presenciais. Perder um sinal que nunca existiu não
   é perda.

3. O CARD VEM EM INGLÊS quando a idade é mostrada em texto ("4 months ago"),
   mesmo pra vaga brasileira — foi assim que `publicado_em` ficou vazio em
   1.061 de 1.061 vagas, e vaga de quatro meses virou alerta imediato sem
   aviso. Este scraper lê o ATRIBUTO `<time datetime="2026-08-21">`, que é
   absoluto e não depende de idioma. Confirmado ao vivo: presente em todos os
   cards testados.

AVISO QUE VALE MANTER POR ESCRITO: o endpoint não é documentado nem
suportado. Funciona sem login hoje, e o LinkedIn pode limitar ou bloquear
acesso automatizado a qualquer momento — principalmente de IP de datacenter,
que é o do GitHub Actions. Se um dia voltar vazio em todo termo, é bloqueio,
não ausência de vaga: a fonte sai do registro e o código fica.
"""

from radar import datas
from radar.fontes.base import Cadencia, Fonte, Resultado, get_html, montar

API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

# Brasil. NÃO trocar por texto — ver a armadilha 1 no cabeçalho.
GEO_ID_BRASIL = "106057199"

POR_PAGINA = 10
MAX_PAGINAS = 3


class LinkedIn(Fonte):
    nome = "LinkedIn"
    cadencia = Cadencia.TODO_CICLO

    # Um termo por vez, com respiro. MEDIDO: busca isolada responde 200, os
    # 15 termos do ciclo disparados juntos falharam TODOS. O endpoint está
    # certo; o que não passa é a rajada.
    max_simultaneas = 1
    pausa = 1.5

    async def _pagina(self, termo: str, inicio: int) -> list:
        sopa = await get_html(
            API, params={"keywords": termo, "geoId": GEO_ID_BRASIL, "start": inicio}
        )
        vagas = []

        for card in sopa.select("li"):
            titulo_el = card.select_one("h3")
            link_el = card.select_one("a[href]")
            if not titulo_el or not link_el:
                continue

            titulo = titulo_el.get_text(strip=True)
            # Card-modelo ainda não hidratado traz texto de espaço reservado
            # em vez de dado real. Gravar isso enche o banco de registro
            # fantasma que a deduplicação passa a considerar visto.
            if "nome da vaga" in titulo.lower():
                continue

            empresa_el = card.select_one("h4")
            local_el = card.select_one("span.job-search-card__location")
            tempo_el = card.select_one("time")

            vaga = montar(
                titulo=titulo,
                empresa=empresa_el.get_text(strip=True) if empresa_el else "",
                local=local_el.get_text(strip=True) if local_el else "",
                link=(link_el.get("href") or "").split("?")[0],
                fonte=self.nome,
                # NUNCA declarar modalidade aqui. O card não expõe essa
                # informação em lugar nenhum, e inventá-la faz a vaga pular
                # o filtro de cidade. Vazio deixa o texto de local decidir.
                modalidade="",
                publicado_em=datas.extrair_data(
                    tempo_el.get("datetime") if tempo_el else None,
                    card.get_text(" ", strip=True) if tempo_el is None else "",
                ),
                termo_busca=termo,
            )
            if vaga:
                vagas.append(vaga)

        return vagas

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        vagas = []
        vistos = set()

        for pagina in range(MAX_PAGINAS):
            encontradas = await self._pagina(termo, pagina * POR_PAGINA)

            novas = [v for v in encontradas if v.link not in vistos]
            # Página que só repetiu o que já veio é o fim da lista. O
            # endpoint devolve a última página de novo em vez de vazia,
            # então parar por "veio vazia" pediria uma requisição a mais em
            # todo termo — e foi confundir esse fim com falha que gerou
            # quinze alarmes falsos por ciclo no projeto original.
            if not novas:
                break

            vistos.update(v.link for v in novas)
            vagas.extend(novas)

        return Resultado(vagas=vagas)
