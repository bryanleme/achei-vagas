"""Agregadores de vaga 100% remota, todos por API pública sem cadastro.

RENDIMENTO ESPERADO: BAIXO, e isso está registrado de propósito.

São cinco fontes em inglês, majoritariamente de vaga tech para o mercado
americano e europeu. Para um perfil de PCP, logística e melhoria contínua em
português, a maioria absoluta do que elas devolvem não serve — o filtro de
cargo derruba quase tudo, e é pra isso que ele existe.

Elas entram assim mesmo por três motivos concretos:

  1. Custam uma requisição HTTP cada. Não abrem navegador, não levam
     bloqueio, não estendem o ciclo de forma perceptível. O critério pra
     manter uma fonte é rendimento POR CUSTO, e o custo aqui é quase zero.

  2. Cobrem o único mercado que nenhuma fonte brasileira alcança: vaga
     remota de empresa estrangeira que contrata no Brasil. Rara, mas é
     exatamente o tipo de vaga que não aparece de outro jeito.

  3. Trazem data de publicação de verdade, o que a maior parte das fontes
     brasileiras não faz.

CRITÉRIO DE PERMANÊNCIA, pra decidir por número e não por impressão: se
depois de um mês uma destas não tiver produzido uma única vaga aprovada,
ela sai. O relatório de precisão por fonte (deposito.precisao_por_fonte) é
o que responde isso.
"""

import re
from datetime import datetime, timezone

from radar.fontes.base import Cadencia, Fonte, Resultado, get_json, montar

_PADRAO_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Todas devolvem só vaga remota por definição do próprio site — daí
# modalidade fixa. Não é suposição sobre o texto, é o que a fonte é.
_REMOTO = "Remoto"


def _data(valor) -> str:
    """Data ISO curta (AAAA-MM-DD) a partir do que a fonte mandar.

    Cada uma destas APIs escolheu um formato diferente pro mesmo dado:
    string ISO com hora e fuso, ou inteiro de época Unix. Normalizar aqui,
    na borda, é o que impede o formato da fonte de vazar pro resto do
    projeto — o banco e o filtro de vaga antiga só conhecem ISO.
    """
    if not valor:
        return ""
    if isinstance(valor, (int, float)):
        try:
            return datetime.fromtimestamp(valor, tz=timezone.utc).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return ""
    texto = str(valor)[:10]
    # Só devolve o que de fato PARECE data. Sem esta guarda, um id numérico
    # ou um texto qualquer viraria "publicado_em" e o aviso de vaga antiga
    # passaria a mentir.
    return texto if _PADRAO_ISO.match(texto) else ""


class Remotive(Fonte):
    nome = "Remotive"
    cadencia = Cadencia.TODO_CICLO
    exige_mercado_declarado = True

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        dados = await get_json(
            "https://remotive.com/api/remote-jobs", params={"search": termo, "limit": 50}
        )
        vagas = [
            montar(
                titulo=i.get("title", ""),
                empresa=i.get("company_name", ""),
                # `candidate_required_location` é o campo que diz PRA QUEM a
                # vaga é remota ("Brazil", "USA Only", "Worldwide"). É
                # exatamente o que o escopo de mercado lê — sem ele, toda
                # vaga daqui pareceria remota sem restrição.
                local=i.get("candidate_required_location") or "Remoto",
                link=i.get("url", ""),
                fonte=self.nome,
                modalidade=_REMOTO,
                publicado_em=_data(i.get("publication_date")),
                descricao=i.get("description", ""),
                termo_busca=termo,
            )
            for i in (dados.get("jobs") or [])
        ]
        return Resultado(vagas=[v for v in vagas if v])


class RemoteOK(Fonte):
    nome = "RemoteOK"
    cadencia = Cadencia.TODO_CICLO
    exige_mercado_declarado = True

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        dados = await get_json("https://remoteok.com/api")
        vagas = []
        for i in dados:
            # O primeiro item da resposta é o aviso legal da API, não vaga.
            if not isinstance(i, dict) or "position" not in i:
                continue
            vaga = montar(
                titulo=i.get("position", ""),
                empresa=i.get("company", ""),
                local=i.get("location") or "Remoto",
                link=i.get("url", ""),
                fonte=self.nome,
                modalidade=_REMOTO,
                publicado_em=_data(i.get("date")),
                descricao=i.get("description", ""),
                termo_busca=termo,
            )
            if vaga:
                vagas.append(vaga)
        return Resultado(vagas=vagas)


