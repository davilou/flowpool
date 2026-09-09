# FlowPool — instruções para quem mexe no código

Serviço local que gera vídeo e imagem no Google Flow dirigindo um pool de contas por
Playwright. **O que o app faz e como se opera está em [`flowpool/LEIA-ME.md`](flowpool/LEIA-ME.md)**
— painel, cadastro de conta, proxy, auto-cura, variáveis de ambiente. Não repita nada
disso aqui; este arquivo é só o que muda a forma de trabalhar no repositório.

O desenho original: [`docs/superpowers/specs/2026-08-19-flowpool-design.md`](docs/superpowers/specs/2026-08-19-flowpool-design.md).

## Comando de check

Três comandos, todos têm que passar limpos. **Rode os três depois de cada mudança.**

```bash
.venv/Scripts/python.exe -m pytest -q
```

752 testes, ~15 s. A suíte não abre navegador, não gasta crédito e não fala com o modelo
de visão — se algum teste ficar lento ou pedir rede, alguma coisa vazou de camada.

```bash
.venv/Scripts/ruff.exe check
```

Zero erros hoje, e é para continuar assim. A configuração está no `pyproject.toml`, e
**cada regra desligada tem o motivo escrito ao lado** — leia antes de desligar outra.
`--fix` só aplica correção segura; `--unsafe-fixes` nunca sem olhar o diff.

```bash
.venv/Scripts/pyright.exe flowpool/nucleo flowpool/dados
```

Zero erros, em **modo estrito**. É onde mora o dinheiro: crédito, escolha de conta,
custo e banco. Quebrar este é bloqueio.

```bash
.venv/Scripts/pyright.exe
```

O repositório inteiro, em modo básico: **19 erros conhecidos em `flowpool/`** (9 em
`api/app.py`, 2 em cada de `transporte/pw/transporte.py`, `api/esquemas.py` e
`motor/orquestrador.py`, 1 em cada de `api/colar.py`, `transporte/pw/conferir.py`,
`cura.py`, `sessao.py`) e ~60 em `tests/`. **Não é portão — é catraca: o número pode
descer, nunca subir.** Quase todos são a mesma coisa: `repo.job(id)` devolve
`Job | None` e o código segue como se nunca fosse None. O conserto certo é falhar alto
com mensagem, não `assert`.

> O 20º erro era bug de verdade e já foi consertado: `transporte.py` usava
> `sel.BOTAO_UPLOAD_FRAME`, atributo que nunca existiu, num desvio herdado de uma
> medição do popover do slot que foi corrigida no mesmo dia. **O popover do slot de
> frame não envia arquivo** — só escolhe do acervo (`seletores.POPOVER_SLOT`), e é por
> isso que `_gerar` sai de Frames antes de enviar.

Subir o serviço: `.venv/Scripts/python.exe -m flowpool` → http://127.0.0.1:8777/

## A direção das dependências

Medido em 09/09/2026 e **é para continuar assim**:

```
nucleo     → (nada)
dados      → nucleo
transporte → nucleo
motor      → nucleo, dados, transporte
api        → nucleo, dados, motor, transporte
__main__   → monta tudo
```

| camada | papel | a regra que a define |
|---|---|---|
| `nucleo/` | tipos, custo, crédito, escolha de conta, erros | puro: sem I/O, sem relógio próprio, sem banco. `agora` entra por parâmetro |
| `dados/` | `Repo` + `esquema.sql` | única parte que conhece SQL |
| `transporte/base.py` | `FlowTransport`, `Pedido`, `Estado` | **a fronteira**: acima daqui ninguém sabe que o Flow existe |
| `transporte/pw/` | implementação Playwright | a única que conhece o DOM do Google |
| `transporte/falso.py` | transporte em memória | é contra ele que o motor inteiro é testado |
| `motor/` | casa job com conta, conduz o ciclo | não conhece Playwright |
| `api/` | FastAPI + painel HTML/JS servido do próprio pacote | sem build step, sem Node |

Se uma peça do `nucleo` precisar de I/O, ela não é do `nucleo`. Se o `motor` precisar
importar `pw`, falta um método em `FlowTransport`.

## O que quebra em silêncio

