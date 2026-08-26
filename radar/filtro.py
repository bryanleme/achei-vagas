"""A vaga passa ou não passa.

Uma função de verdade — `avaliar` — e um objeto de resultado. Filtro e
score leem o MESMO resultado em vez de cada um refazer a conta por conta
própria: dois métodos calculando a mesma coisa de jeitos diferentes é
exatamente o padrão que já produziu bug real em projeto desta família (a
conta de "é remota?" existia em dois lugares e divergiu em silêncio).

O filtro lê SÓ O TÍTULO pra decidir cargo, e SÓ O LOCAL pra decidir
localização. Nunca os dois juntos. O bug fundador desta base era
concatenar: vaga americana com "Hybrid Remote" no TÍTULO batia "remot" e
passava como remota, com local="Bloomington, IN".
"""

from dataclasses import dataclass, field

from radar.localizacao import bate_cidade
from radar.modelos import Vaga
from radar.texto import contem_cargo, contem_qualquer, normalizar


@dataclass(frozen=True)
class Regras:
    """As regras que decidem se uma vaga bate.

    Objeto com campo nomeado, e não seis listas posicionais. A diferença é
    concreta: todos os parâmetros aqui são `list[str]`, então trocar a
    ordem de dois deles não gera erro nenhum em Python — só passa a filtrar
    errado, em silêncio, e ninguém descobre. Com campo nomeado a ordem da
    chamada deixa de importar, e esquecer um campo obrigatório vira erro na
    hora.
    """

    cargos_fortes: list[str]
    cargos_ambiguos: list[str]
    qualificadores_dominio: list[str]
    ferramentas_titulo: list[str]
    qualificadores_cargo: list[str]
    cidades: list[str]
    uf_esperada: str
    # Termos que DERRUBAM a vaga, por mais que o cargo e a cidade batam.
    # Verificado antes de tudo — ver config.EXCLUSOES_TITULO.
    exclusoes_titulo: list[str] = field(default_factory=list)
    aceita_remoto: bool = True
    # Mercados aceitos quando a vaga remota DECLARA um escopo geográfico.
    # None desliga a checagem (aceita qualquer remoto). Lista vazia é
    # diferente de None: significa "só aceito remoto sem escopo declarado".
    mercados_remoto_aceitos: list[str] | None = None


@dataclass(frozen=True)
class Avaliacao:
    aprovada: bool
    bate_cargo_forte: bool
    bate_cargo_ambiguo: bool
    bate_ferramenta: bool
    remota: bool
    cidade: str | None
    escopo: set[str] = field(default_factory=set)
    mercado_confirmado: bool = False
    # Derrubada por EXCLUSAO no título, antes de qualquer outra regra. É
    # diferente de "não aprovada": a vaga nem chegou a ser avaliada por
    # cargo ou por cidade, e não pode contar nos diagnósticos desses dois.
    excluida: bool = False
    # Preenchido só quando a vaga foi barrada por escopo — serve pro log de
    # diagnóstico dizer QUAL mercado derrubou, e quantas vezes.
    escopo_rejeitado: set[str] = field(default_factory=set)

    @property
    def bate_cargo(self) -> bool:
        return self.bate_cargo_forte or self.bate_cargo_ambiguo or self.bate_ferramenta


def avaliar(vaga: Vaga, regras: Regras) -> Avaliacao:
    titulo = vaga.titulo
    titulo_normalizado = normalizar(titulo)

    # EXCLUSÃO vem primeiro, e devolve cedo. Não é otimização: uma vaga
    # excluída não deve aparecer em contador nenhum de diagnóstico como se
    # tivesse sido barrada por cargo ou por cidade — ela não chegou a ser
    # avaliada por esses critérios.
    if contem_qualquer(regras.exclusoes_titulo, titulo_normalizado):
        return Avaliacao(
            aprovada=False,
            bate_cargo_forte=False,
            bate_cargo_ambiguo=False,
            bate_ferramenta=False,
            remota=False,
            cidade=None,
            excluida=True,
        )

    # NÍVEL 1 — cargo forte: o título já identifica a área sozinho.
    bate_forte = any(contem_cargo(c, titulo) for c in regras.cargos_fortes)

    # NÍVEL 2 — cargo ambíguo só vale com qualificador de domínio junto.
    # É o que permite ter "Analista Administrativo" na lista sem que toda
    # vaga administrativa do Brasil entre no radar.
    titulo_norm = titulo_normalizado
    bate_ambiguo = any(
        contem_cargo(c, titulo) for c in regras.cargos_ambiguos
    ) and contem_qualquer(regras.qualificadores_dominio, titulo_norm, aceitar_plural=True)

    # NÍVEL 3 — espelho do nível 2: ferramenta no título só vale com palavra
    # de cargo junto. Sem isso "Power BI" sozinho aprovaria "Desenvolvedor
    # (Power BI + Python)", que é vaga de desenvolvimento.
    bate_ferramenta = contem_qualquer(
        regras.ferramentas_titulo, titulo_norm
    ) and contem_qualquer(regras.qualificadores_cargo, titulo_norm, aceitar_plural=True)

    bate_cargo = bate_forte or bate_ambiguo or bate_ferramenta

    # ---- localização -----------------------------------------------------
    remota = regras.aceita_remoto and vaga.remota
    escopo = vaga.escopo if remota else set()
    escopo_rejeitado: set[str] = set()
    mercado_confirmado = False

    if remota and regras.mercados_remoto_aceitos is not None and escopo:
        aceitos = {normalizar(m) for m in regras.mercados_remoto_aceitos}
        declarados = {normalizar(e) for e in escopo}
        # Interseção, não igualdade: "Remote - Brazil/LATAM" declara DOIS
        # mercados, e aprovar exige que QUALQUER um deles seja aceito.
        # Comparar um valor só descartava a vaga toda vez que o sobrevivente
        # não fosse o aceito, mesmo com o outro sendo.
        if declarados & aceitos:
            mercado_confirmado = True
        else:
            escopo_rejeitado = set(escopo)
            remota = False

    cidade = bate_cidade(vaga.local, regras.cidades, regras.uf_esperada)
    bate_local = remota or cidade is not None

    return Avaliacao(
        aprovada=bate_cargo and bate_local,
        bate_cargo_forte=bate_forte,
        bate_cargo_ambiguo=bate_ambiguo,
        bate_ferramenta=bate_ferramenta,
        remota=remota,
        cidade=cidade,
        escopo=escopo,
        mercado_confirmado=mercado_confirmado,
        escopo_rejeitado=escopo_rejeitado,
    )


def combina(vaga: Vaga, regras: Regras) -> bool:
    return avaliar(vaga, regras).aprovada


def barrada_so_pelo_cargo(vaga: Vaga, regras: Regras) -> bool:
    """A vaga está num local aceito e foi barrada SÓ pelo título?

    Não decide nada — existe pro ciclo conseguir MEDIR o preço da decisão
    de ler só o título. Se esse número for alto e as vagas forem boas, vale
    reconsiderar abrir a descrição; se for ruído, a decisão está certa.
    Sem contar, isso vira discussão de opinião.
    """
    a = avaliar(vaga, regras)
    return not a.bate_cargo and (a.remota or a.cidade is not None)
