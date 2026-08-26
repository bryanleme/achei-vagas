# Radar de Vagas

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
mantém o ruído perto de zero — num ciclo real, 2.760 vagas brutas viraram 14
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

| Fonte | Tipo | Cadência |
|---|---|---|
| **Gupy** | API JSON | todo ciclo |
| Remotive, RemoteOK, Jobicy, Himalayas | API JSON | todo ciclo |
| Arbeitnow | API JSON | diária |

A Gupy é a fonte que importa para este perfil — é o ATS que a indústria
brasileira usa. E ela tem **API JSON pública**, descoberta inspecionando as
requisições reais do portal: entrega `city`, `state`, `workplaceType` e
`publishedDate` em campos separados, com o total de resultados na resposta.
Isso elimina, para a fonte principal, as três classes de bug mais caras deste
tipo de projeto: adivinhar localização em texto solto, deduzir modalidade de
palavra solta, e descobrir o fim da paginação por tentativa e erro.

Os agregadores de vaga remota internacional têm rendimento esperado **baixo**
e isso está registrado no código: custam uma requisição cada e cobrem o único
mercado que nenhuma fonte brasileira alcança. Critério de saída: um mês sem
vaga aprovada e saem.

**Ainda não ligadas** (fase 2, precisam de navegador): LinkedIn, Vagas.com,
InfoJobs, Catho, 99jobs, Indeed, e boards de ATS por empresa
(Greenhouse/Lever) das companhias da região.

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

95 casos. **Cada teste em `tests/test_regressoes.py` é um bug que aconteceu
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

## Ajustar

Tudo que define *o que* procurar está em `radar/config.py`, e nada além de
dado mora lá. Se for preciso editar qualquer outro arquivo para trocar de
cargo ou de cidade, é bug de desenho.
