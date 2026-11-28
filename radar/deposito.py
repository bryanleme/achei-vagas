"""O que já foi visto, o que está na fila e onde o ciclo parou.

SQLite num arquivo versionado no Git. É o modelo de persistência mais barato
que existe pra este problema: sem servidor, sem conta em lugar nenhum, e o
histórico de vagas vistas *é* o histórico de commits.

O preço, e o cuidado que ele exige: o arquivo é binário, escrito por um robô
que roda em paralelo com você. Todo o desenho do passo de push no workflow
existe por causa disso — ver .github/workflows/radar.yml.

CATÁLOGO COMPARTILHADO, ESTADO POR PERFIL (esquema 2)

  vagas        o catálogo: uma linha por vaga coletada, de quem quer que
               seja. Não diz nada sobre pessoa nenhuma.
  resultados   o que cada PERFIL fez com cada vaga: nota, motivo, se está
               na fila do resumo dele, o 👍/👎 dele. Chave (perfil, vaga).
  destinos     o chat do Telegram de cada perfil convidado, vinculado pelo
               convite.

A separação existe porque "esta vaga já foi vista" deixou de ser uma
pergunta global. Já ter sido oferecida ao perfil A não pode impedir que seja
oferecida ao B — e o 👎 de A não pode tirá-la do resumo de B.

As colunas relevancia/motivo/digest_pendente/situacao/feedback de `vagas`
são do esquema 1 e ficaram como histórico: a migração copia tudo pra
`resultados` do perfil principal, e daí em diante ninguém as lê.
"""

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone

from radar import caminhos
from radar.log import logger
from radar.modelos import Vaga

SITUACAO_INICIAL = "nova"
ESQUEMA_ATUAL = 2

# Dono do histórico anterior à separação por perfil. Tem de ser o mesmo id de
# perfis.PERFIL_PRINCIPAL — não importado de lá pra este módulo não depender
# da camada de perfis (que depende do filtro, que depende deste aqui).
PERFIL_LEGADO = "principal"


