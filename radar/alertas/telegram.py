"""Os alertas — o único lugar onde o projeto fala com você.

São quatro, e cada um responde a uma pergunta diferente:

  ALERTA IMEDIATO   "apareceu agora uma vaga que vale interromper seu dia"
  DIGEST DIÁRIO     "isto foi o resto de ontem, ranqueado, leia com café"
  ALERTA DE SAÚDE   "as fontes pararam de responder, o silêncio é falso"
  BATIMENTO         "eu rodei hoje; se este sumir, o robô morreu"

O batimento é o menos óbvio e o mais importante. Sem ele, "o workflow parou
de rodar" e "não apareceu vaga nova" chegam até você exatamente iguais: como
silêncio. Um deles é normal e o outro é o sistema morto.
"""

import json

import requests

from radar.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from radar.texto import normalizar
from radar.log import logger

_API = "https://api.telegram.org/bot{token}/{metodo}"

# Margem sob o limite real do Telegram (4.096 caracteres). Sobra pra
# cabeçalho e pra acentuação, que ocupa mais de um byte por caractere.
LIMITE_MENSAGEM = 3500

# Teto de vagas no resumo. Resumo é resumo.
#
# MEDIDO em produção: uma fila de 382 vagas virou 25 mensagens seguidas no
# Telegram. Isso é pior que o problema que o resumo existe pra resolver — a
# guarda de estreia foi escrita justamente pra evitar rajada de notificação, e
# o próprio resumo virou a rajada.
#
# 40 cabe em ~3 mensagens e é mais do que alguém lê de uma sentada. As vagas
# que ficam de fora são, por definição, as de MENOR relevância da fila (ela
# vem ordenada), continuam no banco e saem em `--estado`. Nenhuma some.
LIMITE_VAGAS_DIGEST = 40


def configurado() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def _chamar(metodo: str, payload: dict) -> dict | None:
    """POST na Bot API, com o cuidado que a credencial exige.

    NUNCA logue `str(e)` nem a URL num except aqui. A URL é
    `.../bot<TOKEN>/sendMessage` — o token está DENTRO dela — e a mensagem
    padrão de erro de conexão do requests/urllib3 inclui a URL completa que
    falhou. No projeto original isso vazou o token do bot seis vezes pro
    log da execução do GitHub Actions, que é público quando o repositório é.

    HTTPError tem `.response`, com status e motivo e nenhum token. Qualquer
    outra RequestException nunca chegou a ter resposta: loga-se só o TIPO da
    exceção, jamais o texto dela.
    """
    if not TELEGRAM_BOT_TOKEN:
        return None

    url = _API.format(token=TELEGRAM_BOT_TOKEN, metodo=metodo)
    try:
        resposta = requests.post(url, data=payload, timeout=15)
        resposta.raise_for_status()
        return resposta.json()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        motivo = e.response.reason if e.response is not None else "sem detalhe"
        logger.error("Telegram %s falhou: HTTP %s (%s)", metodo, status, motivo)
        return None
    except requests.RequestException as e:
        logger.error(
            "Telegram %s falhou: %s (sem resposta do servidor)", metodo, type(e).__name__
        )
        return None


# Placar de entrega do ciclo em andamento.
#
# Existe por causa de uma falha real, e a falha é pior do que parece: na
# primeira execução no GitHub Actions, TODAS as mensagens voltaram HTTP 404
# (um "\r" invisível no token) e o workflow terminou VERDE. Nenhum alerta
# chegou, nenhum resumo chegou, o batimento não chegou — e o painel do
# Actions mostrava sucesso.
#
# Isso é exatamente o cenário que o batimento diário existe pra impedir:
# silêncio no Telegram indistinguível de "não achou vaga". Aqui o silêncio
# passou a ser indistinguível também no Actions, que era o último lugar onde
# ainda dava pra perceber. Um sistema de alerta que falha em silêncio é pior
# que nenhum, porque você confia nele.
_entregas = {"tentativas": 0, "falhas": 0}


