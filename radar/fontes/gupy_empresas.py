"""Varredura por EMPRESA, e não por termo de busca.

O PROBLEMA QUE ISTO RESOLVE

A busca por termo tem um limite estrutural: o rodízio roda 15 termos por
ciclo, então uma vaga só é encontrada quando o termo certo chega a vez. Com
43 termos isso é uma volta a cada ~15 horas — e a vaga precisa que a fonte
case o texto do jeito que a gente escreveu.

MEDIDO em produção: "Analista de Suprimentos (Foco em Defesa) - Pleno" só
apareceu quando "analista de suprimentos" entrou no bloco do ciclo. O título
bate o perfil sem esforço; o que faltava era a busca ter perguntado por ele.

Varrer por EMPRESA elimina essa dependência. Uma empresa do corredor tem
todas as suas vagas listadas de uma vez, com uma requisição, independente de
qual termo está na vez. Se a Yazaki de Tatuí abrir qualquer coisa amanhã, o
próximo ciclo vê — sem esperar o rodízio.

COMO A LISTA DE EMPRESAS APARECE

Ninguém digita lista de empresa à mão aqui. Toda vaga da Gupy carrega
`careerPageName` e a cidade, então o ciclo registra sozinho toda empresa que
publicou vaga NUMA CIDADE DO CORREDOR — inclusive quando a vaga em si foi
reprovada no filtro de cargo.

Registrar pela CIDADE e não pelo cargo é a decisão que faz isto valer a pena:
uma metalúrgica de Tatuí que hoje publicou "Soldador" é exatamente a empresa
que mês que vem publica "Analista de PCP". Filtrar a descoberta por cargo
descartaria justamente as empresas certas, por causa da vaga errada.

O sistema fica mais denso quanto mais roda, sem intervenção.
"""

from radar.fontes.base import Cadencia, Fonte, Resultado
from radar.fontes.gupy import API, CABECALHOS, _converter
from radar.fontes.base import get_json

# Uma requisição por empresa. Cadência diária porque a lista cresce sem
# limite natural — 80 empresas por ciclo de 3h seriam 640 requisições/dia
# atrás de vaga que muda devagar. Abertura de vaga não é evento de minuto.
POR_EMPRESA = 100


class GupyPorEmpresa(Fonte):
    """Varre as empresas que já publicaram vaga no corredor.

    Não recebe termo de busca: `buscar` ignora o termo de propósito, e o
    ciclo chama esta fonte UMA vez por execução em vez de uma por termo —
    ver o tratamento de `ignora_termos` em ciclo.py. Repetir a mesma
    varredura 15 vezes por ciclo seria 15x o custo pelo mesmo resultado.
    """

    nome = "Gupy (empresas do corredor)"
    cadencia = Cadencia.DIARIA
    ignora_termos = True

    # Teto de empresas por execução. A lista cresce sozinha e sem teto viraria
    # centenas de requisições no dia em que o corredor tiver sido bem
    # mapeado. As mais recentes primeiro — empresa que publicou vaga na
    # região esta semana é mais provável de publicar de novo que uma que
    # apareceu uma vez há seis meses.
    MAX_EMPRESAS = 60

    def __init__(self, empresas: list[str] | None = None):
        self._empresas = empresas

    def _lista(self) -> list[str]:
        if self._empresas is not None:
            return self._empresas
        # Import tardio: `deposito` importa `modelos`, que importa daqui em
        # cadeia. Resolver no momento da chamada evita import circular.
        from radar import deposito

        return deposito.empresas_do_corredor("Gupy", limite=self.MAX_EMPRESAS)

    async def buscar(self, termo: str = "", navegador=None) -> Resultado:
        vagas = []
        vistos = set()

        for empresa in self._lista():
            dados = await get_json(
                API,
                params={"careerPageName": empresa, "limit": POR_EMPRESA, "offset": 0},
                headers=CABECALHOS,
            )
            for item in dados.get("data") or []:
                vaga = _converter(item, termo_busca=f"empresa:{empresa}")
                if vaga and vaga.link not in vistos:
                    vistos.add(vaga.link)
                    vagas.append(vaga)

        return Resultado(vagas=vagas)
