"""Os alertas — o único lugar onde o projeto fala com as pessoas.

São quatro, e cada um responde a uma pergunta diferente:

  ALERTA IMEDIATO   "apareceu agora uma vaga que vale interromper seu dia"
  DIGEST DIÁRIO     "isto foi o resto de ontem, ranqueado, leia com café"
  ALERTA DE SAÚDE   "as fontes pararam de responder, o silêncio é falso"
  BATIMENTO         "eu rodei hoje; se este sumir, o robô morreu"

Os dois primeiros vão pra cada perfil; os dois últimos só pra quem opera o
radar (perfil com `admin`). Falha de fonte é problema de operação, não de
quem recebe vaga.

O batimento é o menos óbvio e o mais importante. Sem ele, "o workflow parou
de rodar" e "não apareceu vaga nova" chegam até você exatamente iguais: como
silêncio. Um deles é normal e o outro é o sistema morto.

UM BOT, UM CHAT POR PESSOA. Toda função de envio recebe o chat de destino
explicitamente — não existe "o chat" global. É o que impede a vaga de uma
pessoa de cair na conversa de outra.
"""

import hmac
import html
import json

import requests

from radar.config import TELEGRAM_BOT_TOKEN
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
# Cada perfil pode pedir menos (perfis.Perfil.max_resumo), nunca mais.
LIMITE_VAGAS_DIGEST = 40


def configurado() -> bool:
    """Há token de bot. O destino é por perfil e é conferido na hora de
    enviar — ver ciclo.destino_do_perfil."""
    return bool(TELEGRAM_BOT_TOKEN)