def placar_de_entrega() -> dict:
    return dict(_entregas)


def zerar_placar() -> None:
    _entregas["tentativas"] = 0
    _entregas["falhas"] = 0


def enviar(texto: str, teclado: dict | None = None) -> bool:
    if not configurado():
        logger.warning("Telegram sem token/chat_id — alerta não enviado.")
        return False

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    # A API exige reply_markup como string JSON quando o corpo é
    # form-encoded. Passar o dict cru falha em SILÊNCIO: a resposta vem 200,
    # a mensagem chega, e o teclado simplesmente não aparece.
    if teclado is not None:
        payload["reply_markup"] = json.dumps(teclado)

    _entregas["tentativas"] += 1
    entregue = _chamar("sendMessage", payload) is not None
    if not entregue:
        _entregas["falhas"] += 1
    return entregue


# ---------------------------------------------------------------------------
# Renderização
# ---------------------------------------------------------------------------

def estrelas(pontos: int) -> str:
    """0-10 vira 5 estrelas.

    `(pontos + 1) // 2` e não `round(pontos / 2)`: o round do Python
    arredonda .5 pro par mais próximo, então 5/10 daria 2 estrelas e 7/10
    daria 4 — inconsistente pra quem só olha o emoji.
    """
    cheias = (pontos + 1) // 2
    return "⭐" * cheias + "☆" * (5 - cheias) + f" ({pontos}/10)"



def local_legivel(local: str, modalidade: str) -> str:
    """Como o local aparece na mensagem.

    MEDIDO no primeiro ciclo real: nove vagas chegaram como "Salvador, BA",
    "Brasília, DF", "Campinas, SP" — todas REMOTAS, marcadas assim pelo
    filtro nativo do LinkedIn. O texto de local de uma vaga remota traz a
    sede da empresa, não o lugar onde se trabalha.

    O filtro estava certo e a mensagem estava mentindo. E o estrago dessa
    combinação é específico: uma vaga que parece estar em Salvador quando
    você mora em Itapetininga não é lida como "remota" — é lida como "o
    robô está errado". Basta acontecer duas vezes pra você parar de abrir
    as notificações.

    "Remoto · Salvador, BA" diz as duas coisas: onde se trabalha e de onde
    vem a empresa.
    """
    local = (local or "").strip()
    if modalidade != "Remoto":
        return local or "não informado"
    if not local:
        return "Remoto"
    # O local já diz que é remoto — não repetir.
    if any(v in normalizar(local) for v in ("remot", "home office", "anywhere")):
        return local
    return f"Remoto · {local}"


def _teclado_feedback(vaga_id: str) -> dict:
    """Botões 👍/👎 na notificação.

    O `callback_data` carrega direção e id porque o clique que volta do
    Telegram não tem como saber de qual vaga é — a mensagem em si não
    basta. Formato curto de propósito: o limite real é 64 bytes e
    "fb|1|" + hash md5 já usa 37.
    """
    return {
        "inline_keyboard": [[
            {"text": "👍", "callback_data": f"fb|1|{vaga_id}"},
            {"text": "👎", "callback_data": f"fb|0|{vaga_id}"},
        ]]
    }


def alerta_vaga(vaga, relevancia: int, motivo: str, dias_antiga: int) -> bool:
    """Vaga de alta relevância, na hora."""
    linhas = [f"🚨 <b>Vaga nova</b> — {estrelas(relevancia)}", ""]

    if vaga.antiga(dias_antiga):
        linhas.append(
            f"⚠️ <b>Publicada {vaga.publicado_em_legivel}</b> — pode já estar preenchida."
        )
        linhas.append("")

    linhas.append(f"<b>{vaga.titulo}</b>")
    linhas.append(f"{vaga.empresa}")
    linhas.append("")
    linhas.append(f"📍 {local_legivel(vaga.local, vaga.modalidade_legivel)}")
    linhas.append(f"🎯 {vaga.senioridade}")
    if motivo:
        linhas.append(f"✅ {motivo}")
    linhas.append(f"🔎 {vaga.fonte}")
    if vaga.publicado_em_legivel and not vaga.antiga(dias_antiga):
        linhas.append(f"📅 {vaga.publicado_em_legivel}")
    linhas.append("")
    linhas.append(vaga.link)

    return enviar("\n".join(linhas), teclado=_teclado_feedback(vaga.id))


