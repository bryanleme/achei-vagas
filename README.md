# Achei!! Vagas

Monitor automatizado de vagas para um perfil específico: **PCP, Logística e
Frotas, BI e Indicadores, Auditoria e Conformidade, Melhoria Contínua** —
no corredor oeste de São Paulo (Itapetininga, Sorocaba, Botucatu, Avaré,
Itapeva e região) mais vagas remotas no Brasil.

Roda sozinho no GitHub Actions a cada 3 horas. Sem servidor, sem custo.
Avisa no Telegram.

```
buscar → filtrar → pontuar → deduplicar → avisar → gravar
```

## Como ele decide

**O filtro lê só o TÍTULO da vaga, nunca a descrição.** É essa restrição que
mantém o ruído perto de zero — num ciclo real, 4.169 vagas brutas viraram 25
notificações. O preço simétrico está registrado no código e é medido a cada
ciclo: vaga com título genérico e conteúdo da área sempre escapa.

Cargo passa por três níveis de confiança:

| Nível | Exemplo | Regra |
|---|---|---|
| Forte | `Analista de PCP` | passa sozinho |
| Ambíguo | `Analista Administrativo` | só com qualificador de domínio junto |
| Ferramenta | `Power BI` | só com palavra de cargo junto |

Nada aprova por palavra-chave solta.

Localização: cidade do corredor (com **guarda de UF** — `Piedade - PE` não é
`Piedade/SP`) ou vaga remota. Remota que declara mercado estrangeiro é
descartada; remota sem mercado declarado passa, exceto vinda de agregador
estrangeiro, onde a ausência significa o contrário.

Relevância de 0 a 10, soma de sinais conhecidos, sem IA:

```
cargo (0-4) + domínio extra (0-1) + senioridade (-1 a 2) + local (1-3) - antiga (2)
```

Vaga presencial na região vale **mais** que remota, não menos: remota aparece
às centenas e some no ruído; presencial na região é rara.

## Os quatro alertas

| Alerta | Quando | Responde |
|---|---|---|
| 🚨 Imediato | relevância ≥ 7 | "apareceu algo que vale interromper seu dia" |
| 📋 Resumo | 07h20 de Brasília | "isto foi o resto de ontem, ranqueado" |
| ⚠️ Saúde | maioria estrita das fontes falhou | "o silêncio é falso" |
| 💓 Batimento | 1× por dia | "eu rodei hoje" |

O batimento é o menos óbvio e o mais importante: sem ele, *"o robô parou"* e
*"não apareceu vaga"* chegam até você exatamente iguais — como silêncio.

Cada notificação vem com 👍/👎. O voto é lido no ciclo seguinte e vira o
relatório de rendimento por fonte (`--precisao`), que é o que decide se uma
fonte fica ou sai.

## Fontes

**19 fontes, nenhuma usando navegador.** Elas se dividem em três formas de
perguntar, e a diferença entre elas importa mais que a contagem.

**Por termo** — "quem tem vaga de PCP?" — roda a cada 3 horas:

| Fonte | Tipo |
|---|---|
| Gupy | API JSON |
| LinkedIn | endpoint de convidado (`geoId`) |
| Vagas.com · InfoJobs · Catho | HTML servido |
| Remotive · RemoteOK · Jobicy · Himalayas · Arbeitnow · Working Nomads · The Muse | API JSON |

**Por cidade** — "o que existe em Itapetininga?" — roda 1× por dia, nas 35
cidades da região:

| Fonte | Tipo |
|---|---|
| BNE | HTML, com **modalidade declarada** em campo próprio |
| Vagas.com · InfoJobs · Catho | HTML servido |

**Por empresa** — "o que a Smurfit abriu?" — 1× por dia, nas empresas que o
próprio radar descobriu: Gupy, Vagas.com, InfoJobs.

### Por que três formas, e não só mais fontes

Buscar por termo tem um limite estrutural: o rodízio roda 15 termos por ciclo,
então uma vaga só aparece quando o termo certo chega a vez — e ainda precisa
que a fonte case o texto do jeito que escrevemos. Uma vaga real
("Analista de Suprimentos (Foco em Defesa)") só apareceu quando aquele termo
entrou no bloco.

Para uma busca presa a uma região, **perguntar pela cidade cobre mais**: pega
vaga de título que nenhum termo do config alcançaria, e não espera rodízio
nenhum. O filtro de cargo continua sendo quem decide — a varredura só amplia o
que chega até ele.

