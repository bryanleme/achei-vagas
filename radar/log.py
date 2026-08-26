"""Logger único do projeto.

Escreve no stdout — que no GitHub Actions é o log da execução, o único lugar
onde dá pra investigar o que aconteceu num ciclo que rodou às 3 da manhã.

O que NÃO se loga aqui está documentado em alertas/telegram.py: a exceção
crua do `requests` inclui a URL que falhou, e a URL da Bot API tem o token
embutido. Logar `f"{e}"` num except vaza a credencial pro log público da
execução. Aconteceu seis vezes no projeto original.
"""

import logging
import sys

_FORMATO = "%(asctime)s | %(levelname)-7s | %(message)s"
_HORA = "%H:%M:%S"


def _saida_utf8():
    """stdout que aguenta acento e seta.

    MEDIDO na primeira execução real: o console do Windows abre em cp1252, e
    a primeira linha de log com "→" derrubou o handler inteiro com
    UnicodeEncodeError — no meio do ciclo, depois de a fonte já ter sido
    consultada. Não é cosmético: metade das mensagens deste projeto tem
    acento, e um log que morre na primeira palavra em português é um log que
    não existe justamente quando se precisa dele, às 3 da manhã.

    O GitHub Actions roda em UTF-8 e nunca teria mostrado isso — o bug só
    aparece na máquina de casa, que é onde se depura.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # stdout capturado (pytest) ou já encerrado: não há o que
        # reconfigurar, e falhar aqui seria pior que o problema.
        pass
    return sys.stdout


def _construir() -> logging.Logger:
    log = logging.getLogger("radar")
    if log.handlers:
        return log

    log.setLevel(logging.INFO)
    handler = logging.StreamHandler(_saida_utf8())
    handler.setFormatter(logging.Formatter(_FORMATO, datefmt=_HORA))
    log.addHandler(handler)
    log.propagate = False
    return log


logger = _construir()
