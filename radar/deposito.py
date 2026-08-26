"""O que já foi visto, o que está na fila e onde o ciclo parou.

SQLite num arquivo versionado no Git. É o modelo de persistência mais barato
que existe pra este problema: sem servidor, sem conta em lugar nenhum, e o
histórico de vagas vistas *é* o histórico de commits.

O preço, e o cuidado que ele exige: o arquivo é binário, escrito por um robô
que roda em paralelo com você. Todo o desenho do passo de push no workflow
existe por causa disso — ver .github/workflows/radar.yml.
"""

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone

from radar import caminhos
from radar.log import logger
from radar.modelos import Vaga

SITUACAO_INICIAL = "nova"


class BancoSuspeito(RuntimeError):
    """O banco existe mas parece ter perdido o histórico.

    Erro alto e barulhento de propósito. A alternativa — seguir em frente
    com banco vazio — é o pior estrago possível neste projeto: toda vaga
    volta a ser "nova", o Telegram recebe centenas de notificações
    repetidas, o rodízio de termos reinicia e a fila do digest é descartada.
    Aconteceu de verdade no projeto original, por um caminho de arquivo que
    mudou em silêncio, e ninguém percebeu por dias porque nada dava erro.

    Melhor não rodar do que rodar renotificando tudo.
    """


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS vagas (
    id                TEXT PRIMARY KEY,
    chave_secundaria  TEXT NOT NULL,
    titulo            TEXT NOT NULL,
    empresa           TEXT,
    local             TEXT,
    link              TEXT,
    fonte             TEXT,
    modalidade        TEXT,
    publicado_em      TEXT,
    relevancia        INTEGER,
    motivo            TEXT,
    encontrada_em     TEXT NOT NULL,
    digest_pendente   INTEGER NOT NULL DEFAULT 0,
    situacao          TEXT NOT NULL DEFAULT 'nova',
    feedback          TEXT
);

CREATE INDEX IF NOT EXISTS idx_vagas_chave_secundaria ON vagas(chave_secundaria);
CREATE INDEX IF NOT EXISTS idx_vagas_digest ON vagas(digest_pendente);

