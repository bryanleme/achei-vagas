"""Perfil de busca: o que procurar, onde aceitar, e quando avisar.

Este é o único arquivo que muda quando o alvo de carreira muda. Nenhuma
regra de negócio mora aqui — só dado. Se você precisar editar qualquer
outro arquivo pra trocar de cargo ou de cidade, é bug de desenho.
"""

import os

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# CARGO — filtro em três níveis de confiança
# ---------------------------------------------------------------------------
# O filtro lê SÓ O TÍTULO da vaga, nunca a descrição. É essa restrição que
# mantém o ruído perto de zero. O preço simétrico, que vale conhecer: vaga
# com título genérico e conteúdo da área ("Analista Comercial JR" numa vaga
# que é de BI) sempre escapa. Aceito de propósito — abrir a descrição de
# toda vaga multiplicaria o custo do ciclo por dez pra recuperar poucos
# casos.

# NÍVEL 1 — cargo forte: o título sozinho já identifica a área. Passa sem
# precisar de mais nada.
#
# ALVO TROCADO em 28/11/2026: o perfil era PCP, logística, frotas e Melhoria
# Contínua; o usuário passou a Analista de Dados (Sabesp, set/2026) e o alvo
# agora é dados e BI na plataforma Microsoft. Logística e PCP continuam como
# plano B, mas só como cargo AMBÍGUO (nível 2): "Analista de Logística"
# sozinho deixou de passar; "Analista de Logística e Indicadores" passa.
#
# "Cientista de Dados" e "Engenheiro de Dados" ficaram de FORA: pedem
# Python/SQL avançados, que hoje são o ponto fraco declarado do perfil.
# "Desenvolvedor" entra só colado em BI ou Power Platform — "Desenvolvedor"
# genérico continua sendo vaga de software.
CARGOS_FORTES = [
    # Dados e BI
    "Analista de Dados",
    "Analista de BI",
    "Analista de Business Intelligence",
    "Analista de Inteligência de Dados",
    "Analista de Inteligência de Negócios",
    "Analista de Analytics",
    "Analista de Data Analytics",
    "Analista de Indicadores",
    "Analista de Dados e Processos",
    "Especialista em BI",
    "Especialista em Power BI",
    "Consultor de BI",
    "Consultor Power BI",
    "Desenvolvedor BI",
    "Desenvolvedor de BI",
    "Desenvolvedor Power BI",
    "Data Analyst",
    "BI Analyst",
    "Business Intelligence Analyst",
    "BI Developer",
    "Power BI Developer",

    # Power Platform (apps e automação na Microsoft)
    "Analista Power Platform",
    "Desenvolvedor Power Platform",
    "Desenvolvedor Power Apps",
]

# NÍVEL 2 — cargo ambíguo: o título também é usado em vaga de outra área
# inteira ("Analista de Planejamento" existe em financeiro, RH, TI...).
# Só conta se o título TAMBÉM tiver um QUALIFICADOR_DOMINIO junto.
#
# PCP, logística e processos moraram no nível 1 até 28/11/2026. Aqui, com
# qualificadores só de dados, eles viram o plano B pedido pelo usuário: vaga
# de operação que é, no título, uma vaga de dados.
CARGOS_AMBIGUOS = [
    "Analista de Planejamento",
    "Analista Administrativo",
    "Assistente Administrativo",
    "Técnico Administrativo",
    "Analista de Operações",
    "Analista de Performance",
    "Business Analyst",
    "Analista de Negócios",
    # Plano B: operação e cadeia de suprimentos
    "Analista de PCP",
    "Analista de Planejamento e Controle de Produção",
    "Analista de Planejamento de Produção",
    "Planejador de Produção",
    "Analista de Logística",
    "Analista Logístico",
    "Analista de Supply Chain",
    "Analista de Suprimentos",
    "Analista de Transportes",
    "Analista de Operações Logísticas",
    "Analista de Frotas",
    "Analista de Processos",
    "Analista de Melhoria Contínua",
    "Analista de Excelência Operacional",
]

