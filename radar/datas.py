"""Quando a vaga foi publicada.

Por que isso tem módulo próprio: uma extração que procure só padrão em
PORTUGUÊS ("há 4 meses", "publicada em 11/08") parece funcionar, mas o
LinkedIn — uma das maiores fontes do banco — devolve o card em INGLÊS
("4 months ago"). Nunca casaria, `publicado_em` ficaria vazio em todas as
vagas dele, e sem data `vaga_antiga` sempre responderia False: vaga de
quatro meses iria como alerta imediato, igual a uma publicada hoje.

Medido na página 1 de um termo qualquer: 4 dos 6 primeiros resultados
tinham mais de um mês.

Duas decisões de desenho vieram daí:

  1. ATRIBUTO ANTES DE TEXTO. `<time datetime="2026-03-26">` é absoluto e
     não depende de idioma. Houve card cujo texto não trazia idade nenhuma,
     só a tag — pra esse, padrão de texto nenhum resolveria, em idioma
     nenhum.

  2. Quando cair no texto, entender os DOIS idiomas. Não é caso exótico: é
     o comportamento normal da maior fonte do projeto.

Tudo aqui devolve data em ISO (AAAA-MM-DD) ou "". Formatação pra leitura
humana é problema da camada de alerta, não desta.
"""

import re
from datetime import date, datetime, timedelta

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

# "11/08/2026", "11/08/26" e "11-08-2026" — dia primeiro, como se escreve
# em português.
_ABSOLUTA_BR = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")

# "há 4 meses", "ha 2 dias", "publicada há 3 semanas", "4 months ago",
# "2 weeks ago", "30+ days ago". O número e a unidade são o que importa;
# o resto da frase muda de fonte pra fonte e não vale tentar prever.
_RELATIVA = re.compile(
    r"(\d+)\s*\+?\s*"
    r"(minuto|minutos|minute|minutes|"
    r"hora|horas|hour|hours|"
    r"dia|dias|day|days|"
    r"semana|semanas|week|weeks|"
    r"mes|mês|meses|month|months|"
    r"ano|anos|year|years)\b",
    re.IGNORECASE,
)

_HOJE = re.compile(r"\b(hoje|today|agora|just now)\b", re.IGNORECASE)
_ONTEM = re.compile(r"\b(ontem|yesterday)\b", re.IGNORECASE)

_DIAS_POR_UNIDADE = {
    "minuto": 0, "minutos": 0, "minute": 0, "minutes": 0,
    "hora": 0, "horas": 0, "hour": 0, "hours": 0,
    "dia": 1, "dias": 1, "day": 1, "days": 1,
    "semana": 7, "semanas": 7, "week": 7, "weeks": 7,
    "mes": 30, "mês": 30, "meses": 30, "month": 30, "months": 30,
    "ano": 365, "anos": 365, "year": 365, "years": 365,
}


def _normalizar_unidade(unidade: str) -> str:
    return unidade.lower().replace("ê", "e")


def extrair_data(atributo_datetime: str | None, texto: str, hoje: date | None = None) -> str:
    """Data de publicação em ISO, ou "" quando a fonte não informa.

    `atributo_datetime` vem do `<time datetime="...">` do card, quando a
    fonte tem um. `texto` é o texto visível do card. `hoje` existe pro
    teste conseguir fixar a referência — em produção fica None.

    O atributo VENCE o texto sempre que estiver presente e for legível: é
    absoluto, não depende de idioma e não precisa de aritmética.
    """
    hoje = hoje or date.today()

    if atributo_datetime:
        m = _ISO.match(atributo_datetime.strip())
        if m:
            return m.group(0)

    if not texto:
        return ""

    if _HOJE.search(texto):
        return hoje.isoformat()
    if _ONTEM.search(texto):
        return (hoje - timedelta(days=1)).isoformat()

    m = _RELATIVA.search(texto)
    if m:
        quantidade = int(m.group(1))
        unidade = _normalizar_unidade(m.group(2))
        dias = _DIAS_POR_UNIDADE.get(unidade)
        if dias is not None:
            return (hoje - timedelta(days=quantidade * dias)).isoformat()

    m = _ABSOLUTA_BR.search(texto)
    if m:
        dia, mes, ano = (int(g) for g in m.groups())
        if ano < 100:
            ano += 2000
        try:
            return date(ano, mes, dia).isoformat()
        except ValueError:
            # "31/02" e afins: data impossível é o mesmo que data ausente.
            return ""

    m = _ISO.search(texto)
    if m:
        return m.group(0)

    return ""


def vaga_antiga(data_iso: str, dias_limite: int, hoje: date | None = None) -> bool:
    """A vaga é velha o bastante pra provavelmente já estar preenchida?

    Data ausente devolve False de propósito. Não saber a idade não é o
    mesmo que saber que é velha, e tratar como velha rebaixaria toda vaga
    de fonte que não informa data — que ainda é a maioria delas.
    """
    if not data_iso:
        return False
    try:
        publicada = date.fromisoformat(data_iso[:10])
    except ValueError:
        return False
    return (hoje or date.today()) - publicada > timedelta(days=dias_limite)


def formatar(data_iso: str) -> str:
    """ISO para dd/mm/aaaa. "Postada 2026-03-26" se lê mal em português."""
    if not data_iso:
        return ""
    try:
        return datetime.strptime(data_iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return data_iso
