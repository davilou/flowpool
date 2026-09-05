# O Flow mudou de aplicação — medição de 2026-09-05

Medido ao vivo com os perfis reais, sem gerar nada e sem gastar crédito.
Scripts: `scratch/sonda_acesso.py`, `scratch/sonda_projeto.py`, `scratch/sonda_video.py`.

## 1. O domínio mudou, e o antigo morreu

```
antigo: https://labs.google/fx/pt/tools/flow    -> redireciona
novo:   https://flow.google.com/
```

`seletores.URL_FLOW` aponta para o antigo. **Nenhuma geração funciona hoje.**

## 2. A aplicação foi reescrita: React/Next -> Angular

O DOM antigo era `styled-components` com classe-hash (`sc-59223abb-3 dmZGYv`), e por
isso a regra 10 da skill mandava casar por ligadura de ícone e `data-*`.

O novo é Angular + Angular Material, e expõe **elementos customizados nomeados**:

| elemento | o que é |
|---|---|
| `flow-project-card` | cartão de projeto na home |
| `flow-prompt-box`, `flow-base-prompt-box` | a barra do prompt |
| `flow-rich-text-editor` | o campo de texto do comando |
| `flow-add-menu` | o menu do `+` |
| `flow-agent-mode-toggle-chip` | o interruptor do Agente |
| `flow-generate-icon-button` | o botão de enviar |
| `flow-media-upload` | o envio de arquivo |
| `flow-credit-banner` | o aviso de crédito |
| `flow-media-trash-context-menu` | o menu de lixeira do card |
| `flow-editable-text` | o nome do projeto |
| `flow-project-nav-list` | a barra lateral |

**Isto é uma melhora grande para a automação.** Nome de elemento customizado é muito
mais estável que ligadura de ícone: ele existe porque o código do Google o usa.

O painel de configuração vive num overlay do CDK (`.cdk-overlay-pane`), não num
`[role=dialog]`.

## 3. Navegação

| ação | antes | agora |
|---|---|---|
| URL do projeto | `/fx/pt/tools/flow/project/<uuid>` | `/project/<uuid>` |
| abrir projeto | clicar no `<a>` do card | clicar em `flow-project-card` |
| renomear | campo `input[aria-label="Texto editável"]` | botão `edit` no card / `flow-editable-text` |

**Cuidado medido:** o botão `edit` do card **não abre** o projeto — provavelmente
renomeia. Clicar nele não navegou. Abrir é clicar no cartão.

## 4. O painel de configuração, medido nos quatro modos

`aria-checked="true"` marca a opção selecionada — **em todos os grupos**. O app antigo
não tinha esse marcador e precisava conferir a configuração lendo o texto do chip.
Agora a seleção se lê direto, o que fecha uma classe inteira de defeito silencioso.

### Modo Imagem

```
[image imagem]* [videocam video]
[crop_16_9 16:9]* [crop_landscape 4:3] [crop_square 1:1] [crop_portrait 3:4] [crop_9_16 9:16]
[<emoji> nano banana pro  arrow_drop_down]      aria-label="selecionar familia de modelos"
[x1] [x2]* [x3] [x4]
```

Sem resolução e sem duração — como antes.
Chip: `<emoji> nano banana pro crop_16_9 x2`

### Modo Vídeo (e os dois submodos)

```
[image imagem] [videocam video]*
[crop_free frames] [chrome_extension elementos]      <- SEMPRE VISÍVEIS, um deles marcado
[crop_16_9 16:9]* [crop_9_16 9:16]
[omni 1.1 flash  arrow_drop_down]
[360p info]* [720p]                                   <- GRUPO NOVO
[4s] [6s] [8s]* [10s]
[x1] [x2]* [x3] [x4]
```

Chip: `video · 360p · 8s crop_16_9 x2`

## 5. O que mudou no comportamento, e importa

1. **Existe um grupo de RESOLUÇÃO (360p / 720p)** que não existia. O app não o
   conhece — e o padrão agora é **360p**, não 720p. Sem tratar isso, todo vídeo sai
   na resolução baixa.

