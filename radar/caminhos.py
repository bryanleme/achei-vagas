"""Caminhos do projeto, ancorados na RAIZ — nunca na pasta deste arquivo.

Por que isso é um módulo próprio em vez de uma linha em config.py:

Calcular o caminho do banco como
`os.path.join(os.path.dirname(__file__), "data", "vagas.db")` amarra o banco
à pasta do módulo. Basta mover o arquivo pra uma subpasta numa reorganização
e o banco se muda junto, em silêncio.

O estrago seria:

  - o banco real, versionado, fica órfão: ninguém mais escreve nele;
  - o novo nasce vazio a cada execução, porque a pasta não existe no
    repositório;
  - com banco vazio, TODA vaga vira "nova" — renotificação a cada 3 horas;
  - o rodízio de termos trava no offset 0: só o primeiro bloco de termos
    é buscado, pra sempre;
  - a fila do digest é descartada a cada ciclo;
  - e o passo `git add data/vagas.db` do workflow não vê mudança nenhuma,
    então o estado nunca mais persiste.

Nada disso daria erro. Tudo continuaria "verde".

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