def _chamar(metodo: str, payload: dict) -> dict | None:
    """POST na Bot API, com o cuidado que a credencial exige.

    NUNCA logue `str(e)` nem a URL num except aqui. A URL é
    `.../bot<TOKEN>/sendMessage` — o token está DENTRO dela — e a mensagem
    padrão de erro de conexão do requests/urllib3 inclui a URL completa que
    falhou. Logar isso vaza o token do bot pro log da execução do GitHub
    Actions, que é público quando o repositório é.

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
        corpo = resposta.json()
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
    except ValueError:
        logger.error("Telegram %s falhou: resposta sem JSON", metodo)
        return None

    # HTTP 200 não basta: a Bot API documenta `ok` no corpo como o sinal de
    # sucesso. Marcar "entregue" só pelo status é contar como enviada uma
    # mensagem que a API recusou.
    if not isinstance(corpo, dict) or not corpo.get("ok"):
        logger.error("Telegram %s falhou: resposta com ok=false", metodo)
        return None
    return corpo


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


def enviar(texto: str, chat_id: str, teclado: dict | None = None) -> bool:
    if not configurado() or not chat_id:
        logger.warning("Telegram sem token ou sem destino — mensagem não enviada.")
        return False

    payload = {
        "chat_id": str(chat_id),
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

def _h(texto) -> str:
    """Escapa texto vindo de FORA (portal de vaga) pro parse_mode HTML.

    Sem isto, "Analista de P&D" ou "Vendedor <Externo>" quebram a mensagem
    inteira: a API responde 400 ("can't parse entities"), a vaga não é
    gravada (ver ciclo.py: avisa primeiro, grava depois) e volta no ciclo
    seguinte — pra falhar de novo, pra sempre, contando como falha de
    entrega em todo ciclo. E um título com `<a href=...>` viraria link
    clicável montado por terceiro dentro da mensagem do radar.
    """
    return html.escape(str(texto or ""), quote=True)


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


def _botoes_de_voto(vaga_id: str) -> dict:
    """Botões 👍/👎 na notificação.

    O `callback_data` carrega direção e id porque o clique que volta do
    Telegram não tem como saber de qual vaga é — a mensagem em si não
    basta. Formato curto de propósito: o limite real é 64 bytes e
    "v|s|" + hash md5 já usa 36.

    O PERFIL não vai no botão: vem do chat de onde o clique saiu. Botão é
    texto controlado pelo cliente; o chat é o Telegram que diz.
    """
    return {
        "inline_keyboard": [[
            {"text": "👍", "callback_data": f"v|s|{vaga_id}"},
            {"text": "👎", "callback_data": f"v|n|{vaga_id}"},
        ]]
    }


def alerta_vaga(vaga, chat_id: str, relevancia: int, motivo: str, dias_antiga: int) -> bool:
    """Vaga de alta relevância, na hora."""
    linhas = [f"🚨 <b>Vaga nova</b> — {estrelas(relevancia)}", ""]

    if vaga.antiga(dias_antiga):
        linhas.append(
            f"⚠️ <b>Publicada {_h(vaga.publicado_em_legivel)}</b> — pode já estar preenchida."
        )
        linhas.append("")

    linhas.append(f"<b>{_h(vaga.titulo)}</b>")
    linhas.append(_h(vaga.empresa))
    linhas.append("")
    linhas.append(f"📍 {_h(local_legivel(vaga.local, vaga.modalidade_legivel))}")
    linhas.append(f"🎯 {_h(vaga.senioridade)}")
    if motivo:
        linhas.append(f"✅ {_h(motivo)}")
    linhas.append(f"🔎 {_h(vaga.fonte)}")
    if vaga.publicado_em_legivel and not vaga.antiga(dias_antiga):
        linhas.append(f"📅 {_h(vaga.publicado_em_legivel)}")
    linhas.append("")
    linhas.append(_h(vaga.link))

    return enviar("\n".join(linhas), chat_id, teclado=_botoes_de_voto(vaga.id))


def montar_resumo(vagas: list[tuple], limite: int = LIMITE_VAGAS_DIGEST) -> list[str]:
    """Monta o resumo diário — devolve uma LISTA de mensagens, não uma só.

    Um dia cheio passa dos 4.096 caracteres do Telegram com folga. Quebrar
    em partes numeradas é melhor que estourar (a API recusa a mensagem
    inteira) ou truncar (some vaga sem ninguém saber).
    """
    if not vagas:
        return []

    limite = max(1, min(limite, LIMITE_VAGAS_DIGEST))
    total = len(vagas)
    vagas = vagas[:limite]

    linhas = [
        f'{estrelas(rel or 0)} <a href="{_h(link)}">{_h(titulo)}</a>\n'
        f'    {_h(empresa or "empresa não informada")} · '
        f'{_h(local_legivel(local, modalidade))} · {_h(fonte)}'
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


def enviar_resumo(vagas: list[tuple], chat_id: str, limite: int = LIMITE_VAGAS_DIGEST) -> bool:
    """Só devolve True se TODAS as partes confirmarem.

    Quem chama só esvazia a fila com True — então falha parcial mantém tudo
    pendente, inclusive a parte que já saiu. Preferir repetir uma parte a
    perder vaga que nunca chegou.
    """
    mensagens = montar_resumo(vagas, limite)
    if not mensagens:
        return True
    return all(enviar(m, chat_id) for m in mensagens)


def alerta_saude(chat_id: str, com_problema: list[str], total_fontes: int) -> bool:
    return enviar(
        f"⚠️ <b>Radar com problema</b>\n\n"
        f"{len(com_problema)} de {total_fontes} fontes falharam ou voltaram vazias "
        f"neste ciclo:\n{_h(', '.join(com_problema))}\n\n"
        "Vale abrir o log da execução no GitHub Actions.",
        chat_id,
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


def batimento(
    chat_id: str, novas: int, com_problema: list[str], total_fontes: int, hora: str,
    perfis_ativos: int = 1,
) -> bool:
    estado = (
        "todas as fontes ok"
        if not com_problema
        else f"{len(com_problema)} de {total_fontes} fontes com problema"
    )
    quem = f" para {perfis_ativos} perfil(is)" if perfis_ativos > 1 else ""
    return enviar(
        f"💓 <b>Radar ativo</b>\n\n"
        f"O ciclo rodou às {hora} ({estado}) e encontrou {novas} vaga(s) "
        f"nova(s){quem}.\n\n"
        "<i>Se esta mensagem parar de chegar, o robô parou — não é falta de vaga.</i>",
        chat_id,
    )


# ---------------------------------------------------------------------------
# O que chega do Telegram: 👍/👎, convites e comandos
# ---------------------------------------------------------------------------

_CHAVE_OFFSET = "telegram_offset"


def parsear_callback(data: str) -> tuple[str, str] | None:
    """Extrai (id_da_vaga, feedback) de 'v|s|<id>' / 'v|n|<id>'.

    None quando não reconhece — inclusive pro botão de confirmação
    'v|ok|-', que substitui o teclado depois do clique e existe justamente
    pra não ser clicável de novo. Função pura, separada da rede de
    propósito: é a parte que vale testar sem simular HTTP.
    """
    partes = (data or "").split("|")
    if len(partes) != 3 or partes[0] != "v" or partes[1] not in ("s", "n"):
        return None
    return partes[2], ("positivo" if partes[1] == "s" else "negativo")


def parsear_comando(texto: str) -> tuple[str, str] | None:
    """'/start abc' -> ('start', 'abc'). '/pausar@MeuBot' -> ('pausar', '').

    None pra texto que não é comando. Função pura, testável sem rede.
    """
    texto = (texto or "").strip()
    if not texto.startswith("/"):
        return None
    cabeca, _, resto = texto.partition(" ")
    comando = cabeca[1:].split("@", 1)[0].lower()
    if not comando:
        return None
    return comando, resto.strip()


def _perfil_do_convite(token: str, perfis: list) -> object | None:
    """Comparação em tempo constante: o convite é o único segredo do
    vínculo, e `==` sai no primeiro caractere diferente."""
    for perfil in perfis:
        if perfil.convite and hmac.compare_digest(perfil.convite, token or ""):
            return perfil
    return None


def apresentacao(perfil) -> str:
    """O que o radar desta pessoa procura, em linguagem de gente. Vai na
    mensagem de boas-vindas e no /status — "é isto que seu radar faz"."""
    r = perfil.regras
    cargos = (r.cargos_fortes or r.cargos_ambiguos)[:6]
    mais = len(r.cargos_fortes) + len(r.cargos_ambiguos) - len(cargos)
    modos = [m for m, ok in (("presencial", r.aceita_presencial),
                             ("híbrido", r.aceita_hibrido),
                             ("remoto", r.aceita_remoto)) if ok]
    linhas = [
        f"🎯 <b>Cargos:</b> {_h(', '.join(cargos))}" + (f" e mais {mais}" if mais > 0 else ""),
    ]
    if r.aceita_presencial or r.aceita_hibrido:
        linhas.append(f"📍 <b>Cidades:</b> {_h(', '.join(r.cidades))} ({_h(perfil.uf)})")
    linhas.append(f"🏢 <b>Modalidade:</b> {', '.join(modos)}")
    linhas.append(
        f"📋 <b>Resumo diário:</b> a partir das {perfil.hora_resumo:02d}h, até "
        f"{perfil.max_resumo} vagas"
    )
    linhas.append(
        "🚨 <b>Alerta na hora:</b> "
        + (f"vagas com nota {perfil.limiar_imediato}/10 ou mais"
           if perfil.alerta_imediato else "desligado")
    )
    return "\n".join(linhas)


AJUDA = (
    "Comandos:\n"
    "/status — o que o seu radar procura\n"
    "/pausar — para de enviar vagas\n"
    "/retomar — volta a enviar\n\n"
    "<i>O radar roda algumas vezes por dia, e as respostas chegam na rodada "
    "seguinte — não na hora.</i>"
)


def _responder_mensagem(mensagem: dict, deposito, perfis: list, chats: dict) -> bool:
    """Trata UMA mensagem de texto. Devolve True se era comando reconhecido.

    `chats` mapeia chat_id -> perfil pros chats já conhecidos.
    """
    chat = mensagem.get("chat") or {}
    if chat.get("type") != "private":
        return False  # bot de conversa privada; grupo é ignorado
    chat_id = str(chat.get("id", ""))
    comando = parsear_comando(mensagem.get("text", ""))
    if not chat_id or comando is None:
        return False
    nome, argumento = comando
    perfil = chats.get(chat_id)

    if nome == "start" and argumento:
        convidado = _perfil_do_convite(argumento, perfis)
        if convidado is None:
            enviar("Convite não reconhecido. Confira o link com quem te convidou.", chat_id)
            return True
        situacao = deposito.vincular(convidado.id, chat_id)
        if situacao in ("vinculado", "ja_vinculado"):
            if situacao == "vinculado":
                # Marca o começo da estreia deste perfil: até ela acabar,
                # nada alerta na hora — ver ciclo.perfil_em_estreia.
                from datetime import datetime, timezone
                deposito.gravar(
                    f"inicio:{convidado.id}",
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
                chats[chat_id] = convidado
                logger.info("Perfil '%s' vinculado a um chat.", convidado.id)
            enviar(
                f"✅ <b>Olá, {_h(convidado.nome)}! Seu radar de vagas está ligado.</b>\n\n"
                f"{apresentacao(convidado)}\n\n"
                "Nas primeiras ~30 horas tudo vai pro resumo diário, sem alerta na "
                "hora: é quando o radar encontra o que já estava publicado.\n\n"
                + AJUDA,
                chat_id,
            )
        elif situacao == "perfil_ocupado":
            logger.warning("Convite do perfil '%s' usado por um segundo chat.", convidado.id)
            enviar("Este convite já foi usado em outra conversa. Peça um novo a quem te convidou.",
                   chat_id)
        else:  # chat_ocupado
            enviar("Esta conversa já está ligada a outro radar.", chat_id)
        return True

    if perfil is None:
        if nome == "start":
            enviar("Este radar é só para convidados. Use o link de convite que você recebeu.",
                   chat_id)
            return True
        return False  # chat desconhecido: não conversa

    if nome == "pausar":
        deposito.gravar(f"pausado:{perfil.id}", "1")
        enviar("⏸️ Radar pausado. Use /retomar para voltar a receber vagas.", chat_id)
    elif nome == "retomar":
        deposito.apagar(f"pausado:{perfil.id}")
        enviar("▶️ Radar retomado.", chat_id)
    elif nome == "status":
        pausado = deposito.ler(f"pausado:{perfil.id}") == "1"
        fila = len(deposito.pendentes_do_digest(perfil.id))
        enviar(
            ("⏸️ <b>Pausado</b>\n\n" if pausado else "")
            + f"{apresentacao(perfil)}\n\n📥 Na fila do próximo resumo: {fila} vaga(s)",
            chat_id,
        )
    else:  # /start sem convite, /ajuda, /help e qualquer outro
        enviar(AJUDA, chat_id)
    return True


def processar_atualizacoes(deposito, perfis: list, chats: dict) -> dict:
    """Consome o que chegou do Telegram desde o ciclo anterior.

    Sem webhook e sem servidor: o próprio cron faz o papel de polling —
    cada execução pergunta "o que mudou desde a última vez" e segue pro
    ciclo normal. Mesma filosofia de custo zero do resto. O preço é que
    resposta a comando chega na rodada seguinte, não na hora; as mensagens
    do bot dizem isso.

    O offset fica salvo no banco: `getUpdates` com offset=N+1 confirma pro
    Telegram que tudo até N já foi visto. Sem isso o mesmo clique seria
    reprocessado em todo ciclo, pra sempre.

    `chats` (chat_id -> perfil) é atualizado no lugar quando um convite
    vincula um chat novo, pro resto do ciclo já enxergá-lo.
    """
    contagem = {"votos": 0, "comandos": 0}
    if not configurado():
        return contagem

    payload = {
        "timeout": 0,  # pergunta o que há agora, não fica esperando
        "limit": 100,
        "allowed_updates": json.dumps(["callback_query", "message"]),
    }
    salvo = deposito.ler(_CHAVE_OFFSET)
    if salvo:
        payload["offset"] = str(int(salvo) + 1)

    resultado = _chamar("getUpdates", payload)
    if not resultado:
        return contagem

    updates = resultado.get("result", [])
    maior_id = None
    for update in updates:
        if update.get("update_id") is not None:
            maior_id = max(maior_id or 0, update["update_id"])

        if update.get("message"):
            if _responder_mensagem(update["message"], deposito, perfis, chats):
                contagem["comandos"] += 1
            continue

        callback = update.get("callback_query")
        if not callback:
            continue

        mensagem = callback.get("message") or {}
        chat_id = str((mensagem.get("chat") or {}).get("id", ""))
        perfil = chats.get(chat_id)
        if perfil is None:
            continue  # clique de um chat que não é de perfil nenhum

        parseado = parsear_callback(callback.get("data", ""))
        if parseado is None:
            _chamar("answerCallbackQuery", {
                "callback_query_id": callback["id"], "text": "Já registrado."
            })
            continue

        vaga_id, feedback = parseado
        # Só grava se a vaga foi oferecida a ESTE perfil — ver
        # deposito.registrar_feedback.
        if not deposito.registrar_feedback(perfil.id, vaga_id, feedback):
            continue
        contagem["votos"] += 1

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
                        {"text": f"✅ {emoji} registrado", "callback_data": "v|ok|-"}
                    ]]
                }),
            })

    if maior_id is not None:
        deposito.gravar(_CHAVE_OFFSET, str(maior_id))

    return contagem


def nome_do_bot() -> str | None:
    """@usuario do bot, pra montar o link de convite."""
    resposta = _chamar("getMe", {})
    if not resposta:
        return None
    return (resposta.get("result") or {}).get("username")
