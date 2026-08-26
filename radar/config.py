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
# "Analista de Qualidade" ficou de FORA de propósito: puxa vaga de controle
# de qualidade industrial (inspeção de peça em linha de produção), que é
# outra profissão. Se um dia interessar, entra como cargo ambíguo (nível 2),
# não como forte.
CARGOS_FORTES = [
    # PCP / Planejamento de Produção
    "Analista de PCP",
    "Analista de Planejamento e Controle de Produção",
    "Analista de Planejamento de Produção",
    "Analista de Programação e Sequenciamento",
    "Planejador de Produção",
    "Programador de Produção",
    "Coordenador de PCP",
    "Supervisor de PCP",
    "Production Planning Analyst",
    "Production Planner",

    # Logística, Frotas e Roteirização
    "Analista de Logística",
    "Analista Logístico",
    "Analista de Roteirização",
    "Analista de Gestão de Frotas",
    "Analista de Frotas",
    "Analista de Transportes",
    "Analista de Operações Logísticas",
    "Analista de Suprimentos",
    "Analista de Supply Chain",
    "Coordenador de Logística",
    "Supervisor de Logística",
    "Logistics Analyst",
    "Fleet Analyst",
    "Supply Chain Analyst",

    # Business Intelligence e Indicadores
    "Analista de BI",
    "Analista de Business Intelligence",
    "Analista de Indicadores",
    "Analista de Dados",
    "Business Intelligence Analyst",
    "BI Analyst",
    "Data Analyst",

    # Auditoria, Conformidade e Certificação (automotivo)
    "Analista de Auditoria",
    "Analista de Auditoria e Conformidade",
    "Analista de Conformidade",
    "Analista de Compliance",
    "Analista de Homologação de Fornecedores",
    "Analista de Certificações",
    "Analista da Qualidade de Fornecedores",

    # Multas, Prazos e Processos Administrativos
    "Analista de Multas",
    "Analista Administrativo de Frotas",
    "Analista de Processos Administrativos",

    # Melhoria Contínua e Excelência Operacional
    "Analista de Melhoria Contínua",
    "Especialista em Melhoria Contínua",
    "Analista de Processos",
    "Especialista em Processos",
    "Analista de Excelência Operacional",
    "Especialista em Excelência Operacional",
    "Analista Lean Six Sigma",
    "Especialista Lean Six Sigma",
    "Continuous Improvement Analyst",
    "Operational Excellence Analyst",
    "Process Analyst",
]

# NÍVEL 2 — cargo ambíguo: o título também é usado em vaga de outra área
# inteira ("Analista de Planejamento" existe em financeiro, RH, TI...).
# Só conta se o título TAMBÉM tiver um QUALIFICADOR_DOMINIO junto.
CARGOS_AMBIGUOS = [
    "Analista de Planejamento",
    "Analista Administrativo",
    "Assistente Administrativo",
    "Técnico Administrativo",
    "Técnico de Processos",
    "Técnico de Produção",
    "Analista de Operações",
    "Analista de Performance",
    "Business Analyst",
    "Analista de Negócios",
]

# O que precisa aparecer JUNTO no título pra confirmar o domínio quando o
# cargo é ambíguo.
QUALIFICADORES_DOMINIO = [
    "processos",
    "produção",
    "pcp",
    "logística",
    "logístico",
    "frotas",
    "roteirização",
    "transporte",
    "suprimentos",
    "supply chain",
    "estoque",
    "armazém",
    "indicadores",
    "power bi",
    "bi",
    "dados",
    "auditoria",
    "conformidade",
    "compliance",
    "mmog",
    "multas",
    "lean",
    "six sigma",
    "melhoria contínua",
    "excelência operacional",
    "operacional",
    "sequenciamento",
]

# NÍVEL 3 — ferramenta/metodologia como núcleo do título ("Analista de
# Power BI", "Especialista MMOG/LE"). Espelho da regra do nível 2: lá o
# cargo é ambíguo e pede domínio, aqui a ferramenta é ambígua e pede cargo.
#
# "SAP" ficou de FORA de propósito. É ERP genérico usado por TI, Financeiro
# e RH — mesmo com qualificador de cargo junto ("Consultor SAP ABAP"), a
# esmagadora maioria é vaga técnica/funcional de SAP, não do domínio de
# interesse. "MMOG/LE" entra porque, ao contrário de SAP, não tem uso fora
# de auditoria/conformidade automotiva.
FERRAMENTAS_TITULO = [
    "Power BI",
    "MMOG/LE",
    "WMS",
    "TMS",
]

