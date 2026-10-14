"""Normalização e casamento de termo.

Duas funções, uma responsabilidade cada. Todo o resto do projeto compara
texto passando por aqui — nunca com `in` cru sobre o texto original.
"""

import re
import unicodedata


def normalizar(texto: str) -> str:
    """Minúsculo, sem acento, com espaços colapsados.

    As fontes escrevem a mesma cidade de todo jeito: "Itapetininga",
    "ITAPETININGA", "Itapetininga/SP", "Itapetininga - SP". E metade delas
    perde o acento em algum ponto do caminho ("Tatui", "Avare", "Botucatu").
    Comparar sem normalizar significa errar por acentuação, que não é uma
    diferença real entre as vagas.
    """
    if not texto:
        return ""
    tirar_acentos = unicodedata.normalize("NFKD", texto)
    tirar_acentos = "".join(c for c in tirar_acentos if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", tirar_acentos.lower()).strip()


def contem_termo(termo: str, texto: str, aceitar_plural: bool = False) -> bool:
    """O termo aparece no texto como PALAVRA, não como pedaço de palavra.

    Os dois argumentos já devem vir normalizados.

    Por que borda de palavra e não `in` cru: com substring simples, "bi"
    bate dentro de "combinado", "ambiente" e "hobbies"; "sp" bate dentro de
    "esporte". A lista de qualificadores tem siglas curtas de propósito
    (bi, pcp, wms, tms), então `in` cru transformaria cada uma delas numa
    fonte de falso positivo silencioso.

    `aceitar_plural` cobre o caso oposto — "processo" no config precisa
    bater "Analista de Processos" no título. Só o "s" final, porque é a
    variação que de fato aparece em nome de cargo; qualquer coisa mais
    esperta que isso (stemming) traria de volta o problema que a borda de
    palavra acabou de resolver.
    """
    if not termo or not texto:
        return False

    sufixo = r"s?" if aceitar_plural else ""
    # re.escape porque termo pode conter caractere de regex — "mmog/le" tem
    # barra, e um termo futuro pode ter ponto ou parêntese.
    padrao = rf"(?<!\w){re.escape(termo)}{sufixo}(?!\w)"
    return re.search(padrao, texto) is not None


def contem_qualquer(termos: list[str], texto: str, aceitar_plural: bool = False) -> bool:
    """Atalho pra `any(contem_termo(...))` — o padrão mais repetido do filtro."""
    return any(
        contem_termo(normalizar(t), texto, aceitar_plural=aceitar_plural) for t in termos
    )


# Conectivos que não carregam significado dentro de um nome de cargo. As
# vagas escrevem o mesmo cargo com e sem eles, sem nenhuma diferença de
# sentido: "Analista de PCP" e "Analista PCP", "Analista de Gestão de
# Frotas" e "Analista Gestão Frotas".
_CONECTIVOS = {"de", "da", "do", "das", "dos", "e", "em", "para", "of", "the", "a", "o"}


def esqueleto(texto: str) -> str:
    """Texto normalizado e sem conectivo, pra comparar NOME DE CARGO.

    Existe porque casar a frase exata erra por um motivo bobo e muito
    comum: metade das vagas escreve "Analista PCP" e a outra metade
    "Analista de PCP". Sem isso, cada variação de conectivo teria de virar
    uma entrada nova na lista de cargos do config — e a lista já é o lugar
    mais fácil de esquecer alguma coisa.

    Continua sendo casamento de FRASE, não de palavras soltas: o cargo
    ainda precisa aparecer com as palavras na ordem e coladas. "Analista de
    Risco e Compliance" vira "analista risco compliance", que NÃO contém
    "analista compliance" — o cargo não é aquele, e continua não sendo.
    """
    palavras = [p for p in normalizar(texto).split() if p not in _CONECTIVOS]
    return " ".join(palavras)


def contem_cargo(cargo: str, titulo: str) -> bool:
    """O título contém este nome de cargo, tolerando conectivo."""
    return contem_termo(esqueleto(cargo), esqueleto(titulo))
