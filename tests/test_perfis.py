"""Testes do radar com vários perfis.

O risco que este arquivo cobre é um só, visto de vários lados: o estado de
uma pessoa vazar pra outra. "Já vista por alguém" no lugar de "já oferecida
a esta pessoa", o 👎 de um mexendo no ranking de outro, a fila de um
esvaziada pelo resumo de outro, a vaga de um caindo no chat de outro.

Os três amigos fictícios são o cenário de aceite do planejamento
(REQUISITOS-E-DECISOES.md, seção 5).
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from radar import ciclo, deposito, perfis
from radar.alertas import telegram
from radar.filtro import avaliar
from radar.fontes import registro
from radar.fontes.base import Cadencia, Fonte, Resultado
from tests.conftest import vaga

CONVITE_1 = "convite-amigo-um-0001"
CONVITE_2 = "convite-amigo-dois-002"
CONVITE_3 = "convite-amigo-tres-003"

AMIGO_1 = f'''
nome = "Amigo 1"
convite = "{CONVITE_1}"
[cargo]
fortes = ["Analista de PCP", "Planejador de Produção"]
[local]
uf = "SP"
cidades = ["Sorocaba"]
modalidades = ["presencial", "hibrido"]
'''

AMIGO_2 = f'''
nome = "Amigo 2"
convite = "{CONVITE_2}"
[cargo]
fortes = ["Analista de Atendimento", "Atendente"]
[local]
uf = "PR"
cidades = ["Curitiba"]
modalidades = ["remoto"]
'''

AMIGO_3 = f'''
nome = "Amigo 3"
convite = "{CONVITE_3}"
[cargo]
fortes = ["Desenvolvedor", "Estagiário de Desenvolvimento"]
[local]
uf = "PE"
cidades = ["Recife"]
modalidades = ["remoto", "hibrido"]
[nivel]
alvo = ["Estágio", "Júnior"]
'''


@pytest.fixture(autouse=True)
def banco_temporario(tmp_path, monkeypatch):
    monkeypatch.setattr(deposito.caminhos, "PASTA_DADOS", tmp_path)
    monkeypatch.setattr(deposito.caminhos, "BANCO", tmp_path / "teste.db")
    deposito.iniciar()


@pytest.fixture
def pasta_perfis(tmp_path) -> Path:
    pasta = tmp_path / "perfis"
    pasta.mkdir()
    (pasta / "amigo-1.toml").write_text(AMIGO_1, encoding="utf-8")
    (pasta / "amigo-2.toml").write_text(AMIGO_2, encoding="utf-8")
    (pasta / "amigo-3.toml").write_text(AMIGO_3, encoding="utf-8")
    return pasta


@pytest.fixture
def amigos(pasta_perfis) -> dict:
    return {p.id: p for p in perfis.convidados(pasta_perfis)}


def _toml(tmp_path, texto: str, nome: str = "teste.toml") -> Path:
    caminho = tmp_path / nome
    caminho.write_text(texto, encoding="utf-8")
    return caminho


# ===========================================================================
# Cenário de aceite: três pessoas, três radares
# ===========================================================================

def test_amigo1_nao_recebe_remoto_nem_outra_cidade_so_por_ter_pcp(amigos):
    regras = amigos["amigo-1"].regras
    assert avaliar(vaga(local="Sorocaba - SP", modalidade="Presencial"), regras).aprovada
    assert avaliar(vaga(local="Sorocaba - SP", modalidade="Híbrido"), regras).aprovada
    assert not avaliar(vaga(local="Remoto", modalidade="Remoto"), regras).aprovada
    assert not avaliar(vaga(local="Campinas - SP", modalidade="Presencial"), regras).aprovada


def test_amigo1_nao_aceita_remota_com_a_sede_na_cidade_dele(amigos):
    """Vaga REMOTA com "Sorocaba, SP" no local: Sorocaba é a sede da
    empresa. Pra quem não aceita remoto, isso não é "presencial em
    Sorocaba"."""
    v = vaga(local="Sorocaba, SP", modalidade="Remoto")
    assert not avaliar(v, amigos["amigo-1"].regras).aprovada


def test_amigo2_so_remoto_nunca_aceita_presencial_na_propria_cidade(amigos):
    regras = amigos["amigo-2"].regras
    presencial = vaga(titulo="Analista de Atendimento", local="Curitiba - PR",
                      modalidade="Presencial")
    assert not avaliar(presencial, regras).aprovada