def montar_digest(vagas: list[tuple]) -> list[str]:
    """Monta o resumo diário — devolve uma LISTA de mensagens, não uma só.

    Um dia cheio passa dos 4.096 caracteres do Telegram com folga. Quebrar
    em partes numeradas é melhor que estourar (a API recusa a mensagem
    inteira) ou truncar (some vaga sem ninguém saber).
    """
    if not vagas:
        return []

    total = len(vagas)
    vagas = vagas[:LIMITE_VAGAS_DIGEST]

    linhas = [
        f'{estrelas(rel or 0)} <a href="{link}">{titulo}</a>\n'
        f'    {empresa or "empresa não informada"} · '
        f'{local_legivel(local, modalidade)} · {fonte}'
        for titulo, empresa, link, rel, local, fonte, modalidade in vagas
    ]

    partes: list[list[str]] = []
    atual: list[str] = []
    tamanho = 0
    for linha in linhas:
        if atual and tamanho + len(linha) + 1 > LIMITE_MENSAGEM:
            partes.append(atual)
            atual, tamanho = [], 0
        atual.append(linha)
        tamanho += len(linha) + 1
    if atual:
        partes.append(atual)

    mensagens = []
    for i, parte in enumerate(partes, start=1):
        if total > len(vagas):
            cabecalho = (f"📋 <b>Resumo do dia</b> — as {len(vagas)} melhores "
                         f"de {total}")
        else:
            cabecalho = f"📋 <b>Resumo do dia</b> — {total} vaga(s)"
        if len(partes) > 1:
            cabecalho += f" · parte {i}/{len(partes)}"
        mensagens.append(cabecalho + "\n\n" + "\n\n".join(parte))
    return mensagens


def enviar_digest(vagas: list[tuple]) -> bool:
    """Só devolve True se TODAS as partes confirmarem.

    Quem chama só esvazia a fila com True — então falha parcial mantém tudo
    pendente, inclusive a parte que já saiu. Preferir repetir uma parte a
    perder vaga que nunca chegou.
    """
    mensagens = montar_digest(vagas)
    if not mensagens:
        return True
    return all(enviar(m) for m in mensagens)


def alerta_saude(com_problema: list[str], total_fontes: int) -> bool:
    return enviar(
        f"⚠️ <b>Radar com problema</b>\n\n"
        f"{len(com_problema)} de {total_fontes} fontes falharam ou voltaram vazias "
        f"neste ciclo:\n{', '.join(com_problema)}\n\n"
        "Vale abrir o log da execução no GitHub Actions."
    )


def deve_alertar_saude(com_problema: int, total: int) -> bool:
    """Maioria ESTRITA das fontes, não metade.

    Com metade (`>=`), duas fontes significam que UMA sozinha dispara o
    alerta — e fonte pequena volta vazia num dia fraco sem nada de errado.
    Alerta que dispara sem motivo é pior que alerta nenhum: depois de duas
    ou três vezes ele deixa de ser lido, e aí não serve nem quando o
    problema é real.

        2 fontes -> exige 2      8 fontes -> exige 5
        3 fontes -> exige 2     12 fontes -> exige 7
    """
    if total <= 0:
        return False
    return com_problema > total / 2


