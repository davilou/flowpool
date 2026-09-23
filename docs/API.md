# API do FlowPool

Guia para quem quer gerar pelo FlowPool sem o painel — um script, outro serviço, um
agente. O painel é só mais um cliente desta mesma API.

- **Endereço:** `http://127.0.0.1:8900` (`FLOWPOOL_PORTA` muda a porta).
- **Sem autenticação**, e a porta escuta só em `127.0.0.1`: é serviço local, de um
  usuário, numa máquina.
- **Referência completa, gerada do código:** `http://127.0.0.1:8900/docs` (Swagger) com
  o serviço no ar. Este guia cobre o fluxo; os campos exatos moram lá e em
  [`flowpool/api/esquemas.py`](../flowpool/api/esquemas.py).
- Corpo e resposta em JSON. Erro de validação volta `422` com o motivo em texto.

## O fluxo em quatro chamadas

```
POST /jobs            →  cria o job (estado "pendente")
GET  /jobs/{id}       →  acompanha: pendente → rodando → pronto | falhou
GET  /baixar?caminho= →  baixa o arquivo quando "pronto"
GET  /jobs/{id}/eventos  →  quando falhou, o porquê
```

O motor escolhe a conta sozinho — quem chama nunca diz qual. Contas **free** vêm
primeiro (o crédito delas expira no dia); **pro** e **ultra** ficam de reserva.

## 1. Criar um job

```bash
curl -X POST http://127.0.0.1:8900/jobs \
  -H "Content-Type: application/json" \
  -d '{
        "modo": "t2v",
        "prompt": "a red fox running through fresh snow at sunrise, tracking shot",
        "produto": "Raposa",
        "nome": "raposa-01",
        "duracao": "4s"
      }'
```