def test_amigo2_recebe_remoto_com_sede_em_outro_estado(amigos):
    remota = vaga(titulo="Analista de Atendimento", local="São Paulo, SP",
                  modalidade="Remoto")
    assert avaliar(remota, amigos["amigo-2"].regras).aprovada


def test_amigo3_desenvolvedor_nao_herda_regras_do_perfil_de_logistica(amigos):
    """O perfil principal deixa "desenvolvedor" de fora de propósito. Isso é
    escolha de UMA pessoa e não pode valer pra quem procura justamente
    desenvolvimento."""
    regras = amigos["amigo-3"].regras
    v = vaga(titulo="Desenvolvedor Python Júnior", local="Recife - PE", modalidade="Híbrido")
    assert avaliar(v, regras).aprovada
    assert not avaliar(v, perfis.perfil_principal().regras).aprovada
    presencial = vaga(titulo="Desenvolvedor Python", local="Recife - PE",
                      modalidade="Presencial")
    assert not avaliar(presencial, regras).aprovada


def test_exclusao_pcd_do_principal_nao_vale_pros_convidados(amigos):
    v = vaga(titulo="Analista de PCP - PCD", local="Sorocaba - SP")
    assert avaliar(v, perfis.perfil_principal().regras).excluida
    assert avaliar(v, amigos["amigo-1"].regras).aprovada


def test_cidade_de_mesmo_nome_em_outra_uf_nao_confunde(tmp_path):
    p = perfis.de_toml(_toml(tmp_path, '''
[cargo]
fortes = ["Analista de PCP"]
[local]
uf = "PE"
cidades = ["Recife"]
'''))
    assert not avaliar(vaga(local="Recife - SP"), p.regras).aprovada
    assert avaliar(vaga(local="Recife - PE"), p.regras).aprovada


# ===========================================================================
# Isolamento no banco
# ===========================================================================

def test_vaga_ja_oferecida_a_um_perfil_continua_nova_pro_outro():
    v = vaga()
    deposito.salvar(v, "amigo-1", 8, "cargo direto", digest_pendente=True)
    assert deposito.vaga_conhecida(v, "amigo-1") is True
    assert deposito.vaga_conhecida(v, "amigo-2") is False
    deposito.salvar(v, "amigo-2", 6, "remoto", digest_pendente=True)
    with deposito._conectar() as conn:
        assert conn.execute("SELECT COUNT(*) FROM vagas").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM resultados").fetchone()[0] == 2


def test_republicada_com_outro_link_e_conhecida_so_pro_mesmo_perfil():
    deposito.salvar(vaga(link="https://gupy.io/1"), "amigo-1", 8, "", digest_pendente=False)
    outra_fonte = vaga(link="https://vagas.com.br/9")
    assert deposito.vaga_conhecida(outra_fonte, "amigo-1") is True
    assert deposito.vaga_conhecida(outra_fonte, "amigo-2") is False


def test_feedback_de_um_nao_mexe_no_do_outro():
    v = vaga()
    deposito.salvar(v, "amigo-1", 8, "", digest_pendente=False)
    deposito.salvar(v, "amigo-2", 8, "", digest_pendente=False)
    assert deposito.registrar_feedback("amigo-1", v.id, "negativo") is True
    with deposito._conectar() as conn:
        votos = dict(conn.execute("SELECT perfil, feedback FROM resultados").fetchall())
    assert votos == {"amigo-1": "negativo", "amigo-2": None}


def test_feedback_forjado_de_vaga_nunca_oferecida_nao_cria_nada():
    v = vaga()
    deposito.salvar(v, "amigo-1", 8, "", digest_pendente=False)
    assert deposito.registrar_feedback("amigo-2", v.id, "positivo") is False
    with deposito._conectar() as conn:
        assert conn.execute("SELECT COUNT(*) FROM resultados").fetchone()[0] == 1


def test_resumo_de_um_nao_esvazia_a_fila_do_outro():
    v = vaga()
    deposito.salvar(v, "amigo-1", 5, "", digest_pendente=True)
    deposito.salvar(v, "amigo-2", 5, "", digest_pendente=True)
    assert deposito.limpar_fila_do_digest("amigo-1") == 1
    assert deposito.pendentes_do_digest("amigo-1") == []
    assert len(deposito.pendentes_do_digest("amigo-2")) == 1


