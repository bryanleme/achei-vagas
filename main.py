"""Ponto de entrada.

    python main.py                       roda um ciclo completo (é o que o cron faz)
    python main.py --teste               roda sem enviar nada e sem gravar nada
    python main.py --estado              mostra o que o banco tem hoje
    python main.py --precisao            rendimento por fonte, pra decidir quem fica
    python main.py --perfis              valida perfis/*.toml e mostra quem está ligado
    python main.py --novo-convite        gera um código de convite pra colar num perfil
    python main.py --convite <perfil>    mostra o link de convite daquele perfil
    python main.py --desvincular <perfil> solta o chat de um perfil (pra re-convidar)
"""

import argparse
import asyncio
import secrets
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

from radar import caminhos

load_dotenv(caminhos.ARQUIVO_ENV)

from radar import ciclo, deposito, perfis  # noqa: E402  (precisa do .env carregado antes)
from radar.alertas import telegram  # noqa: E402
from radar.log import logger  # noqa: E402


def mostrar_estado() -> None:
    deposito.iniciar()
    e = deposito.estatisticas()
    print(f"\nBanco: {caminhos.BANCO}")
    print(f"Vagas no catálogo: {e['total']}")
    print(f"Na fila do resumo:  {e['na_fila']} (somando os perfis)\n")
    if e["por_perfil"]:
        print("Por perfil:")
        for pid, d in e["por_perfil"].items():
            print(f"  {pid:20} {d['vagas']:>6} oferecidas  {d['na_fila']:>4} na fila")
        print()
    if e["por_fonte"]:
        print("Por fonte:")
        for fonte, total in e["por_fonte"].items():
            print(f"  {fonte:20} {total}")
    print()


def mostrar_precisao() -> None:
    deposito.iniciar()
    linhas = deposito.precisao_por_fonte()
    if not linhas:
        print("Ainda não há vaga gravada.")
        return
    print(f"\n{'Fonte':20} {'Vagas':>7} {'👍':>5} {'👎':>5}")
    print("-" * 40)
    for fonte, total, positivos, negativos in linhas:
        print(f"{fonte or '?':20} {total:>7} {positivos or 0:>5} {negativos or 0:>5}")
    print("\nFonte sem vaga aprovada em um mês é candidata a sair.\n")


def mostrar_perfis() -> int:
    try:
        todos = perfis.todos()
    except perfis.PerfilInvalido as e:
        print(f"\nERRO no perfil: {e}\n")
        return 1
    deposito.iniciar()
    print()
    for p in todos:
        destino = ciclo.destino_do_perfil(p)
        if not p.ativo:
            situacao = "desativado no arquivo"
        elif destino:
            situacao = "ligado"
        elif p.convite:
            situacao = "esperando a pessoa abrir o convite"
        else:
            situacao = "SEM DESTINO (falta convite ou variável de ambiente)"
        if deposito.ler(f"pausado:{p.id}") == "1":
            situacao += " · pausado"
        modos = "/".join(m for m, ok in (("presencial", p.regras.aceita_presencial),
                                         ("híbrido", p.regras.aceita_hibrido),
                                         ("remoto", p.regras.aceita_remoto)) if ok)
        print(f"{p.id:16} {situacao}")
        print(f"{'':16} {len(p.regras.cargos_fortes)} cargo(s) forte(s), {len(p.termos)} termo(s), "
              f"{', '.join(p.regras.cidades) or '-'} ({p.uf}), {modos}")
    print()
    return 0


def mostrar_convite(pid: str) -> int:
    try:
        perfil = next((p for p in perfis.todos() if p.id == pid), None)
    except perfis.PerfilInvalido as e:
        print(f"ERRO no perfil: {e}")
        return 1
    if perfil is None:
        print(f"Não há perfil '{pid}' em perfis/.")
        return 1
    if not perfil.convite:
        print(f"O perfil '{pid}' não tem convite. Gere um com --novo-convite e cole em "
              f"perfis/{pid}.toml.")
        return 1
    bot = telegram.nome_do_bot() if telegram.configurado() else None
    usuario = bot or "NOME_DO_BOT"
    print(f"\nLink de convite de '{perfil.nome}':\n\n  https://t.me/{usuario}?start={perfil.convite}\n")
    if not bot:
        print("(Sem TELEGRAM_BOT_TOKEN aqui não deu pra descobrir o @ do bot — troque "
              "NOME_DO_BOT pelo usuário dele.)\n")
    print("A pessoa abre o link, toca em Iniciar, e o radar confirma na rodada seguinte.\n")
    return 0


