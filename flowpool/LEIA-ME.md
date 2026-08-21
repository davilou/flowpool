# FlowPool

Serviço local que gera vídeo e imagem no Google Flow rodando um **pool de contas
Google**, escolhendo sozinho qual conta usar conforme o crédito disponível. Expõe uma
API HTTP; o painel web e os scripts do pipeline são clientes dela.

O desenho inteiro está em `docs/superpowers/specs/2026-08-19-flowpool-design.md`.

## Abrir o painel

```bash
.venv/Scripts/python.exe -m flowpool
```

Depois abra **http://127.0.0.1:8777/** no navegador.

É a mesma porta da API — o painel é HTML/JS puro servido pelo próprio FastAPI, sem build
step, sem Node e sem segundo servidor. Subir o serviço já liga o motor da fila: jobs
pendentes começam a ser despachados assim que houver conta com crédito.

Serviço local, um usuário, uma máquina: **não há login, e a porta escuta só em
127.0.0.1**.

## O que o painel faz

**Contas** — o centro. Cadastrar uma conta nova, revalidar sessão caída, ler o saldo,
ativar e desativar, e trocar tier e proxy. Cada linha mostra e-mail, tier, saldo, saúde
da sessão e o proxy (sempre mascarado: a senha nunca sai do servidor).

**O saldo é lido no cadastro**, e não fica em zero esperando o primeiro job. Tem que ser
assim: `escolher` só manda job para conta cujo saldo cobre o custo, então conta com saldo
zero nunca rodaria nada — e sem rodar nada o saldo nunca seria lido. O botão **Ler saldo**
refaz a leitura quando você quiser conferir; o motor também refaz sozinho quando precisa
despachar e não acha conta apta. Ler saldo abre uma janela e **não gasta crédito**.

**Cadastrar conta abre um Chrome comum e espera você.** A chamada bloqueia até o login
terminar — pode levar minutos, e é normal. O painel desabilita o formulário, mostra os
cinco passos e um cronômetro, e confirma sozinho quando a sessão estiver de pé.
**Não clique de novo enquanto isso**: um segundo Chrome no mesmo perfil rouba a janela
do primeiro e o cadastro falha. Os cinco passos, resumidos:

1. Faça o login na conta Google na janela que abriu.
2. Consentimento: deixe as duas caixas desmarcadas e clique em "Avançar".
3. Aviso de privacidade: **role até o fim do texto** — o botão "Continuar" só habilita
   quando o fim entra na tela de verdade; janela minimizada ou coberta não conta.
4. Confirme que a lista de projetos do Flow carregou.
5. **Feche a janela do Chrome.** O resto é com o serviço.

**Revalidar é o mesmo fluxo**, e vira rotina: a sessão do Flow vive cerca de **48h**.
Quando cai, a conta sai do pool e aparece na faixa laranja do topo, que é a informação
mais acionável do painel — é a única coisa ali que exige uma pessoa.

**Saúde do pool** — crédito total das contas vivas, quantas estão vivas, quantos jobs
esperam na fila, e as contas expiradas em destaque.

**Jobs** — a fila e o histórico, com estado, modo, modelo, conta usada, arquivo e erro;
um formulário para enfileirar e um botão para cancelar job ainda pendente. O formulário
esconde o que a API recusaria: duração só existe em vídeo, quantidade acima de 1 só em
imagem, e proporção de vídeo é só 9:16 ou 16:9.

**Imagem aceita referência** — até 3, pelo mesmo `+` (ou Ctrl+V, ou arrastando). É o
Nano Banana gerando a partir de uma foto, e é por aí que o pipeline começa: foto da
roupa → imagem da modelo vestida. Imagem sem referência continua sendo geração por
texto puro. Anexar em **vídeo — texto** é o único caso em que o painel troca o modo
sozinho (para *vídeo — referências*), porque lá o arquivo não teria para onde ir.

A tela se atualiza sozinha a cada 4 segundos.

## Proxy

O proxy é **por conta** e entra já no cadastro: o login precisa sair do mesmo IP por
onde a conta vai operar. Sem proxy, a conta usa o IP desta máquina — que é o estado
normal hoje.

No painel, o valor atual aparece como texto (mascarado) e o campo ao lado é para
**trocar**; "Tirar" devolve a conta ao IP da máquina. O campo de troca nasce vazio de
propósito: gravar de volta o valor mascarado escreveria `***` no lugar da senha.

Proxy **com usuário e senha não funciona no cadastro** — o `chrome.exe` abre um diálogo
de autenticação que trava tudo sem mensagem. Use proxy autorizado por IP ou um
retransmissor local sem senha.

## API