def test_mudanca_de_regra_de_um_so_descarta_a_fila_dele(amigos):
    v = vaga(titulo="Analista de PCP", local="Sorocaba - SP")
    deposito.salvar(v, "amigo-1", 5, "", digest_pendente=True)
    deposito.salvar(v, "amigo-2", 5, "", digest_pendente=True)
    # Pras regras do amigo 2 (atendimento, só remoto) esta vaga não serve.
    assert deposito.descartar_pendentes_fora_do_perfil("amigo-2", amigos["amigo-2"].regras) == 1
    assert len(deposito.pendentes_do_digest("amigo-1")) == 1


# ===========================================================================
# Migração do esquema 1 (estado global) pro 2 (estado por perfil)
# ===========================================================================

def _banco_esquema_1(caminho: Path) -> None:
    conn = sqlite3.connect(caminho)
    conn.executescript("""
        CREATE TABLE vagas (
            id TEXT PRIMARY KEY, chave_secundaria TEXT NOT NULL, titulo TEXT NOT NULL,
            empresa TEXT, local TEXT, link TEXT, fonte TEXT, modalidade TEXT,
            publicado_em TEXT, relevancia INTEGER, motivo TEXT,
            encontrada_em TEXT NOT NULL, digest_pendente INTEGER NOT NULL DEFAULT 0,
            situacao TEXT NOT NULL DEFAULT 'nova', feedback TEXT);
        CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
    """)
    a, b = vaga(link="https://x/a"), vaga(link="https://x/b", titulo="Analista de BI")
    for v, rel, fila, fb in ((a, 8, 0, "positivo"), (b, 4, 1, None)):
        conn.execute(
            "INSERT INTO vagas (id, chave_secundaria, titulo, empresa, local, link, fonte,"
            " relevancia, motivo, encontrada_em, digest_pendente, feedback)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (v.id, v.chave_secundaria, v.titulo, v.empresa, v.local, v.link, v.fonte,
             rel, "motivo", "2026-11-01T10:00:00+00:00", fila, fb),
        )
    conn.execute("INSERT INTO metadados VALUES ('total_vagas_ultimo_ciclo', '2')")
    conn.execute("INSERT INTO metadados VALUES ('digest_ultimo_dia', '2026-11-10')")
    conn.commit()
    conn.close()


def test_migracao_entrega_o_historico_ao_principal_sem_renotificar(tmp_path, monkeypatch):
    """Sem a migração, o primeiro ciclo depois da troca veria `resultados`
    vazia e trataria TODO o histórico como inédito — a renotificação em
    massa que BancoSuspeito existe pra impedir, só que sem erro nenhum."""
    banco = tmp_path / "legado.db"
    _banco_esquema_1(banco)
    monkeypatch.setattr(deposito.caminhos, "BANCO", banco)

    deposito.iniciar()
    deposito.iniciar()  # idempotente: rodar de novo não duplica nem apaga

    principal = deposito.PERFIL_LEGADO
    assert deposito.vaga_conhecida(vaga(link="https://x/a"), principal) is True
    assert deposito.vaga_conhecida(vaga(link="https://x/a"), "amigo-1") is False
    # A fila do resumo foi junto, com nota e ordem.
    fila = deposito.pendentes_do_digest(principal)
    assert [linha[0] for linha in fila] == ["Analista de BI"]
    assert deposito.precisao_por_fonte()[0][2] == 1  # o 👍 sobreviveu
    with deposito._conectar() as conn:
        assert conn.execute("SELECT COUNT(*) FROM resultados").fetchone()[0] == 2
    # Já tinha mandado resumo hoje: não manda outro no dia da troca.
    assert deposito.ler(f"digest_ultimo_dia:{principal}") == "2026-11-10"
    # Principal com histórico não está estreando.
    p = perfis.perfil_principal()
    deposito.gravar(ciclo.CHAVE_VOLTAS, "3")
    assert ciclo.perfil_em_estreia(p, datetime.now(timezone.utc)) is False


# ===========================================================================
# Validação dos arquivos de perfil
# ===========================================================================