# O que precisa aparecer JUNTO no título pra confirmar o domínio quando o
# cargo é ambíguo. Só vocabulário de DADOS: um qualificador de logística
# aqui ("logística") faria "Analista de Logística" aprovar a si mesmo e
# desfaria o plano B.
QUALIFICADORES_DOMINIO = [
    "dados",
    "bi",
    "power bi",
    "business intelligence",
    "indicadores",
    "kpi",
    "analytics",
    "dashboards",
    "inteligência",
    "power platform",
]

# NÍVEL 3 — ferramenta como núcleo do título ("Analista Power BI",
# "Especialista Tableau"). Espelho da regra do nível 2: lá o cargo é ambíguo
# e pede domínio, aqui a ferramenta é ambígua e pede cargo.
#
# "SQL" ficou de FORA de propósito: "Analista SQL" / "Analista de Banco de
# Dados SQL Server" é quase sempre DBA ou infraestrutura, outra profissão.
# WMS e TMS saíram junto com a logística do nível 1.
FERRAMENTAS_DE_CARGO = [
    "Power BI",
    "Power Platform",
    "Power Apps",
    "Tableau",
    "Qlik",
    "Looker",
]

# Palavra de cargo que confirma que a vaga de ferramenta é de análise, não
# de desenvolvimento. "desenvolvedor"/"developer"/"engenheiro" ficam FORA
# de propósito — é o que mantém vaga de dev genérico fora do radar. (As
# vagas de "Desenvolvedor Power BI" entram pelo nível 1, nominalmente.)
PALAVRAS_DE_CARGO = [
    "analista",
    "analyst",
    "especialista",
    "specialist",
    "consultor",
    "consultant",
    "coordenador",
    "supervisor",
    "planejador",
    "técnico",
    "assistente",
]


# ---------------------------------------------------------------------------
# EXCLUSÕES — o que derruba a vaga por mais que o cargo bata
# ---------------------------------------------------------------------------
# Verificado ANTES de qualquer outra regra: se um destes aparece no título, a
# vaga é descartada mesmo com cargo forte e cidade perfeita.
#
# PCD: vaga reservada a pessoas com deficiência, sob a Lei de Cotas. No
# Brasil a marcação no TÍTULO é a convenção para posição reservada — vaga
# aberta a todos não escreve isso no nome.
#
# MEDIDO antes de escolher o critério: a API da Gupy tem um campo próprio
# `disabilities`, e a tentação era usá-lo por ser dado estruturado. Ele vem
# `True` em 25 de 30 vagas — significa "aceita candidato PCD", que quase toda
# vaga aceita por obrigação legal, e NÃO "exclusiva para PCD". Filtrar por ele
# teria descartado 83% das vagas em silêncio. O título é o único sinal
# confiável, e é o que se usa aqui.
#
# Custo aceito, registrado: uma vaga que mencione PCD no título sem ser
# exclusiva seria perdida. É o único lugar do projeto onde "perder" foi
# escolhido de propósito sobre "duplicar" — porque aqui a vaga não serve, e
# recebê-la só gasta a atenção que o alerta imediato precisa preservar.
#
# "Analista de Dados" tem homônimos que são outra profissão: cadastro e
# digitação (entrada de dados) e proteção de dados (LGPD, jurídico).
EXCLUSOES_TITULO = [
    "pcd",
    "pessoa com deficiência",
    "pessoas com deficiência",
    "portador de deficiência",
    "portadores de deficiência",
    "candidatos com deficiência",
    "cadastro",
    "cadastral",
    "cadastrais",
    "digitação",
    "digitador",
    "proteção de dados",
    "privacidade de dados",
    # MEDIDO em 28/11/2026 no Remotar: 10 de 50 resultados de "power bi"
    # eram o mesmo anúncio da BairesDev ("Work From Home Data Analyst
    # (Power BI & ...)") repetido com ferramentas diferentes — vaga em
    # inglês, para contratação internacional. Inglês básico é a lacuna
    # declarada do perfil, e dez cópias soterrariam o resumo.
    "work from home",
]


