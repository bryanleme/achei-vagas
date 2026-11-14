# Perfis

Cada arquivo `perfis/<id>.toml` é o radar de uma pessoa: o que ela procura,
onde, e quando quer ser avisada. O perfil principal continua em
`radar/config.py`.

O radar busca **uma vez** para todos os perfis e avalia cada vaga contra
cada perfil separadamente. A mesma vaga pode chegar a duas pessoas. O 👍/👎,
a fila do resumo e o "já recebi esta" são de cada um.

> **O repositório precisa ser privado antes do primeiro convidado.** Os
> arquivos daqui e o banco `data/vagas.db` (com o chat de cada pessoa) são
> versionados. O workflow se recusa a rodar com perfil convidado num
> repositório público.

## Convidar alguém

1. Gere um código de convite:

   ```bash
   python main.py --novo-convite
   ```

2. Copie `modelo.toml.exemplo` para `perfis/<id>.toml` (ex.: `perfis/joao.toml`),
   cole o código em `convite` e preencha cargos, cidades e modalidades.

3. Valide:

   ```bash
   python main.py --perfis
   ```

4. Pegue o link e mande para a pessoa:

   ```bash
   python main.py --convite joao
   ```

5. Faça commit e push do arquivo. A pessoa abre o link e toca em **Iniciar**.
   O radar confirma na rodada seguinte, porque não há servidor esperando: o
   cron roda algumas vezes por dia.

Nas primeiras ~30 horas, tudo vai para o resumo diário, sem alerta na hora.
É quando o radar encontra o que já estava publicado na cidade dela.

## Montar a lista de cargos

A qualidade do radar depende dos cargos, mais do que de qualquer outra coisa.
O filtro lê **só o título** da vaga:

- **fortes**: títulos que sozinhos já dizem a área ("Analista de PCP").
- **ambiguos** + **qualificadores**: títulos usados em várias áreas
  ("Assistente Administrativo"), que só passam com um qualificador no
  título ("... de Logística").
- **ferramentas**: "Power BI", "Zendesk", que só passam com palavra de cargo
  junto (analista, assistente...). É o que mantém "Desenvolvedor Power BI"
  fora de um perfil de análise.

Não coloque todas as palavras do currículo: "Excel" ou "Python" como cargo
trazem vaga de qualquer área. Para montar a lista de alguém, peça ao Claude
Code: *"crie perfis/joao.toml para atendimento ao cliente em Curitiba, só
remoto, a partir do modelo"*. Depois revise com a pessoa.

## O que a pessoa pode fazer pelo Telegram

| Comando    | Efeito                                   |
|------------|------------------------------------------|
| `/status`  | mostra o que o radar dela procura        |
| `/pausar`  | para de enviar vagas                     |
| `/retomar` | volta a enviar                           |

Mudar cargo, cidade ou horário é editar o arquivo dela e fazer push.

## Tirar alguém

- Pausar sem apagar: `ativo = false` no arquivo.
- Trocar o Telegram da pessoa: `python main.py --desvincular <id>` e mandar o
  convite de novo. Se o convite vazou, troque também o código.
- Remover: apagar o arquivo e rodar `--desvincular <id>`.

## Limites desta versão

- Uma UF por perfil, e as cidades presenciais têm de ser dessa UF.
- Respostas a comandos chegam na rodada seguinte, não na hora.
- Cobertura fora de SP não foi medida em produção. Os endereços das quatro
  varreduras por cidade foram conferidos ao vivo em Curitiba (PR) e Recife (PE).