@pytest.mark.parametrize("texto,trecho", [
    ('[cargo]\nfortes=["A"]\n[local]\nuf="SP"\ncidade=["Sorocaba"]', "desconhecida"),
    ('[cargo]\nfortes=["A"]\n[local]\nuf="XX"\ncidades=["X"]', "sigla"),
    ('[cargo]\nfortes=["A"]\n[local]\nuf="SP"', "cidade"),
    ('[local]\nuf="SP"\ncidades=["X"]', "não aprovaria"),
    ('convite="curto"\n[cargo]\nfortes=["A"]\n[local]\nuf="SP"\ncidades=["X"]', "convite"),
    ('[cargo]\nfortes=["A"]\n[local]\nuf="SP"\ncidades=["X"]\nmodalidades=["home"]', "modalidades"),
    ('[cargo]\nfortes=["A"]\n[local]\nuf="SP"\ncidades=["X"]\n[nivel]\nalvo=["Junior"]', "níveis"),
    ('[cargo]\nfortes=["A"]\nprioritarios=["b"]\n[local]\nuf="SP"\ncidades=["X"]', "prioritário"),
])
def test_perfil_com_erro_e_recusado_com_motivo(tmp_path, texto, trecho):
    with pytest.raises(perfis.PerfilInvalido, match=trecho):
        perfis.de_toml(_toml(tmp_path, texto))


def test_excesso_de_termos_e_recusado(tmp_path):
    fortes = json.dumps([f"Cargo {i}" for i in range(perfis.MAX_TERMOS_CONVIDADO + 1)])
    with pytest.raises(perfis.PerfilInvalido, match="teto"):
        perfis.de_toml(_toml(tmp_path, f'[cargo]\nfortes={fortes}\n[local]\nuf="SP"\ncidades=["X"]'))


def test_nome_do_arquivo_precisa_ser_id_valido(tmp_path):
    with pytest.raises(perfis.PerfilInvalido, match="id"):
        perfis.de_toml(_toml(tmp_path, '[cargo]\nfortes=["A"]', nome="Amigo João.toml"))
    with pytest.raises(perfis.PerfilInvalido, match="id"):
        perfis.de_toml(_toml(tmp_path, '[cargo]\nfortes=["A"]', nome="principal.toml"))


def test_convite_repetido_entre_perfis_e_recusado(pasta_perfis):
    (pasta_perfis / "amigo-4.toml").write_text(AMIGO_1, encoding="utf-8")
    with pytest.raises(perfis.PerfilInvalido, match="mesmo convite"):
        perfis.convidados(pasta_perfis)


def test_prioritarios_padrao_sao_os_primeiros_cargos(amigos):
    assert amigos["amigo-1"].prioritarios == ["analista de pcp", "planejador de produção"]


def test_modelo_de_perfil_do_repositorio_e_valido(tmp_path):
    """O modelo que a documentação manda copiar tem de passar na validação —
    senão a primeira experiência de quem configura um perfil é um erro."""
    modelo = (perfis.caminhos.PASTA_PERFIS / "modelo.toml.exemplo").read_text(encoding="utf-8")
    destino = tmp_path / "amigo.toml"

    # Copiado sem trocar o convite: recusado (o código do modelo é público).
    destino.write_text(modelo, encoding="utf-8")
    with pytest.raises(perfis.PerfilInvalido, match="modelo"):
        perfis.de_toml(destino)

    destino.write_text(
        modelo.replace("TROQUE-por-um-codigo-gerado", "codigo-de-teste-gerado-123"),
        encoding="utf-8",
    )
    p = perfis.de_toml(destino)
    assert p.regras.cargos_fortes and p.convite


def test_termos_da_coleta_sao_a_uniao_sem_repetir(amigos):
    todos = [perfis.perfil_principal(), *amigos.values()]
    prioritarios, demais = perfis.termos_da_coleta(todos)
    assert "analista de pcp" in prioritarios
    assert "analista de pcp" not in demais  # prioritário de alguém sai do rodízio
    juntos = prioritarios + demais
    assert len(juntos) == len(set(juntos))
    assert "atendente" in juntos and "desenvolvedor" in juntos


# ===========================================================================
# Geografia das fontes
# ===========================================================================