# ---------------------------------------------------------------------------
# TERMOS DE BUSCA — o que é digitado em cada fonte
# ---------------------------------------------------------------------------
# Os termos são DERIVADOS dos cargos acima, nunca mantidos numa lista
# paralela à mão.
#
# Duas listas independentes divergem em silêncio: cargo filtrado que nunca
# é buscado de verdade, e vaga correspondente que só aparece por acidente,
# quando outro termo a traz junto. Derivando, cargo novo vira busca nova
# automaticamente — a divergência deixa de ser possível.

# Termos mais largos que o cargo exato: dão rede maior na busca sem afrouxar
# o filtro (o título ainda precisa bater um cargo pra ser aprovado).
TERMOS_EXTRA = [
    "power bi",
    "business intelligence",
    "analytics",
    "inteligência de dados",
    "power platform",
    "indicadores",
]

TERMOS_DE_BUSCA = sorted(
    {c.lower() for c in CARGOS_FORTES + CARGOS_AMBIGUOS} | set(TERMOS_EXTRA)
)

# Termos que rodam em TODO ciclo, fora do rodízio.
#
# São os títulos que mais aparecem no que interessa de verdade. Num rodízio
# em que todos disputam vez por igual, com ~50 termos e 10 por ciclo, uma
# volta completa leva 15 horas, e o termo que dá nome ao perfil inteiro
# seria buscado uma vez a cada 15h. Tirar os principais do rodízio custa
# alguns minutos por ciclo e resolve isso.
TERMOS_PRIORITARIOS = [
    "analista de dados",
    "analista de bi",
    "power bi",
    "business intelligence",
    "desenvolvedor power bi",
]

# Tamanho do BLOCO do rodízio por ciclo — não é o total de termos. O ciclo
# roda os prioritários + um bloco deste tamanho, e avança o bloco no ciclo
# seguinte (posição salva no banco). Isso desacopla o custo de cada ciclo do
# tamanho da lista: dobrar TERMOS_DE_BUSCA dobra quantos ciclos até cobrir tudo
# de novo, não o custo de um ciclo.
TERMOS_POR_RODADA = 10

# Versão do perfil. TROCAR quando o alvo mudar de verdade (cargos, cidades):
# o ciclo compara com a versão gravada no banco e, se for diferente, religa a
# varredura inicial — os termos novos trazem de uma vez tudo o que já estava
# publicado, e isso tem de ir pro resumo, não virar rajada de alerta. Ver
# ciclo.reiniciar_varredura_se_o_perfil_mudou.
PERFIL_VERSAO = "2026-11-28-dados-bi"


# ---------------------------------------------------------------------------
# LOCALIZAÇÃO
# ---------------------------------------------------------------------------
# Vagas presenciais e híbridas só interessam nestas duas cidades. A
# modalidade híbrida continua sendo tratada como não remota, portanto precisa
# casar uma destas cidades do mesmo jeito que a presencial.
CIDADES_REGIAO = [
    "Itapetininga",
    "Sorocaba",
]

# Mantido vazio por compatibilidade com o ranking e o ciclo. Nenhuma cidade
# fora das duas acima deve passar pelo filtro de vaga presencial ou híbrida.
CIDADES_DISTANTES = []

CIDADES = CIDADES_REGIAO + CIDADES_DISTANTES

# Estado usado pelas fontes que filtram por UF inteira (Gupy, Catho).
# O recorte fino pra CIDADES acontece depois, no filtro local.
ESTADO_BUSCA = "São Paulo"
UF_BUSCA = "SP"

# Cidade-âncora das fontes que buscam por RAIO (Indeed). Sorocaba cobre bem
# o corredor oeste; ancorar em "São Paulo" puxaria pra Grande São Paulo, que
# é a região errada.
CIDADE_ANCORA = "Sorocaba, SP"

# Vaga remota é sempre aceita, independentemente de CIDADES.
ACEITA_REMOTO = True