Esta é a lista que justifica o arquivo. Cada item já custou caro uma vez.

1. **Seletor do Flow só existe em `transporte/pw/seletores.py`.** Um seletor escrito
   direto no `transporte.py` sobrevive ao `conferir.py` e morre em produção sem
   diagnóstico.
2. **Nunca torne curável o menu do card, excluir projeto ou lixeira** (`seletores.NAO_CURAVEIS`).
   "Casou 1 elemento" não protege quando o elemento errado também casa 1 — e ali o erro
   apaga trabalho do usuário. Falhar é o comportamento certo.
3. **Arquivo novo em `flowpool/api/` que o painel carrega precisa entrar em
   `[tool.setuptools.package-data]`** do `pyproject.toml`. Sem isso, `GET /` dá 404 numa
   instalação não-editável — com todos os testes passando.
4. **Campo novo em `Pedido` que não atravessa a fronteira é ignorado em silêncio.** O
   transporte gera no default da UI, a saída existe e parece certa. Foi assim que todo
   vídeo saiu em 8 s, 70% mais caro que o de 4 s.
5. **Data gravada no banco vai sempre em UTC** (`repo._txt`). A comparação de
   `reavaliar_em` é lexicográfica sobre texto: dois offsets diferentes quebram a ordem
   da fila sem erro nenhum.
6. **`Tier` e `SaudeSessao` herdam de `str, Enum` — compare por valor, nunca com `is`.**
   Valor cru vindo do SQLite quebra `is` em silêncio.
7. **`grep -r` em `flowpool/` devolve 27 MB.** `flowpool/perfis/` guarda perfis do Chrome
   com JS minificado de extensão. Restrinja sempre:
   `flowpool/nucleo flowpool/motor flowpool/api flowpool/dados flowpool/transporte`.
8. **Duas sessões no mesmo perfil do Chrome são impossíveis.** O segundo processo
   entrega a URL ao primeiro e morre. O teto de paralelismo é o número de contas livres,
   não de janelas.
9. **`sel.PENDENTE` vale `"..."`, que o Playwright lê como XPath — e é *truthy*.** Os dois
   jeitos de se machucar com isso já aconteceram. Mandá-lo a um locator estoura com
   `SyntaxError: The string '...' is not a valid XPath expression`, uma mensagem que fala
   de sintaxe quando o problema é medição: use `acoes.quantos` ("não sei" = zero) ou
   `acoes.medido` (recusa antes de gastar crédito), nunca `.locator(sel.X).count()` cru.
   E usá-lo como booleano responde **sim** — é o que `CARDS_NOVOS_PRIMEIRO` faz hoje na
   linha `indice = 0 if sel.CARDS_NOVOS_PRIMEIRO else antes`, acertando por sorte.
10. **Recusa do Flow não é falha de geração, e ela tem mais de uma redação.**
   `grade.recusa_sem_cobranca` decide por "o Flow recusou **E** não cobrou"; o motivo
   (limite de uso, atividade incomum) é detalhe da mensagem. Motivo novo entra em
   `_MOTIVOS_DE_RECUSA`, e não num `if` no transporte — foi o nome estreito
   (`por_limite_de_uso`) que deixou o antiabuso passar por falha comum nos jobs 583-588.
11. **Mensagem de erro não afirma o que ninguém verificou.** "O resultado EXISTE na conta
   X e já foi pago" era categórico e falso: a geração tinha sido recusada sem cobrança, e
   o usuário passou meia tarde procurando um vídeo que nunca houve. O que se sabe se
   afirma; o resto manda **conferir**.

Quando alguma coisa parar de funcionar contra o Flow real, o primeiro comando é o
`conferir` — ele diz qual seletor morreu e não gasta crédito:

```bash
.venv/Scripts/python.exe -m flowpool.transporte.pw.conferir <perfil> <uuid-do-projeto>
```

## Dívida conhecida — não piore

Auditado em 09/09/2026 contra as regras de estrutura (teto de 500 linhas por arquivo,
50 por função):