def desvincular(pid: str) -> int:
    deposito.iniciar()
    if deposito.desvincular(pid):
        print(f"Perfil '{pid}' desvinculado. O próximo /start com o convite dele vincula de novo.")
        return 0
    print(f"O perfil '{pid}' não tinha chat vinculado.")
    return 1


@contextmanager
def banco_descartavel():
    """Aponta o banco pra uma CÓPIA durante o --teste.

    `--teste` prometia "sem gravar nada" e gravava: o rodízio de termos
    avançava, a marca de "fontes diárias já rodaram hoje" era gravada (e o
    ciclo real seguinte pulava as varreduras do dia) e a descoberta de
    empresas escrevia na tabela. Cada função teria de lembrar de não gravar;
    bastava uma esquecer.

    Trocar o arquivo inteiro fecha a classe de bug: o ciclo grava à vontade,
    numa cópia que é apagada no fim.
    """
    original_pasta, original_banco = caminhos.PASTA_DADOS, caminhos.BANCO
    with tempfile.TemporaryDirectory(prefix="radar-teste-") as tmp:
        copia = Path(tmp) / "vagas.db"
        if original_banco.exists():
            shutil.copy2(original_banco, copia)
        caminhos.PASTA_DADOS, caminhos.BANCO = Path(tmp), copia
        try:
            yield
        finally:
            caminhos.PASTA_DADOS, caminhos.BANCO = original_pasta, original_banco


def main() -> int:
    parser = argparse.ArgumentParser(description="Radar de vagas")
    parser.add_argument(
        "--teste", action="store_true",
        help="roda o ciclo sem enviar alerta nem gravar no banco",
    )
    parser.add_argument("--estado", action="store_true", help="resumo do banco")
    parser.add_argument("--precisao", action="store_true", help="rendimento por fonte")
    parser.add_argument("--perfis", action="store_true", help="valida e lista os perfis")
    parser.add_argument("--novo-convite", action="store_true", help="gera um código de convite")
    parser.add_argument("--convite", metavar="PERFIL", help="link de convite do perfil")
    parser.add_argument("--desvincular", metavar="PERFIL", help="solta o chat do perfil")
    args = parser.parse_args()

    if args.estado:
        mostrar_estado()
        return 0
    if args.precisao:
        mostrar_precisao()
        return 0
    if args.perfis:
        return mostrar_perfis()
    if args.novo_convite:
        print(secrets.token_urlsafe(18))
        return 0
    if args.convite:
        return mostrar_convite(args.convite)
    if args.desvincular:
        return desvincular(args.desvincular)

    try:
        carregados = perfis.todos()
    except perfis.PerfilInvalido as e:
        # Falha ALTO. Perfil quebrado em silêncio é uma pessoa que para de
        # receber vaga sem ninguém saber.
        logger.error("Perfil inválido: %s", e)
        return 4

    if not args.teste and not telegram.configurado():
        # Falha cedo e clara. Rodar um ciclo inteiro pra descobrir no fim
        # que não havia como avisar ninguém desperdiça o ciclo e, pior,
        # marcaria vagas como vistas sem nunca as ter entregado.
        logger.error(
            "TELEGRAM_BOT_TOKEN não está definido. Sem ele não há como enviar "
            "alerta — use --teste pra rodar em seco."
        )
        return 1

    try:
        if args.teste:
            with banco_descartavel():
                resumo = asyncio.run(ciclo.rodar(so_teste=True, perfis=carregados))
        else:
            resumo = asyncio.run(ciclo.rodar(so_teste=False, perfis=carregados))
    except deposito.BancoSuspeito as e:
        logger.error("%s", e)
        return 2

    # Falhar ALTO quando nada foi entregue.
    #
    # MEDIDO na primeira execução no GitHub Actions: todas as mensagens
    # voltaram 404 por um carriage return invisível no fim do token, e o
    # workflow terminou VERDE. O painel dizia sucesso, o Telegram estava
    # mudo, e não havia nenhum lugar onde perceber a diferença.
    #
    # O batimento diário existe pra impedir exatamente esse silêncio. Se ele
    # próprio não consegue sair, o Actions precisa ficar vermelho — é a
    # última linha de defesa contra um sistema de alerta que falha calado.
    entregas = resumo.get("entregas", {})
    if entregas.get("tentativas") and entregas["falhas"] == entregas["tentativas"]:
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