2. **Frames TEM duração.** A medição de 2026-08-21 dizia que o painel em Frames não
   tinha grupo de duração, e `SEM_DURACAO` foi criado por causa disso. No app novo os
   quatro valores estão lá. **Aquela conclusão morreu com a aplicação antiga.**

3. **Frames e Elementos são sempre visíveis** sob Vídeo, e um dos dois está sempre
   marcado. No app antigo eram submodos que só apareciam vindo de vídeo, e a escada de
   duas etapas (`MODO_PAI`) existia por isso. Falta medir se há vídeo "puro" sem
   submodo, ou se `elementos` sem referência é o t2v de hoje.

4. **O modelo virou "omni 1.1 flash"** (era "Omni Flash") e a imagem virou
   "nano banana pro". `ROTULO_MODELO` está desatualizado inteiro.

5. **A quantidade padrão veio x2**, não x1.

## 6. Acesso das contas — portão de idade

Medido nas oito, no domínio novo:

| conta | acesso |
|---|---|
| 1 daviright | OK (21 projetos) |
| 2 louastropay | OK (15) |
| 4 pmara5537 | OK (12) |
| 5 lonelyoffice95 | OK (19) |
| 8 dg6153580 | OK (15) |
| 3 sthefanyferreira901 | **flow.google.com/age-restricted** |
| 6 alexandrasc215 | **age-restricted** |
| 7 fehorta588 | **age-restricted** |

Texto do portão: *"Você precisa ter 18 anos ou mais para usar o flow.google.com."*

Cinco das oito contas funcionam. As três bloqueadas exigem verificação de idade na
conta Google — decisão e ação do dono das contas, não da automação.

## 7. Defeito colateral encontrado

`transporte.saldo()` quebrou com `Execution context was destroyed, most likely because
of a navigation`: ele avalia o DOM enquanto a página ainda navega. Vale uma espera de
estado antes da leitura. Não foi consertado ainda.

---

# Segunda rodada: o caminho de geração, provado ao vivo

Provas em `scratch/fumaca_novo.py` (imagem simples) e `scratch/fumaca_completa.py`
(imagem com duas referências). Custo: **zero** — imagem não gasta crédito.

## 8. Geração de imagem, ponta a ponta — FUNCIONA

```
[  12s] projeto aberto
[  15s] marcado: ['image imagem', 'crop_9_16 9:16', 'x1']
[  18s] acervo assentado: 15 itens
[  29s] subiram 2 arquivos em 11.2s
[  31s] referencia 1 incluida: 8ced8effebecb2f9.jpg
[  32s] referencia 2 incluida: e511c628ec3c92a4.png
[  32s] fichas: ['ff7d612b-...', '645b0be6-...']   -> conferencia por IDENTIDADE ok
[  34s] enviado
[  68s] PRONTO: 209c2d14-39fc-4fda-a720-f054b13a3cce
```

## 9. Como o resultado se identifica

O card gerando traz a **porcentagem** no texto (`['image','20%',<prompt>]`); o pronto
perde a porcentagem, ganha `img`/`video` carregado e um id:

```
https://flow-content.google/image/<uuid>?expires=...&keyname=...&signature=...
https://flow-content.google/video/<uuid>?...
```

O **tipo vem no caminho**, o que dá de graça o que a aplicação antiga guardava por
fora (`_modo_do_card`) para saber que resolução pedir no download.

Card novo entra **no topo** da grade — registrado como fato, não como forma de
escolher: a escolha continua por id.

## 10. Anexar: mais simples, e com uma armadilha nova

**O envio é por seletor nativo, e aceita vários arquivos numa tacada.** Dois arquivos
subiram juntos em 11,2 s — não há laço por arquivo no envio.

**Incluir é em dois passos:** clicar no item o SELECIONA e abre uma pré-visualização
com o botão "Incluir no comando" ao lado; só então ele entra na barra. (Uma sonda viu
o menu fechar sozinho num caso isolado, então o código trata o botão como opcional:
se não estiver na tela, o clique já resolveu.)

### A armadilha: retrato do antes com carregamento preguiçoso

