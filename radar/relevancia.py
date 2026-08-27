"""Quanto a vaga vale, de 0 a 10.

Isto NÃO é filtro. Só ordena o que já passou por `filtro.avaliar` — nenhuma
vaga é descartada por pontuar pouco, ela só cai no ranking e sai no digest
em vez de alertar na hora.

Soma de sinais conhecidos, sem aprendizado de máquina. São cinco sinais e o
peso de cada um é uma decisão de preferência, não um parâmetro a descobrir
— não há o que um modelo aprenderia aqui que já não esteja escrito abaixo.
Além disso: score explicável é auditável. Quando uma vaga chega com 8 dá
pra dizer exatamente por quê, e é isso que aparece na notificação.

O teto de 10 é exato por construção, não por corte: cargo 4 + domínio 1 +
senioridade 2 + local 3 = 10. O desconto de vaga antiga é o único valor
negativo, e o resultado é limitado a [0, 10] no fim.
"""

from dataclasses import dataclass

from radar.filtro import Avaliacao, Regras
from radar.modelos import Vaga
from radar.texto import contem_qualquer, normalizar

# --- cargo (0 a 4) ---------------------------------------------------------
PESO_CARGO_FORTE = 4
PESO_CARGO_AMBIGUO = 3
PESO_FERRAMENTA = 2

# --- segundo sinal de domínio (0 ou 1) -------------------------------------
# O título bateu um cargo forte E ainda menciona o domínio ("Analista de
# Logística de Frotas"). Dois sinais independentes valem mais que um.
PESO_DOMINIO_EXTRA = 1

# --- senioridade (-1 a 2) --------------------------------------------------
# Não especificado ganha 1 e não 0: não saber o nível não é defeito da vaga,
# é ausência de informação — e é a maioria dos títulos. Zerar aqui
# empataria "não sei" com "acima do alvo", que são coisas diferentes.
PESO_SENIORIDADE_ALVO = 2
PESO_SENIORIDADE_NAO_ESPECIFICADA = 1
PESO_SENIORIDADE_ACIMA = -1
PESO_SENIORIDADE_ESTAGIO = 0

# --- localização (1 a 3) ---------------------------------------------------
# Presencial numa cidade do corredor vale MAIS que remoto, e não menos.
# Parece contraintuitivo até olhar a oferta: vaga remota aparece às
# centenas e some no ruído; vaga presencial na região é rara e é a que de
# fato muda a vida de quem mora ali. O score serve pra destacar o escasso.
PESO_CIDADE_REGIAO = 3
# Cidade aceita, mas longe (São Paulo, Campinas, Cotia...). Vale MENOS que
# remoto confirmado de propósito: uma vaga remota pro Brasil você faz de
# casa; uma presencial na capital custa duas horas de estrada por dia.
#
# MEDIDO no mapeamento da região: São Paulo e Campinas sozinhas bateram o
# teto de 1.200 vagas da coleta, contra 556 de Sorocaba. Sem o desconto, o
# resumo seria dominado por elas pelo volume, e a vaga da região — que é
# rara e é a que interessa — ficaria enterrada no fim da lista.
PESO_CIDADE_DISTANTE = 1
PESO_REMOTO_MERCADO_CONFIRMADO = 2
PESO_REMOTO_SEM_ESCOPO = 1

# --- frescor (-2 a 0) ------------------------------------------------------
# Único negativo além de senioridade. Vaga velha continua sendo notificada,
# só nunca na frente de uma fresca.
PESO_VAGA_ANTIGA = -2

MAXIMO = 10


@dataclass(frozen=True)
class Pontuacao:
    total: int
    motivos: list[str]

    def __str__(self) -> str:
        return ", ".join(self.motivos)


def pontuar(
    vaga: Vaga,
    regras: Regras,
    avaliacao: Avaliacao,
    niveis_alvo: set[str],
    niveis_acima: set[str],
    dias_vaga_antiga: int,
    cidades_distantes: set[str] | None = None,
) -> Pontuacao:
    """Pontua uma vaga JÁ APROVADA, reaproveitando a avaliação do filtro.

    Recebe a `Avaliacao` pronta em vez de recalcular: o filtro já decidiu
    se bateu cargo forte, se é remota e qual cidade bateu, e refazer essa
    conta aqui seria uma segunda implementação da mesma regra, livre pra
    divergir da primeira sem ninguém notar.
    """
    cidades_distantes = cidades_distantes or set()
    total = 0
    motivos: list[str] = []

    if avaliacao.bate_cargo_forte:
        total += PESO_CARGO_FORTE
        motivos.append("cargo direto")
    elif avaliacao.bate_cargo_ambiguo:
        total += PESO_CARGO_AMBIGUO
        motivos.append("cargo + domínio")
    elif avaliacao.bate_ferramenta:
        total += PESO_FERRAMENTA
        motivos.append("ferramenta + cargo")

    # Segundo sinal só conta pra quem entrou por cargo forte. Quem entrou
    # por cargo ambíguo já usou o qualificador de domínio pra ser aprovado —
    # contar de novo aqui seria pagar duas vezes pelo mesmo sinal.
    if avaliacao.bate_cargo_forte and contem_qualquer(
        regras.qualificadores_dominio, normalizar(vaga.titulo), aceitar_plural=True
    ):
        total += PESO_DOMINIO_EXTRA
        motivos.append("domínio no título")

    nivel = vaga.senioridade
    if nivel in niveis_alvo:
        total += PESO_SENIORIDADE_ALVO
        motivos.append(f"nível {nivel.lower()}")
    elif nivel in niveis_acima:
        total += PESO_SENIORIDADE_ACIMA
        motivos.append(f"acima do alvo ({nivel.lower()})")
    elif nivel == "Estágio":
        total += PESO_SENIORIDADE_ESTAGIO
    else:
        total += PESO_SENIORIDADE_NAO_ESPECIFICADA

    if avaliacao.cidade:
        if avaliacao.cidade in cidades_distantes:
            total += PESO_CIDADE_DISTANTE
            motivos.append(f"{avaliacao.cidade} (longe)")
        else:
            total += PESO_CIDADE_REGIAO
            motivos.append(avaliacao.cidade)
    elif avaliacao.mercado_confirmado:
        total += PESO_REMOTO_MERCADO_CONFIRMADO
        motivos.append("remoto Brasil")
    elif avaliacao.remota:
        total += PESO_REMOTO_SEM_ESCOPO
        motivos.append("remoto")

    if vaga.antiga(dias_vaga_antiga):
        total += PESO_VAGA_ANTIGA
        motivos.append("publicação antiga")

    return Pontuacao(total=max(0, min(MAXIMO, total)), motivos=motivos)