class BancoSuspeito(RuntimeError):
    """O banco existe mas parece ter perdido o histórico.

    Erro alto e barulhento de propósito. A alternativa — seguir em frente
    com banco vazio — é o pior estrago possível neste projeto: toda vaga
    volta a ser "nova", o Telegram recebe centenas de notificações
    repetidas, o rodízio de termos reinicia e a fila do digest é descartada.
    Basta um caminho de arquivo mudar em silêncio pra isso acontecer, e
    ninguém perceberia por dias porque nada daria erro.

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

CREATE TABLE IF NOT EXISTS resultados (
    perfil           TEXT NOT NULL,
    vaga_id          TEXT NOT NULL REFERENCES vagas(id),
    relevancia       INTEGER NOT NULL,
    motivo           TEXT,
    encontrada_em    TEXT NOT NULL,
    digest_pendente  INTEGER NOT NULL DEFAULT 0,
    feedback         TEXT,
    PRIMARY KEY (perfil, vaga_id)
);

CREATE INDEX IF NOT EXISTS idx_resultados_fila ON resultados(perfil, digest_pendente);

CREATE TABLE IF NOT EXISTS destinos (
    perfil        TEXT PRIMARY KEY,
    chat_id       TEXT NOT NULL UNIQUE,
    vinculado_em  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS empresas (
    nome        TEXT PRIMARY KEY,
    fonte       TEXT NOT NULL,
    url         TEXT,
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
_CHAVE_ESQUEMA = "esquema_versao"


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    """Cria o esquema se não existir, migra e confere que o histórico
    continua lá.

    O esquema nasce COMPLETO, com todas as colunas. Isso é uma decisão, não
    preguiça: se cada recurso novo adicionasse a sua coluna por `ALTER
    TABLE` em tempo de execução, bastaria uma migração adicionar a coluna de
    deduplicação sem preencher o histórico. As linhas antigas ficariam com
    NULL, e `WHERE chave = ?` nunca bate contra NULL em SQL — vaga
    duplicada voltaria a notificar.

    A regra vale também pra tabela nova: `resultados` nasce E é preenchida
    com o histórico na mesma função, na mesma transação. Ver _migrar_para_2.
    """
    with _conectar() as conn:
        conn.executescript(_ESQUEMA)
        _migrar_para_2(conn)

    _conferir_historico()


def _migrar_para_2(conn: sqlite3.Connection) -> None:
    """Esquema 1 -> 2: o estado que era global vira do perfil principal.

    Sem esta cópia, o primeiro ciclo depois da troca veria `resultados`
    vazia pro perfil principal e trataria TODO o histórico como inédito —
    exatamente a renotificação em massa que BancoSuspeito existe pra
    impedir, só que por outro caminho, e sem erro nenhum.

    Copia junto o "já mandei hoje" do resumo e do batimento, que passaram a
    ter chave por perfil: sem isso o dia da troca teria resumo duplicado.
    """
    versao = conn.execute(
        "SELECT valor FROM metadados WHERE chave = ?", (_CHAVE_ESQUEMA,)
    ).fetchone()
    if versao and int(versao[0]) >= 2:
        return

    copiadas = conn.execute(
        "INSERT OR IGNORE INTO resultados "
        "(perfil, vaga_id, relevancia, motivo, encontrada_em, digest_pendente, feedback) "
        "SELECT ?, id, COALESCE(relevancia, 0), motivo, encontrada_em, digest_pendente, feedback "
        "FROM vagas",
        (PERFIL_LEGADO,),
    ).rowcount
    conn.execute("UPDATE vagas SET digest_pendente = 0 WHERE digest_pendente = 1")

    for antiga, nova in (
        ("digest_ultimo_dia", f"digest_ultimo_dia:{PERFIL_LEGADO}"),
        ("batimento_ultimo_dia", f"batimento_ultimo_dia:{PERFIL_LEGADO}"),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO metadados (chave, valor) "
            "SELECT ?, valor FROM metadados WHERE chave = ?",
            (nova, antiga),
        )

    if copiadas:
        # Perfil que já tem histórico não está estreando. Sem esta marca ele
        # passaria 30 horas com o alerta imediato desligado — ver
        # ciclo.perfil_em_estreia.
        conn.execute(
            "INSERT OR IGNORE INTO metadados (chave, valor) VALUES (?, ?)",
            (f"inicio:{PERFIL_LEGADO}", "2000-01-01T00:00:00+00:00"),
        )
        logger.info(
            "Banco migrado pro esquema %d: %d vaga(s) do histórico atribuídas ao "
            "perfil '%s'.", ESQUEMA_ATUAL, copiadas, PERFIL_LEGADO,
        )

    conn.execute(
        "INSERT INTO metadados (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (_CHAVE_ESQUEMA, str(ESQUEMA_ATUAL)),
    )


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
# Deduplicação — por perfil
# ---------------------------------------------------------------------------

def vaga_conhecida(vaga: Vaga, perfil: str) -> bool:
    """Esta vaga já foi oferecida A ESTE PERFIL — pelo link OU por
    empresa+título.

    As duas chaves importam. O link pega a mesma vaga reencontrada na mesma
    fonte; empresa+título pega a MESMA vaga republicada em fonte diferente,
    que tem link diferente e notificaria de novo sem esta segunda chave.

    `perfil` é obrigatório e sem default de propósito. "Já vista por
    alguém" no lugar de "já oferecida a esta pessoa" é exatamente o bug que
    faria o segundo perfil nunca receber a vaga que o primeiro já recebeu.
    """
    chave = vaga.chave_secundaria
    with _conectar() as conn:
        if chave:
            achou = conn.execute(
                "SELECT 1 FROM resultados r JOIN vagas v ON v.id = r.vaga_id "
                "WHERE r.perfil = ? AND (v.id = ? OR v.chave_secundaria = ?) LIMIT 1",
                (perfil, vaga.id, chave),
            ).fetchone()
        else:
            # Sem empresa não há segunda chave — ver Vaga.chave_secundaria.
            # Comparar contra "" casaria com toda vaga confidencial já
            # gravada e engoliria vaga nova em silêncio.
            achou = conn.execute(
                "SELECT 1 FROM resultados WHERE perfil = ? AND vaga_id = ? LIMIT 1",
                (perfil, vaga.id),
            ).fetchone()
    return achou is not None


def salvar(
    vaga: Vaga, perfil: str, relevancia: int, motivo: str, digest_pendente: bool
) -> None:
    """Grava a vaga no catálogo e o resultado dela pra este perfil.

    Chamado SÓ DEPOIS de a notificação ter sido confirmada — ver ciclo.py.
    Gravar antes de confirmar é como se perde vaga de verdade: se o envio
    falhar, ela fica marcada como vista pra sempre e nunca mais é oferecida.
    """
    agora = _agora_iso()
    with _conectar() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO vagas ("
            " id, chave_secundaria, titulo, empresa, local, link, fonte,"
            " modalidade, publicado_em, encontrada_em, situacao"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
                agora,
                SITUACAO_INICIAL,
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO resultados ("
            " perfil, vaga_id, relevancia, motivo, encontrada_em, digest_pendente"
            ") VALUES (?,?,?,?,?,?)",
            (perfil, vaga.id, relevancia, motivo, agora, 1 if digest_pendente else 0),
        )


# ---------------------------------------------------------------------------
# Fila do digest — por perfil
# ---------------------------------------------------------------------------

def pendentes_do_digest(perfil: str) -> list[tuple]:
    """Vagas esperando o resumo DESTE perfil, da mais relevante pra menos.

    (titulo, empresa, link, relevancia, local, fonte, modalidade)
    """
    with _conectar() as conn:
        return conn.execute(
            "SELECT v.titulo, v.empresa, v.link, r.relevancia, v.local, v.fonte, v.modalidade "
            "FROM resultados r JOIN vagas v ON v.id = r.vaga_id "
            "WHERE r.perfil = ? AND r.digest_pendente = 1 "
            "ORDER BY r.relevancia DESC, r.encontrada_em DESC",
            (perfil,),
        ).fetchall()


def descartar_pendentes_fora_do_perfil(perfil: str, regras) -> int:
    """Retira da fila deste perfil as vagas que não atendem mais às regras
    dele.

    O histórico continua intacto: apenas o envio pendente é cancelado. Isso
    importa quando cidades, cargos ou exclusões mudam entre o momento em que
    uma vaga entrou na fila e o resumo seguinte.
    """
    # Import local evita um ciclo de imports no carregamento dos módulos:
    # filtro usa Vaga, e deposito também precisa reconstruí-la aqui.
    from radar.filtro import avaliar

    with _conectar() as conn:
        linhas = conn.execute(
            "SELECT v.id, v.titulo, v.empresa, v.local, v.link, v.fonte, v.modalidade "
            "FROM resultados r JOIN vagas v ON v.id = r.vaga_id "
            "WHERE r.perfil = ? AND r.digest_pendente = 1",
            (perfil,),
        ).fetchall()

        rejeitadas = []
        for id_vaga, titulo, empresa, local, link, fonte, modalidade in linhas:
            vaga = Vaga(
                titulo=titulo,
                empresa=empresa or "",
                local=local or "",
                link=link or "",
                fonte=fonte or "",
                modalidade=modalidade or "",
            )
            if not avaliar(vaga, regras).aprovada:
                rejeitadas.append((perfil, id_vaga))

        if rejeitadas:
            conn.executemany(
                "UPDATE resultados SET digest_pendente = 0 WHERE perfil = ? AND vaga_id = ?",
                rejeitadas,
            )

    return len(rejeitadas)


def limpar_fila_do_digest(perfil: str) -> int:
    """Esvazia a fila deste perfil. Chamado SÓ depois de o envio ter sido
    confirmado — falha parcial mantém tudo pendente, inclusive a parte que
    já saiu. Preferir repetir uma mensagem a perder vaga que nunca chegou.

    A fila dos OUTROS perfis não é tocada: o resumo de um que falhou não
    pode ser esvaziado pelo resumo de outro que saiu."""
    with _conectar() as conn:
        cursor = conn.execute(
            "UPDATE resultados SET digest_pendente = 0 "
            "WHERE perfil = ? AND digest_pendente = 1",
            (perfil,),
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


def apagar(chave: str) -> None:
    with _conectar() as conn:
        conn.execute("DELETE FROM metadados WHERE chave = ?", (chave,))


def marcar_feito_hoje(chave: str, hoje: date | None = None) -> None:
    gravar(chave, (hoje or datetime.now(timezone.utc).date()).isoformat())


def ja_feito_hoje(chave: str, hoje: date | None = None) -> bool:
    """Compara com o dia que QUEM CHAMA calculou.

    Quem chama decide o fuso e passa a data já convertida — ver
    ciclo.dia_local. O que não pode é cada chamada usar o relógio local da
    máquina: dava resultado diferente no GitHub Actions (UTC) e na máquina
    de casa (UTC-3), e o mesmo envio contava como "de hoje" num lugar e "de
    ontem" no outro.
    """
    hoje = hoje or datetime.now(timezone.utc).date()
    return ler(chave) == hoje.isoformat()


# ---------------------------------------------------------------------------
# Destinos (chat de cada perfil convidado)
# ---------------------------------------------------------------------------

def destino(perfil: str) -> str | None:
    with _conectar() as conn:
        linha = conn.execute(
            "SELECT chat_id FROM destinos WHERE perfil = ?", (perfil,)
        ).fetchone()
    return linha[0] if linha else None


def perfil_do_chat(chat_id: str) -> str | None:
    with _conectar() as conn:
        linha = conn.execute(
            "SELECT perfil FROM destinos WHERE chat_id = ?", (str(chat_id),)
        ).fetchone()
    return linha[0] if linha else None


def vincular(perfil: str, chat_id: str) -> str:
    """Amarra o chat ao perfil. Devolve o que aconteceu:

      "vinculado"      primeira vez
      "ja_vinculado"   mesmo perfil, mesmo chat (clicou no link duas vezes)
      "perfil_ocupado" o perfil já tem OUTRO chat — o convite vazou ou foi
                       repassado; não se toma o perfil de ninguém em silêncio
      "chat_ocupado"   este chat já é de OUTRO perfil

    Desvincular é decisão de quem opera o radar: `--desvincular <perfil>`.
    """
    chat_id = str(chat_id)
    atual = destino(perfil)
    if atual == chat_id:
        return "ja_vinculado"
    if atual is not None:
        return "perfil_ocupado"
    dono = perfil_do_chat(chat_id)
    if dono is not None:
        return "chat_ocupado"
    with _conectar() as conn:
        conn.execute(
            "INSERT INTO destinos (perfil, chat_id, vinculado_em) VALUES (?, ?, ?)",
            (perfil, chat_id, _agora_iso()),
        )
    return "vinculado"


def desvincular(perfil: str) -> bool:
    with _conectar() as conn:
        removido = conn.execute(
            "DELETE FROM destinos WHERE perfil = ?", (perfil,)
        ).rowcount > 0
        # Fila de quem saiu não pode sair pro próximo dono do chat.
        conn.execute(
            "UPDATE resultados SET digest_pendente = 0 WHERE perfil = ?", (perfil,)
        )
    return removido


# ---------------------------------------------------------------------------
# Empresas (descoberta pela cidade)
# ---------------------------------------------------------------------------

def registrar_empresa(nome: str, fonte: str, cidade: str, url: str = "") -> None:
    """Anota que esta empresa publicou vaga numa cidade de algum perfil.

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
            "INSERT INTO empresas (nome, fonte, url, cidade, vista_em) VALUES (?,?,?,?,?) "
            "ON CONFLICT(nome) DO UPDATE SET "
            " vista_em = excluded.vista_em,"
            " cidade = excluded.cidade,"
            # COALESCE e não sobrescrita: um card sem link não pode APAGAR o
            # link que outro card da mesma empresa já trouxe.
            " url = COALESCE(NULLIF(excluded.url, ''), empresas.url),"
            " aparicoes = aparicoes + 1",
            (nome, fonte, url, cidade, _agora_iso()),
        )