A primeira versão da sonda "achou" **6 arquivos novos tendo enviado 2**. Causa medida:
dos 15 itens do acervo, só 8 tinham `src` no primeiro instante — os outros carregam a
miniatura depois. Item sem id não entra no retrato, então quando a miniatura chega ele
**parece recém-enviado**.

É a regra 3 da skill vista de outro ângulo: o retrato do antes só vale com a lista
assentada. O conserto é esperar a lista parar de mudar E não sobrar item sem id.

### Homônimos continuam existindo

O acervo mostrou `02a56f959156564b.png` e `0ac53d49dabaa6dd.jpg` **duas vezes cada**,
de envios diferentes. Nome não identifica nada — a escolha por id continua sendo o que
separa "a referência certa" de "uma que tem o mesmo nome".

## 11. O painel do seletor de mídia

Abas à esquerda: `Tudo · Imagens · Vídeos · Vozes · Personagens · Avatares · Envios`.
Busca própria (`input[aria-label*="pesquisar recursos"]`) e "Enviar mídia" no rodapé.

**Vozes e Avatares são categorias que a aplicação antiga não tinha** — ainda não
medidas, e possivelmente modos de geração que o painel do FlowPool nem conhece.

---

# Terceira rodada: o transporte portado, provado ao vivo

## 12. Contrato do transporte — o que já funciona

Provado com `scratch/fumaca_transporte.py`, que usa o **transporte de verdade**, não
um script solto:

| método | estado |
|---|---|
| `abrir_sessao` | OK — valida pelo avatar, reconhece o portão de idade |
| `saldo` | OK — 50 em cada uma das cinco contas vivas |
| `abrir_projeto` | OK — por URL direta, o que o app antigo não permitia |
| `listar_projetos` | OK — `flow-project-card` traz nome e href |
| `gerar` (imagem) | OK — com duas referências, conferidas por identidade |
| `gerar` (vídeo) | OK — `ingredients2v` / omni-flash / 4s, pronto em 40 s |
| `consultar` | OK para pronto; **falha não é detectada** (não medida) |
| `baixar` | OK em imagem; vídeo medido, confirmação em curso |
| `criar_projeto` | **não portado** — `BOTAO_NOVO_PROJETO` não medido |

Corrida completa de imagem, cronometrada:

```
configuração confirmada por aria-checked; custo anunciado: 0 créditos
2 arquivos enviados numa tacada ......... 14,4 s
2 referências incluídas .................. 13,4 s + 1,6 s
o Flow aceitou: card-0 → PRONTO ......... 24 s
baixado: resultado-imagem.jpeg (305.793 bytes)
saldo 50 → 50
```

Vídeo: **7 créditos** (50 → 43) para omni-flash 4s — o mesmo custo do app antigo.

## 13. Menu do resultado: imagem e vídeo diferem

```
comum:   favorito · reutilizar comando · incluir no comando · fazer o download ·
         copiar · renomear · compartilhar · definir capa · sinalizar saída ·
         MOVER PARA A LIXEIRA (último, nos dois)
imagem:  motion_blur / animar
vídeo:   play_movies / adicionar ao cenário · video_library / publicar no youtube
```

Submenu de download:

```
imagem : 1k / tamanho original    2k / aprimorada     4k / aprimorada
vídeo  : 270p / gif animado       720p / tamanho original
         1080p / aprimorada       4k / aprimorada
```

**"270p" é GIF animado, não vídeo pequeno.** Pedir a resolução mais barata traria
outro *formato*, e o arquivo pareceria certo até alguém abrir.

## 14. O que ainda não foi medido

| o quê | consequência hoje |
|---|---|
| card de FALHA | uma geração falha parece RODANDO até o teto do polling |
| aviso de crédito zerado | não encurta o prazo quando a conta seca |
| criar projeto | produto novo não ganha projeto sozinho |
| renomear projeto | idem |
| aviso de direitos | se ainda existir, pode travar o primeiro envio de uma conta |
| abas Vozes e Avatares | modos de geração que o painel não conhece |