CREATE TABLE IF NOT EXISTS empresas (
    nome        TEXT PRIMARY KEY,
    fonte       TEXT NOT NULL,
    cidade      TEXT,
    vista_em    TEXT NOT NULL,
    aparicoes   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS metadados (
    chave  TEXT PRIMARY KEY,
    valor  TEXT NOT NULL
);
"""

_CHAVE_TOTAL_CONHECIDO = "total_vagas_ultimo_ciclo"


@contextmanager
def _conectar():
    caminhos.PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminhos.BANCO)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def iniciar() -> None:
    """Cria o esquema se não existir e confere que o histórico continua lá.

    O esquema nasce COMPLETO, com todas as colunas. Isso é uma decisão, não
    preguiça: no projeto original cada recurso novo adicionava a sua coluna
    por `ALTER TABLE` em tempo de execução, e uma dessas migrações adicionou
    a coluna de deduplicação sem preencher o histórico. Resultado: 373
    linhas antigas com valor NULL, e `WHERE chave = ?` nunca bate contra
    NULL em SQL — 52 vagas duplicadas voltaram a notificar.

    Se um dia uma coluna nova for mesmo necessária, a regra fica registrada
    aqui: adicionar a coluna E preencher o histórico na mesma função, sempre.
    """
    with _conectar() as conn:
        conn.executescript(_ESQUEMA)

    _conferir_historico()


def _conferir_historico() -> None:
    with _conectar() as conn:
        total_atual = conn.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
        registrado = conn.execute(
            "SELECT valor FROM metadados WHERE chave = ?", (_CHAVE_TOTAL_CONHECIDO,)
        ).fetchone()

    if registrado is None:
        return  # primeira execução: não há histórico com o que comparar

    esperado = int(registrado[0])
    if total_atual == 0 and esperado > 0:
        raise BancoSuspeito(
            f"O banco registrou {esperado} vaga(s) no ciclo anterior e agora tem 0. "
            f"Isso significa que {caminhos.BANCO} foi recriado do zero em vez de "
            "carregado. Rodar assim renotificaria todo o histórico. "
            "Confira se o arquivo do repositório está no lugar antes de continuar."
        )
    if total_atual < esperado:
        logger.warning(
            "O banco tem %d vaga(s), menos que as %d do ciclo anterior. "
            "Não é fatal, mas alguma coisa removeu registro.",
            total_atual,
            esperado,
        )


def registrar_total() -> int:
    """Grava quantas vagas o banco tem agora — é o que `iniciar` compara no
    ciclo seguinte pra detectar banco recriado do zero."""
    with _conectar() as conn:
        total = conn.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
        conn.execute(
            "INSERT INTO metadados (chave, valor) VALUES (?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (_CHAVE_TOTAL_CONHECIDO, str(total)),
        )
    return total


# ---------------------------------------------------------------------------
# Deduplicação
# ---------------------------------------------------------------------------

def ja_vista(vaga: Vaga) -> bool:
    """A vaga já foi vista — pelo link OU por empresa+título.

    As duas chaves importam. O link pega a mesma vaga reencontrada na mesma
    fonte; empresa+título pega a MESMA vaga republicada em fonte diferente,
    que tem link diferente e notificaria de novo sem esta segunda chave.
    """
    chave = vaga.chave_secundaria
    with _conectar() as conn:
        if chave:
            achou = conn.execute(
                "SELECT 1 FROM vagas WHERE id = ? OR chave_secundaria = ? LIMIT 1",
                (vaga.id, chave),
            ).fetchone()
        else:
            # Sem empresa não há segunda chave — ver Vaga.chave_secundaria.
            # Comparar contra "" casaria com toda vaga confidencial já
            # gravada e engoliria vaga nova em silêncio.
            achou = conn.execute(
                "SELECT 1 FROM vagas WHERE id = ? LIMIT 1", (vaga.id,)
            ).fetchone()
    return achou is not None


def salvar(vaga: Vaga, relevancia: int, motivo: str, digest_pendente: bool) -> None:
    """Grava a vaga como vista.

    Chamado SÓ DEPOIS de a notificação ter sido confirmada — ver ciclo.py.
    Gravar antes de confirmar é como se perde vaga de verdade: se o envio
    falhar, ela fica marcada como vista pra sempre e nunca mais é oferecida.
    """
    with _conectar() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO vagas ("
            " id, chave_secundaria, titulo, empresa, local, link, fonte,"
            " modalidade, publicado_em, relevancia, motivo, encontrada_em,"
            " digest_pendente, situacao"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                vaga.id,
                vaga.chave_secundaria,
                vaga.titulo,
                vaga.empresa,
                vaga.local,
                vaga.link,
                vaga.fonte,
                vaga.modalidade_legivel,
                vaga.publicado_em,
                relevancia,
                motivo,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                1 if digest_pendente else 0,
                SITUACAO_INICIAL,
            ),
        )


# ---------------------------------------------------------------------------
# Fila do digest
# ---------------------------------------------------------------------------

def pendentes_do_digest() -> list[tuple]:
    """Vagas esperando o resumo, da mais relevante pra menos."""
    with _conectar() as conn:
        return conn.execute(
            "SELECT titulo, empresa, link, relevancia, local, fonte, modalidade "
            "FROM vagas WHERE digest_pendente = 1 "
            "ORDER BY relevancia DESC, encontrada_em DESC"
        ).fetchall()


def limpar_fila_do_digest() -> int:
    """Esvazia a fila. Chamado SÓ depois de o envio ter sido confirmado —
    falha parcial mantém tudo pendente, inclusive a parte que já saiu.
    Preferir repetir uma mensagem a perder vaga que nunca chegou."""
    with _conectar() as conn:
        cursor = conn.execute(
            "UPDATE vagas SET digest_pendente = 0 WHERE digest_pendente = 1"
        )
        return cursor.rowcount


# ---------------------------------------------------------------------------
# Metadados (rodízio de termos, datas de último envio)
# ---------------------------------------------------------------------------

def ler(chave: str) -> str | None:
    with _conectar() as conn:
        linha = conn.execute(
            "SELECT valor FROM metadados WHERE chave = ?", (chave,)
        ).fetchone()
    return linha[0] if linha else None


def gravar(chave: str, valor: str) -> None:
    with _conectar() as conn:
        conn.execute(
            "INSERT INTO metadados (chave, valor) VALUES (?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (chave, valor),
        )


def marcar_feito_hoje(chave: str, hoje: date | None = None) -> None:
    gravar(chave, (hoje or datetime.now(timezone.utc).date()).isoformat())


def ja_feito_hoje(chave: str, hoje: date | None = None) -> bool:
    """Comparação em dia UTC, não em dia local.

    Misturar os dois dava resultado diferente no GitHub Actions (UTC) e na
    máquina de casa (UTC-3): entre 21h e meia-noite o dia local ainda é o
    anterior ao dia UTC, e o mesmo envio contava como "de hoje" num lugar e
    "de ontem" no outro.
    """
    hoje = hoje or datetime.now(timezone.utc).date()
    return ler(chave) == hoje.isoformat()


def registrar_empresa(nome: str, fonte: str, cidade: str) -> None:
    """Anota que esta empresa publicou vaga numa cidade do corredor.

    Registrada pela CIDADE, nunca pelo cargo — inclusive quando a vaga que a
    revelou foi reprovada no filtro. Uma metalúrgica de Tatuí que hoje
    publicou "Soldador" é exatamente a empresa que mês que vem publica
    "Analista de PCP"; filtrar a descoberta por cargo descartaria as
    empresas certas por causa da vaga errada.

    `fonte` é obrigatória, e é o campo que corrige um erro real que este
    código já cometeu. Nome de empresa NÃO é identificador universal: cada
    portal escreve o seu jeito. A varredura por empresa da Gupy consulta por
    `careerPageName`, que só bate com o nome exato que a PRÓPRIA Gupy usa —
    MEDIDO: "Grupo Caio" (vindo da Gupy) devolve 44 vagas, e "Yazaki do
    Brasil Ltda." (vindo do Vagas.com) devolve ZERO, porque na Gupy essa
    empresa não se chama assim. Guardar de onde veio o nome é o que impede
    consultar um portal com o vocabulário de outro.
    """
    nome = (nome or "").strip()
    if not nome:
        return
    with _conectar() as conn:
        conn.execute(
            "INSERT INTO empresas (nome, fonte, cidade, vista_em) VALUES (?,?,?,?) "
            "ON CONFLICT(nome) DO UPDATE SET "
            " vista_em = excluded.vista_em,"
            " cidade = excluded.cidade,"
            " aparicoes = aparicoes + 1",
            (nome, fonte, cidade, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )


def empresas_do_corredor(fonte: str, limite: int = 60) -> list[str]:
    """As empresas daquela fonte, vistas mais recentemente primeiro.

    `fonte` não tem default de propósito: consultar um portal com nome de
    empresa colhido de outro devolve zero em silêncio, que é o pior tipo de
    falha — parece "não há vaga" e é "perguntei errado".

    Recência antes de frequência: empresa que publicou na região esta semana
    tem mais chance de publicar de novo que uma que apareceu uma vez há seis
    meses. O teto existe pra a lista, que cresce sozinha, não virar centenas
    de requisições por execução.
    """
    with _conectar() as conn:
        return [
            linha[0]
            for linha in conn.execute(
                "SELECT nome FROM empresas WHERE fonte = ? "
                "ORDER BY vista_em DESC, aparicoes DESC LIMIT ?",
                (fonte, limite),
            )
        ]


def registrar_feedback(vaga_id: str, feedback: str) -> bool:
    """Grava o 👍/👎 na própria vaga.

    Na coluna da vaga e não numa chave solta de metadados: o feedback só tem
    valor quando dá pra cruzar com fonte, cargo e cidade — é assim que se
    responde "qual fonte me traz vaga boa" depois de algumas semanas.
    """
    with _conectar() as conn:
        cursor = conn.execute(
            "UPDATE vagas SET feedback = ? WHERE id = ?", (feedback, vaga_id)
        )
        return cursor.rowcount > 0


def precisao_por_fonte() -> list[tuple]:
    """Aprovadas, curtidas e descartadas por fonte — só do que teve
    feedback. É o número que decide se uma fonte fica ou sai."""
    with _conectar() as conn:
        return conn.execute(
            "SELECT fonte, COUNT(*),"
            " SUM(CASE WHEN feedback = 'positivo' THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN feedback = 'negativo' THEN 1 ELSE 0 END)"
            " FROM vagas GROUP BY fonte ORDER BY 2 DESC"
        ).fetchall()


def estatisticas() -> dict:
    with _conectar() as conn:
        total = conn.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
        por_fonte = dict(
            conn.execute(
                "SELECT fonte, COUNT(*) FROM vagas GROUP BY fonte ORDER BY 2 DESC"
            ).fetchall()
        )
        na_fila = conn.execute(
            "SELECT COUNT(*) FROM vagas WHERE digest_pendente = 1"
        ).fetchone()[0]
    return {"total": total, "por_fonte": por_fonte, "na_fila": na_fila}