A lista de empresas **se monta sozinha**: o ciclo registra toda empresa que
publicou numa cidade da região, inclusive quando a vaga em si foi reprovada.
Uma metalúrgica de Tatuí que hoje publicou "Soldador" é exatamente a empresa
que mês que vem publica "Analista de PCP".

### O que foi testado e ficou de fora

Cada fonte foi sondada ao vivo antes de virar código. Não entraram:
Empregos.com.br (HTTP 502), Netvagas (404), Emprega SP (fora do ar),
Jobspresso e 99jobs (sem card no HTML servido), Adzuna e Jooble (exigem
chave), Teamtailor (406). TrabalhaBrasil e Solides montam a lista só no
navegador — voltam se alguma expuser API.

Greenhouse, Lever e Ashby respondem sem chave, mas identificam empresa por
token próprio e no Brasil são quase só empresa de tecnologia. Ficam anotadas
para quando houver lista de empresa que as use.

Os agregadores de vaga remota internacional têm rendimento **baixo** e isso
está registrado no código: exigem que a vaga **declare** o mercado — num
portal brasileiro "remoto sem escopo" quer dizer Brasil, num agregador em
inglês quer dizer o contrário. Sem essa regra, um deles sozinho aprovou 60
vagas irrelevantes num ciclo.

## Rodar

```bash
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
```

`.env` na raiz, a partir de `.env.example`:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

```bash
python main.py            # ciclo completo (é o que o cron faz)
python main.py --teste    # sem enviar nada, sem gravar nada
python main.py --estado   # o que o banco tem
python main.py --precisao # rendimento por fonte
```

## Testes

```bash
python -m pytest
```

97 casos. **Cada teste em `tests/test_regressoes.py` é um bug que aconteceu
de verdade em produção** num projeto desta família — não são cenários
hipotéticos. Os comentários trazem o número de vagas que cada um afetou.

Vale conhecer os quatro piores, porque explicam decisões de desenho que de
outra forma pareceriam paranoia:

1. **O resumo diário nunca foi enviado. Nenhuma vez.** A condição era `hora
   == 9`, mas o ciclo terminava às 10h20. E a rede de segurança para esse
   caso exigia um envio anterior registrado — dependia exatamente daquilo que
   deveria consertar. 408 vagas presas na fila, sem nenhum erro em lugar
   nenhum.

2. **O robô reverteu código em produção.** O passo de salvar o banco usava
   `git rebase -X theirs` quando o push era recusado; o histórico foi
   reescrito durante uma execução e ele reaplicou commits antigos por cima.
   Hoje o robô adota o estado remoto e recoloca só o banco — e o passo falha
   se o commit tocar qualquer outro arquivo.

3. **`location=Brasil` no LinkedIn devolvia resultado dos EUA** — sem erro,
   sem lista vazia, indistinguível de sucesso. 281 das 910 vagas eram
   americanas e 19 eram brasileiras. A grafia certa é `Brazil`.

4. **O caminho do banco era relativo ao arquivo.** Mover um módulo para uma
   subpasta moveu o banco junto, em silêncio: histórico órfão, banco novo
   vazio a cada execução, toda vaga virando "nova" e renotificando a cada 3
   horas.

## Segredos

O token do bot fica **só no `.env`**, que é ignorado pelo git, e nas secrets
do repositório. Nunca em arquivo rastreado.

Isso não depende de ninguém lembrar — o projeto verifica sozinho, em duas
camadas:

- `tests/test_segredos.py` roda em CI a cada push e falha se qualquer arquivo
  rastreado contiver token do Telegram, token do GitHub, chave da AWS ou chave
  privada, ou se o `.env` for adicionado ao git;
- `.githooks/pre-commit` pega antes do commit, que é onde importa. Instale uma
  vez por clone:

```bash
git config core.hooksPath .githooks
```

As duas camadas existem porque o CI só reclama **depois** do push, e depois do
push já é tarde: token commitado fica no histórico de todo clone e de todo
fork mesmo depois de removido do arquivo. A única correção de verdade passa a
ser revogar o token. Repositório privado não muda isso — basta virar público
um dia, ou ganhar um colaborador.

Verificado por mutação: um arquivo com token de mentira é recusado pelo gancho
e quebra o teste.

## Ajustar

Tudo que define *o que* procurar está em `radar/config.py`, e nada além de
dado mora lá. Se for preciso editar qualquer outro arquivo para trocar de
cargo ou de cidade, é bug de desenho.
