"""A ÚNICA fonte de verdade sobre a interface do Flow.

REESCRITO EM 2026-09-05. O Flow mudou de endereço E de aplicação:

    labs.google/fx/pt/tools/flow  ->  flow.google.com
    React/Next (classe com hash)  ->  Angular + Angular Material

Tudo que este arquivo dizia antes valia para a aplicação antiga. A medição que
sustenta a versão atual está em `docs/2026-09-05-flow-novo-medicao.md`, e o histórico
da anterior continua no git — vale a leitura quando um seletor daqui morrer, porque
os DEFEITOS que cada regra evita continuam valendo mesmo com o DOM trocado.

AS SEIS REGRAS DURAS DESTE ARQUIVO

1. NUNCA classe gerada. Angular põe `_ngcontent-ng-c580089636` em tudo, e esse hash
   muda a cada build. Se você viu um `_ngcontent` num seletor, ele está errado.

2. O QUE ENTRA NUM LOG OU NUM MODELO PASSA POR `dom.seguro`. A página carrega token de
   sessão; ele já vazou duas vezes na aplicação antiga.

3. NOME DE ELEMENTO CUSTOMIZADO É O SELETOR PREFERIDO. Esta é a novidade boa da
   aplicação nova: `flow-prompt-box`, `flow-add-menu`, `flow-generate-icon-button`
   existem porque o código do Google os usa. São mais estáveis que ligadura de ícone,
   que por sua vez era mais estável que texto traduzido. A ordem de preferência virou:
   elemento customizado > `aria-*` > ligadura de ícone > texto visível.

4. `aria-checked` É O MARCADOR DE SELEÇÃO, e a aplicação antiga não tinha nada
   parecido. Todo grupo de opção do painel (modo, proporção, resolução, duração,
   quantidade) marca o escolhido com `aria-checked="true"`. Conferir a configuração
   deixou de depender de ler o texto do chip — o que fecha uma classe inteira de
   defeito silencioso.

5. AMBIGUIDADE É ERRO. Seletor que casa dois elementos não decide nada. Quem clica usa
   `_clicar_unico` / `analise.exigir_unico`.

6. `PENDENTE` significa NÃO MEDIDO, e nunca "provavelmente é isso". Quem consome trata
   pendente como "não sei" e nunca como valor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Valor de constante ainda NÃO MEDIDA na aplicação nova. Quem consome tem que tratar
# como "não sei fazer isso" e falhar claro — nunca chutar.
PENDENTE = "..."

# --------------------------------------------------------------------- endereços
#
# MEDIDO 2026-09-05: `labs.google/fx/pt/tools/flow` redireciona. Contas sem idade
# verificada param em `flow.google.com/age-restricted` — ver a seção de acesso no
# documento de medição.
URL_BASE = "https://flow.google.com"
URL_FLOW = "https://flow.google.com/"

# A rota de sessão da aplicação antiga (`/fx/api/auth/session`) não foi reencontrada.
# Enquanto estiver pendente, a saúde da sessão se mede pela tela, não por API.
URL_SESSAO = PENDENTE

# MEDIDO: o cartão da home traz `href="/project/<uuid>"`, e a URL do projeto aberto é
# `https://flow.google.com/project/<uuid>`. O UUID do app antigo CONTINUA VALENDO —
# `/project/749a3b01-...` abriu o mesmo projeto de agosto.
PADRAO_PROJETO = "/project/"
PADRAO_UUID = r"/project/([0-9a-fA-F-]{36})"

# ------------------------------------------------------------------- home / projetos
#
# MEDIDO: cada projeto é um `flow-project-card` contendo um `<a href="/project/...">`.
# O texto do cartão é `[nome, "edit", "delete"]`.
CARD_PROJETO = 'flow-project-card a[href*="/project/"]'
CARTAO_PROJETO = "flow-project-card"

# CONFIRMADO 2026-09-05 pelo `aria-label`: o `edit` do cartão é "editar titulo do
# projeto" e o `delete` é "excluir projeto". Nenhum dos dois abre o projeto — a
# primeira medição já tinha visto que clicar em `edit` não navegava, e o rótulo
# explica por quê. Abrir é clicar no cartão (ou seguir o href).
BOTAO_EDITAR_CARTAO = 'flow-project-card button:has-text("edit")'
# `delete` é vizinho de `edit` no mesmo cartão. Está aqui para ser RECONHECIDO e
# evitado, nunca clicado por automação — mesma razão do menu de lixeira.
BOTAO_APAGAR_CARTAO = 'flow-project-card button:has-text("delete")'

# MEDIDO 2026-09-05: o botão da home traz a ligadura e o rótulo, "add novo projeto".
# Clicar nele CRIA E ENTRA no projeto direto, sem pedir nome — mesma forma da aplicação
# antiga, e por isso criar continua sendo DUAS etapas: criar-e-entrar, depois renomear.
#
# Quem tratar isso como uma etapa só acaba com uma conta cheia de projeto sem título, e
# a adoção por nome para de funcionar: a geração seguinte do mesmo produto cria outro.
BOTAO_NOVO_PROJETO = 'button:has-text("novo projeto")'

# Dentro do projeto, o título é um `flow-editable-text` com um input dentro.
#
# SEM `aria-label` NO SELETOR, e de propósito. A primeira versão foi
# `input[aria-label="texto editavel"]` e casou ZERO: o rótulo real tem maiúscula e
# acento ("Texto editável"), e seletor de atributo CSS é sensível a caixa. O valor
# saiu da saída de uma sonda, que passa tudo por `dom.seguro` — normalizado PARA
# IMPRIMIR, nunca para virar seletor. Foi o TERCEIRO erro do mesmo tipo num dia.
#
# O escopo do elemento customizado resolve sem depender de rótulo: dentro de
# `flow-editable-text` só existe o input do título. O outro input da tela (a busca)
# fica fora dele.
CAMPO_NOME_PROJETO = "flow-editable-text input"
BOTAO_CONCLUIR_NOME = PENDENTE     # não há botão "Concluir"; fecha-se com Enter

# O `more_vert` do CABEÇALHO do projeto abre: "edit renomear", "delete ver lixeira",
# "delete excluir". Os dois últimos são destrutivos e estão em NAO_CURAVEIS.
MENU_RENOMEAR = '[role=menuitem]:has-text("renomear")'
MENU_VER_LIXEIRA = '[role=menuitem]:has-text("ver lixeira")'
MENU_EXCLUIR_PROJETO = '[role=menuitem]:has-text("excluir")'

# ------------------------------------------------------------------ barra do prompt
#
# MEDIDO: a barra inteira é `flow-prompt-box` > `flow-base-prompt-box`, e dentro dela
# ficam o editor, o `+`, o interruptor do Agente e o botão de enviar.
BARRA_PROMPT = "flow-base-prompt-box"

# O editor é um `contenteditable` DENTRO de `flow-rich-text-editor`. O escopo importa:
# `[contenteditable=true]` solto pode casar outro campo da página no futuro.
CAMPO_PROMPT = "flow-rich-text-editor [contenteditable=true]"

# MEDIDO: `flow-generate-icon-button` embrulha o botão de enviar (ligadura
# `arrow_forward`). Na aplicação antiga, dois botões continham a palavra "Criar" e só a
# ligadura separava; aqui o elemento customizado resolve sozinho.
BOTAO_ENVIAR = "flow-generate-icon-button button"

# MEDIDO nos dois estados, igual à aplicação antiga: o atributo EXISTE ligado e
# desligado, e é ele que diz o estado.
BOTAO_AGENTE = "flow-agent-mode-toggle-chip button"
ATRIBUTO_AGENTE = "aria-pressed"
AGENTE_LIGADO = "true"
AGENTE_DESLIGADO = "false"

# MEDIDO 2026-09-05 comparando a barra vazia com a barra com uma referência. Com
# referência anexada aparecem três coisas que não existiam:
#
#     button "cancel"  aria-label="element…"   -> tira AQUELA referência
#     img    alt="imagem do elemento"          -> a ficha
#     button "close"                           -> some quando não há nada
#
# `_limpar_referencias` usa o `cancel` de cada ficha, e não o `close`: o primeiro tem
# efeito conhecido e verificável (a ficha some), enquanto o segundo é uma suposição
# sobre o que "fechar" significa ali. Limpar rascunho errado apaga o prompt do usuário.
BOTAO_TIRAR_REFERENCIA = f'{BARRA_PROMPT} button:has-text("cancel")'
BOTAO_APAGAR_COMANDO = PENDENTE    # o `close` existe, mas o efeito dele não foi medido

# --------------------------------------------------------- frames: início e fim
#
# MEDIDO 2026-09-05: em modo FRAMES a barra do prompt troca de forma. Entra um
# `flow-ingredient-bar` com TRÊS botões — `início`, `swap_horiz` (troca os dois) e
# `fim` — e o `flow-add-menu` fica SEM botão clicável.
#
# Era isso que fazia `frames2v` morrer com
#     TimeoutError: waiting for locator("flow-add-menu button")
# — o `+` do anexo comum simplesmente não existe ali.
#
# A ESCOLHA É POR POSIÇÃO, e isso normalmente seria erro. Aqui é aceitável por três
# razões, e só com as três juntas: a barra tem exatamente dois slots, o do meio se
# identifica por ligadura (`swap_horiz`, que não é traduzida), e o resultado é
# CONFERIDO depois — cada slot tem que mostrar o arquivo que se pediu. Sem a
# conferência, isto seria o mesmo `.last` que já mandou vídeo para a lixeira.
#
# Casar por texto não serve: o rótulo é traduzido e acentuado ("Início"), e
# `has-text` é sensível a acento.
BARRA_INGREDIENTES = "flow-ingredient-bar"
SLOT_FRAME = 'flow-ingredient-bar button:not(:has-text("swap_horiz"))'
BOTAO_TROCAR_FRAMES = 'flow-ingredient-bar button:has-text("swap_horiz")'

# O SLOT ABRE OUTRO SELETOR DE MIDIA, e nao o do `+`. MEDIDO 2026-09-05 clicando em
# `inicio`: abre um overlay com 4 botoes, NENHUMA aba, filtro de tipo, busca e
# `upload` — algo como
#     ["teste skill", "close", "image", "imagens", "arrow_drop_down", "search",
#      "upload", "e511c628ec3c92a4.png"]
#
# O seletor do `+` tem 7 ABAS; este nao tem nenhuma. Exigir abas aqui reprovava um
# painel que estava aberto — foi o "o slot inicio nao abriu o seletor de midia".
#
# O sinal de montado e o botao `upload`: ele existe com acervo cheio E VAZIO, que e a
# mesma razao pela qual as abas servem no outro painel. Contar itens nao serve, pela
# regra de sempre: acervo vazio e estado legitimo.
# O POPOVER DO SLOT, e ele NAO E o painel do `+`. MEDIDO 2026-09-05 clicando em
# `inicio` numa janela maximizada (a mesma condicao do transporte):
#
#     titulo  : "selecione uma imagem de ..."
#     conteudo: seletor de projeto, busca, ordenacao, os itens do acervo
#     acao    : "incluir no comando"
#     upload  : `flow-media-upload` EXISTE mas esta VAZIO e invisivel
#
# Duas consequencias que mudam o desenho:
#
# 1. O sinal de "abriu" e o elemento NOMEADO, nao as abas — este painel nao tem
#    nenhuma, e exigir abas reprovava um popover que estava aberto.
# 2. NAO DA PARA ENVIAR ARQUIVO POR AQUI. O slot so escolhe do acervo. Por isso o
#    envio dos frames acontece ANTES de trocar para o modo Frames, enquanto o `+`
#    ainda existe na barra.
#
# E o layout muda com o tamanho da janela: numa janela pequena aparece
# `flow-mobile-add-menu`, com botao de upload proprio. O transporte roda maximizado, e
# e o popover que vale.
POPOVER_SLOT = "flow-add-menu-popover-content"

# ----------------------------------------------------------------- anexar / mídia
#
# MEDIDO: o `+` vive em `flow-add-menu`. O painel que ele abre é um overlay do CDK.
BOTAO_ANEXAR = "flow-add-menu button"
ATRIBUTO_ANEXAR = "aria-expanded"
ANEXAR_ABERTO = "true"
ANEXAR_FECHADO = "false"

# O painel de configuração e o seletor de mídia moram no MESMO tipo de contêiner: o
# overlay do Angular CDK. Não há `[role=dialog]` como na aplicação antiga.
OVERLAY = ".cdk-overlay-pane"
SELETOR_MIDIA = OVERLAY
PAINEL_CHIP = OVERLAY

# MEDIDO no menu do `+`: um botão "upload / enviar midia", os itens do acervo como
# `[role=option]` (texto = nome do arquivo + tipo), e "incluir no comando" no fim.
BOTAO_ENVIAR_MIDIA = f'{OVERLAY} button:has-text("enviar")'
ITEM_MIDIA = "[role=option]"
BOTAO_INCLUIR_NO_COMANDO = f'{OVERLAY} button:has-text("incluir no comando")'
# O painel tem busca própria — útil para achar um item sem depender de rolagem.
CAMPO_BUSCA_MIDIA = 'input[aria-label*="pesquisar recursos"]'

# O ENVIO DE ARQUIVO MUDOU DE MECANISMO, e isto não é detalhe.
#
# MEDIDO 2026-09-05: NÃO existe `input[type=file]` no DOM esperando arquivo. Clicar em
# "enviar mídia" cria o input na hora e abre o SELETOR NATIVO do sistema:
#
#     <input type="file" multiple style="display:none"
#            accept=".png,.jpg,.jpeg,.webp,.gif,.heif,.heic,.mp4,.m4v,.mov,.3gp,.avi">
#
# Ou seja, `set_input_files` num seletor fixo — o que a aplicação antiga fazia — falha
# aqui, e falha do pior jeito: sem achar o elemento. O caminho é
# `pagina.expect_file_chooser()` em volta do clique.
#
# `multiple=true` MEDIDO: dá para mandar mais de um arquivo numa tacada. Isso pode
# fechar sozinho o defeito das duas referências que custou caro na aplicação antiga —
# mas NÃO ESTÁ MEDIDO se os dois entram no comando juntos ou se ainda é preciso incluir
# um a um. Medir antes de simplificar.
UPLOAD = PENDENTE          # não há input fixo; ver `UPLOAD_POR_SELETOR_NATIVO`
UPLOAD_POR_SELETOR_NATIVO = True
UPLOAD_ACEITA = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".heif", ".heic",
                 ".mp4", ".m4v", ".mov", ".3gp", ".avi")
UPLOAD_MULTIPLO = True

# O id estável da mídia sai do `src` da miniatura — mesma ideia da aplicação antiga,
# onde nome de arquivo NÃO identificava item (dois envios do mesmo arquivo geram dois
# itens de mesmo nome e ids diferentes). Ver `analise.escolher_novo_item`.
ITEM_MIDIA_POR_ID = '[role=option]:has([src*="{id}"])'

# O `src` DA MINIATURA MUDA AO LONGO DA VIDA DO ITEM. Medido 2026-09-05 enviando um
# arquivo e acompanhando o item por 40 s:
#
#     recém-enviado : https://flow-content.google/image/1e56f02d-674c-49c9-...
#     depois de um tempo : https://lh3.googleusercontent.com/asb/ab-nouyx9lmd...
#
# São HOSTS diferentes para a mesma mídia — o segundo é a miniatura em cache. Isso tem
# uma consequência prática: `src` identifica bem DENTRO de uma operação curta (que é o
# caso do anexo, medido em ~12 s), e NÃO serve para comparar entre sessões.
#
# Por isso o retrato antes/depois do anexo não confia só nele: o item novo tem que
# estar fora do retrato E casar o nome do arquivo enviado. Duas condições erram menos
# que uma — e a que sobra sozinha aqui seria justamente a instável.
# PERMISSIVO de propósito, como era na aplicação antiga: o id é OPACO. Exigir formato
# (hexadecimal, 36 caracteres) não protege de nada — o id vem do Google, não é validado
# aqui — e quebraria no dia em que ele mudar de forma, que é justamente o dia em que se
# precisa que a leitura continue funcionando.
PADRAO_ID_MIDIA = (r"(?:flow-content\.google/(?:image|video)/([^?&#/]+)"
                   r"|googleusercontent\.com/[^/]+/([^?&#/]+)"
                   r"|[?&]name=([^&#]+))")

# AS ABAS SÃO O SINAL DE "O PAINEL MONTOU", e a escolha é medida contra o caso que
# causou a regressão de agosto.
#
# MEDIDO 2026-09-05, o MESMO painel em dois projetos:
#
#                          projeto VAZIO   projeto cheio
#     [role=option]              0              15
#     [role=tab]                 7               7
#     "enviar mídia"             1               1
#
# "A lista tem itens" parece prova de que o painel montou e NÃO É: num projeto novo o
# acervo está legitimamente vazio, e usar isso como critério travou todo projeto novo
# do usuário — como ele tem um projeto por produto, todo produto novo nascia sem
# conseguir anexar. As abas existem com acervo cheio E vazio.
#
# As sete: tudo · imagens · vídeos · vozes · avatares · (rolagem) · enviar mídia.
ABAS_SELETOR_MIDIA = f"{OVERLAY} [role=tab]"

# VOZES e AVATARES são categorias que a aplicação antiga não tinha. Não foram medidas,
# e podem ser modos de geração que o painel do FlowPool ainda não conhece.
ABA_VOZES = f'{OVERLAY} [role=tab]:has-text("vozes")'
ABA_AVATARES = f'{OVERLAY} [role=tab]:has-text("avatares")'


# MEDIDO: a ficha da referência já anexada é uma `img` dentro da barra do prompt,
# apontando para `flow-content.google/<tipo>/<uuid>?expires=...`. É o MESMO id do item
# do acervo, o que permite conferir QUAL entrou — e não só quantas.
REFERENCIA_ANEXADA = 'flow-base-prompt-box img[src*="flow-content"]'

# COMO SE INCLUI UMA REFERÊNCIA — e aqui a aplicação nova é MUITO mais simples.
#
# MEDIDO 2026-09-05, cronometrado: um clique no `[role=option]` INCLUI e FECHA o menu.
#
#     antes:  15 opções, menu aberto, 0 fichas
#     +1s:     0 opções, menu fechado, 1 ficha com o id do arquivo clicado
#
# Não há mais o par "marcar item" + "Incluir no comando" que a aplicação antiga exigia.
# E com ele somem, de uma vez, três defeitos que custaram caro lá: a seleção exclusiva
# que desmarcava ao clicar de novo, o índice decorado numa lista que reordena, e o
# homônimo velho do acervo passando por recém-enviado.
#
# `BOTAO_INCLUIR_NO_COMANDO` continua existindo no painel e NÃO é o caminho normal.
CLIQUE_NO_ITEM_JA_INCLUI = True

# MEDIDO: arquivo recém-enviado entra no TOPO da lista. Está registrado como fato, não
# como forma de escolher: a escolha continua sendo por id (regra 1).
ITENS_NOVOS_PRIMEIRO = True

# ----------------------------------------------------------- painel de configuração
#
# MEDIDO 2026-09-05, os quatro modos. O painel tem SETE grupos em vídeo e QUATRO em
# imagem, todos marcando o escolhido com `aria-checked`:
#
#   [image imagem] [videocam video]
#   [crop_free frames] [chrome_extension elementos]     <- só em vídeo, sempre visíveis
#   [crop_16_9 16:9] [crop_9_16 9:16]                   <- imagem tem as cinco
#   [<modelo>  arrow_drop_down]
#   [360p info] [720p]                                  <- só em vídeo
#   [4s] [6s] [8s] [10s]                                <- só em vídeo
#   [x1] [x2] [x3] [x4]
#
# O chip fechado escreve, por exemplo: "video . 360p . 8s crop_16_9 x2".
CHIP_CONFIG = f'{BARRA_PROMPT} button:has-text("crop_")'

# O marcador de seleção. VER A REGRA 4: é a novidade que torna a conferência confiável.
ATRIBUTO_ESCOLHIDO = "aria-checked"
ESCOLHIDO = "true"

# Modos: casar pela LIGADURA do ícone, que não é traduzida.
MODO_ICONE = {
    "imagem": "image",
    "t2v": "videocam",
    "frames2v": "crop_free",
    "ingredients2v": "chrome_extension",
}
# RELATIVOS ao painel: quem clica escopa em `_painel(...)`. Prefixar aqui
# produziria `.cdk-overlay-pane .cdk-overlay-pane ...` e casaria ZERO.
BOTAO_MODO = {k: f'button:has-text("{lig}")' for k, lig in MODO_ICONE.items()}

# MEDIDO: `frames` e `elementos` agora ficam SEMPRE VISÍVEIS sob vídeo, e um dos dois
# está sempre marcado. Na aplicação antiga eles só apareciam depois de entrar em vídeo,
# e por isso existia uma escada de duas etapas.
#
# A escada CONTINUA valendo, por outro motivo: vindo de IMAGEM, a linha de submodo não
# existe — é preciso entrar em vídeo primeiro para ela aparecer.
MODO_PAI = {
    "frames2v": "t2v",
    "ingredients2v": "t2v",
}

# NÃO MEDIDO: se existe vídeo "puro", sem submodo. Nas três medições, um de
# frames/elementos estava sempre marcado. Pode ser que `elementos` sem referência SEJA
# o t2v de hoje. Enquanto não se medir, `t2v` escolhe só `videocam` e aceita o submodo
# em que o painel estiver.
T2V_TEM_SUBMODO_PROPRIO = PENDENTE

PROPORCAO = {
    "16:9": 'button:has-text("crop_16_9")',
    "4:3": 'button:has-text("crop_landscape")',
    "1:1": 'button:has-text("crop_square")',
    "3:4": 'button:has-text("crop_portrait")',
    "9:16": 'button:has-text("crop_9_16")',
}
LIGADURA_PROPORCAO = {
    "16:9": "crop_16_9", "4:3": "crop_landscape", "1:1": "crop_square",
    "3:4": "crop_portrait", "9:16": "crop_9_16",
}
# MEDIDO: vídeo oferece só 16:9 e 9:16; imagem oferece as cinco.
PROPORCOES_POR_MODO = {
    "imagem": ("16:9", "4:3", "1:1", "3:4", "9:16"),
    "t2v": ("9:16", "16:9"),
    "frames2v": ("9:16", "16:9"),
    "ingredients2v": ("9:16", "16:9"),
}

# GRUPO NOVO. Não existia na aplicação antiga, e o PADRÃO É 360p — a antiga era 720p.
# Sem tratar isto, todo vídeo sai na resolução baixa sem ninguém pedir.
RESOLUCAO = {
    "360p": 'button:has-text("360p")',
    "720p": 'button:has-text("720p")',
}
RESOLUCOES = ("360p", "720p")
RESOLUCAO_PADRAO_DO_FLOW = "360p"

# MEDIDO: os quatro valores existem em TODOS os modos de vídeo, inclusive `frames`.
# Isto DERRUBA a medição de 2026-08-21, que dizia que o painel em Frames não tinha
# grupo de duração — aquilo valia para a aplicação antiga e morreu com ela.
DURACAO = {d: f'button:has-text("{d}")' for d in ("4s", "6s", "8s", "10s")}

QUANTIDADE = {n: f'button:has-text("x{n}")' for n in (1, 2, 3, 4)}

# O dropdown de família de modelos, dentro do painel.
DROPDOWN_MODELO = 'button:has-text("arrow_drop_down")'

# MEDIDO nas opções do dropdown, já normalizado (`dom.sem_acento`). Os de vídeo vêm
# com a ligadura `volume_up` na frente; a comparação é por SUBSTRING do rótulo.
#
# ATENÇÃO ao prefixo: "veo 3.1 - lite" NÃO é prefixo de "veo 3.1 - fast", mas
# "omni 1.1 flash" pode virar prefixo de um "omni 1.1 flash pro" amanhã. Quem escolhe
# usa `analise.escolher_opcao`, que trata empate como erro.
# CAIXA IMPORTA, e isto ja custou uma execucao. `indice_por_sufixo` compara com
# `endswith` SEM normalizar, entao o rotulo aqui tem que ter as maiusculas exatas do
# DOM. A primeira versao deste dicionario saiu em minusculas porque foi assim que a
# SONDA imprimiu — e a sonda passa tudo por `dom.seguro`, que baixa a caixa e tira o
# acento SO PARA IMPRIMIR. Ler a saida de uma ferramenta de diagnostico como se fosse o
# DOM e um jeito silencioso de errar.
#
# MEDIDO no dropdown, texto cru:
#   imagem : "<emoji> Nano Banana Pro", "<emoji> Nano Banana 2", "<emoji> Nano Banana 2 Lite"
#   video  : "volume_up" + quebra de linha + "Omni 1.1 Flash" (idem para os tres Veo)
ROTULO_MODELO: dict[str, str | tuple[str, ...]] = {
    "omni-flash": "Omni 1.1 Flash",
    "veo-lite": "Veo 3.1 - Lite",
    "veo-fast": "Veo 3.1 - Fast",
    "veo-quality": "Veo 3.1 - Quality",
    "nano-banana": "Nano Banana Pro",
    # os dois medidos em 2026-09-06 no mesmo dropdown; "Nano Banana 2" nao e sufixo de
    # "Nano Banana 2 Lite", entao o `endswith` de `analise` separa os dois
    # ⚠️ DOIS NOMES, NA ORDEM. MEDIDO 2026-10-05 21:32 (job 6015): o Flow renomeou para
    # "Nano Banana 2.1", e a troca chega CONTA A CONTA — as 20:21 a conta 3 ainda
    # mostrava o antigo. "2.1" nao termina em "2" nem em "2 Lite": o sufixo separa os
    # tres. Quando nenhuma conta mostrar mais o antigo, ele sai daqui.
    "nano-banana-2": ("Nano Banana 2.1", "Nano Banana 2"),
    "nano-banana-2-lite": "Nano Banana 2 Lite",
}
OPCAO_MODELO = f"{OVERLAY} [role=menuitem], {OVERLAY} [role=option]"

# O custo aparece como texto no rodapé do painel: "A geração vai usar N créditos".
CUSTO_DA_GERACAO = "text=/vai usar/i"

# --------------------------------------------------------------------- grade
#
# MEDIDO: cada resultado é um `flow-tile-container`, e o TIPO tem elemento próprio —
# `flow-image-tile` ou `flow-video-tile`. Isso é melhor que a aplicação antiga, onde o
# tipo do card precisava ser lembrado por fora (`_modo_do_card`).
GRADE = "flow-grid-tile-container"
CARD_GRADE = "flow-tile-container"
CARD_IMAGEM = "flow-image-tile"
CARD_VIDEO = "flow-video-tile"
# A barra de ações que aparece ao passar o mouse sobre um resultado.
HOTBAR_VIDEO = "flow-video-hotbar"
HOTBAR_IMAGEM = "flow-image-hotbar"
RODAPE_TILE = "flow-tile-hover-footer"

# PRONTO vs GERANDO, MEDIDO numa geração de imagem ao vivo em 2026-09-05:
#
#   gerando: o texto do card traz a PORCENTAGEM
#            ['image', '20%',  '<o prompt>']
#            ['image', '86%',  '<o prompt>']
#   pronto:  a porcentagem some, entra um `img`/`video` carregado, e o card ganha id
#            ['<o prompt>', 'keyboard_return']
#
# A regra que vale é a mesma da aplicação antiga: PRONTO É TER MÍDIA CARREGADA. A
# porcentagem não serve de sinal de conclusão — ela some antes da imagem aparecer, e
# confiar nela devolveria um card vazio.
# RELATIVO ao card: quem usa faz `card.locator(...)`. Absoluto aqui viraria
# `flow-tile-container flow-tile-container img` e casaria zero.
CARD_MIDIA = "img, video"
PADRAO_PORCENTAGEM = r"(\d{1,3})\s*%"

# ⚠️ O TERCEIRO ESTADO, e ele custou uma tarde. MEDIDO 2026-09-13 no log de passo, no
# texto de um card que o app tinha acabado de dar por morto:
#
#     ['movie', 'na fila', 'the large container ship glides steadily across...']
#
# Entre aceitar o pedido e começar a gerar, o Flow ENFILEIRA. Card enfileirado não tem
# porcentagem e não tem mídia — os dois únicos sinais que `estado_do_card` conhecia —,
# então ele era indistinguível de um card travado e morria em `SEM_PROGRESSO_S`. O
# usuário abria a conta, via o vídeo sendo gerado, e o painel dizia que falhou.
#
# `na fila` é o texto MEDIDO; `in queue`/`queued` entram pelo mesmo motivo que em
# `CARD_LIMITE_DE_USO` — o rótulo vem do i18n do Google e um deploy pode trocá-lo.
PADRAO_NA_FILA = r"(?i)\bna fila\b|\bin queue\b|\bqueued\b"

# O ID DO RESULTADO MUDOU DE FORMA. A aplicação antiga trazia
# `...getMediaUrlRedirect?name=<uuid>`; a nova traz o conteúdo noutro domínio:
#
#     https://flow-content.google/image/b25a2e51-0d81-4d99-8c4b-47b35c5d882b
#     https://flow-content.google/video/<uuid>
#
# O tipo vem no caminho, o que dá de graça a informação que a aplicação antiga
# precisava guardar por fora (`_modo_do_card`) para saber que resolução pedir no
# download.
PADRAO_ID_RESULTADO = r"flow-content\.google/(image|video)/([0-9a-fA-F-]{36})"

# MEDIDO 2026-09-09 por print do card, na aplicação nova. Os dois estavam em
# PENDENTE, e `_card_falhou` responde False para seletor pendente — ou seja, o
# FlowPool era INCAPAZ de enxergar qualquer card de falha. O sintoma: o painel
# mostrava "gerando" enquanto o card na tela já dizia Falha, e o job só saía disso
# pelo teto do polling, martelando a mesma conta por horas.
#
# O card tem três linhas:
#     Falha
#     Você chegou ao limite de uso. Tente de novo mais tarde.
#     Não houve cobrança por esta geração.
#
# `CARD_FALHA_TITULO` casa o título sozinho, que é o que TODA falha tem — é o sinal
# estrutural, e por isso ele é o que decide "falhou". Casar pela frase do corpo
# reabriria o buraco na próxima redação do Google.
CARD_FALHA = 'text=/Tente de novo mais tarde|Tente usar outro comando/i'
CARD_FALHA_TITULO = 'text=/^\\s*Falha\\s*$/i'

# ⚠️ FALHA POR LIMITE DE USO NÃO É FALHA DE GERAÇÃO — é hora de trocar de conta.
# A conta ainda MOSTRA saldo, mas o Flow recusa: "Você chegou ao limite de uso" e,
# logo abaixo, "Não houve cobrança por esta geração". Repetir na mesma conta é
# garantido não funcionar, e era o que fazia o robô martelar.
#
# Quem trata isso é `SaldoInsuficiente` no orquestrador, que já faz exatamente o
# certo: estorna o crédito reservado, marca a conta com prazo (`seca_ate`), NÃO
# gasta tentativa do job e devolve para a fila — outra conta pega.
CARD_LIMITE_DE_USO = 'text=/limite de uso|usage limit/i'
CARD_SEM_COBRANCA = 'text=/n[ãa]o houve cobran[çc]a|no charge/i'

# ⚠️ O BLOQUEIO ANTIABUSO — O SEGUNDO MOTIVO DE RECUSA SEM COBRANÇA, e ele custou os
# jobs 583-588 inteiros. MEDIDO por print em 2026-09-09
# (`scratch/falhas/consultar-error.png`), o card tem as três linhas:
#
#     Falha
#     Notamos uma atividade incomum. Aguarde alguns instantes antes de tentar de novo
#     ou acesse a Central de Ajuda para mais informações.
#     Não houve cobrança por esta geração.
#
# A ação é a MESMA do limite de uso — nada foi cobrado, repetir nesta conta não
# adianta, troca-se de conta —, e por isso as duas frases entram na MESMA decisão
# (`grade.recusa_sem_cobranca`).
#
# ⚠️ O MOTIVO ERA O NAVEGADOR, NÃO A CONCORRÊNCIA. Em 09/09 este comentário dizia que
# era "o Google reagindo à concorrência: três contas disparando no mesmo segundo pelo
# mesmo IP" — a leitura razoável do que estava na tela, e errada. MEDIDO 2026-09-10:
# o job 598 levou esta mesma recusa em CINCO contas, uma de cada vez, 30 a 40 s entre
# elas, cada uma por um proxy diferente; e a mesma conta, pelo mesmo IP e no mesmo
# perfil, gerava vídeo quando o Chrome subia pelo botão Abrir. A diferença medida
# entre os dois, em 19 sinais, foi `navigator.webdriver` — ver
# `sessao.argumentos_playwright`. Imagem passava nos dois: o Google gasta antiabuso
# no recurso caro, que é por que a recusa parecia aleatória.
CARD_ATIVIDADE_INCOMUM = 'text=/atividade incomum|unusual activity/i'

# DUAS FALHAS QUE NAO SAO DA CONTA. MEDIDO 2026-09-23 por dois prints do Davi, os
# dois com o mesmo titulo "Falha" e "Nao houve cobranca por esta geracao":
#
#     Esse comando pode violar nossas politicas contra a geracao de conteudo
#     prejudicial. Tente usar outro comando ou envie feedback.
#
#     O Flow esta com alta demanda. Tente de novo mais tarde.
#
# Nao entram em `grade._MOTIVOS_DE_RECUSA`, que e "a CONTA foi recusada, troque de
# conta": politica e o PROMPT (qualquer conta recusa igual) e alta demanda e o Google.
# O ingles entra pelo mesmo motivo de `CARD_LIMITE_DE_USO` — o rotulo vem do i18n.
CARD_POLITICA = 'text=/violar nossas pol[ií]ticas|conte[úu]do prejudicial|violate our policies/i'
CARD_ALTA_DEMANDA = 'text=/alta demanda|high demand/i'

BOTAO_REPETIR_CARD = PENDENTE

# MEDIDO: os menus são `mat-menu` renderizados no overlay do CDK.
MENU_CARD = f"{OVERLAY}"
MENU_ITEM = "[role=menuitem]"

# A barra de ação que aparece ao passar o mouse sobre um resultado, e o `more_vert`
# que abre o menu completo.
BOTAO_MAIS_DO_CARD = f'{CARD_GRADE} button:has-text("more_vert")'
BOTAO_REUTILIZAR_COMANDO = f'{CARD_GRADE} button[aria-label*="reutilizar"]'

# O MENU DO RESULTADO, MEDIDO INTEIRO em 2026-09-05, na ordem em que aparece:
#
#     favorite        / favorito
#     keyboard_return / reutilizar comando
#     motion_blur     / animar
#     add_2           / incluir no comando
#     download        / fazer o download          <- tem submenu (aria-haspopup=menu)
#     content_copy    / copiar
#     edit            / renomear
#     share           / compartilhar
#     photo_library   / definir capa do projeto
#     flag            / sinalizar saida
#     delete          / mover para a lixeira      <- O ÚLTIMO
#
# "Mover para a lixeira" continua sendo o ÚLTIMO item, em outra aplicação e em outro
# framework. A cicatriz não mudou de lugar: um `.last` aqui apaga o trabalho do
# usuário. Escolha por TEXTO, e ambiguidade é erro.
MENU_BAIXAR = '[role=menuitem]:has-text("fazer o download")'
MENU_LIXEIRA = '[role=menuitem]:has-text("mover para a lixeira")'
MENU_ANIMAR = '[role=menuitem]:has-text("animar")'
MENU_REUTILIZAR = '[role=menuitem]:has-text("reutilizar comando")'

# O submenu de download, MEDIDO num resultado de IMAGEM:
#     1k / tamanho original
#     2k / aprimorada
#     4k / aprimorada
# Falta medir o de VÍDEO — a aplicação antiga oferecia 270p/720p/1080p/4K ali.
#
# O valor é a PRIMEIRA PALAVRA do rótulo, não um seletor: os rótulos têm duas linhas
# ("1k" e "tamanho original"), e rótulo inteiro estoura o parser de seletor CSS. Quem
# escolhe é `analise.escolher_opcao`, que trata empate como erro.
# MEDIDO nos dois tipos de resultado, em 2026-09-05:
#
#   imagem : "1k / tamanho original"  "2k / aprimorada"   "4k / aprimorada"
#   vídeo  : "270p / gif animado"     "720p / tamanho original"
#            "1080p / aprimorada"     "4k / aprimorada"
#
# O valor é a PRIMEIRA PALAVRA do rótulo, não um seletor: os rótulos têm duas linhas e
# rótulo inteiro estoura o parser de seletor CSS. Quem escolhe é `analise.escolher_opcao`,
# que trata empate como erro.
#
# Note que "270p" é GIF, não vídeo pequeno — pedir a resolução mais barata ali traria
# outro formato. E "4k" existe nos dois, mas exige plano pago; fica fora do que o
# transporte pede por padrão.
BAIXAR = {
    "1k": "1k",
    "2k": "2k",
    "270p": "270p",
    "720p": "720p",
    "1080p": "1080p",
    "4k": "4k",
}

# O menu do card de VÍDEO tem itens que o de imagem não tem — medido:
#     play_movies   / adicionar ao cenario
#     video_library / publicar no youtube
# (no de imagem, o lugar deles é ocupado por `motion_blur / animar`)
MENU_ADICIONAR_AO_CENARIO = '[role=menuitem]:has-text("adicionar ao cenario")'
MENU_PUBLICAR_YOUTUBE = '[role=menuitem]:has-text("publicar no youtube")'

# ARMADILHA MEDIDA: ao abrir o submenu, DOIS panes ficam abertos ao mesmo tempo — o
# menu pai e o submenu. `.last` entre eles é tão frágil quanto índice em lista que
# reordena, e o vizinho errado aqui é "mover para a lixeira". Quem escolhe percorre
# TODOS os panes e acha o que CONTÉM a opção (`analise.achar_no_submenu`).
PANES_ABERTOS = OVERLAY

# NÃO MEDIDO: se o novo entra no começo ou no fim da grade. Enquanto pendente, quem
# identifica um resultado usa o RETRATO ANTES/DEPOIS, nunca a posição.
CARDS_NOVOS_PRIMEIRO = PENDENTE

# O menu de lixeira do card tem elemento próprio na aplicação nova. Está aqui para ser
# RECONHECIDO e evitado: um `.last` num menu já mandou um vídeo do usuário para a
# lixeira, e essa cicatriz não depende de qual framework desenha o menu.
MENU_LIXEIRA_CARD = "flow-media-trash-context-menu"

# ---------------------------------------------------------------- conta e avisos
#
# MEDIDO: o avatar fica em `flow-header-user-icon`; a foto vem de `lh3.google.com`,
# e não mais de `googleusercontent`.
AVATAR_CONTA = "flow-header-user-icon img"

# NÃO É UM `button`, e o chute custou uma medição. MEDIDO 2026-09-05: o alvo é um
#     <div role="button" tabindex="0" aria-label="detalhes da conta" class="header-...">
# dentro de `flow-header-user-icon`. `flow-header-user-icon button` casa ZERO, e o
# sintoma é um timeout que fala do avatar sem dizer que ele não é botão.
BOTAO_AVATAR = 'flow-header-user-icon [role=button]'

# MEDIDO no menu que esse clique abre:
#     "50 créditos do Google Flow"
#     "os créditos são atualizados diariamente"
PADRAO_CREDITOS = r"(?i)(\d[\d.,]*)\s+cr[ée]ditos"

# NÃO MEDIDO na aplicação nova. `flow-credit-banner` existe no projeto, mas não trouxe
# texto de crédito na medição — pode aparecer só quando o saldo está baixo.
BANNER_CREDITO = "flow-credit-banner"
# SALDO INSUFICIENTE SE RECONHECE ANTES DE CLICAR, e isto e melhor que o aviso da
# aplicação antiga.
#
# MEDIDO 2026-09-05, sem gastar nada: numa conta com ~36 créditos, configurar
# "Veo 3.1 - Quality" (que o painel anuncia como 100 créditos) faz o BOTÃO DE ENVIAR
# DESAPARECER, e um botão `info` toma o lugar dele na barra:
#
#     saldo suficiente : add · agente · <chip> · arrow_forward
#     saldo curto      : add · agente · <chip> · info
#
# `flow-generate-icon-button button` passa a casar ZERO.
#
# A aplicação antiga só avisava DEPOIS do clique, com "Seus créditos do Google Flow
# acabaram" — e foi por ler esse aviso antes do card que um job voltava para a fila
# com o vídeo já gerado. Aqui não há esse risco: se não há botão, nada foi enviado.
#
# O texto do aviso pós-clique continua NÃO MEDIDO na aplicação nova, e continua
# PENDENTE abaixo. Ele deixou de ser o caminho principal.
BOTAO_INFO_BARRA = f'{BARRA_PROMPT} button:has-text("info")'
AVISO_SEM_CREDITO = PENDENTE
AVISO_ALTA_DEMANDA = PENDENTE
# O AVISO DE DIREITOS. MEDIDO 2026-09-23 so pelo PRINT
# (`scratch/falhas/saldo-timeouterror.png`), nao pelo DOM: modal "Direitos de uso desta
# imagem", botoes "Cancelar" e "Concordo", com `cdk-overlay-dark-backdrop` por tras e
# ABERTO POR CIMA do seletor de midia. Escape nao o fecha. Enquanto era PENDENTE, ele
# travou o upload da conta 12 (jobs 2327/2382/2393 refeitos na Pro) e a leitura de
# saldo (14 `reconciliacao_falhou` por "intercepts pointer events"). O painel
# (`.cdk-overlay-pane`) e medido; o TEXTO so foi visto no print — o `conferir` nao o
# checa, e a prova e o log "aviso de direitos dispensado" no proximo upload.
AVISO_DIREITOS = f'{OVERLAY}:has-text("Direitos de uso")'

# O BANNER DE COOKIES DO `labs.google/fx`. MEDIDO 2026-09-09 por sonda no perfil da
# conta 2, lendo o DOM sem clicar em nada:
#
#     <div class="glue-cookie-notification-bar" id="glue-cookie-notification-bar-1"
#          aria-hidden="true" inert="true" role="region" data-nosnippet>
#       <p class="glue-cookie-notification-bar__text">labs.google/fx usa cookies ...</p>
#       <a  class="glue-cookie-notification-bar__more">Saiba mais</a>
#       <button class="glue-cookie-notification-bar__accept">Concordo</button>
#       <button class="glue-cookie-notification-bar__reject">Agora não</button>
#     </div>
#
# ⚠️ ELE EXISTE NO DOM MESMO QUANDO NÃO ESTÁ NA TELA. Na conta 2 a medição pegou o
# banner com `inert=true`, `aria-hidden=true` e ALTURA ZERO — inerte, sem cobrir nada.
# Na conta 6 ele estava visível, ocupando o rodapé bem em cima da barra do prompt (ver
# `scratch/falhas/gerar-ingredients2v-geracaofalhou.png`). Depende do perfil ter ou não
# o consentimento gravado. Por isso quem dispensa PERGUNTA SE ESTÁ VISÍVEL, e não se
# existe: clicar num botão inerte falha, e falha por um motivo que não é o de verdade.
BANNER_COOKIES = "div.glue-cookie-notification-bar"

# ⚠️ `__reject`, "Agora não" — NUNCA `__accept`. A escolha é do Davi, dada em
# 2026-09-09, e é a que preserva privacidade: recusa os cookies não essenciais. O
# vizinho na mesma barra é "Concordo", e é por isso que este seletor mira a classe
# exata do botão e não a posição.
BANNER_COOKIES_RECUSAR = (
    f"{BANNER_COOKIES} button.glue-cookie-notification-bar__reject")
# O "Concordo" DENTRO do aviso — o do banner de cookies tambem se chama assim e
# mora fora do overlay. Aceitar autorizado pelo Davi em 2026-08-20 e de novo em
# 2026-09-23; continua em `NAO_CURAVEIS`.
BOTAO_CONCORDO = f'{AVISO_DIREITOS} button:has-text("Concordo")'
# O VÉU DO OVERLAY. MEDIDO por um clique que falhou: com um menu aberto, o Playwright
# recusa o clique seguinte com
#     <div class="cdk-overlay-backdrop ..."> ... intercepts pointer events
# Ou seja, o véu bloqueia TUDO enquanto estiver na tela, e quem não o fecha vê um
# timeout falando do elemento coberto e nunca do que o cobre — foi exatamente assim
# que o aviso de direitos travou a geração na aplicação antiga.
VEU_ABERTO = ".cdk-overlay-backdrop"

# O `more_vert` DO PROJETO, e nao qualquer um: MEDIDO 2026-09-05, a pagina tem DOIS,
# e o do projeto e o unico com o atributo `more-options`.
#
#     button[more-options]              -> 1   (o do projeto)
#     button:has-text("more_vert")      -> 2   (ambiguo, seria erro)
#     button[aria-label*="mais op"]     -> 0   (!)
#
# O ZERO do `aria-label` e a armadilha, e ela ja me pegou duas vezes hoje: seletor de
# atributo CSS e SENSIVEL A CAIXA, e o rotulo real tem maiuscula e acento. A saida da
# sonda mostrava "mais opcoes" porque `dom.seguro` normaliza PARA IMPRIMIR — copiar
# valor de seletor da saida de uma ferramenta de diagnostico e errar em silencio.
#
# O atributo `more-options` e melhor que o rotulo por outro motivo tambem: existe
# porque o codigo do Google o usa, e nao muda com o idioma da interface.
BOTAO_MAIS_OPCOES = "button[more-options]"

# ----------------------------------------------------------------- portão de idade
#
# MEDIDO 2026-09-05: contas sem idade verificada terminam aqui, vindas de qualquer URL.
# Reconhecer isto importa porque o sintoma sem ele é "o projeto sumiu da lista" — que
# manda investigar o lugar errado.
URL_PORTAO_IDADE = "/age-restricted"
TEXTO_PORTAO_IDADE = "text=/18 anos ou mais/i"

# --------------------------------------------------------------- não curáveis
#
# A REGRA: se um seletor curado errado dispara ação irreversível ou gasta dinheiro, ele
# não é curável. Zero candidatos ali significa que a tela mudou de verdade, e a resposta
# certa é erro claro com print — não um modelo de visão chutando naquela barra.
# `BOTAO_ENVIAR` ficou DE FORA, como estava na aplicação antiga, e a razão importa:
# um seletor de envio curado errado clica em algo que NÃO envia — dá erro claro, não
# gasta crédito. Já `BOTAO_AGENTE` está dentro justamente porque o vizinho dele na
# barra é o de enviar, e ali um chute custa uma geração cobrada.
NAO_CURAVEIS = {
    # menus do resultado: o último item é "mover para a lixeira"
    "MENU_CARD", "MENU_ITEM", "MENU_ANIMAR", "MENU_BAIXAR", "MENU_REUTILIZAR",
    "MENU_LIXEIRA_CARD", "MENU_LIXEIRA", "MENU_RENOMEAR", "MENU_EXCLUIR",
    # projetos: apagar e renomear pelo cartão
    "BOTAO_EXCLUIR_PROJETO", "BOTAO_APAGAR_CARTAO", "BOTAO_EDITAR_CARTAO",
    "MENU_EXCLUIR_PROJETO", "MENU_VER_LIXEIRA",
    # aceite em nome do usuário — e recusa também: escolher por alguém o que fazer com
    # os cookies dele não é conserto de interface, e um substituto proposto pelo modelo
    # poderia acabar sendo o "Concordo" ao lado
    "BOTAO_CONCORDO", "CAIXA_CONSENTIMENTO",
    "BANNER_COOKIES", "BANNER_COOKIES_RECUSAR",
    # o interruptor do Agente — vizinho do botão de enviar
    "BOTAO_AGENTE",
    # repetir mora numa fila de botões dentro do card de falha, e um deles apaga
    "BOTAO_REPETIR_CARD",
}

# O que a auto-cura mostra ao modelo sobre cada seletor. Só entra aqui o que for
# curável — descrever um não curável seria convidar o modelo a propor um substituto.
DESCRICAO = {
    "CHIP_CONFIG": "o chip na barra do prompt que resume a configuração da geração "
                   "(algo como 'video 360p 8s 16:9 x2') e abre o painel de opções",
    "BOTAO_ANEXAR": "o botão + da barra do prompt, que abre o seletor de mídia",
    "CAMPO_PROMPT": "o campo de texto onde se escreve o comando da geração",
    "CARD_GRADE": "o contêiner de UM resultado na grade, não a miniatura em si",
    "DROPDOWN_MODELO": "o seletor de família de modelos dentro do painel de opções",
    "BOTAO_MAIS_OPCOES": "no cabeçalho do projeto aberto, o botão de três pontinhos "
                         "que abre renomear / ver lixeira / excluir",
    "BOTAO_NOVO_PROJETO": "na lista de projetos, o botão que cria um projeto novo",
    "CAMPO_NOME_PROJETO": "o campo de texto do título do projeto, no cabeçalho",
    # --- uma entrada por OPCAO do painel. Sem descricao a cura nao acontece, e o
    # teste `test_os_seletores_que_o_transporte_clica_tem_descricao` guarda isso.
    "BOTAO_MODO[imagem]": "no painel de configuração, o botão do modo IMAGEM",
    "BOTAO_MODO[t2v]": "no painel de configuração, o botão do modo VÍDEO",
    "BOTAO_MODO[frames2v]": "no painel de configuração já em vídeo, o botão do submodo "
                            "FRAMES (primeiro e último quadro)",
    "BOTAO_MODO[ingredients2v]": "no painel de configuração já em vídeo, o botão do "
                                 "submodo ELEMENTOS (referências de personagem/produto)",
    "PROPORCAO[16:9]": "no painel de configuração, o botão da proporção 16:9 (deitado)",
    "PROPORCAO[4:3]": "no painel de configuração, o botão da proporção 4:3",
    "PROPORCAO[1:1]": "no painel de configuração, o botão da proporção 1:1 (quadrado)",
    "PROPORCAO[3:4]": "no painel de configuração, o botão da proporção 3:4",
    "PROPORCAO[9:16]": "no painel de configuração, o botão da proporção 9:16 (em pé)",
    "DURACAO[4s]": "no painel de configuração em vídeo, o botão da duração 4s",
    "DURACAO[6s]": "no painel de configuração em vídeo, o botão da duração 6s",
    "DURACAO[8s]": "no painel de configuração em vídeo, o botão da duração 8s",
    "DURACAO[10s]": "no painel de configuração em vídeo, o botão da duração 10s",
    "RESOLUCAO[360p]": "no painel de configuração em vídeo, o botão da resolução 360p",
    "RESOLUCAO[720p]": "no painel de configuração em vídeo, o botão da resolução 720p",
    "QUANTIDADE[1]": "no painel de configuração, o botão de quantidade x1",
    "QUANTIDADE[2]": "no painel de configuração, o botão de quantidade x2",
    "QUANTIDADE[3]": "no painel de configuração, o botão de quantidade x3",
    "QUANTIDADE[4]": "no painel de configuração, o botão de quantidade x4",
    "BOTAO_ENVIAR": "o botão que dispara a geração, no canto direito da barra do "
                    "prompt — a seta para a direita",
}


# --- constantes herdadas, ainda NAO remedidas na aplicacao nova ---------
#
# Estavam no arquivo antigo e continuam sendo consumidas por codigo e por teste. Ficam
# aqui marcadas pelo que sao: NAO conferidas contra o Flow de hoje. Quem depender delas
# tem que tratar como pendente.
BOTAO_EXCLUIR_PROJETO = PENDENTE
BOTAO_PERSONAGENS = PENDENTE
BOTAO_VER_CENAS = PENDENTE
MENU_EXCLUIR = PENDENTE
CONFIG_SEM_AUDIO = PENDENTE

# O numero dentro de "A geracao vai usar N creditos", no rodape do painel. O TEXTO foi
# reencontrado na aplicacao nova (`CUSTO_DA_GERACAO` casa 1 elemento); o padrao segue
# valendo porque a frase e a mesma.
PADRAO_CUSTO = r"(?i)vai usar\s+(\d[\d.,]*)\s+cr[eé]dito"

# O que o CHIP escreve para cada modo, ja normalizado. Na aplicacao nova o chip diz
# "video . 360p . 8s crop_16_9 x2" em video e o nome do modelo em imagem — mesma forma
# de antes. Continua sendo tolerancia, e nao verificacao: quem confere de verdade agora
# e `aria-checked`.
ROTULO_MODO_NO_CHIP = {
    "imagem": (), "t2v": ("video",),
    "frames2v": ("frames", "video"), "ingredients2v": ("elementos", "video"),
}

# --- os curados: arquivo a parte, sobreposto na leitura -----------------
# O que a IA curou NAO volta para dentro deste arquivo. Motivos:
#   * `seletores.py` continua sendo autoria humana, com os comentarios que explicam
#     cada armadilha medida — e um `sed` de robo apagaria justamente isso
#   * o que a IA mexeu fica visivel, auditavel e REVERSIVEL: apagar o JSON volta ao
#     original, sem git, sem merge, sem conflito
#
# Cada entrada guarda valor novo, valor antigo, data e a justificativa que o modelo deu.
CURADOS = Path(__file__).with_name("curados.json")

# `NOME` ou `NOME[chave]` — nada mais entra. Um nome fora desta forma e ignorado em vez
# de virar `setattr` de coisa nenhuma.
_ALVO = re.compile(r"^([A-Z][A-Z0-9_]*)(?:\[([^\]]+)\])?$")

# Valor original de cada nome sobreposto, para `restaurar_originais` poder desfazer sem
# reimportar o modulo — e o que faz "apagar o JSON reverte" valer tambem em processo
# que ja esta de pe.
_ORIGINAIS: dict[str, str] = {}


def _chave_de(mapa: dict, chave: str):
    """A chave REAL do mapa a partir do texto do nome, ou None.

    `QUANTIDADE` tem chaves inteiras (1..4) e o nome do curado e texto
    ("QUANTIDADE[1]"). Comparar por `str()` cobre os dois tipos sem obrigar o arquivo
    de curados — que uma pessoa pode editar — a saber o tipo de cada mapa daqui.
    """
    for real in mapa:
        if str(real) == chave:
            return real
    return None


def _ler_curados() -> dict:
    """O JSON, ou vazio. NUNCA levanta: isto roda na importacao do modulo.

    Um arquivo corrompido nao pode derrubar o app inteiro — o comportamento correto e
    seguir com `seletores.py` puro, que e exatamente o estado anterior a cura.
    """
    try:
        dados = json.loads(CURADOS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:                      # JSON quebrado, permissao, encoding
        print(f"curados.json ilegivel ({type(e).__name__}) - "
              f"seguindo com os seletores originais")
        return {}
    return dados if isinstance(dados, dict) else {}


def aplicar_curados(dados: dict | None = None) -> list[str]:
    """Sobrepoe os curados sobre este modulo. Devolve os nomes que valeram.

    Recusa em silencio o que nao devia estar la: nome destrutivo (segunda barreira da
    regra de seguranca), nome que nao existe no modulo, e valor que nao e texto. O
    arquivo e escrito por codigo mas pode ser editado a mao, e a leitura tem que
    tratar isso como entrada nao confiavel.
    """
    dados = _ler_curados() if dados is None else dados
    aqui = globals()
    aplicados: list[str] = []
    for nome, entrada in dados.items():
        valor = entrada.get("valor") if isinstance(entrada, dict) else entrada
        if not isinstance(valor, str) or not valor:
            continue
        casado = _ALVO.match(nome)
        if not casado:
            continue
        base, chave = casado.group(1), casado.group(2)
        if base in NAO_CURAVEIS or nome in NAO_CURAVEIS:
            print(f"curados.json: {nome} e destrutivo e NAO foi aplicado")
            continue
        atual = aqui.get(base)
        if chave is None:
            if not isinstance(atual, str):
                continue                        # nome inexistente ou nao e seletor
            _ORIGINAIS.setdefault(nome, atual)
            aqui[base] = valor
        else:
            if not isinstance(atual, dict):
                continue
            real = _chave_de(atual, chave)
            if real is None or not isinstance(atual[real], str):
                continue
            _ORIGINAIS.setdefault(nome, atual[real])
            atual[real] = valor
        aplicados.append(nome)
    return aplicados


def restaurar_originais() -> None:
    """Desfaz toda sobreposicao, em memoria. E o "apagar o JSON" sem reiniciar."""
    aqui = globals()
    for nome, valor in _ORIGINAIS.items():
        casado = _ALVO.match(nome)
        if not casado:
            continue
        base, chave = casado.group(1), casado.group(2)
        if chave is None:
            aqui[base] = valor
        elif isinstance(aqui.get(base), dict):
            real = _chave_de(aqui[base], chave)
            if real is not None:
                aqui[base][real] = valor
    _ORIGINAIS.clear()


def recarregar_curados(dados: dict | None = None) -> list[str]:
    """Volta ao original e aplica de novo. E o que a cura chama depois de gravar."""
    restaurar_originais()
    return aplicar_curados(dados)


_APLICADOS = aplicar_curados()
if _APLICADOS:
    print(f"seletores curados em uso ({len(_APLICADOS)}): {', '.join(_APLICADOS)} "
          f"- apague {CURADOS.name} para voltar ao original")