Nenhum deles está chutado: todos estão como `PENDENTE` em `seletores.py`, e o
transporte trata pendente como "não sei" em vez de mandar `"..."` ao Playwright.

---

# Quarta rodada: o que faltava

## 15. Criar e renomear projeto — portado

Duas etapas, como na aplicação antiga: `add novo projeto` **cria e entra**, e renomear
vem depois pelo `more_vert` do cabeçalho. Não há botão "Concluir" — fecha com Enter, e
o código **confere** que o título virou o pedido.

Verificado ao vivo: criou, renomeou, e o nome apareceu na lista.

### O erro que mais se repetiu no dia

```
button[aria-label*="mais op"]          -> 0
input[aria-label="texto editavel"]     -> 0
```

Seletor de atributo CSS é **sensível a caixa**, e os rótulos reais têm maiúscula e
acento. Os valores errados saíram da saída das **minhas próprias sondas**, que passam
tudo por `dom.seguro` — normalizado *para imprimir*, nunca para virar seletor.

O conserto não foi acertar a caixa; foi **parar de depender do rótulo**:

```
BOTAO_MAIS_OPCOES  = "button[more-options]"      (há DOIS more_vert na tela)
CAMPO_NOME_PROJETO = "flow-editable-text input"
```

Atributo que o código do Google usa não muda com o idioma; rótulo traduzido muda.

## 16. Card de falha: não medível hoje, resolvido por estrutura

Varri os oito projetos das cinco contas vivas: **não há um único card falho guardado**.
O Flow não os mantém. Sem provocar uma falha de propósito, não há texto para casar.

Então a detecção não usa texto. Usa o progresso, que **foi** medido: card gerando traz a
**porcentagem** no texto; card pronto tem mídia. Ausência das duas, sustentada por
`VOLTAS_SEM_PROGRESSO` leituras (~1 min), é sinal estrutural de card parado.

Não depende de frase — sobrevive à próxima tradução do Google, que foi exatamente o que
quebrou o reconhecimento em agosto.

## 17. Saldo curto: reconhecido ANTES do clique

MEDIDO **sem gastar um crédito**: numa conta com ~36, escolher um modelo que o painel
anuncia por 100 faz o **botão de enviar desaparecer**, e um `info` toma o lugar dele.

```
saldo suficiente : add · agente · <chip> · arrow_forward
saldo curto      : add · agente · <chip> · info
```

O truque de medição vale registrar: para ver o estado "sem crédito" não foi preciso
drenar uma conta — bastou escolher um modelo mais caro que o saldo.

E é melhor do que era: a aplicação antiga só avisava **depois** do clique, e foi por ler
aquele aviso antes de conferir o card que um job voltou para a fila com o vídeo já
gerado e pago. Sem botão, nada foi enviado.

## 18. Projeto novo e vazio — o caso que quebrou tudo em agosto

```
acervo assentado: 0 itens
referência 1 incluída em 1,4 s
PRONTO em 20 s → 454 KB baixados
```

Funciona. E a referência entrou em 1,4 s em vez de 13,4 s: a espera pelo `+` declarar
fechado, com o painel já aberto, custava ~12 s por geração.

## 19. Abas novas do seletor de mídia

```
tudo · imagens · vídeos · vozes · personagens · avatares · envios
```

* **Vozes** — 15 opções, vozes nomeadas (`achernar`, …)
* **Avatares** — vazio, com o texto *"seu rosto, sua voz, sua história… gere um avatar
  (recurso experimental) para aparecer em qualquer vídeo"*
* **Personagens** — categoria própria

São **categorias de acervo**, não modos de geração do chip. O FlowPool não as usa hoje;
podem virar referência como qualquer outra mídia.

## 20. O que continua sem medir

| o quê | por quê | efeito |
|---|---|---|
| texto do card de falha | não há card falho para olhar | nenhum — a detecção por progresso cobre |
| aviso de direitos | só aparece no 1º envio de uma conta, e as cinco já enviaram | apareceria numa conta nova |
| aviso pós-clique de crédito | deixou de ser o caminho | nenhum — o botão ausente cobre antes |
