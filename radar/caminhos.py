"""Caminhos do projeto, ancorados na RAIZ — nunca na pasta deste arquivo.

Por que isso é um módulo próprio em vez de uma linha em config.py:

No projeto que deu origem a este, o caminho do banco era calculado como
`os.path.join(os.path.dirname(__file__), "data", "jobs.db")`. Quando o
config.py foi movido da raiz pra uma subpasta numa reorganização, o banco
se mudou junto, em silêncio — `data/jobs.db` virou `core/data/jobs.db`.

O estrago medido:

  - o banco real, versionado, ficou órfão: ninguém mais escrevia nele;
  - o novo nasceu vazio a cada execução, porque a pasta não existe no
    repositório;
  - com banco vazio, TODA vaga vira "nova" — renotificação a cada 3 horas;
  - o rodízio de termos travou no offset 0: só os 10 primeiros termos de 44
    eram buscados, pra sempre;
  - a fila do digest era descartada a cada ciclo;
  - e o passo `git add data/jobs.db` do workflow não via mudança nenhuma,
    então o estado nunca mais persistia.

Nada disso deu erro. Tudo continuou "verde".

RAIZ sobe um nível a partir de radar/, então o caminho deixa de depender de
onde este arquivo mora. Mover ou renomear módulo não move mais o banco
junto. Coberto por tests/test_caminhos.py, pra uma reorganização futura
quebrar o teste em vez da produção.
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

PASTA_DADOS = RAIZ / "data"
BANCO = PASTA_DADOS / "vagas.db"
ARQUIVO_ENV = RAIZ / ".env"