def test_fontes_cobrem_o_estado_de_cada_perfil(amigos):
    fontes = registro.fontes_ativas([perfis.perfil_principal(), *amigos.values()])
    gupy = next(f for f in fontes if f.nome == "Gupy")
    assert set(gupy.estados) == {"São Paulo", "Paraná", "Pernambuco"}
    nomes = [f.nome for f in fontes]
    assert {"Catho", "Catho (PR)", "Catho (PE)"} <= set(nomes)

    varredura = next(f for f in fontes if f.nome == "BNE")
    # Amigo 2 é só remoto: varrer Curitiba por cidade seria custo sem uso.
    assert ("Curitiba", "pr") not in varredura.cidades
    assert ("Recife", "pe") in varredura.cidades
    assert ("Sorocaba", "sp") in varredura.cidades


def test_sem_perfis_as_fontes_ficam_como_antes():
    nomes = [f.nome for f in registro.fontes_ativas()]
    assert nomes.count("Catho") == 1 and "Catho (PR)" not in nomes


# ===========================================================================
# Vínculo pelo convite
# ===========================================================================

def test_vinculo_nao_toma_perfil_nem_chat_de_ninguem():
    assert deposito.vincular("amigo-1", "111") == "vinculado"
    assert deposito.vincular("amigo-1", "111") == "ja_vinculado"
    assert deposito.vincular("amigo-1", "222") == "perfil_ocupado"
    assert deposito.vincular("amigo-2", "111") == "chat_ocupado"
    assert deposito.destino("amigo-1") == "111"


def test_desvincular_cancela_a_fila_pendente():
    deposito.vincular("amigo-1", "111")
    deposito.salvar(vaga(), "amigo-1", 5, "", digest_pendente=True)
    assert deposito.desvincular("amigo-1") is True
    assert deposito.destino("amigo-1") is None
    assert deposito.pendentes_do_digest("amigo-1") == []


class _TelegramFalso:
    """Bot API em memória: devolve `updates` no getUpdates e anota o resto."""

    def __init__(self, updates: list[dict]):
        self.updates = updates
        self.enviadas: list[tuple[str, str]] = []
        self.chamadas: list[str] = []

    def __call__(self, metodo: str, payload: dict):
        self.chamadas.append(metodo)
        if metodo == "getUpdates":
            return {"ok": True, "result": self.updates}
        if metodo == "sendMessage":
            self.enviadas.append((payload["chat_id"], payload["text"]))
        return {"ok": True, "result": {}}


def _mensagem(update_id: int, chat_id: int, texto: str, tipo: str = "private") -> dict:
    return {"update_id": update_id,
            "message": {"chat": {"id": chat_id, "type": tipo}, "text": texto}}


def _clique(update_id: int, chat_id: int, data: str) -> dict:
    return {"update_id": update_id, "callback_query": {
        "id": f"cb{update_id}", "data": data,
        "message": {"chat": {"id": chat_id}, "message_id": 10}}}


@pytest.fixture
def bot(monkeypatch):
    def _instalar(updates):
        falso = _TelegramFalso(updates)
        monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setattr(telegram, "_chamar", falso)
        return falso
    return _instalar


def test_start_com_convite_vincula_e_da_boas_vindas(bot, amigos):
    falso = bot([_mensagem(1, 555, f"/start {CONVITE_2}")])
    chats = {}
    contagem = telegram.processar_atualizacoes(deposito, list(amigos.values()), chats)
    assert contagem["comandos"] == 1
    assert deposito.destino("amigo-2") == "555"
    assert chats["555"].id == "amigo-2"
    assert falso.enviadas[0][0] == "555"
    assert "Amigo 2" in falso.enviadas[0][1]
    assert deposito.ler("inicio:amigo-2") is not None  # começa a estreia
    assert deposito.ler("telegram_offset") == "1"


def test_convite_errado_nao_vincula_nada(bot, amigos):
    falso = bot([_mensagem(1, 555, "/start convite-que-nao-existe")])
    telegram.processar_atualizacoes(deposito, list(amigos.values()), {})
    assert deposito.perfil_do_chat("555") is None
    assert "não reconhecido" in falso.enviadas[0][1]


def test_convite_em_grupo_e_ignorado(bot, amigos):
    bot([_mensagem(1, -100, f"/start {CONVITE_1}", tipo="group")])
    telegram.processar_atualizacoes(deposito, list(amigos.values()), {})
    assert deposito.destino("amigo-1") is None


