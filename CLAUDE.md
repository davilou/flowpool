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

676 testes, ~15 s. A suíte não abre navegador, não gasta crédito e não fala com o modelo
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

O repositório inteiro, em modo básico: **20 erros conhecidos em `flowpool/`** (9 em
`api/app.py`, 3 em `transporte/pw/transporte.py`, 2 em `api/esquemas.py`, 2 em
`motor/orquestrador.py`, 1 em cada de `api/colar.py`, `transporte/pw/conferir.py`,
`cura.py`, `sessao.py`) e ~60 em `tests/`. **Não é portão — é catraca: o número pode
descer, nunca subir.** Quase todos são a mesma coisa: `repo.job(id)` devolve
`Job | None` e o código segue como se nunca fosse None. O conserto certo é falhar alto
com mensagem, não `assert`.

> **Um desses 20 é bug de verdade**, achado pelo pyright no primeiro dia:
> `transporte.py:1391` usa `sel.BOTAO_UPLOAD_FRAME`, que **não existe** em
> `seletores.py`. Só é avaliado quando `BOTAO_ENVIAR_MIDIA` não está na tela — ou seja,
> exatamente no caminho de upload por slot de frame. `AttributeError` esperando
> acontecer, e nenhum teste passa por ali. Precisa de medição contra o Flow real para
> saber qual é o seletor certo; não invente um.

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
| `transporte/pw/transporte.py` | 2315 | ~70 métodos numa classe |
| `api/painel.js` | 1801 | |
| `motor/orquestrador.py` | 983 | `_executar` tem 202 linhas |
| `transporte/pw/seletores.py` | 874 | mapa de seletores; cresce por natureza |
| `api/app.py` | 696 | `criar_app` tem 588 — 25 rotas numa closure |
| `transporte/pw/analise.py` | 639 | |
| `transporte/pw/cura.py` | 545 | |

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