def empresas_com_url(fonte: str, limite: int = 60) -> list[tuple[str, str]]:
    """(nome, url) das empresas daquela fonte que têm link conhecido.

    Só as com link: varrer exige o endereço, e nome sem link não dá pra
    consultar em portal que identifica empresa por id numérico.
    """
    with _conectar() as conn:
        return [
            (linha[0], linha[1])
            for linha in conn.execute(
                "SELECT nome, url FROM empresas "
                "WHERE fonte = ? AND url IS NOT NULL AND url <> '' "
                "ORDER BY vista_em DESC, aparicoes DESC LIMIT ?",
                (fonte, limite),
            )
        ]


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


# ---------------------------------------------------------------------------
# Feedback e relatórios
# ---------------------------------------------------------------------------

def registrar_feedback(perfil: str, vaga_id: str, feedback: str) -> bool:
    """Grava o 👍/👎 no resultado DESTE perfil.

    Só atualiza se a vaga foi de fato oferecida a este perfil: um clique
    forjado (callback com id de vaga que esta pessoa nunca recebeu) não cria
    linha nem mexe no voto de outra pessoa. Devolve False nesse caso.

    No resultado e não numa chave solta de metadados: o feedback só tem
    valor quando dá pra cruzar com fonte, cargo e cidade — é assim que se
    responde "qual fonte me traz vaga boa" depois de algumas semanas.
    """
    with _conectar() as conn:
        cursor = conn.execute(
            "UPDATE resultados SET feedback = ? WHERE perfil = ? AND vaga_id = ?",
            (feedback, perfil, vaga_id),
        )
        return cursor.rowcount > 0


