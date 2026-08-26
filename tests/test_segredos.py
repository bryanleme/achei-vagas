"""Nenhuma credencial pode entrar no git.

Isto não é teste de comportamento do programa — é uma trava. A regra "o token
fica só no .env" depende de todo mundo lembrar dela em todo commit, e é
exatamente esse tipo de regra que falha uma vez e falha para sempre: token
que entra no histórico do git continua lá depois de removido do arquivo, em
todo clone, em todo fork, e a única correção de verdade é revogar o token.

Um segredo vazado num repositório PRIVADO também é vazado: basta o
repositório virar público um dia, um colaborador ser adicionado, ou um fork
existir. "É privado" adia o problema, não resolve.

Roda em CI a cada push (.github/workflows/testes.yml). Para pegar ANTES do
commit, instale o gancho:

    git config core.hooksPath .githooks
"""

import re
import subprocess

import pytest

from radar import caminhos

# Token de bot do Telegram: id numérico, dois-pontos, 35 caracteres.
# Formato real, conferido contra um token de verdade.
_TOKEN_TELEGRAM = re.compile(r"\b\d{8,12}:AA[A-Za-z0-9_-]{32,34}\b")

# Outros formatos que costumam vazar por descuido em projeto deste tipo.
_OUTROS_SEGREDOS = [
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "token pessoal do GitHub"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"), "token fino do GitHub"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"), "chave de API estilo OpenAI"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "chave de acesso da AWS"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "chave privada"),
]

# Extensões binárias: não têm segredo em texto e o banco de vagas é grande.
_IGNORAR = {".db", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip"}


def _arquivos_rastreados() -> list[str]:
    saida = subprocess.run(
        ["git", "ls-files"],
        cwd=caminhos.RAIZ,
        capture_output=True,
        text=True,
        check=True,
    )
    return [linha for linha in saida.stdout.splitlines() if linha]


def test_env_esta_no_gitignore():
    """A primeira linha de defesa. Se o .env deixar de ser ignorado, o token
    entra no próximo `git add -A` sem ninguém perceber."""
    gitignore = (caminhos.RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore.split(), ".env precisa estar no .gitignore"


def test_env_nao_esta_rastreado():
    assert ".env" not in _arquivos_rastreados(), (
        ".env foi adicionado ao git — remova com `git rm --cached .env` e "
        "considere revogar o token, porque ele já está no histórico."
    )


def test_nenhum_arquivo_rastreado_contem_token_do_telegram():
    """O caso concreto deste projeto: o token do bot dá controle total sobre
    ele — ler todas as mensagens do chat e enviar em seu nome."""
    achados = []
    for caminho in _arquivos_rastreados():
        arquivo = caminhos.RAIZ / caminho
        if arquivo.suffix.lower() in _IGNORAR or not arquivo.is_file():
            continue
        try:
            texto = arquivo.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _TOKEN_TELEGRAM.search(texto):
            achados.append(caminho)

    assert not achados, (
        f"token do Telegram encontrado em: {achados}. "
        "Remova, e REVOGUE o token no @BotFather — uma vez commitado, ele "
        "está no histórico de todo clone."
    )


@pytest.mark.parametrize("padrao,nome", _OUTROS_SEGREDOS)
def test_nenhum_arquivo_rastreado_contem_outros_segredos(padrao, nome):
    achados = []
    for caminho in _arquivos_rastreados():
        arquivo = caminhos.RAIZ / caminho
        if arquivo.suffix.lower() in _IGNORAR or not arquivo.is_file():
            continue
        try:
            texto = arquivo.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if padrao.search(texto):
            achados.append(caminho)

    assert not achados, f"{nome} encontrado em: {achados}"


def test_exemplo_de_env_nao_tem_valor_de_verdade():
    """`.env.example` é rastreado de propósito — ele documenta quais variáveis
    existem. Justamente por ser rastreado, não pode ter valor nenhum."""
    exemplo = (caminhos.RAIZ / ".env.example").read_text(encoding="utf-8")
    for linha in exemplo.splitlines():
        if "=" in linha and not linha.strip().startswith("#"):
            chave, _, valor = linha.partition("=")
            assert valor.strip() == "", (
                f"{chave.strip()} tem valor preenchido no .env.example"
            )
