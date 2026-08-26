"""Ponto de entrada.

    python main.py                 roda um ciclo completo (é o que o cron faz)
    python main.py --teste         roda sem enviar nada e sem gravar nada
    python main.py --estado        mostra o que o banco tem hoje
    python main.py --precisao      rendimento por fonte, pra decidir quem fica
"""

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from radar import caminhos

load_dotenv(caminhos.ARQUIVO_ENV)

from radar import ciclo, deposito  # noqa: E402  (precisa do .env carregado antes)
from radar.alertas import telegram  # noqa: E402
from radar.log import logger  # noqa: E402


def mostrar_estado() -> None:
    deposito.iniciar()
    e = deposito.estatisticas()
    print(f"\nBanco: {caminhos.BANCO}")
    print(f"Vagas no histórico: {e['total']}")
    print(f"Na fila do resumo:  {e['na_fila']}\n")
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Radar de vagas")
    parser.add_argument(
        "--teste", action="store_true",
        help="roda o ciclo sem enviar alerta nem gravar no banco",
    )
    parser.add_argument("--estado", action="store_true", help="resumo do banco")
    parser.add_argument("--precisao", action="store_true", help="rendimento por fonte")
    args = parser.parse_args()

    if args.estado:
        mostrar_estado()
        return 0
    if args.precisao:
        mostrar_precisao()
        return 0

    if not args.teste and not telegram.configurado():
        # Falha cedo e clara. Rodar um ciclo inteiro pra descobrir no fim
        # que não havia como avisar ninguém desperdiça o ciclo e, pior,
        # marcaria vagas como vistas sem nunca as ter entregado.
        logger.error(
            "TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID não estão definidos. "
            "Sem eles não há como enviar alerta — use --teste pra rodar em seco."
        )
        return 1

    try:
        resumo = asyncio.run(ciclo.rodar(so_teste=args.teste))
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