| arquivo | linhas | |
|---|---|---|
| `transporte/pw/transporte.py` | 1529 | corte parado na etapa 4 — ver abaixo |
| `api/painel.js` | 1801 | |
| `motor/orquestrador.py` | 988 | `_executar` tem 202 linhas |
| `transporte/pw/seletores.py` | 874 | mapa de seletores; cresce por natureza |
| `transporte/pw/analise.py` | 640 | |
| `transporte/pw/cura.py` | 544 | |
| `api/esquemas.py` | 440 | acima do alvo de 400, abaixo do teto |

**O corte do `transporte.py` parou na etapa 4** (09/09/2026), de 2315 para 1529:

| etapa | módulo | linhas | testes |
|---|---|---|---|
| 1 ✔ | `pw/acoes.py` | 154 | 13 |
| 2 ✔ | `pw/grade.py` | 261 | 10 |
| 3 ✔ | `pw/download.py` | 212 | 10 |
| 4 ✔ | `pw/chip.py` | 422 | 12 |
| 5 ✗ | anexos e mídia (~606 linhas) | — | — |

A forma: função livre recebendo o que precisa; o transporte fica com o estado e vira
costura de três linhas onde há estado a entregar (`_clicar_unico`, `_card`, `_baixar`,
`_configurar`). **Rede antes do corte, e mutação para provar que a rede tem dente** —
três canais laterais viraram retorno no caminho (`_identidade_nova`, `_ultimo_custo`, o
modo do download).

**A etapa 5 foi decidida contra, e o motivo importa.** Os quatro cortes anteriores
tinham regras enumeráveis, uma por teste. O bloco de mídia são ~18 métodos em cadeia
com armadilhas de TEMPO E ORDEM — o retry que fechava o painel recém-aberto, o homônimo
do acervo que passava por "upload terminou", a espera que terminava cedo demais. Duplo
de página honesto para isso é bem mais caro, e duplo desonesto é pior que nenhum. Além
disso a conta não fecha: 1529 − 606 ainda é 923, acima do teto. Se for investir nessa
direção, **escreva a rede da mídia sem mover nada** — o ganho real das etapas 3 e 4 não
foi o arquivo menor, foi `_baixar` e `_configurar` saírem de zero teste para 22.

`api/app.py` saiu da lista em 09/09/2026: as 29 rotas foram para `api/rotas/`, uma
fábrica de `APIRouter` por grupo, e o `app.py` ficou com 62 linhas de composição. Cada
handler tem no máximo 45 linhas. **A métrica de função vai reclamar das fábricas**
(`rotas_contas` tem 180 linhas) — é artefato do padrão: a fábrica é um contêiner de
rotas, como um corpo de classe, e o que se lê é cada handler.

Também: ~85 funções com argumento sem tipo (quase todas recebendo `pagina`/`escopo` do
Playwright, que é por que `transporte/pw` não está em modo estrito), e `api/app.py` e
`api/esquemas.py` sem docstring de módulo.

**Não conserte isso de passagem.** Mas: mudança nesses arquivos não aumenta a conta —
função nova nasce abaixo de 50 linhas e com tipos nas bordas, e código novo entra em
módulo novo em vez de engordar o `transporte.py`. Quebrar qualquer um deles é tarefa
própria, com plano aprovado antes, e a divisão é por responsabilidade
(configurar / anexar / consultar / baixar), nunca `parte1` / `parte2`.

## Estilo desta base

- **Comentário explica o porquê, com data e medição.** O padrão daqui é
  `# MEDIDO 2026-09-05: ...` e correção explícita quando uma crença anterior cai
  (veja o topo do `__main__.py`). Não apague um comentário desses ao editar a linha
  que ele explica — atualize a medição.
- **Docstring de módulo diz a fronteira em uma frase**: "Única parte do sistema que
  conhece SQL", "Não conhece Playwright". Módulo novo nasce com uma.
- **`except: pass` aqui costuma ser intencional** — limpeza de página morta, log que não
  pode derrubar job, volta de retry. Leia o docstring acima antes de "consertar".
  O que não se admite é default silencioso: erro de domínio recusa (`SaldoInsuficiente`,
  `LimiteDeUso`), não zera.
- **Commits**: `tipo(escopo): frase em minúscula`, em português —
  `feat(nucleo):`, `fix(transporte):`, `perf(motor):`, `docs:`, `chore:`.