class Arbeitnow(Fonte):
    nome = "Arbeitnow"
    cadencia = Cadencia.DIARIA  # base europeia, quase nada em português
    exige_mercado_declarado = True

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        dados = await get_json(
            "https://arbeitnow.com/api/job-board-api", params={"search": termo}
        )
        vagas = []
        for i in dados.get("data") or []:
            remoto = bool(i.get("remote"))
            vaga = montar(
                titulo=i.get("title", ""),
                empresa=i.get("company_name", ""),
                local="Remoto" if remoto else i.get("location", ""),
                link=i.get("url", ""),
                fonte=self.nome,
                modalidade=_REMOTO if remoto else "",
                descricao=i.get("description", ""),
                termo_busca=termo,
            )
            if vaga:
                vagas.append(vaga)
        return Resultado(vagas=vagas)


class Jobicy(Fonte):
    nome = "Jobicy"
    cadencia = Cadencia.TODO_CICLO
    exige_mercado_declarado = True

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        dados = await get_json(
            "https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "tag": termo}
        )
        vagas = [
            montar(
                titulo=i.get("jobTitle", ""),
                empresa=i.get("companyName", ""),
                # `jobGeo` é o escopo geográfico ("Brazil", "USA", "Anywhere").
                local=i.get("jobGeo") or "Remoto",
                link=i.get("url", ""),
                fonte=self.nome,
                modalidade=_REMOTO,
                publicado_em=_data(i.get("pubDate")),
                descricao=i.get("jobExcerpt", ""),
                termo_busca=termo,
            )
            for i in (dados.get("jobs") or [])
        ]
        return Resultado(vagas=[v for v in vagas if v])


class Himalayas(Fonte):
    nome = "Himalayas"
    cadencia = Cadencia.TODO_CICLO
    exige_mercado_declarado = True

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        dados = await get_json(
            "https://himalayas.app/jobs/api", params={"limit": 50, "search": termo}
        )
        vagas = []
        for i in dados.get("jobs") or []:
            # `locationRestrictions` é lista de países onde a vaga aceita
            # candidato. Lista vazia = sem restrição.
            restricoes = i.get("locationRestrictions") or []
            local = ", ".join(str(r) for r in restricoes) if restricoes else "Remoto"
            vaga = montar(
                titulo=i.get("title", ""),
                empresa=i.get("companyName", ""),
                local=local,
                link=i.get("applicationLink") or i.get("guid", ""),
                fonte=self.nome,
                modalidade=_REMOTO,
                publicado_em=_data(i.get("pubDate")),
                descricao=i.get("excerpt", ""),
                termo_busca=termo,
            )
            if vaga:
                vagas.append(vaga)
        return Resultado(vagas=vagas)


class WorkingNomads(Fonte):
    """Working Nomads — lista completa num GET, sem filtro por termo.

    O endpoint devolve o acervo inteiro e não aceita busca; o filtro de cargo
    faz o recorte depois. `location` traz o mercado ("Europe", "Worldwide",
    "Brazil"), que é o que o escopo lê.
    """

    nome = "Working Nomads"
    cadencia = Cadencia.DIARIA
    exige_mercado_declarado = True

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        dados = await get_json("https://www.workingnomads.com/api/exposed_jobs/")
        vagas = []
        for i in dados if isinstance(dados, list) else []:
            vaga = montar(
                titulo=i.get("title", ""),
                empresa=i.get("company_name", ""),
                local=i.get("location") or "Remoto",
                link=i.get("url", ""),
                fonte=self.nome,
                modalidade=_REMOTO,
                publicado_em=_data(i.get("pub_date")),
                descricao=i.get("description", ""),
                termo_busca=termo,
            )
            if vaga:
                vagas.append(vaga)
        return Resultado(vagas=vagas)


class TheMuse(Fonte):
    """The Muse — API pública com paginação, sem chave.

    Diferente das outras daqui, NÃO é board de vaga remota: publica vaga
    presencial americana em massa. Entra mesmo assim porque aceita filtro de
    localização e o acervo tem empresa que contrata no Brasil — mas com
    `exige_mercado_declarado`, senão as vagas de Seattle entrariam como
    "remoto sem escopo".
    """

    nome = "The Muse"
    cadencia = Cadencia.DIARIA
    exige_mercado_declarado = True
    max_simultaneas = 2
    pausa = 0.4

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        dados = await get_json(
            "https://www.themuse.com/api/public/jobs",
            params={"page": 1, "location": "Brazil"},
        )
        vagas = []
        for i in dados.get("results") or []:
            locais = [l.get("name", "") for l in (i.get("locations") or [])]
            vaga = montar(
                titulo=i.get("name", ""),
                empresa=(i.get("company") or {}).get("name", ""),
                local=", ".join(locais) or "Remoto",
                link=(i.get("refs") or {}).get("landing_page", ""),
                fonte=self.nome,
                publicado_em=_data(i.get("publication_date")),
                termo_busca=termo,
            )
            if vaga:
                vagas.append(vaga)
        return Resultado(vagas=vagas)


TODAS = [Remotive, RemoteOK, Arbeitnow, Jobicy, Himalayas, WorkingNomads, TheMuse]