# Mercado que a vaga remota precisa aceitar quando o texto DECLARA um escopo
# geográfico ("Remote — US only", "Remoto — LATAM"). Vaga remota SEM escopo
# declarado no texto — que é a maioria — passa normalmente: não há base pra
# rejeitar.
#
# Só Brasil: vaga remota pra outro país não serve, e deixar a lista aberta
# só encheria o digest de vaga em inglês pro mercado americano.
PAISES_REMOTO_PERMITIDOS = ["Brasil"]


# ---------------------------------------------------------------------------
# SENIORIDADE
# ---------------------------------------------------------------------------
# Nenhum destes exclui vaga — só mexe na posição do ranking.
NIVEIS_ALVO = {"Júnior", "Pleno"}
NIVEIS_ACIMA_DO_ALVO = {"Sênior", "Especialista", "Liderança"}


# ---------------------------------------------------------------------------
# ALERTAS
# ---------------------------------------------------------------------------
# Vaga com relevância >= este valor notifica NA HORA. Abaixo disso, entra na
# fila do digest diário. Nada é descartado nos dois casos.
#
# 7 não é chute — é onde a escala de relevancia.py separa exatamente o que
# você quer saber na hora do que pode esperar o resumo da manhã:
#
#   cargo direto + cidade do corredor + nível não dito  = 4+3+1 = 8  -> agora
#   cargo direto + remoto confirmado Brasil             = 4+2+1 = 7  -> agora
#   cargo direto + remoto sem escopo declarado          = 4+1+1 = 6  -> digest
#   ferramenta + cargo + remoto genérico                = 2+1+1 = 4  -> digest
#
# Ou seja: presencial na região OU remoto confirmado pro Brasil interrompe
# seu dia. Remoto genérico, que aparece às centenas, não.
#
# REVISAR depois de uma semana de dado real: este valor foi calibrado contra
# a escala, não contra o histórico (que ainda não existe). Se o alerta
# imediato estiver disparando demais, sobe pra 8; se estiver mudo, desce
# pra 6.
LIMIAR_ALERTA_IMEDIATO = 7

# Hora UTC A PARTIR DA QUAL o digest diário pode sair. A regra é "ainda não
# enviei hoje E já passou desta hora" — nunca "a hora é exatamente esta".
#
# A diferença não é estilo. Com janela exata de uma hora, um ciclo que
# começa às 09:00 e dura 80 minutos termina às 10:20, quando a janela já
# fechou: o digest nunca dispara, e a fila cresce pra sempre sem que nenhum
# resumo seja enviado uma única vez.
#
# 9 UTC = o ciclo das 09:00 termina por volta das 10:20 UTC = 07:20 em
# Brasília. Chega de manhã, com a lista da véspera pronta pra revisar.
HORA_RESUMO_UTC = 9

# Vaga publicada há mais de N dias não dispara alerta imediato: vai pro
# digest com aviso de que pode já estar preenchida. Nunca é descartada —
# "duplicar é aceitável, perder não".
DIAS_PARA_VAGA_ANTIGA = 30


# ---------------------------------------------------------------------------
# INFRAESTRUTURA
# ---------------------------------------------------------------------------
# .strip() não é zelo excessivo — é correção de um bug real.
#
# MEDIDO na primeira execução no GitHub Actions: TODAS as chamadas ao
# Telegram voltaram HTTP 404, e o workflow terminou VERDE. A causa foi um
# "\r" invisível no fim do token, grudado por um pipe do PowerShell na hora
# de gravar a secret.
#
# O modo de falha é traiçoeiro: a URL da Bot API é `/bot<TOKEN>/sendMessage`,
# então um caractere a mais no token não dá "credencial inválida" — dá 404,
# que se lê como "endpoint errado" e manda você investigar o lugar errado.
#
# Um espaço, um \r ou um \n sobrando é o erro mais fácil de cometer ao colar
# credencial em .env ou em campo de secret. Aparar aqui custa nada e fecha a
# classe inteira, venha o valor de onde vier.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