def test_clique_de_um_chat_vale_so_pro_perfil_daquele_chat(bot, amigos):
    v = vaga()
    deposito.salvar(v, "amigo-1", 8, "", digest_pendente=False)
    deposito.salvar(v, "amigo-2", 8, "", digest_pendente=False)
    bot([_clique(1, 111, f"v|n|{v.id}"),          # amigo-1 vota
         _clique(2, 999, f"v|s|{v.id}")])         # chat desconhecido: ignorado
    chats = {"111": amigos["amigo-1"], "222": amigos["amigo-2"]}
    contagem = telegram.processar_atualizacoes(deposito, list(amigos.values()), chats)
    assert contagem["votos"] == 1
    with deposito._conectar() as conn:
        votos = dict(conn.execute("SELECT perfil, feedback FROM resultados").fetchall())
    assert votos == {"amigo-1": "negativo", "amigo-2": None}


def test_pausar_e_retomar(bot, amigos):
    chats = {"111": amigos["amigo-1"]}
    bot([_mensagem(1, 111, "/pausar")])
    telegram.processar_atualizacoes(deposito, list(amigos.values()), chats)
    assert deposito.ler("pausado:amigo-1") == "1"
    bot([_mensagem(2, 111, "/retomar@MeuRadarBot")])
    telegram.processar_atualizacoes(deposito, list(amigos.values()), chats)
    assert deposito.ler("pausado:amigo-1") is None


def test_chat_desconhecido_nao_pausa_ninguem(bot, amigos):
    falso = bot([_mensagem(1, 777, "/pausar")])
    telegram.processar_atualizacoes(deposito, list(amigos.values()), {})
    assert falso.enviadas == []


@pytest.mark.parametrize("texto,esperado", [
    ("/start abc", ("start", "abc")),
    ("/pausar@RadarBot", ("pausar", "")),
    ("  /STATUS  ", ("status", "")),
    ("oi", None),
    ("/", None),
    ("", None),
])
def test_parsear_comando(texto, esperado):
    assert telegram.parsear_comando(texto) == esperado


# ===========================================================================
# Mensagem com texto vindo do portal
# ===========================================================================

def test_titulo_com_caractere_de_html_e_escapado_no_resumo():
    """"P&D" cru no parse_mode HTML faz a API recusar a mensagem inteira
    (400), a vaga não é gravada e volta no ciclo seguinte pra falhar de
    novo — pra sempre."""
    partes = telegram.montar_resumo([
        ("Analista de P&D <Sênior>", "A&B <script>", "https://x/1?a=1&b=2", 7,
         "Sorocaba - SP", "Gupy", ""),
    ])
    texto = partes[0]
    assert "P&amp;D &lt;Sênior&gt;" in texto
    assert "<script>" not in texto
    assert 'href="https://x/1?a=1&amp;b=2"' in texto


def test_alerta_imediato_escapa_titulo_e_empresa(monkeypatch):
    enviado = {}

    def _enviar(texto, chat_id, teclado=None):
        enviado.update(texto=texto, chat=chat_id)
        return True

    monkeypatch.setattr(telegram, "enviar", _enviar)
    v = vaga(titulo="Analista de P&D", empresa="<b>Empresa</b>")
    assert telegram.alerta_vaga(v, "42", 8, "cargo direto", 30)
    assert enviado["chat"] == "42"
    assert "P&amp;D" in enviado["texto"]
    assert "&lt;b&gt;Empresa&lt;/b&gt;" in enviado["texto"]


def test_resposta_200_com_ok_false_nao_conta_como_entregue(monkeypatch):
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": False, "description": "Bad Request"}

    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: _Resp())
    assert telegram._chamar("sendMessage", {}) is None


# ===========================================================================
# O ciclo inteiro, com fonte e Telegram falsos
# ===========================================================================

class _FonteFalsa(Fonte):
    nome = "Falsa"
    cadencia = Cadencia.TODO_CICLO

    def __init__(self, vagas):
        self._vagas = vagas

    async def buscar(self, termo: str, navegador=None) -> Resultado:
        return Resultado(vagas=list(self._vagas))