def batimento(novas: int, com_problema: list[str], total_fontes: int, hora: str) -> bool:
    estado = (
        "todas as fontes ok"
        if not com_problema
        else f"{len(com_problema)} de {total_fontes} fontes com problema"
    )
    return enviar(
        f"💓 <b>Radar ativo</b>\n\n"
        f"O ciclo rodou às {hora} ({estado}) e encontrou {novas} vaga(s) nova(s).\n\n"
        "<i>Se esta mensagem parar de chegar, o robô parou — não é falta de vaga.</i>"
    )


# ---------------------------------------------------------------------------
# Feedback 👍/👎
# ---------------------------------------------------------------------------

_CHAVE_OFFSET = "telegram_offset"


def parsear_callback(data: str) -> tuple[str, str] | None:
    """Extrai (id_da_vaga, feedback) de 'fb|1|<id>' / 'fb|0|<id>'.

    None quando não reconhece — inclusive pro botão de confirmação
    'fb|ok|-', que substitui o teclado depois do clique e existe justamente
    pra não ser clicável de novo. Função pura, separada da rede de
    propósito: é a parte que vale testar sem simular HTTP.
    """
    partes = (data or "").split("|")
    if len(partes) != 3 or partes[0] != "fb" or partes[1] not in ("1", "0"):
        return None
    return partes[2], ("positivo" if partes[1] == "1" else "negativo")


def processar_feedback(deposito) -> int:
    """Consome os cliques em 👍/👎 desde o ciclo anterior.

    Sem webhook e sem servidor: o próprio cron de 3 em 3 horas faz o papel
    de polling — cada execução pergunta "o que mudou desde a última vez" e
    segue pro ciclo normal. Mesma filosofia de custo zero do resto.

    O offset fica salvo no banco: `getUpdates` com offset=N+1 confirma pro
    Telegram que tudo até N já foi visto. Sem isso o mesmo clique seria
    reprocessado em todo ciclo, pra sempre.
    """
    if not configurado():
        return 0

    payload = {
        "timeout": 0,  # pergunta o que há agora, não fica esperando
        "limit": 100,
        "allowed_updates": json.dumps(["callback_query"]),
    }
    salvo = deposito.ler(_CHAVE_OFFSET)
    if salvo:
        payload["offset"] = str(int(salvo) + 1)

    resultado = _chamar("getUpdates", payload)
    if not resultado or not resultado.get("ok"):
        return 0

    updates = resultado.get("result", [])
    if not updates:
        return 0

    processados = 0
    maior_id = None
    for update in updates:
        if update.get("update_id") is not None:
            maior_id = max(maior_id or 0, update["update_id"])

        callback = update.get("callback_query")
        if not callback:
            continue

        mensagem = callback.get("message") or {}
        chat_id = str((mensagem.get("chat") or {}).get("id", ""))
        if chat_id != str(TELEGRAM_CHAT_ID):
            continue  # bot pessoal, um chat só

        parseado = parsear_callback(callback.get("data", ""))
        if parseado is None:
            _chamar("answerCallbackQuery", {
                "callback_query_id": callback["id"], "text": "Já registrado."
            })
            continue

        vaga_id, feedback = parseado
        deposito.registrar_feedback(vaga_id, feedback)
        processados += 1

        emoji = "👍" if feedback == "positivo" else "👎"
        _chamar("answerCallbackQuery", {
            "callback_query_id": callback["id"], "text": f"Registrado: {emoji}"
        })
        if mensagem.get("message_id"):
            _chamar("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": mensagem["message_id"],
                # Botão único e propositalmente inerte: dá confirmação visual
                # e evita clique duplo mudando o valor sem querer.
                "reply_markup": json.dumps({
                    "inline_keyboard": [[
                        {"text": f"✅ {emoji} registrado", "callback_data": "fb|ok|-"}
                    ]]
                }),
            })

    if maior_id is not None:
        deposito.gravar(_CHAVE_OFFSET, str(maior_id))

    return processados