# Palavra de cargo que confirma que a vaga de ferramenta é de análise, não
# de desenvolvimento. "desenvolvedor"/"developer"/"engenheiro" ficam FORA
# de propósito — é o que mantém vaga de dev fora do radar.
QUALIFICADORES_CARGO = [
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
# TERMOS DE BUSCA — o que é digitado em cada fonte
# ---------------------------------------------------------------------------
# Os termos são DERIVADOS dos cargos acima, nunca mantidos numa lista
# paralela à mão.
#
# No projeto que deu origem a este, as duas listas eram independentes e
# divergiram em silêncio: metade dos cargos filtrados nunca chegava a ser
# buscada de verdade, e as vagas correspondentes só apareciam por acidente,
# quando outro termo as trazia junto. Derivando, cargo novo vira busca nova
# automaticamente — a divergência deixa de ser possível.

# Termos mais largos que o cargo exato: dão rede maior na busca sem afrouxar
# o filtro (o título ainda precisa bater um cargo pra ser aprovado).
TERMOS_EXTRA = [
    "power bi",
    "lean six sigma",
    "melhoria contínua",
    "excelência operacional",
    "pcp",
    "planejamento e controle de produção",
    "roteirização",
    "gestão de frotas",
    "mmog",
    "homologação de fornecedores",
    "kaizen",
    "indicadores logísticos",
]

TERMOS_BUSCA = sorted(
    {c.lower() for c in CARGOS_FORTES + CARGOS_AMBIGUOS} | set(TERMOS_EXTRA)
)

# Termos que rodam em TODO ciclo, fora do rodízio.
#
# São os títulos que mais aparecem no que interessa de verdade. No projeto
# original todos os termos disputavam vez por igual num rodízio alfabético:
# com 44 termos e 10 por ciclo, uma volta completa levava 13 horas, e o
# termo que dava nome ao perfil inteiro era buscado uma vez a cada 13h —
# uma vaga real publicada logo depois da passagem dele ficava invisível meio
# dia. Tirar os principais do rodízio custa alguns minutos por ciclo e
# resolve isso.
TERMOS_PRIORITARIOS = [
    "analista de pcp",
    "analista de logística",
    "analista de processos",
    "analista de melhoria contínua",
    "analista de bi",
]

# Tamanho do BLOCO do rodízio por ciclo — não é o total de termos. O ciclo
# roda os prioritários + um bloco deste tamanho, e avança o bloco no ciclo
# seguinte (posição salva no banco). Isso desacopla o custo de cada ciclo do
# tamanho da lista: dobrar TERMOS_BUSCA dobra quantos ciclos até cobrir tudo
# de novo, não o custo de um ciclo.
TERMOS_POR_CICLO = 10


# ---------------------------------------------------------------------------
# LOCALIZAÇÃO
# ---------------------------------------------------------------------------
# Corredor oeste de SP a partir de Itapetininga (onde você mora) até o raio
# de deslocamento viável. Vaga presencial ou híbrida só passa se estiver
# numa destas cidades.
CIDADES = [
    "Itapetininga",
    "Sorocaba",
    "Tatuí",
    "Boituva",
    "Porto Feliz",
    "Cesário Lange",
    "Cerquilho",
    "Tietê",
    "Laranjal Paulista",
    "Conchas",
    "Pilar do Sul",
    "São Miguel Arcanjo",
    "Guareí",
    "Angatuba",
    "Itapeva",
    "Itararé",
    "Capão Bonito",
    "Buri",
    "Taquarituba",
    "Avaré",
    "Cerqueira César",
    "Botucatu",
    "Itaí",
    "Paranapanema",
    "Votorantim",
    "Salto de Pirapora",
    "Araçoiaba da Serra",
    "Alumínio",
    "Piedade",
]

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
MERCADOS_REMOTO_ACEITOS = ["Brasil"]


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
# fechou: o digest nunca dispara, e a fila cresce pra sempre. Foi exatamente
# assim que 408 vagas ficaram presas no projeto original, sem nenhum digest
# ter sido enviado uma única vez.
#
# 9 UTC = o ciclo das 09:00 termina por volta das 10:20 UTC = 07:20 em
# Brasília. Chega de manhã, com a lista da véspera pronta pra revisar.
DIGEST_HORA_UTC = 9

# Vaga publicada há mais de N dias não dispara alerta imediato: vai pro
# digest com aviso de que pode já estar preenchida. Nunca é descartada —
# "duplicar é aceitável, perder não".
DIAS_PARA_VAGA_ANTIGA = 30


# ---------------------------------------------------------------------------
# INFRAESTRUTURA
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