def _rodar_ciclo(monkeypatch, todos, vagas, chats_env=None):
    enviadas: list[tuple[str, str]] = []
    monkeypatch.setattr(registro, "fontes_ativas", lambda perfis=None: [_FonteFalsa(vagas)])
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "123:abc")

    def _chamar(metodo, payload):
        if metodo == "getUpdates":
            return {"ok": True, "result": []}
        if metodo == "sendMessage":
            enviadas.append((payload["chat_id"], payload["text"]))
        return {"ok": True, "result": {}}

    monkeypatch.setattr(telegram, "_chamar", _chamar)
    for nome, valor in (chats_env or {}).items():
        monkeypatch.setenv(nome, valor)
    resumo = asyncio.run(ciclo.rodar(perfis=todos))
    return resumo, enviadas


def test_ciclo_entrega_a_mesma_vaga_a_dois_perfis_cada_um_no_seu_chat(monkeypatch, amigos):
    principal = perfis.perfil_principal()
    deposito.vincular("amigo-1", "111")
    deposito.vincular("amigo-2", "222")  # atendimento remoto: a vaga não serve
    pcp = vaga(titulo="Analista de PCP Pleno", local="Sorocaba - SP", modalidade="Presencial",
               link="https://x/pcp")
    # Fora da estreia, pros dois, pra alerta imediato poder sair.
    deposito.gravar(ciclo.CHAVE_VOLTAS, "5")
    passado = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    for pid in ("principal", "amigo-1", "amigo-2"):
        deposito.gravar(f"inicio:{pid}", passado)

    resumo, enviadas = _rodar_ciclo(
        monkeypatch, [principal, amigos["amigo-1"], amigos["amigo-2"]], [pcp],
        chats_env={"TELEGRAM_CHAT_ID": "999"},
    )

    assert resumo["por_perfil"] == {"principal": 1, "amigo-1": 1, "amigo-2": 0}
    alertas = [chat for chat, texto in enviadas if "Vaga nova" in texto]
    assert sorted(alertas) == ["111", "999"]
    assert "222" not in {chat for chat, _ in enviadas}
    # Batimento só pra quem opera (admin).
    batimentos = [chat for chat, texto in enviadas if "Radar ativo" in texto]
    assert batimentos == ["999"]


def test_ciclo_pula_perfil_pausado_e_perfil_sem_chat(monkeypatch, amigos):
    deposito.vincular("amigo-1", "111")
    deposito.gravar("pausado:amigo-1", "1")
    # amigo-3 nunca abriu o convite: sem chat.
    pcp = vaga(titulo="Analista de PCP", local="Sorocaba - SP", link="https://x/pcp")
    dev = vaga(titulo="Desenvolvedor", local="Recife - PE", modalidade="Híbrido",
               link="https://x/dev")
    resumo, enviadas = _rodar_ciclo(
        monkeypatch, [amigos["amigo-1"], amigos["amigo-3"]], [pcp, dev]
    )
    assert resumo["por_perfil"] == {}
    assert enviadas == []
    assert deposito.vaga_conhecida(pcp, "amigo-1") is False


def test_perfil_novo_estreia_sem_alerta_imediato(monkeypatch, amigos):
    """Quem acabou de entrar vê tudo o que já estava publicado na cidade
    dele. "Novo pra ele" não é "novo no mundo": vai pro resumo."""
    deposito.vincular("amigo-1", "111")
    deposito.gravar(ciclo.CHAVE_VOLTAS, "5")
    deposito.gravar("inicio:amigo-1", datetime.now(timezone.utc).isoformat())
    pcp = vaga(titulo="Analista de PCP Pleno", local="Sorocaba - SP", link="https://x/pcp")
    _, enviadas = _rodar_ciclo(monkeypatch, [amigos["amigo-1"]], [pcp])
    assert not [t for _, t in enviadas if "Vaga nova" in t]
    assert deposito.vaga_conhecida(pcp, "amigo-1") is True  # gravada, na fila


def test_banco_descartavel_do_teste_nao_toca_o_banco_real(monkeypatch, tmp_path):
    import main

    real = deposito.caminhos.BANCO
    deposito.gravar("marca", "antes")
    with main.banco_descartavel():
        assert deposito.caminhos.BANCO != real
        deposito.iniciar()
        deposito.gravar("marca", "durante")
        deposito.gravar(ciclo.CHAVE_RODIZIO, "7")
    assert deposito.caminhos.BANCO == real
    assert deposito.ler("marca") == "antes"
    assert deposito.ler(ciclo.CHAVE_RODIZIO) is None