Responde `201` com o job (ver [a resposta](#o-job-na-resposta)).

### Campos do pedido

| campo | obrigatório | padrão | o que é |
|---|---|---|---|
| `modo` | sim | — | `t2v`, `frames2v`, `ingredients2v` ou `imagem` |
| `prompt` | sim | — | o texto da geração |
| `produto` | sim | — | nome do projeto no Flow **e** da pasta de saída |
| `nome` | sim | — | nome do arquivo, sem extensão |
| `modelo` | não | o do modo | ver [modos e modelos](#modos-e-modelos) |
| `referencias` | não | `[]` | até 3 imagens; só `ingredients2v` e `imagem` |
| `frame_inicial` / `frame_final` | não | — | só `frames2v` |
| `proporcao` | não | `9:16` | `16:9`, `4:3`, `1:1`, `3:4`, `9:16` |
| `duracao` | não | `4s` | `4s`, `6s`, `8s`, `10s` — **só vídeo**, e muda o custo |
| `quantidade` | não | `1` | saídas por geração; até 4 em `imagem`, sempre 1 em vídeo |
| `destino` | não | `<produto>/clipes` | pasta de saída, relativa à raiz |
| `prioridade` | não | `0` | maior sai antes |

O arquivo cai em `<raiz>/<destino>/<nome>.mp4` (ou `.png` em `imagem`).

**Caminhos de imagem** (`referencias`, `frame_inicial`, `frame_final`) são caminhos em
disco, relativos à raiz do projeto — é assim que o painel os grava, por exemplo
`_coladas/3f9a….png`. Para mandar uma imagem que não está no disco, suba-a antes com
[`POST /colar`](#subir-uma-imagem).

### Modos e modelos

| modo | o que faz | modelos aceitos | padrão |
|---|---|---|---|
| `t2v` | vídeo só a partir do texto | `omni-flash`, `veo-lite`, `veo-fast`, `veo-quality` | `omni-flash` |
| `frames2v` | vídeo entre um quadro inicial e um final | `veo-lite` | `veo-lite` |
| `ingredients2v` | vídeo a partir de imagens de referência | `omni-flash`, `veo-lite`, `veo-fast`, `veo-quality` | `omni-flash` |
| `imagem` | imagem, com ou sem referência | `nano-banana`, `nano-banana-2`, `nano-banana-2-lite` | `nano-banana-2` |

Pedido com modelo que o modo não aceita é recusado com `422` antes de gastar crédito. A
lista viva — e a tabela de custo por modelo, tier e duração — sai de `GET /info`:

```bash
curl http://127.0.0.1:8900/info
```

### Exemplos por modo

Vídeo a partir de uma foto de produto:

```bash
curl -X POST http://127.0.0.1:8900/jobs -H "Content-Type: application/json" -d '{
  "modo": "ingredients2v",
  "prompt": "the model walks toward the camera wearing the dress, soft daylight",
  "produto": "Vestido Azul",
  "nome": "vestido-01",
  "referencias": ["Vestido Azul/fotos/frente.png"]
}'
```

Vídeo entre dois quadros:

```bash
curl -X POST http://127.0.0.1:8900/jobs -H "Content-Type: application/json" -d '{
  "modo": "frames2v",
  "prompt": "smooth camera move from the first frame to the last",
  "produto": "Obra",
  "nome": "obra-01",
  "frame_inicial": "Obra/quadros/inicio.png",
  "frame_final": "Obra/quadros/fim.png"
}'
```

Quatro variações de imagem (imagem não tem `duracao` — mandar o campo dá `422`):

```bash
curl -X POST http://127.0.0.1:8900/jobs -H "Content-Type: application/json" -d '{
  "modo": "imagem",
  "prompt": "product shot of a ceramic mug on a wooden table",
  "produto": "Caneca",
  "nome": "caneca-01",
  "quantidade": 4
}'
```

## 2. Vários de uma vez

```bash
curl -X POST http://127.0.0.1:8900/jobs/lote -H "Content-Type: application/json" -d '{
  "jobs": [
    {"modo": "imagem", "prompt": "scene 1 ...", "produto": "Video 09", "nome": "cena-01"},
    {"modo": "imagem", "prompt": "scene 2 ...", "produto": "Video 09", "nome": "cena-02"}
  ]
}'
```

Responde `201` com `{"ids": [2450, 2451]}`. Cada item segue as regras de `POST /jobs`;
um item inválido recusa o lote inteiro.

## 3. Acompanhar

```bash
curl http://127.0.0.1:8900/jobs/2450
```

Os estados:

| estado | significa |
|---|---|
| `pendente` | na fila; `reavaliar_em` diz quando o motor volta a olhar, se estiver esperando conta |
| `rodando` | gerando numa conta agora |
| `pronto` | arquivo em disco — ver `caminho` |
| `falhou` | desistiu; `erro` traz a mensagem |
| `cancelado` | cancelado antes de rodar |

Um job pode voltar de `rodando` para `pendente` sozinho: conta que bateu no limite,
sessão que caiu, download que falhou. `tentativas` conta essas voltas. Consulte a cada
10–15 s; um vídeo costuma levar 1–3 min.

Listar por estado: `GET /jobs?estado=pendente`.

### O job na resposta

Os campos que importam para quem chama (a lista completa está no `/docs`):

| campo | o que é |
|---|---|
| `id`, `estado`, `modo`, `modelo`, `nome`, `produto` | o que foi pedido e onde está |
| `caminho` | quando `pronto`: o arquivo, **relativo à raiz** — é o que `/baixar` recebe |
| `arquivo` | o mesmo arquivo, com caminho absoluto |
| `conta_id` | a conta que gerou (ou está gerando) |
| `custo_estimado` | créditos reservados para o job |
| `tentativas`, `erro` | quantas vezes voltou à fila e a última mensagem de falha |
| `reavaliar_em` | quando um job pendente será reavaliado |

## 4. Baixar o resultado

```bash
curl -o raposa-01.mp4 "http://127.0.0.1:8900/baixar?caminho=Raposa/clipes/raposa-01.mp4"
```

`caminho` é o campo `caminho` do job. Caminho absoluto ou que saia da raiz do projeto é
recusado. Rodando na mesma máquina, dá para ler direto de `arquivo`, sem baixar.

Vários num zip: `GET /baixar-lote?caminho=a.mp4&caminho=b.mp4`.

## Quando falha

```bash
curl http://127.0.0.1:8900/jobs/2450/eventos
```

A linha do tempo do job: `despachado`, `conta_no_limite`, `download_falhou`, `pronto`,
`falhou`… cada evento com `em`, `conta_id` e um `payload` com o detalhe. É o que
responde "o que aconteceu", já que `erro` guarda só a última tentativa.

### O que o `erro` diz, e o que o cliente faz

`erro` começa com o nome do erro, seguido de `:` — um prefixo estável, feito para
quem chama por código (um script ou um LLM) decidir sem interpretar a frase:

| prefixo | o que houve | o que o motor já fez | o que o cliente faz |
|---|---|---|---|
| `RecusaPorPolitica:` | o Flow barrou o **prompt** por política de conteúdo | parou na hora, **sem** levar o prompt a outra conta | reescrever o prompt e criar um job **novo** |
| `FlowEmAltaDemanda:` | o Flow estava sobrecarregado | tentou outra conta na hora, sem gastar tentativa; com o pool todo no pico, espera 10 min | nada — o job segue sozinho |
| `CardFalhou:` | o Flow marcou a geração como falha, sem dizer por quê | gerou de novo em outra conta, até o teto de tentativas | se falhou de vez, revisar prompt e referência |
| `LimiteDeUso:` | a conta bateu no limite de uso ou no antiabuso | trocou de conta, sem gastar tentativa | nada |

Depois do prefixo vem o texto do card do Flow, normalizado (minúsculas, sem acento).
Nenhum desses casos foi cobrado nas vezes medidas.

`POST /jobs/{id}/diagnosticar` pede ao modelo de visão local uma explicação do print da
falha (exige o llama.cpp da auto-cura no ar — ver o [LEIA-ME](../flowpool/LEIA-ME.md)).

## Mudar ou cancelar um job

| chamada | o que faz | quando recusa |
|---|---|---|
| `DELETE /jobs/{id}` | cancela | `409` se o job não está mais `pendente` |
| `PATCH /jobs/{id}` com `{"nome": "novo"}` | renomeia o arquivo que vai sair | `409` depois que o arquivo existe |

## Subir uma imagem

Para usar como referência uma imagem que não está no disco:

```bash
# os bytes crus da imagem no corpo, sem multipart
curl -X POST http://127.0.0.1:8900/colar --data-binary @foto.png

# ou pela URL
curl -X POST http://127.0.0.1:8900/colar-url -H "Content-Type: application/json" \
  -d '{"url": "https://exemplo.com/foto.jpg"}'
```

Os dois respondem `201` com `{"nome", "caminho", "tamanho"}` — o `caminho` vai direto
em `referencias`. O tipo é decidido pelos bytes, não pelo nome nem pelo `Content-Type`.

`GET /arquivos?caminho=<pasta>` lista pastas e imagens da raiz, para escolher o que já
está em disco.

## Contas

| chamada | o que faz |
|---|---|
| `GET /contas` | o pool: tier, saldo, sessão, proxy, ativa |
| `POST /contas` com `{"email", "tier", "proxy"}` | cadastra — **abre o Chrome para você logar** |
| `PATCH /contas/{id}` com `ativa`, `tier` e/ou `proxy` | liga/desliga, corrige o tier, troca o proxy (`""` tira) |
| `POST /contas/{id}/saldo` | relê o saldo no Flow (abre o navegador, não gasta crédito) |
| `POST /contas/{id}/revalidar` | confere se a sessão ainda está logada |
| `POST /contas/{id}/abrir` | abre o Chrome da conta, para mexer à mão |
| `POST /contas/{id}/mostrar-janela` | traz para a frente a janela de uma geração em curso |
| `POST /contas/{id}/conferir-ip` | confere por qual IP a conta sai |
| `GET /contas/{id}/eventos?limite=60` | o histórico da conta |

**Marque o tier certo.** Conta paga cadastrada como `free` passa a ser a primeira
escolha — tem o maior saldo — em vez da última.

Cadastro, proxy e o passo a passo do login estão no [LEIA-ME](../flowpool/LEIA-ME.md).

## Serviço e projetos

| chamada | o que devolve |
|---|---|
| `GET /saude` | contas vivas, gerando agora, fila, crédito total, contas com sessão caída |
| `GET /info` | raiz, custos, modelos por modo, proporções, durações, limites |
| `GET /projetos?conta_id=` | o projeto do Flow de cada produto em cada conta |
| `DELETE /projetos/{id}` | esquece o vínculo local (não apaga nada no Flow) |
