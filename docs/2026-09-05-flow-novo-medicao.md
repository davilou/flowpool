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