Documentação interativa em **http://127.0.0.1:8777/docs**.

`GET /saude`, `GET|POST /contas`, `PATCH /contas/{id}`, `POST /contas/{id}/revalidar`,
`POST /contas/{id}/saldo`,
`GET|POST /jobs`, `POST /jobs/lote`, `GET|DELETE /jobs/{id}`, `GET /projetos`,
`DELETE /projetos/{id}`.

## Quando o Google muda a interface

Os seletores do Flow moram todos em `flowpool/transporte/pw/seletores.py`. Quando um
deles quebra, o diagnóstico é uma linha:

```bash
.venv/Scripts/python.exe -m flowpool.transporte.pw.conferir <perfil> <uuid-do-projeto>
```

Ele abre a interface, confere cada seletor do mapa e diz qual morreu. Não gasta crédito.

### Auto-cura: o app tenta se consertar sozinho

Quando um clique não acha **nenhum** candidato, o FlowPool tira um print da tela, monta
um resumo do DOM e pergunta a um **modelo de visão local** qual é o seletor novo. Valida
a resposta (tem que casar com **exatamente 1** elemento), grava em
`flowpool/transporte/pw/curados.json` e **repete a ação**. O job segue em vez de falhar.

**Para ligar, basta ter o Ollama rodando com um modelo de visão** — é o padrão, não
precisa configurar nada:

```bash
ollama serve            # se ainda não estiver de pé
ollama pull qwen3-vl:32b
```

Sem o Ollama respondendo, a cura **se desliga sozinha** e o comportamento é o de sempre:
erro claro e print em `scratch/falhas/`. Nada estoura por falta de modelo.

Dá para ajustar por variável de ambiente:

| variável | padrão | para quê |
|---|---|---|
| `FLOWPOOL_CURA` | ligada | `0`, `off` ou `nao` desliga a cura por completo |
| `FLOWPOOL_CURA_MODELO` | `qwen3-vl:32b` | trocar de modelo (precisa ter **visão**) |
| `FLOWPOOL_OLLAMA_URL` | `http://localhost:11434` | Ollama em outra máquina |
| `FLOWPOOL_CURA_CLIENTE` | `ollama` | `anthropic` usa a API da Anthropic (exige `pip install anthropic` e credencial — o SDK não é dependência do projeto) |

**Sobre a GPU:** `qwen3-vl:32b` ocupa ~20 GB e a placa tem 24. Com o ComfyUI carregado
os dois não cabem juntos, e a cura passa a rodar parte na CPU — fica lenta, não quebra
(o prazo é de 5 minutos). Se isso incomodar, aponte `FLOWPOOL_CURA_MODELO` para um
modelo de visão menor.

### Como reverter uma cura

Tudo o que a IA mudou está em `flowpool/transporte/pw/curados.json`, e cada entrada
guarda o valor novo, **o valor antigo**, a data e a justificativa que o modelo deu:

```json
{
  "DROPDOWN_MODELO": {
    "valor": "button:has-text(\"expand_more\")",
    "antigo": "button:has-text(\"arrow_drop_down\")",
    "em": "2026-08-21T14:03:11+00:00",
    "porque": "o botão que abre a lista de modelos agora usa a ligadura expand_more"
  }
}
```

- **Desfazer uma cura:** apague a entrada do JSON.
- **Desfazer todas:** apague o arquivo. `seletores.py` nunca é reescrito — ele volta a
  valer inteiro, com os comentários que explicam cada armadilha medida.
- **Tornar a cura permanente:** copie o valor para dentro de `seletores.py`, à mão, e
  apague a entrada. Aí ela vira autoria humana e entra no git com o resto.

O arquivo aparece como não versionado no `git status` de propósito: é o lembrete de que
alguma coisa mudou e vale uma olhada. Cada cura também vira um evento `seletor_curado`
no banco.

### O que a cura nunca toca

**Item de menu do card, excluir projeto e lixeira não são curáveis.** O menu do card
contém "Mover para a lixeira", e um seletor errado ali apaga trabalho seu — "casou 1
elemento" não protege de nada quando o elemento errado também casa 1. Nesses casos
falhar é o comportamento certo: erro, print salvo, e **você** decide. A lista está em
`seletores.NAO_CURAVEIS`, com o motivo escrito ao lado.

Também há um **teto de 3 curas por execução**: interface quebrada em dez lugares não é
seletor obsoleto, é mudança grande — e aí alguém precisa olhar.

## Testes

```bash
.venv/Scripts/python.exe -m pytest -q
```

Nada na suíte abre navegador nem gasta crédito: a escolha de conta e a contabilidade de
crédito são código puro, e o transporte tem uma implementação falsa.
