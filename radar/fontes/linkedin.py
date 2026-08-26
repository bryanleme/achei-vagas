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

2. O CARD VEM EM INGLÊS quando a idade é mostrada em texto ("4 months ago"),
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

# Filtro nativo de vaga remota do LinkedIn. Existe uma passada só pra isso
# porque o card de busca quase nunca escreve "Remoto" no campo de local —
# mesmo vaga 100% remota aparece com a cidade onde a empresa está
# registrada. Sem esta passada, vaga remota só apareceria por acidente.
FILTRO_REMOTO = "2"


class LinkedIn(Fonte):
    nome = "LinkedIn"
    cadencia = Cadencia.TODO_CICLO

    # Um termo por vez, com respiro. MEDIDO: busca isolada responde 200, os
    # 15 termos do ciclo disparados juntos falharam TODOS. O endpoint está
    # certo; o que não passa é a rajada.
    max_simultaneas = 1
    pausa = 1.5

    async def _pagina(self, termo: str, inicio: int, remoto: bool) -> list:
        params = {"keywords": termo, "geoId": GEO_ID_BRASIL, "start": inicio}
        if remoto:
            params["f_WT"] = FILTRO_REMOTO

        sopa = await get_html(API, params=params)
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
                # A passada com f_WT=2 já garante que é remota — o próprio
                # LinkedIn filtrou. Declarar aqui é mais confiável que
                # esperar a palavra aparecer num campo de local que
                # tipicamente traz a cidade da sede.
                modalidade="Remoto" if remoto else "",
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

        for remoto in (False, True):
            # A passada remota vai menos fundo: o volume de vaga remota por
            # termo é bem menor que o nacional, e ir a três páginas ali é
            # pagar requisição por página que costuma voltar repetida.
            paginas = MAX_PAGINAS if not remoto else 2

            for pagina in range(paginas):
                encontradas = await self._pagina(termo, pagina * POR_PAGINA, remoto)

                novas = [v for v in encontradas if v.link not in vistos]
                # Página que só repetiu o que já veio é o fim da lista. O
                # endpoint devolve a última página de novo em vez de vazia,
                # então parar por "veio vazia" pediria uma requisição a mais
                # em todo termo — e foi confundir esse fim com falha que
                # gerou quinze alarmes falsos por ciclo no projeto original.
                if not novas:
                    break

                vistos.update(v.link for v in novas)
                vagas.extend(novas)

        return Resultado(vagas=vagas)