def precisao_por_fonte() -> list[tuple]:
    """Aprovadas, curtidas e descartadas por fonte, somando todos os perfis.
    É o número que decide se uma fonte fica ou sai."""
    with _conectar() as conn:
        return conn.execute(
            "SELECT v.fonte, COUNT(*),"
            " SUM(CASE WHEN r.feedback = 'positivo' THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN r.feedback = 'negativo' THEN 1 ELSE 0 END)"
            " FROM resultados r JOIN vagas v ON v.id = r.vaga_id"
            " GROUP BY v.fonte ORDER BY 2 DESC"
        ).fetchall()


def estatisticas() -> dict:
    with _conectar() as conn:
        total = conn.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
        por_fonte = dict(
            conn.execute(
                "SELECT fonte, COUNT(*) FROM vagas GROUP BY fonte ORDER BY 2 DESC"
            ).fetchall()
        )
        por_perfil = {
            perfil: {"vagas": vagas, "na_fila": fila or 0}
            for perfil, vagas, fila in conn.execute(
                "SELECT perfil, COUNT(*), SUM(digest_pendente) FROM resultados "
                "GROUP BY perfil ORDER BY perfil"
            )
        }
    na_fila = sum(p["na_fila"] for p in por_perfil.values())
    return {"total": total, "por_fonte": por_fonte, "na_fila": na_fila, "por_perfil": por_perfil}
