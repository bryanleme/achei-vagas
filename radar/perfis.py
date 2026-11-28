"""Quem o radar atende, e o que cada pessoa procura.

UM CATÁLOGO, VÁRIOS PERFIS. A coleta é compartilhada: cada fonte é
consultada uma vez com a união dos termos de todos os perfis, e cada vaga
coletada é avaliada contra CADA perfil, separadamente. A mesma vaga pode
servir a duas pessoas, e o 👎 de uma não mexe no resultado da outra.

DE ONDE VÊM OS PERFIS

  - O perfil "principal" continua em `config.py`. Não foi movido de
    propósito: aquele arquivo carrega, em comentário, o porquê de cada cargo
    e de cada exclusão — histórico que um arquivo de dados perderia.
  - Perfis convidados ficam em `perfis/<id>.toml`, um arquivo por pessoa.
    O nome do arquivo é o id. Ver perfis/README.md.

Um perfil convidado só passa a receber alguma coisa depois de a pessoa abrir
o link de convite no Telegram (ver alertas/telegram.py). Até lá ele é
carregado, validado e ignorado pelo ciclo.

VALIDAÇÃO ESTRITA. Chave desconhecida no TOML é erro, não aviso: "cidade"
no lugar de "cidades" seria lido como lista vazia e o perfil passaria a
aprovar só remoto, em silêncio. Melhor o ciclo recusar o arquivo e dizer
por quê.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from radar import caminhos, config
from radar.filtro import Regras
from radar.localizacao import SIGLAS_UF

PERFIL_PRINCIPAL = "principal"

# Nome do estado como a Gupy espera no parâmetro `state` — COM acento.
# MEDIDO: state=Paraná devolve 87 vagas de "atendimento"; state=Parana, zero.
# Sem erro, sem aviso: só lista vazia.
ESTADO_POR_UF = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas",
    "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
    "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão",
    "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco",
    "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima",
    "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe",
    "TO": "Tocantins",
}

NIVEIS_VALIDOS = {"Júnior", "Pleno", "Sênior", "Especialista", "Liderança", "Estágio"}
MODALIDADES_VALIDAS = {"presencial", "hibrido", "remoto"}

# Teto de termos por perfil convidado. Cada termo vira uma consulta em cada
# fonte por termo (~12), em todo ciclo em que entra no bloco. Sem teto, um
# perfil com 80 sinônimos dobraria o custo do ciclo de todo mundo.
MAX_TERMOS_CONVIDADO = 40

# Prioritários por perfil convidado: rodam em todo ciclo, fora do rodízio.
MAX_PRIORITARIOS_CONVIDADO = 5

_ID_VALIDO = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


class PerfilInvalido(ValueError):
    """O arquivo do perfil tem erro. A mensagem diz qual arquivo e o quê."""


@dataclass(frozen=True)
class Perfil:
    id: str
    nome: str
    regras: Regras
    termos: list[str]
    prioritarios: list[str]
    uf: str
    niveis_alvo: frozenset[str]
    niveis_acima: frozenset[str]
    cidades_distantes: frozenset[str] = frozenset()
    alerta_imediato: bool = True
    limiar_imediato: int = 7
    # Hora LOCAL a partir da qual o resumo do dia pode sair, e o fuso dessa
    # hora em relação ao UTC. Offset fixo e não nome de fuso: o Brasil não
    # tem horário de verão desde 2019, e zoneinfo no Windows exige o pacote
    # tzdata — dependência a mais pra resolver um problema que não existe.
    hora_resumo: int = 8
    fuso_utc: int = -3
    max_resumo: int = 20
    dias_vaga_antiga: int = 30
    # Recebe batimento e alerta de saúde. Convidado recebe só vaga: falha de
    # fonte é problema de quem opera o radar, não de quem o usa.
    admin: bool = False
    # Destino fixo vindo de variável de ambiente (perfil principal). Perfil
    # convidado deixa vazio e é vinculado pelo convite.
    chat_id_env: str = ""
    convite: str = ""
    ativo: bool = True

    @property
    def estado(self) -> str:
        return ESTADO_POR_UF[self.uf]


# ---------------------------------------------------------------------------
# Perfil principal (config.py)
# ---------------------------------------------------------------------------

def perfil_principal() -> Perfil:
    return Perfil(
        id=PERFIL_PRINCIPAL,
        nome="Principal",
        regras=Regras(
            cargos_fortes=config.CARGOS_FORTES,
            cargos_ambiguos=config.CARGOS_AMBIGUOS,
            qualificadores_dominio=config.QUALIFICADORES_DOMINIO,
            ferramentas_de_cargo=config.FERRAMENTAS_DE_CARGO,
            palavras_de_cargo=config.PALAVRAS_DE_CARGO,
            cidades=config.CIDADES,
            uf_esperada=config.UF_BUSCA,
            aceita_remoto=config.ACEITA_REMOTO,
            paises_remoto_permitidos=config.PAISES_REMOTO_PERMITIDOS,
            exclusoes_titulo=config.EXCLUSOES_TITULO,
        ),
        termos=list(config.TERMOS_DE_BUSCA),
        prioritarios=list(config.TERMOS_PRIORITARIOS),
        uf=config.UF_BUSCA.upper(),
        niveis_alvo=frozenset(config.NIVEIS_ALVO),
        niveis_acima=frozenset(config.NIVEIS_ACIMA_DO_ALVO),
        cidades_distantes=frozenset(config.CIDADES_DISTANTES),
        limiar_imediato=config.LIMIAR_ALERTA_IMEDIATO,
        # Mesmo instante de antes: 9h UTC = 6h em Brasília.
        hora_resumo=(config.HORA_RESUMO_UTC + config.FUSO_RESUMO_UTC) % 24,
        fuso_utc=config.FUSO_RESUMO_UTC,
        max_resumo=40,
        dias_vaga_antiga=config.DIAS_PARA_VAGA_ANTIGA,
        admin=True,
        chat_id_env="TELEGRAM_CHAT_ID",
    )


# ---------------------------------------------------------------------------
# Perfis convidados (perfis/*.toml)
# ---------------------------------------------------------------------------

_SECOES = {
    "nome": str, "ativo": bool, "convite": str,
    "cargo": dict, "local": dict, "nivel": dict, "alertas": dict,
}
_CHAVES = {
    "cargo": {"fortes", "ambiguos", "qualificadores", "ferramentas",
              "palavras_de_cargo", "exclusoes", "termos_extra", "prioritarios"},
    "local": {"uf", "cidades", "cidades_distantes", "modalidades", "paises_remoto"},
    "nivel": {"alvo", "acima"},
    "alertas": {"imediato", "limiar_imediato", "hora_resumo", "fuso_utc",
                "max_resumo", "dias_vaga_antiga"},
}


def _lista(secao: dict, chave: str, arquivo: str, padrao=None) -> list[str]:
    valor = secao.get(chave, padrao if padrao is not None else [])
    if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
        raise PerfilInvalido(f"{arquivo}: '{chave}' precisa ser lista de textos")
    # Sem item vazio: "" casaria com qualquer título.
    limpos = [v.strip() for v in valor if v.strip()]
    return limpos


def _inteiro(secao: dict, chave: str, arquivo: str, padrao: int, minimo: int, maximo: int) -> int:
    valor = secao.get(chave, padrao)
    if not isinstance(valor, int) or isinstance(valor, bool) or not minimo <= valor <= maximo:
        raise PerfilInvalido(f"{arquivo}: '{chave}' precisa ser inteiro entre {minimo} e {maximo}")
    return valor


def de_toml(caminho: Path) -> Perfil:
    arquivo = caminho.name
    pid = caminho.stem
    if not _ID_VALIDO.match(pid) or pid == PERFIL_PRINCIPAL:
        raise PerfilInvalido(
            f"{arquivo}: o nome do arquivo é o id do perfil — use só letras "
            f"minúsculas, números e hífen (2 a 32), e não '{PERFIL_PRINCIPAL}'"
        )

    try:
        dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise PerfilInvalido(f"{arquivo}: TOML inválido ({e})") from e

    for chave, valor in dados.items():
        if chave not in _SECOES:
            raise PerfilInvalido(f"{arquivo}: chave desconhecida '{chave}'")
        if not isinstance(valor, _SECOES[chave]):
            raise PerfilInvalido(f"{arquivo}: '{chave}' tem o tipo errado")
    for secao, permitidas in _CHAVES.items():
        sobrando = set(dados.get(secao, {})) - permitidas
        if sobrando:
            raise PerfilInvalido(
                f"{arquivo}: chave(s) desconhecida(s) em [{secao}]: {', '.join(sorted(sobrando))}"
            )

    cargo = dados.get("cargo", {})
    local = dados.get("local", {})
    nivel = dados.get("nivel", {})
    alertas = dados.get("alertas", {})

    fortes = _lista(cargo, "fortes", arquivo)
    ambiguos = _lista(cargo, "ambiguos", arquivo)
    qualificadores = _lista(cargo, "qualificadores", arquivo)
    ferramentas = _lista(cargo, "ferramentas", arquivo)
    if not fortes and not (ambiguos and qualificadores):
        raise PerfilInvalido(
            f"{arquivo}: sem [cargo].fortes (nem ambiguos + qualificadores) o perfil "
            "não aprovaria vaga nenhuma"
        )

    uf = str(local.get("uf", "")).strip().upper()
    if uf.lower() not in SIGLAS_UF:
        raise PerfilInvalido(f"{arquivo}: [local].uf precisa ser sigla de estado (ex.: \"PR\")")

    modalidades = {m.lower() for m in _lista(local, "modalidades", arquivo,
                                             padrao=sorted(MODALIDADES_VALIDAS))}
    invalidas = modalidades - MODALIDADES_VALIDAS
    if not modalidades or invalidas:
        raise PerfilInvalido(
            f"{arquivo}: [local].modalidades aceita só presencial, hibrido e remoto"
        )

    cidades = _lista(local, "cidades", arquivo)
    distantes = _lista(local, "cidades_distantes", arquivo)
    if modalidades & {"presencial", "hibrido"} and not cidades:
        raise PerfilInvalido(
            f"{arquivo}: presencial/híbrido exige pelo menos uma cidade em [local].cidades"
        )

    alvo = set(_lista(nivel, "alvo", arquivo, padrao=["Júnior", "Pleno"]))
    acima = set(_lista(nivel, "acima", arquivo, padrao=["Sênior", "Especialista", "Liderança"]))
    if (alvo | acima) - NIVEIS_VALIDOS:
        raise PerfilInvalido(
            f"{arquivo}: níveis válidos são {', '.join(sorted(NIVEIS_VALIDOS))}"
        )

    termos = sorted(
        {c.lower() for c in fortes + ambiguos}
        | {t.lower() for t in _lista(cargo, "termos_extra", arquivo)}
    )
    if len(termos) > MAX_TERMOS_CONVIDADO:
        raise PerfilInvalido(
            f"{arquivo}: {len(termos)} termos de busca — o teto é {MAX_TERMOS_CONVIDADO}. "
            "Cada termo é uma consulta em cada fonte; enxugue os sinônimos."
        )

    prioritarios = [t.lower() for t in _lista(cargo, "prioritarios", arquivo)]
    if not prioritarios:
        # Sem prioritário declarado, os primeiros cargos fortes fazem o papel.
        # Sem isso o termo que dá nome ao perfil entraria no rodízio geral e
        # podia esperar meio dia pela vez — ver ciclo.termos_desta_rodada.
        prioritarios = [c.lower() for c in fortes[:MAX_PRIORITARIOS_CONVIDADO]]
    if len(prioritarios) > MAX_PRIORITARIOS_CONVIDADO:
        raise PerfilInvalido(
            f"{arquivo}: no máximo {MAX_PRIORITARIOS_CONVIDADO} termos prioritários"
        )
    fora = [p for p in prioritarios if p not in termos]
    if fora:
        raise PerfilInvalido(
            f"{arquivo}: prioritário que não é cargo nem termo extra: {', '.join(fora)}"
        )

    convite = str(dados.get("convite", "")).strip()
    if convite.upper().startswith("TROQUE"):
        # O código do modelo é público (está no repositório). Quem copiou o
        # modelo e esqueceu de trocar entregaria o perfil a qualquer um.
        raise PerfilInvalido(
            f"{arquivo}: o convite ainda é o do modelo — gere um com: python main.py --novo-convite"
        )
    if convite and not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", convite):
        # Limite do Telegram pro parâmetro de /start: 64 caracteres, só
        # letras, números, _ e -. Mínimo de 16 pra não ser adivinhável.
        raise PerfilInvalido(
            f"{arquivo}: convite precisa ter 16 a 64 caracteres (letras, números, _ ou -). "
            "Gere um com: python main.py --novo-convite"
        )

    return Perfil(
        id=pid,
        nome=str(dados.get("nome", pid)).strip() or pid,
        regras=Regras(
            cargos_fortes=fortes,
            cargos_ambiguos=ambiguos,
            qualificadores_dominio=qualificadores,
            ferramentas_de_cargo=ferramentas,
            palavras_de_cargo=_lista(cargo, "palavras_de_cargo", arquivo,
                                     padrao=list(config.PALAVRAS_DE_CARGO)),
            cidades=cidades + distantes,
            uf_esperada=uf,
            # Nenhuma exclusão herdada do perfil principal: a lista de lá
            # (vagas PCD) é escolha de uma pessoa, não padrão de todo mundo.
            exclusoes_titulo=_lista(cargo, "exclusoes", arquivo),
            aceita_remoto="remoto" in modalidades,
            aceita_presencial="presencial" in modalidades,
            aceita_hibrido="hibrido" in modalidades,
            paises_remoto_permitidos=_lista(local, "paises_remoto", arquivo, padrao=["Brasil"]),
        ),
        termos=termos,
        prioritarios=prioritarios,
        uf=uf,
        niveis_alvo=frozenset(alvo),
        niveis_acima=frozenset(acima),
        cidades_distantes=frozenset(distantes),
        alerta_imediato=bool(alertas.get("imediato", True)),
        limiar_imediato=_inteiro(alertas, "limiar_imediato", arquivo, 7, 1, 10),
        hora_resumo=_inteiro(alertas, "hora_resumo", arquivo, 8, 0, 23),
        fuso_utc=_inteiro(alertas, "fuso_utc", arquivo, -3, -12, 14),
        max_resumo=_inteiro(alertas, "max_resumo", arquivo, 20, 1, 40),
        dias_vaga_antiga=_inteiro(alertas, "dias_vaga_antiga", arquivo, 30, 1, 365),
        convite=convite,
        ativo=bool(dados.get("ativo", True)),
    )


def convidados(pasta: Path | None = None) -> list[Perfil]:
    """Todos os perfis de perfis/*.toml. Erro em UM arquivo derruba a carga
    inteira — perfil quebrado em silêncio é pessoa que para de receber vaga
    sem ninguém saber."""
    pasta = pasta or caminhos.PASTA_PERFIS
    if not pasta.exists():
        return []
    perfis = [de_toml(p) for p in sorted(pasta.glob("*.toml"))]
    convites = [p.convite for p in perfis if p.convite]
    if len(convites) != len(set(convites)):
        raise PerfilInvalido("dois perfis usam o mesmo convite")
    return perfis


def todos(pasta: Path | None = None) -> list[Perfil]:
    return [perfil_principal(), *convidados(pasta)]


def termos_da_coleta(perfis: list[Perfil]) -> tuple[list[str], list[str]]:
    """(prioritários, demais) da UNIÃO dos perfis, sem repetição.

    Um termo de dois perfis é buscado uma vez só — a vaga que ele traz é
    avaliada contra os dois. É a parte "coleta compartilhada" do desenho.
    """
    prioritarios = sorted({t for p in perfis for t in p.prioritarios})
    demais = sorted({t for p in perfis for t in p.termos} - set(prioritarios))
    return prioritarios, demais
