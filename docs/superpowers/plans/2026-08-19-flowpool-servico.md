# FlowPool — Plano 1: o serviço

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Um serviço local que recebe pedidos de vídeo/imagem por HTTP, escolhe sozinho qual conta Google usar conforme o crédito, gera no Google Flow via Playwright e devolve o arquivo.

**Architecture:** Quatro camadas. O núcleo (custos, escolha de conta, contabilidade) é código puro sem I/O. O repositório guarda contas, jobs e eventos em SQLite. O transporte é uma interface com duas implementações — uma falsa, para teste, e uma real sobre Playwright + Chrome instalado. O orquestrador puxa da fila e casa job com conta. A API FastAPI é a única porta de entrada.

**Tech Stack:** Python 3.13, FastAPI, uvicorn, SQLite (`sqlite3` da stdlib), Playwright async com `channel="chrome"`, pytest, pytest-asyncio, httpx.

**Spec:** `docs/superpowers/specs/2026-08-19-flowpool-design.md`

---

## Estrutura de arquivos

| arquivo | responsabilidade |
|---|---|
| `flowpool/nucleo/tipos.py` | enums e dataclasses do domínio. Sem I/O, sem dependências. |
| `flowpool/nucleo/custos.py` | tabela de custo por (modelo, tier) e o teto diário do free |
| `flowpool/nucleo/escolha.py` | `escolher(contas, modelo, agora) -> Conta | None`. Função pura. |
| `flowpool/nucleo/credito.py` | debitar previsto, reconciliar com o lido, decidir confiança |
| `flowpool/dados/esquema.sql` | DDL das três tabelas |
| `flowpool/dados/repo.py` | leitura/escrita SQLite. Única parte que conhece SQL. |
| `flowpool/transporte/base.py` | `FlowTransport` (Protocol), `Pedido`, `Estado`, exceções |
| `flowpool/transporte/falso.py` | transporte em memória que simula latência, saldo e falhas |
| `flowpool/transporte/pw/seletores.py` | **todos** os seletores da UI do Flow, em um lugar só |
| `flowpool/transporte/pw/sessao.py` | abrir Chrome com perfil da conta, cadastro, revalidação |
| `flowpool/transporte/pw/transporte.py` | implementação real do `FlowTransport` |
| `flowpool/motor/orquestrador.py` | workers, retry, troca de conta, transições de job |
| `flowpool/api/esquemas.py` | modelos Pydantic de entrada/saída |
| `flowpool/api/app.py` | rotas FastAPI |
| `flowpool/cliente.py` | cliente Python fino, para o pipeline chamar |

Regra que se repete em todo o plano: **nada acima de `transporte/` importa Playwright.** Se um teste do núcleo precisar de navegador, o desenho quebrou.

---

### Task 0: Repositório e esqueleto

**Files:**
- Create: `flowpool/__init__.py`, `flowpool/nucleo/__init__.py`, `tests/__init__.py`, `pyproject.toml`, `.gitignore`

- [ ] **Step 1: Inicializar o git na pasta do projeto**

A pasta `Tiktok Shop` ainda não é repositório. Rode na raiz dela:

```bash
git init && git add -A && git commit -m "chore: estado atual antes do flowpool"
```

- [ ] **Step 2: Criar o `.gitignore`**

Crie `.gitignore` na raiz com:

```
__pycache__/
*.pyc
flowpool/perfis/
flowpool/flowpool.db
.venv/
```

`perfis/` fica de fora porque contém sessão autenticada das contas — isso nunca entra em git.

- [ ] **Step 3: Criar o `pyproject.toml`**

```toml
[project]
name = "flowpool"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "playwright>=1.48",
    "httpx>=0.27",
    "pydantic>=2.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 4: Criar os pacotes vazios**

```bash
mkdir -p flowpool/nucleo flowpool/dados flowpool/transporte/pw flowpool/motor flowpool/api tests
touch flowpool/__init__.py flowpool/nucleo/__init__.py flowpool/dados/__init__.py flowpool/transporte/__init__.py flowpool/transporte/pw/__init__.py flowpool/motor/__init__.py flowpool/api/__init__.py tests/__init__.py
```

- [ ] **Step 5: Instalar**

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
```

- [ ] **Step 6: Verificar que o pytest roda**

Run: `.venv/Scripts/pytest -q`
Expected: `no tests ran` — sem erro de import.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore flowpool tests
git commit -m "chore: esqueleto do flowpool"
```

---

### Task 1: Tipos do domínio

**Files:**
- Create: `flowpool/nucleo/tipos.py`, `flowpool/nucleo/erros.py`
- Test: `tests/test_tipos.py`

- [ ] **Step 0: Criar os erros de domínio**

`flowpool/nucleo/erros.py` — vem antes de tudo porque a Task 2 já importa dele:

```python
"""Erros de dominio. Camadas de cima importam daqui; nucleo nao importa de ninguem."""


class SaldoInsuficiente(Exception):
    """A conta nao tem credito para o custo pedido. Nao e falha: e hora de trocar de conta."""


class ModeloSemCusto(Exception):
    """Modelo/tier sem custo cadastrado. Erro de configuracao, nao de execucao."""
```

Mora em `nucleo/` porque as camadas de cima dependem de `nucleo`, nunca o contrário. `transporte/base.py` (Task 6) reexporta `SaldoInsuficiente` daqui em vez de definir a sua própria — duas classes com o mesmo nome fariam o `except` do orquestrador pegar uma e deixar a outra passar como falha de rede.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_tipos.py`:

```python
from datetime import datetime, timezone

from flowpool.nucleo.tipos import (
    Conta, EstadoJob, Job, Modelo, Modo, Projeto, SaudeSessao, Tier,
)


def test_conta_nasce_viva_e_ativa():
    c = Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil="perfis/1")
    assert c.saude is SaudeSessao.VIVA
    assert c.ativa is True
    assert c.saldo == 0
    assert c.falhas == 0


def test_job_nasce_pendente_sem_tentativas():
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="uma gata", destino="x", nome="c1")
    assert j.estado is EstadoJob.PENDENTE
    assert j.tentativas == 0
    assert j.referencias == []
    assert j.conta_id is None


def test_job_nasce_sem_custo_estimado():
    # sem registrar o reservado nao ha como estornar um job que morreu antes de gerar
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="x", nome="c1")
    assert j.custo_estimado is None


def test_enums_serializam_como_texto():
    assert Tier.ULTRA.value == "ultra"
    assert Modo.INGREDIENTS2V.value == "ingredients2v"
    assert Modelo.VEO_QUALITY.value == "veo-quality"
    assert EstadoJob.PRONTO.value == "pronto"
    assert SaudeSessao.EXPIRADA.value == "expirada"


def test_reavaliar_em_aceita_datetime():
    j = Job(id=1, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA, prompt="p", destino="x", nome="i1")
    assert j.reavaliar_em is None
    j.reavaliar_em = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)
    assert j.reavaliar_em.year == 2026


def test_conta_nasce_sem_prazo_de_seca():
    # `seca_ate` e o que tira do pool a conta que o Flow recusou apesar do saldo
    c = Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil="perfis/1")
    assert c.seca_ate is None
    c.seca_ate = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
    assert c.seca_ate.hour == 13


def test_projeto_liga_uma_conta_a_um_produto():
    # projeto pertence a UMA conta: a chave e o par (conta, produto), nunca o produto
    # sozinho — a conta g2 nao enxerga nem abre projeto da g1
    p = Projeto(id=1, conta_id=2, produto="Panelas Monaco",
                id_remoto="a1b2c3", nome_remoto="Panelas Monaco")
    assert p.conta_id == 2
    assert p.produto == "Panelas Monaco"
    assert p.id_remoto == "a1b2c3"
    assert p.criado_em is None


def test_job_nasce_sem_produto():
    # o default existe so para nao quebrar a ordem dos campos da dataclass;
    # na pratica a API exige `produto`
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="x", nome="c1")
    assert j.produto == ""
    outro = Job(id=2, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="x",
                nome="c1", produto="Panelas Monaco")
    assert outro.produto == "Panelas Monaco"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_tipos.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.nucleo.tipos'`

- [ ] **Step 3: Implementar**

`flowpool/nucleo/tipos.py`:

```python
"""Tipos do dominio. Sem I/O, sem dependencias externas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Tier(str, Enum):
    FREE = "free"
    PRO = "pro"
    ULTRA = "ultra"


class SaudeSessao(str, Enum):
    VIVA = "viva"
    EXPIRADA = "expirada"
    BLOQUEADA = "bloqueada"


class Modo(str, Enum):
    T2V = "t2v"
    FRAMES2V = "frames2v"
    INGREDIENTS2V = "ingredients2v"
    IMAGEM = "imagem"


class Modelo(str, Enum):
    VEO_LITE = "veo-lite"
    VEO_FAST = "veo-fast"
    VEO_QUALITY = "veo-quality"
    NANO_BANANA = "nano-banana"


class EstadoJob(str, Enum):
    PENDENTE = "pendente"
    RODANDO = "rodando"
    PRONTO = "pronto"
    FALHOU = "falhou"
    CANCELADO = "cancelado"


@dataclass
class Conta:
    id: int
    email: str
    tier: Tier
    perfil: str
    saldo: int = 0
    saldo_lido_em: datetime | None = None
    saldo_confiavel: bool = False
    reset_em: datetime | None = None
    # Ate quando o Flow recusou gerar apesar do saldo aparente (cota diaria, throttle).
    # Enquanto estiver no futuro a conta sai da escolha, senao o motor gira em laco quente.
    seca_ate: datetime | None = None
    saude: SaudeSessao = SaudeSessao.VIVA
    falhas: int = 0
    ativa: bool = True


@dataclass
class Job:
    id: int
    modo: Modo
    modelo: Modelo
    prompt: str
    destino: str
    nome: str
    # Pasta do produto na pasta do usuario ("Panelas Monaco") e, ao mesmo tempo, o nome
    # do projeto no Flow. Tem default so para nao quebrar a ordem dos campos da
    # dataclass; a API exige.
    produto: str = ""
    referencias: list[str] = field(default_factory=list)
    frame_inicial: str | None = None
    frame_final: str | None = None
    prioridade: int = 0
    estado: EstadoJob = EstadoJob.PENDENTE
    conta_id: int | None = None
    id_remoto: str | None = None
    arquivo: str | None = None
    tentativas: int = 0
    custo_estimado: int | None = None
    erro: str | None = None
    reavaliar_em: datetime | None = None
    criado_em: datetime | None = None
    atualizado_em: datetime | None = None


@dataclass
class Projeto:
    """Vinculo entre uma conta e um projeto do Flow.

    Projeto pertence a UMA conta: a `g2` nao enxerga nem abre projeto da `g1`. Por isso
    a chave e o par (conta, produto) — nao existe "o projeto do Vestido Vermelho",
    existe um projeto do Vestido Vermelho em cada conta que gerou algo dele.
    """

    id: int
    conta_id: int
    produto: str
    id_remoto: str
    nome_remoto: str
    criado_em: datetime | None = None
```

`custo_estimado` guarda quanto o job reservou. Sem esse número não há como estornar a
reserva de um job que morreu antes de gerar — nem depois de um restart do serviço.

`seca_ate` guarda até quando a conta está fora do pool. Existe porque o Flow às vezes
mostra saldo e mesmo assim recusa gerar (cota diária, throttle, rate limit). Sem esse
prazo o job volta para a fila no mesmo instante, cai de novo na mesma conta, e o motor
gira em laço quente: CPU a 100%, um Chrome aberto e fechado por iteração, tabela de
eventos crescendo sem parar e nenhuma tentativa sendo consumida — ou seja, nada nunca
desiste. É a coluna que o spec já pedia quando falava em "marcar a conta seca até o
reset estimado".

`Projeto` existe porque, no Flow real, **toda geração acontece dentro de um projeto** e
o projeto tem id na URL (`labs.google/fx/tools/flow/[id]`). Sem gerenciar isso, cada
geração criaria um projeto novo e a conta viraria um lixão de projetos órfãos.

A restrição que define o desenho: **projeto pertence a UMA conta.** A `g2` não enxerga
nem abre projeto da `g1`. Como o app roda entre vinte contas, não existe "o projeto do
Vestido Vermelho" — existe um projeto do Vestido Vermelho *em cada conta que gerou algo
dele*. Por isso a chave é o par `(conta_id, produto)`, nunca o produto sozinho.

`Job.produto` é o nome da pasta do produto e, ao mesmo tempo, o nome do projeto no
Flow. Tem default `""` só para não quebrar a ordem dos campos da dataclass — os campos
com default precisam vir depois dos sem default, e `produto` entra logo após `nome`. Na
prática a API exige (Task 8), e é dele que `destino` passa a derivar.

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_tipos.py -q`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add flowpool/nucleo/tipos.py tests/test_tipos.py
git commit -m "feat: tipos do dominio do flowpool"
```

---

### Task 2: Tabela de custos

**Files:**
- Create: `flowpool/nucleo/custos.py`
- Test: `tests/test_custos.py`

O custo depende do par (modelo, tier): conta Ultra paga menos por Lite e Fast. O teto diário do free é o que impede Quality de rodar em conta grátis.

`custo_de` recusa par desconhecido com `ModeloSemCusto` em vez de deixar vazar um `KeyError`. Motivo: `custo_de` roda dentro de `apta()`, que roda para CADA conta dentro de `escolher()` — um `KeyError` cru não derruba um job, derruba a avaliação do pool inteiro e trava a fila. E o spec já promete acomodar um modelo novo (Gemini Omni) sem migração, então o dia em que alguém acrescenta um modelo e esquece o custo vai chegar. `ModeloSemCusto` vem de `flowpool/nucleo/erros.py`, criado no Step 0 da Task 1.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_custos.py`:

```python
import pytest

from flowpool.nucleo.custos import TETO_DIARIO_FREE, cabe_no_free, custo_de
from flowpool.nucleo.tipos import Modelo, Tier


def test_teto_diario_do_free():
    assert TETO_DIARIO_FREE == 50


@pytest.mark.parametrize(
    "modelo,tier,esperado",
    [
        (Modelo.VEO_LITE, Tier.FREE, 10),
        (Modelo.VEO_LITE, Tier.PRO, 10),
        (Modelo.VEO_LITE, Tier.ULTRA, 5),
        (Modelo.VEO_FAST, Tier.FREE, 20),
        (Modelo.VEO_FAST, Tier.PRO, 20),
        (Modelo.VEO_FAST, Tier.ULTRA, 10),
        (Modelo.VEO_QUALITY, Tier.FREE, 100),
        (Modelo.VEO_QUALITY, Tier.PRO, 100),
        (Modelo.VEO_QUALITY, Tier.ULTRA, 100),
        (Modelo.NANO_BANANA, Tier.FREE, 2),
        (Modelo.NANO_BANANA, Tier.ULTRA, 2),
    ],
)
def test_custo_por_modelo_e_tier(modelo, tier, esperado):
    assert custo_de(modelo, tier) == esperado


def test_tabela_cobre_todos_os_pares():
    # um modelo novo sem custo cadastrado travaria a fila inteira, nao so um job
    for m in Modelo:
        for t in Tier:
            assert isinstance(custo_de(m, t), int)


def test_quality_nao_cabe_em_conta_gratis():
    # 100 creditos contra teto diario de 50 — nunca cabe
    assert cabe_no_free(Modelo.VEO_QUALITY) is False


@pytest.mark.parametrize("modelo", [Modelo.VEO_LITE, Modelo.VEO_FAST, Modelo.NANO_BANANA])
def test_o_resto_cabe_em_conta_gratis(modelo):
    assert cabe_no_free(modelo) is True
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_custos.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.nucleo.custos'`

- [ ] **Step 3: Implementar**

`flowpool/nucleo/custos.py`:

```python
"""Custo em creditos por (modelo, tier), e o teto diario da conta gratuita.

Os numeros vem do suporte do Flow e de medicao publica (ver o spec). O custo da
imagem e o mais incerto: ha fonte dizendo que sai de graca. Por isso o valor aqui e
so uma ESTIMATIVA para reservar saldo antes de despachar — quem manda e a leitura
real, reconciliada em `nucleo/credito.py`.
"""

from __future__ import annotations

from flowpool.nucleo.erros import ModeloSemCusto
from flowpool.nucleo.tipos import Modelo, Tier

TETO_DIARIO_FREE = 50

_CUSTO: dict[tuple[Modelo, Tier], int] = {
    (Modelo.VEO_LITE, Tier.FREE): 10,
    (Modelo.VEO_LITE, Tier.PRO): 10,
    (Modelo.VEO_LITE, Tier.ULTRA): 5,
    (Modelo.VEO_FAST, Tier.FREE): 20,
    (Modelo.VEO_FAST, Tier.PRO): 20,
    (Modelo.VEO_FAST, Tier.ULTRA): 10,
    (Modelo.VEO_QUALITY, Tier.FREE): 100,
    (Modelo.VEO_QUALITY, Tier.PRO): 100,
    (Modelo.VEO_QUALITY, Tier.ULTRA): 100,
    (Modelo.NANO_BANANA, Tier.FREE): 2,
    (Modelo.NANO_BANANA, Tier.PRO): 2,
    (Modelo.NANO_BANANA, Tier.ULTRA): 2,
}


def custo_de(modelo: Modelo, tier: Tier) -> int:
    """Custo estimado em creditos de uma geracao."""
    try:
        return _CUSTO[(modelo, tier)]
    except KeyError:
        raise ModeloSemCusto(f"sem custo cadastrado para {modelo.value} em {tier.value}") from None


def cabe_no_free(modelo: Modelo) -> bool:
    """Se uma conta gratuita consegue, no melhor dia possivel, rodar este modelo."""
    return custo_de(modelo, Tier.FREE) <= TETO_DIARIO_FREE
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_custos.py -q`
Expected: `17 passed`

- [ ] **Step 5: Commit**

```bash
git add flowpool/nucleo/custos.py tests/test_custos.py
git commit -m "feat: tabela de custo por modelo e tier"
```

---

### Task 3: Escolha de conta

**Files:**
- Create: `flowpool/nucleo/escolha.py`
- Test: `tests/test_escolha.py`

Esta é a peça central e é função pura — merece teste de tabela cobrindo os casos reais do pool do Davi.

Os enums são comparados com `==`, nunca com `is`. `Tier` e `SaudeSessao` herdam de `str, Enum`: se em qualquer caminho futuro um valor chegar como string crua vinda do SQLite, o `is` falha em silêncio — e falha nas duas direções erradas, descartando conta viva ou aprovando conta grátis para Veo Quality, que é a única regra que o spec chama de intransponível. O `==` custa zero e elimina o modo de falha.

A assinatura é `escolher(contas, modelo, agora)` e `apta(c, modelo, agora)`: **o relógio entra por parâmetro.** Chamar `datetime.now()` aqui dentro tiraria a pureza justamente da peça que precisa ser testável sem navegador e sem congelar o relógio do processo — e é o núcleo inteiro que se apoia nessa garantia.

O `agora` serve para uma decisão só, mas é uma decisão que não dá para adiar: conta com `seca_ate` no futuro é descartada. Sem esse descarte, a conta que o Flow acabou de recusar volta a ser escolhida na iteração seguinte e o motor entra em laço quente (ver Task 1 e Task 7).

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_escolha.py`:

```python
from datetime import datetime, timedelta, timezone

from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.tipos import Conta, Modelo, SaudeSessao, Tier

# relogio fixo: `escolher` e pura e recebe o agora por parametro
AGORA = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def conta(id, tier, saldo, saude=SaudeSessao.VIVA, ativa=True, seca_ate=None):
    return Conta(id=id, email=f"c{id}@x.com", tier=tier, perfil=f"p/{id}",
                 saldo=saldo, saude=saude, ativa=ativa, seca_ate=seca_ate)


def test_sem_contas_devolve_none():
    assert escolher([], Modelo.VEO_FAST, AGORA) is None


def test_gasta_o_gratis_antes_do_pago():
    # credito de free morre em 24h; o de pago acumula. Aritmetica, nao preferencia.
    free = conta(1, Tier.FREE, 50)
    pro = conta(2, Tier.PRO, 1000)
    assert escolher([pro, free], Modelo.VEO_FAST, AGORA).id == 1


def test_entre_free_pega_a_de_maior_saldo():
    # espalha o uso e evita sobras inuteis de 5 creditos pelo pool
    escolhida = escolher([conta(1, Tier.FREE, 20), conta(2, Tier.FREE, 50)], Modelo.VEO_LITE, AGORA)
    assert escolhida.id == 2


def test_quality_ignora_todas_as_free_e_vai_pra_paga():
    contas = [conta(1, Tier.FREE, 50), conta(2, Tier.FREE, 50), conta(3, Tier.ULTRA, 9000)]
    assert escolher(contas, Modelo.VEO_QUALITY, AGORA).id == 3


def test_quality_sem_conta_paga_devolve_none():
    assert escolher([conta(1, Tier.FREE, 50)], Modelo.VEO_QUALITY, AGORA) is None


def test_pula_conta_com_saldo_insuficiente():
    # Fast custa 20 na free
    escolhida = escolher([conta(1, Tier.FREE, 10), conta(2, Tier.FREE, 40)], Modelo.VEO_FAST, AGORA)
    assert escolhida.id == 2


def test_pula_conta_com_sessao_expirada():
    contas = [conta(1, Tier.FREE, 50, saude=SaudeSessao.EXPIRADA), conta(2, Tier.FREE, 30)]
    assert escolher(contas, Modelo.VEO_FAST, AGORA).id == 2


def test_pula_conta_bloqueada():
    contas = [conta(1, Tier.FREE, 50, saude=SaudeSessao.BLOQUEADA), conta(2, Tier.PRO, 900)]
    assert escolher(contas, Modelo.VEO_FAST, AGORA).id == 2


def test_pula_conta_desativada():
    contas = [conta(1, Tier.FREE, 50, ativa=False), conta(2, Tier.FREE, 30)]
    assert escolher(contas, Modelo.VEO_FAST, AGORA).id == 2


def test_pool_todo_seco_devolve_none():
    assert escolher([conta(1, Tier.FREE, 5), conta(2, Tier.FREE, 0)], Modelo.VEO_FAST, AGORA) is None


def test_ultra_paga_menos_e_por_isso_consegue_onde_pro_nao_conseguiria():
    # Fast custa 10 na ultra e 20 na pro. Com saldo 15, so a ultra da conta.
    contas = [conta(1, Tier.PRO, 15), conta(2, Tier.ULTRA, 15)]
    assert escolher(contas, Modelo.VEO_FAST, AGORA).id == 2


def test_desempate_entre_pagas_e_por_saldo():
    contas = [conta(1, Tier.PRO, 300), conta(2, Tier.ULTRA, 900)]
    assert escolher(contas, Modelo.VEO_QUALITY, AGORA).id == 2


def test_desempate_nao_depende_da_ordem_da_lista():
    # a lista vem de um SELECT sem ORDER BY garantido; a escolha nao pode variar
    a, b = conta(1, Tier.FREE, 50), conta(2, Tier.FREE, 50)
    assert escolher([a, b], Modelo.VEO_FAST, AGORA).id == escolher([b, a], Modelo.VEO_FAST, AGORA).id


def test_saldo_exatamente_igual_ao_custo_serve():
    assert escolher([conta(1, Tier.FREE, 20)], Modelo.VEO_FAST, AGORA).id == 1


def test_pula_conta_seca_ate_o_prazo():
    # o Flow mostrou saldo e recusou gerar; insistir antes do prazo e laco quente
    seca = conta(1, Tier.FREE, 50, seca_ate=AGORA + timedelta(hours=1))
    assert escolher([seca, conta(2, Tier.FREE, 30)], Modelo.VEO_FAST, AGORA).id == 2


def test_conta_seca_volta_ao_pool_depois_do_prazo():
    seca = conta(1, Tier.FREE, 50, seca_ate=AGORA - timedelta(minutes=1))
    assert escolher([seca], Modelo.VEO_FAST, AGORA).id == 1
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_escolha.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.nucleo.escolha'`

- [ ] **Step 3: Implementar**

`flowpool/nucleo/escolha.py`:

```python
"""Escolha de conta. Funcao pura: sem I/O, sem relogio proprio, sem banco.

O `agora` entra por parametro. Chamar `datetime.now()` aqui dentro tiraria a pureza
da unica peca que precisa ser testavel sem navegador e sem congelar o relogio.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from flowpool.nucleo.custos import cabe_no_free, custo_de
from flowpool.nucleo.tipos import Conta, Modelo, SaudeSessao, Tier

# Free primeiro: seu credito expira em 24h, o das pagas acumula ate o fim do mes.
_ORDEM_TIER = {Tier.FREE: 1, Tier.PRO: 0, Tier.ULTRA: 0}


def apta(c: Conta, modelo: Modelo, agora: datetime) -> bool:
    # Comparacao por valor, nao por identidade: `Tier` e `SaudeSessao` herdam de
    # `str, Enum`, e um valor cru vindo do SQLite quebraria `is` em silencio.
    if not c.ativa or c.saude != SaudeSessao.VIVA:
        return False
    # Conta seca: o Flow ja recusou gerar apesar do saldo aparente. Insistir antes do
    # prazo devolve o job a fila na hora e o motor vira laco quente.
    if c.seca_ate is not None and c.seca_ate > agora:
        return False
    if c.tier == Tier.FREE and not cabe_no_free(modelo):
        return False
    return custo_de(modelo, c.tier) <= c.saldo


def escolher(contas: Sequence[Conta], modelo: Modelo, agora: datetime) -> Conta | None:
    """Melhor conta para rodar `modelo` em `agora`, ou None se nenhuma serve."""
    aptas = [c for c in contas if apta(c, modelo, agora)]
    if not aptas:
        return None
    return max(aptas, key=lambda c: (_ORDEM_TIER[c.tier], c.saldo, -c.id))
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_escolha.py -q`
Expected: `16 passed`

- [ ] **Step 5: Commit**

```bash
git add flowpool/nucleo/escolha.py tests/test_escolha.py
git commit -m "feat: politica de escolha de conta"
```

---

### Task 4: Contabilidade de crédito

**Files:**
- Create: `flowpool/nucleo/credito.py`
- Usa: `flowpool/nucleo/erros.py` (criado na Task 1)
- Test: `tests/test_credito.py`, `tests/test_pool.py`

O ponto que o spec insiste: descontar o previsto ao despachar (para dois jobs simultâneos não estourarem o mesmo saldo) e reconciliar com a leitura real ao terminar.

Três coisas que o desenho ingênuo erra e esta task corrige de saída:

1. **Reserva recusa, não zera.** Chegar em `reservar` sem saldo significa que quem chamou ignorou `escolher`; zerar silenciosamente apagaria crédito real que ainda daria outra geração mais barata.
2. **Existe estorno (`liberar`).** Sem ele, três timeouts de rede numa conta de 50 créditos zeram o saldo local com os 50 créditos intactos no Flow — e a conta some do pool para sempre, porque ninguém despacha job para conta que parece seca.
3. **A divergência é medida com sinal.** Sobrar crédito é notícia boa (é o que responde se a imagem custa ou sai de graça); só faltar crédito derruba a confiança. E o reset diário da conta grátis não é divergência nenhuma: é a conta voltando ao teto.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_credito.py`:

```python
from datetime import datetime, timezone

import pytest

from flowpool.nucleo.credito import LIMIAR_DIVERGENCIA, liberar, reconciliar, reservar
from flowpool.nucleo.erros import SaldoInsuficiente
from flowpool.nucleo.tipos import Conta, Modelo, Tier


def conta(saldo, tier=Tier.FREE):
    return Conta(id=1, email="a@b.com", tier=tier, perfil="p", saldo=saldo,
                 saldo_confiavel=True)


AGORA = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


# --- reservar ---

def test_reservar_desconta_o_custo_previsto():
    c = conta(50)
    assert reservar(c, Modelo.VEO_FAST) == 20
    assert c.saldo == 30


def test_reservar_recusa_quando_o_custo_excede_o_saldo():
    # zerar o saldo apagaria credito real que ainda daria outra geracao mais barata
    c = conta(5)
    with pytest.raises(SaldoInsuficiente):
        reservar(c, Modelo.VEO_FAST)


def test_reservar_recusado_nao_altera_o_saldo():
    c = conta(5)
    with pytest.raises(SaldoInsuficiente):
        reservar(c, Modelo.VEO_FAST)
    assert c.saldo == 5


def test_reservar_aceita_saldo_exatamente_igual_ao_custo():
    c = conta(20)
    assert reservar(c, Modelo.VEO_FAST) == 20
    assert c.saldo == 0


# --- liberar ---

def test_liberar_devolve_o_credito():
    c = conta(30)
    liberar(c, 20)
    assert c.saldo == 50


def test_reservar_e_liberar_voltam_ao_saldo_original():
    # tres timeouts de rede nao podem sumir com a conta do pool
    c = conta(50)
    custo = reservar(c, Modelo.VEO_FAST)
    liberar(c, custo)
    assert c.saldo == 50


# --- reconciliar ---

def test_reconciliar_com_leitura_igual_mantem_confianca():
    c = conta(30)
    delta = reconciliar(c, lido=30, agora=AGORA)
    assert delta == 0
    assert c.saldo == 30
    assert c.saldo_confiavel is True
    assert c.saldo_lido_em == AGORA


def test_reconciliar_sempre_adota_o_valor_lido():
    # a tela do Flow e a verdade; o previsto e so estimativa
    c = conta(30)
    reconciliar(c, lido=42, agora=AGORA)
    assert c.saldo == 42


def test_delta_positivo_quando_sobrou_credito():
    # o custo real saiu menor que a tabela — e isso que responde se imagem sai de graca
    c = conta(30)
    assert reconciliar(c, lido=35, agora=AGORA) == 5


def test_delta_negativo_quando_faltou_credito():
    # o Google cobrou mais que a tabela
    c = conta(30)
    assert reconciliar(c, lido=28, agora=AGORA) == -2


def test_divergencia_pequena_para_baixo_nao_derruba_confianca():
    c = conta(30)
    delta = reconciliar(c, lido=30 - LIMIAR_DIVERGENCIA, agora=AGORA)
    assert delta == -LIMIAR_DIVERGENCIA
    assert c.saldo_confiavel is True


def test_divergencia_grande_para_baixo_derruba_confianca():
    c = conta(100)
    delta = reconciliar(c, lido=100 - LIMIAR_DIVERGENCIA - 1, agora=AGORA)
    assert delta < -LIMIAR_DIVERGENCIA
    assert c.saldo_confiavel is False


def test_divergencia_grande_para_cima_em_conta_paga_nao_derruba_confianca():
    # sobrar credito e boa noticia, nao sintoma de contabilidade furada
    c = conta(100, tier=Tier.PRO)
    delta = reconciliar(c, lido=100 + LIMIAR_DIVERGENCIA + 10, agora=AGORA)
    assert delta > LIMIAR_DIVERGENCIA
    assert c.saldo_confiavel is True


def test_reset_da_conta_gratis_nao_e_divergencia():
    # 10 -> 50 e o reset diario, nao contabilidade furada
    c = conta(10, tier=Tier.FREE)
    delta = reconciliar(c, lido=50, agora=AGORA)
    assert delta == 40
    assert c.saldo == 50
    assert c.reset_em == AGORA
    assert c.saldo_confiavel is True


def test_conta_paga_que_sobe_ate_50_nao_e_reset():
    c = conta(10, tier=Tier.PRO)
    reconciliar(c, lido=50, agora=AGORA)
    assert c.reset_em is None
    assert c.saldo_confiavel is True
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_credito.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.nucleo.credito'`

- [ ] **Step 3: Implementar**

`flowpool/nucleo/credito.py`:

```python
"""Contabilidade de credito: reservar o previsto, estornar o que nao foi usado,
reconciliar com o lido.

Descontar so localmente acumula erro (geracao que falha depois de debitar, custo que
o Google muda, imagem que as vezes sai a zero). Em uma semana o app manda job pra
conta seca e a fila trava sem explicacao. Por isso a leitura real sempre vence.
"""

from __future__ import annotations

from datetime import datetime

from flowpool.nucleo.custos import TETO_DIARIO_FREE, custo_de
from flowpool.nucleo.erros import SaldoInsuficiente
from flowpool.nucleo.tipos import Conta, Modelo, Tier

# Gastar mais do que o previsto acima disso significa que o modelo de custo esta
# errado — o numero lido continua valido, mas projetar em cima dele nao.
LIMIAR_DIVERGENCIA = 5


def reservar(conta: Conta, modelo: Modelo) -> int:
    """Desconta o custo previsto antes de despachar. Devolve o custo reservado.

    Recusa em vez de zerar: chegar aqui sem saldo significa que quem chamou ignorou
    `escolher`, e silenciar isso apagaria credito real que ainda daria outra geracao.
    """
    custo = custo_de(modelo, conta.tier)
    if custo > conta.saldo:
        raise SaldoInsuficiente(
            f"conta {conta.email}: saldo {conta.saldo} < custo {custo}"
        )
    conta.saldo -= custo
    return custo


def liberar(conta: Conta, custo: int) -> None:
    """Devolve uma reserva que nao virou geracao.

    Sem isso, tres timeouts de rede numa conta de 50 creditos zeram o saldo local com
    os 50 creditos intactos no Flow — e a conta some do pool para sempre, porque
    ninguem despacha job para conta que parece seca.
    """
    conta.saldo += custo


def reconciliar(conta: Conta, lido: int, agora: datetime) -> int:
    """Adota o saldo lido do Flow. Devolve a diferenca COM SINAL.

    Positivo = sobrou credito (o custo real e menor que a tabela).
    Negativo = faltou credito (o Google cobrou mais que a tabela).
    A distincao importa: e ela que responde se a imagem custa creditos ou sai de graca.
    """
    delta = lido - conta.saldo
    conta.saldo = lido
    conta.saldo_lido_em = agora

    if _parece_reset(conta, lido, delta):
        conta.reset_em = agora
        conta.saldo_confiavel = True
    else:
        # So a direcao pessimista derruba a confianca. Sobrar credito e boa noticia,
        # nao sintoma de contabilidade furada.
        conta.saldo_confiavel = delta >= -LIMIAR_DIVERGENCIA

    return delta


def _parece_reset(conta: Conta, lido: int, delta: int) -> bool:
    """Conta gratuita que volta ao teto diario nao divergiu: ela resetou."""
    return conta.tier == Tier.FREE and delta > 0 and lido >= TETO_DIARIO_FREE
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_credito.py -q`
Expected: `15 passed`

- [ ] **Step 5: Teste de integração do núcleo**

Os testes acima olham uma função de cada vez. Falta o cenário real do pool do Davi — três contas grátis e uma Ultra, dez jobs em sequência — que é o único que exporia uma regressão na contabilidade (reserva que zera saldo, estorno que não volta, grátis que sobra sem ser usada). Continua sem navegador e sem banco: é só núcleo.

`tests/test_pool.py`:

```python
"""Integracao do nucleo: escolher + reservar + liberar sobre o pool real do Davi.

Nao ha navegador nem banco aqui. E o cenario que expoe qualquer regressao na
contabilidade — reserva que zera saldo, estorno que nao volta, free que sobra.
"""

from datetime import datetime, timezone

from flowpool.nucleo.credito import liberar, reservar
from flowpool.nucleo.custos import custo_de
from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.tipos import Conta, Modelo, Tier

AGORA = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def conta(id, tier, saldo):
    return Conta(id=id, email=f"c{id}@x.com", tier=tier, perfil=f"p/{id}", saldo=saldo)


def pool():
    return [
        conta(1, Tier.FREE, 50),
        conta(2, Tier.FREE, 50),
        conta(3, Tier.FREE, 50),
        conta(4, Tier.ULTRA, 9000),
    ]


def test_dez_jobs_fast_drenam_as_free_antes_de_tocar_a_ultra():
    contas = pool()
    ultra = contas[3]
    frees = contas[:3]
    despachos = []

    for _ in range(10):
        escolhida = escolher(contas, Modelo.VEO_FAST, AGORA)
        assert escolhida is not None, "o pool tem Ultra com 9000; nunca deveria secar"
        # so se reserva na conta que `escolher` devolveu — mesmo objeto, nao uma copia
        assert any(escolhida is c for c in contas)
        custo = reservar(escolhida, Modelo.VEO_FAST)
        assert custo == custo_de(Modelo.VEO_FAST, escolhida.tier)
        despachos.append(escolhida.id)

    # Fast custa 20 na free: 50 -> 30 -> 10. Cada free rende 2 jobs, as tres rendem 6.
    assert despachos[:6].count(1) == 2
    assert despachos[:6].count(2) == 2
    assert despachos[:6].count(3) == 2
    assert ultra.id not in despachos[:6]
    # so depois de nenhuma free caber e que a Ultra entra
    assert despachos[6:] == [ultra.id] * 4

    for f in frees:
        assert f.saldo < custo_de(Modelo.VEO_FAST, Tier.FREE)

    assert all(c.saldo >= 0 for c in contas)
    assert ultra.saldo == 9000 - 4 * custo_de(Modelo.VEO_FAST, Tier.ULTRA)


def test_nenhuma_conta_e_reservada_sem_ter_sido_escolhida():
    contas = pool()
    tocadas = set()

    for _ in range(10):
        escolhida = escolher(contas, Modelo.VEO_FAST, AGORA)
        reservar(escolhida, Modelo.VEO_FAST)
        tocadas.add(escolhida.id)

    intactas = [c for c in contas if c.id not in tocadas]
    assert all(c.saldo == 50 or c.saldo == 9000 for c in intactas)


def test_falha_de_geracao_devolve_a_reserva_intacta():
    contas = pool()
    escolhida = escolher(contas, Modelo.VEO_FAST, AGORA)
    antes = escolhida.saldo

    custo = reservar(escolhida, Modelo.VEO_FAST)
    # a geracao morre aqui: timeout de rede, sessao caida, o que for
    liberar(escolhida, custo)

    assert escolhida.saldo == antes
```

- [ ] **Step 6: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_pool.py -q`
Expected: `3 passed`

- [ ] **Step 7: Rodar o núcleo inteiro**

Run: `.venv/Scripts/pytest -q`
Expected: `59 passed` (8 tipos + 17 custos + 16 escolha + 15 credito + 3 pool)

- [ ] **Step 8: Commit**

```bash
git add flowpool/nucleo/erros.py flowpool/nucleo/credito.py tests/test_credito.py tests/test_pool.py
git commit -m "feat: reserva, estorno e reconciliacao de credito"
```

---

### Task 5: Esquema e repositório SQLite

**Files:**
- Create: `flowpool/dados/esquema.sql`, `flowpool/dados/repo.py`
- Test: `tests/test_repo.py`

`custo_estimado` é persistido junto com o job. Não é enfeite: é o que permite estornar a reserva de um job que morreu no meio, inclusive depois de um restart do serviço — quem reabrir o banco precisa saber quanto aquele job tinha reservado. Quem consome esse campo depois do restart é o `recuperar()` da Task 7.

Cinco decisões desta task que não são detalhe de implementação:

**`reivindicar(agora)` no lugar de `proximo_pendente(agora)`.** Ler o job e marcá-lo `rodando` em dois passos deixa entre os dois uma janela em que o job continua `pendente` e visível para todo mundo. Com três workers e teto de simultâneos 1, dois jobs viraram três gerações e 60 créditos gastos em vez de 40 — e o worker perdedor sobrescreveu o arquivo do vencedor. O `UPDATE ... RETURNING` fecha a janela: quem perde a corrida simplesmente não vê mais o job.

**`marcar_sessao(conta_id, saude, falhas=None)`.** `salvar_conta` grava a linha inteira a partir de uma cópia carregada no início do passo. Com dois workers na mesma conta, um marca `EXPIRADA` e o outro ressuscita para `VIVA` com a cópia velha — e o pool volta a despachar para uma conta que exige login manual. A escrita de campo específico é o que impede isso onde a corrida importa.

**`PRAGMA busy_timeout = 5000` e `close()`.** Vários workers escrevem na mesma base; sem o `busy_timeout` o segundo escritor recebe `database is locked` na hora em vez de esperar a transação curta do outro. O `close()` existe por causa do Windows: o handle aberto impede o pytest de apagar o `tmp_path`, e a suíte falha de forma intermitente, sem relação nenhuma com o teste que falhou.

**A tabela `projeto` tem `UNIQUE (conta_id, produto)` e `registrar_projeto` é um upsert.** A chave é o par porque projeto do Flow pertence a uma conta só: a `g2` não abre o projeto da `g1`, então o mesmo produto precisa de um projeto próprio em cada conta. O upsert (`ON CONFLICT ... DO UPDATE ... RETURNING id`) existe porque dois workers podem resolver o mesmo produto na mesma conta ao mesmo tempo; com `INSERT` puro o perdedor da corrida levantaria `IntegrityError` no meio de um job que **já pagou crédito**. O `UNIQUE` continua sendo a garantia de que não existem duas linhas para o mesmo par — o upsert só decide o que fazer quando a garantia é acionada.

`job.produto` entra como `TEXT NOT NULL DEFAULT ''`. O default cobre a linha antiga e o job criado direto pelo repo; quem exige de verdade é a API.

**`_txt` normaliza o datetime para UTC antes do `isoformat()`.** A comparação de `reavaliar_em` no SQL é lexicográfica sobre texto. Gravar dois offsets diferentes quebraria a ordem em silêncio: um horário com `-03:00` ordena antes de um `+00:00` que é anterior de verdade.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_repo.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from flowpool.dados.repo import Repo
from flowpool.nucleo.tipos import Conta, EstadoJob, Job, Modelo, Modo, SaudeSessao, Tier


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path / "t.db")
    r.criar_esquema()
    return r


def test_criar_e_ler_conta(repo):
    id_ = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    c = repo.conta(id_)
    assert c.email == "a@b.com"
    assert c.tier is Tier.FREE
    assert c.saude is SaudeSessao.VIVA


def test_email_de_conta_e_unico(repo):
    repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    with pytest.raises(ValueError, match="ja cadastrada"):
        repo.criar_conta(email="a@b.com", tier=Tier.PRO, perfil="p/2")


def test_salvar_conta_persiste_saldo_e_saude(repo):
    id_ = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    c = repo.conta(id_)
    c.saldo = 42
    c.saude = SaudeSessao.EXPIRADA
    c.saldo_lido_em = datetime(2026, 8, 19, tzinfo=timezone.utc)
    repo.salvar_conta(c)
    lida = repo.conta(id_)
    assert lida.saldo == 42
    assert lida.saude is SaudeSessao.EXPIRADA
    assert lida.saldo_lido_em.year == 2026


def test_contas_lista_todas(repo):
    repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    repo.criar_conta(email="c@d.com", tier=Tier.ULTRA, perfil="p/2")
    assert len(repo.contas()) == 2


def test_criar_e_ler_job(repo):
    id_ = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                             prompt="uma gata", destino="Video Roupa/clipes", nome="c1"))
    j = repo.job(id_)
    assert j.prompt == "uma gata"
    assert j.estado is EstadoJob.PENDENTE
    assert j.criado_em is not None
    assert j.custo_estimado is None
    # o custo reservado precisa sobreviver a um restart para poder ser estornado
    j.custo_estimado = 20
    repo.salvar_job(j)
    assert repo.job(id_).custo_estimado == 20


def test_referencias_sobrevivem_ao_banco(repo):
    id_ = repo.criar_job(Job(id=0, modo=Modo.INGREDIENTS2V, modelo=Modelo.VEO_FAST,
                             prompt="p", destino="d", nome="n",
                             referencias=["a/1.png", "a/2.png", "a/3.png"]))
    assert repo.job(id_).referencias == ["a/1.png", "a/2.png", "a/3.png"]


def test_reivindicar_respeita_prioridade(repo):
    repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                       prompt="baixa", destino="d", nome="n1", prioridade=0))
    repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                       prompt="alta", destino="d", nome="n2", prioridade=9))
    assert repo.reivindicar(agora=datetime.now(timezone.utc)).prompt == "alta"


def test_reivindicar_ignora_job_esperando_reset(repo):
    futuro = datetime(2099, 1, 1, tzinfo=timezone.utc)
    j = Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="d", nome="n")
    j.reavaliar_em = futuro
    repo.criar_job(j)
    assert repo.reivindicar(agora=datetime.now(timezone.utc)) is None


def test_reivindicar_ja_devolve_o_job_marcado_rodando(repo):
    jid = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                             prompt="p", destino="d", nome="n"))
    j = repo.reivindicar(agora=datetime.now(timezone.utc))
    assert j.id == jid
    assert j.estado is EstadoJob.RODANDO
    assert repo.job(jid).estado is EstadoJob.RODANDO


def test_reivindicar_nao_entrega_o_mesmo_job_duas_vezes(repo):
    # e este o passo que impede dois workers de gerarem (e pagarem) o mesmo job
    repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                       prompt="p", destino="d", nome="n"))
    agora = datetime.now(timezone.utc)
    assert repo.reivindicar(agora) is not None
    assert repo.reivindicar(agora) is None


def test_seca_ate_sobrevive_ao_banco(repo):
    id_ = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    c = repo.conta(id_)
    c.seca_ate = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
    repo.salvar_conta(c)
    assert repo.conta(id_).seca_ate == c.seca_ate


def test_marcar_sessao_nao_toca_no_saldo(repo):
    # dois workers na mesma conta: a marcacao de sessao nao pode carregar junto uma
    # copia velha do saldo e desfazer o que o outro worker gravou
    id_ = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    velha = repo.conta(id_)
    nova = repo.conta(id_); nova.saldo = 30; repo.salvar_conta(nova)

    repo.marcar_sessao(velha.id, SaudeSessao.EXPIRADA, falhas=2)

    lida = repo.conta(id_)
    assert lida.saude is SaudeSessao.EXPIRADA
    assert lida.falhas == 2
    assert lida.saldo == 30


def test_txt_normaliza_o_datetime_para_utc(repo):
    # a comparacao de `reavaliar_em` no SQL e lexicografica: offset diferente
    # quebraria a ordem em silencio
    outro_fuso = timezone(timedelta(hours=-3))
    j = Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="d", nome="n")
    j.reavaliar_em = datetime(2026, 8, 20, 9, 0, tzinfo=outro_fuso)   # 12:00 UTC
    jid = repo.criar_job(j)

    cru = repo.con.execute("SELECT reavaliar_em FROM job WHERE id=?", (jid,)).fetchone()[0]
    assert cru == "2026-08-20T12:00:00+00:00"


def test_close_libera_a_conexao(repo):
    repo.close()
    with pytest.raises(Exception):
        repo.contas()


def test_registrar_evento_e_listar_por_job(repo):
    jid = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                             prompt="p", destino="d", nome="n"))
    repo.registrar_evento(job_id=jid, conta_id=None, tipo="despachado", payload="{}")
    eventos = repo.eventos(job_id=jid)
    assert len(eventos) == 1
    assert eventos[0]["tipo"] == "despachado"


def test_registrar_e_ler_projeto(repo):
    cid = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    pid = repo.registrar_projeto(cid, "Panelas Monaco", "a1b2c3", "Panelas Monaco")
    p = repo.projeto_de(cid, "Panelas Monaco")
    assert p.id == pid
    assert p.id_remoto == "a1b2c3"
    assert p.nome_remoto == "Panelas Monaco"
    assert p.criado_em is not None


def test_projeto_de_produto_nunca_visto_e_none(repo):
    cid = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    assert repo.projeto_de(cid, "Furadeira Pnoeoce") is None


def test_mesmo_produto_em_duas_contas_sao_dois_projetos(repo):
    # projeto pertence a UMA conta: a g2 nao abre o projeto da g1, entao o mesmo
    # produto precisa de um projeto proprio em cada conta
    g1 = repo.criar_conta(email="g1@b.com", tier=Tier.FREE, perfil="p/1")
    g2 = repo.criar_conta(email="g2@b.com", tier=Tier.FREE, perfil="p/2")
    repo.registrar_projeto(g1, "Video Roupa", "rem-1", "Video Roupa")
    repo.registrar_projeto(g2, "Video Roupa", "rem-2", "Video Roupa")

    assert repo.projeto_de(g1, "Video Roupa").id_remoto == "rem-1"
    assert repo.projeto_de(g2, "Video Roupa").id_remoto == "rem-2"
    assert len(repo.projetos()) == 2


def test_registrar_o_mesmo_par_duas_vezes_nao_duplica(repo):
    # duas corridas simultaneas na mesma dupla (conta, produto) nao podem virar duas
    # linhas: projeto duplicado no Flow e bagunca que ninguem limpa depois
    cid = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    primeiro = repo.registrar_projeto(cid, "Moda", "rem-1", "Moda")
    segundo = repo.registrar_projeto(cid, "Moda", "rem-2", "Moda")
    assert primeiro == segundo
    assert len(repo.projetos(cid)) == 1
    assert repo.projeto_de(cid, "Moda").id_remoto == "rem-2"


def test_projetos_filtra_por_conta(repo):
    g1 = repo.criar_conta(email="g1@b.com", tier=Tier.FREE, perfil="p/1")
    g2 = repo.criar_conta(email="g2@b.com", tier=Tier.FREE, perfil="p/2")
    repo.registrar_projeto(g1, "Moda", "rem-1", "Moda")
    repo.registrar_projeto(g1, "Vestir", "rem-2", "Vestir")
    repo.registrar_projeto(g2, "Moda", "rem-3", "Moda")

    assert len(repo.projetos(g1)) == 2
    assert [p.id_remoto for p in repo.projetos(g2)] == ["rem-3"]


def test_esquecer_projeto_apaga_so_o_vinculo_local(repo):
    cid = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    pid = repo.registrar_projeto(cid, "Moda", "rem-1", "Moda")
    repo.esquecer_projeto(pid)
    assert repo.projeto_de(cid, "Moda") is None
    assert repo.projetos() == []


def test_produto_do_job_sobrevive_ao_banco(repo):
    id_ = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p",
                             destino="Panelas Monaco/clipes", nome="c1",
                             produto="Panelas Monaco"))
    assert repo.job(id_).produto == "Panelas Monaco"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_repo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.dados.repo'`

- [ ] **Step 3: Escrever o esquema**

`flowpool/dados/esquema.sql`:

```sql
CREATE TABLE IF NOT EXISTS conta (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    NOT NULL UNIQUE,
    tier            TEXT    NOT NULL,
    perfil          TEXT    NOT NULL,
    saldo           INTEGER NOT NULL DEFAULT 0,
    saldo_lido_em   TEXT,
    saldo_confiavel INTEGER NOT NULL DEFAULT 0,
    reset_em        TEXT,
    seca_ate        TEXT,
    saude           TEXT    NOT NULL DEFAULT 'viva',
    falhas          INTEGER NOT NULL DEFAULT 0,
    ativa           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS job (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    modo           TEXT    NOT NULL,
    modelo         TEXT    NOT NULL,
    prompt         TEXT    NOT NULL,
    destino        TEXT    NOT NULL,
    nome           TEXT    NOT NULL,
    produto        TEXT    NOT NULL DEFAULT '',
    referencias    TEXT    NOT NULL DEFAULT '[]',
    frame_inicial  TEXT,
    frame_final    TEXT,
    prioridade     INTEGER NOT NULL DEFAULT 0,
    estado         TEXT    NOT NULL DEFAULT 'pendente',
    conta_id       INTEGER REFERENCES conta(id),
    id_remoto      TEXT,
    arquivo        TEXT,
    tentativas     INTEGER NOT NULL DEFAULT 0,
    custo_estimado INTEGER,
    erro           TEXT,
    reavaliar_em   TEXT,
    criado_em      TEXT    NOT NULL,
    atualizado_em  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_fila ON job (estado, prioridade DESC, id);

CREATE TABLE IF NOT EXISTS projeto (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    conta_id    INTEGER NOT NULL REFERENCES conta(id),
    produto     TEXT    NOT NULL,
    id_remoto   TEXT    NOT NULL,
    nome_remoto TEXT    NOT NULL,
    criado_em   TEXT    NOT NULL,
    UNIQUE (conta_id, produto)
);

CREATE TABLE IF NOT EXISTS evento (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    INTEGER REFERENCES job(id),
    conta_id  INTEGER REFERENCES conta(id),
    tipo      TEXT NOT NULL,
    payload   TEXT NOT NULL DEFAULT '{}',
    em        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evento_job ON evento (job_id, id);
```

- [ ] **Step 4: Implementar o repositório**

`flowpool/dados/repo.py`:

```python
"""Unica parte do sistema que conhece SQL."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flowpool.nucleo.tipos import (
    Conta, EstadoJob, Job, Modelo, Modo, Projeto, SaudeSessao, Tier,
)

_ESQUEMA = Path(__file__).parent / "esquema.sql"


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _txt(d: datetime | None) -> str | None:
    """Serializa sempre em UTC.

    A comparacao de `reavaliar_em` no SQL e lexicografica sobre texto. Gravar dois
    offsets diferentes faria a ordem quebrar em silencio: um horario com `-03:00`
    ordena antes de um `+00:00` que e anterior de verdade.
    """
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


class Repo:
    def __init__(self, caminho: Path | str) -> None:
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.caminho, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        # Varios workers escrevem na mesma base. Sem isto o segundo escritor recebe
        # "database is locked" na hora em vez de esperar a transacao curta do outro.
        self.con.execute("PRAGMA busy_timeout = 5000")

    def close(self) -> None:
        """Fecha a conexao.

        No Windows o handle aberto impede o pytest de apagar o `tmp_path`, o que faz a
        suite falhar de forma intermitente e sem relacao com o teste que falhou.
        """
        self.con.close()

    def criar_esquema(self) -> None:
        self.con.executescript(_ESQUEMA.read_text(encoding="utf-8"))
        self.con.commit()

    # ---------- contas ----------

    def criar_conta(self, email: str, tier: Tier, perfil: str) -> int:
        try:
            cur = self.con.execute(
                "INSERT INTO conta (email, tier, perfil) VALUES (?, ?, ?)",
                (email, tier.value, perfil),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"conta {email} ja cadastrada") from e
        self.con.commit()
        return int(cur.lastrowid)

    def conta(self, id_: int) -> Conta | None:
        r = self.con.execute("SELECT * FROM conta WHERE id = ?", (id_,)).fetchone()
        return self._conta(r) if r else None

    def contas(self) -> list[Conta]:
        rs = self.con.execute("SELECT * FROM conta ORDER BY id").fetchall()
        return [self._conta(r) for r in rs]

    def salvar_conta(self, c: Conta) -> None:
        self.con.execute(
            """UPDATE conta SET tier=?, perfil=?, saldo=?, saldo_lido_em=?,
                   saldo_confiavel=?, reset_em=?, seca_ate=?, saude=?, falhas=?, ativa=?
               WHERE id=?""",
            (c.tier.value, c.perfil, c.saldo, _txt(c.saldo_lido_em),
             int(c.saldo_confiavel), _txt(c.reset_em), _txt(c.seca_ate),
             c.saude.value, c.falhas, int(c.ativa), c.id),
        )
        self.con.commit()

    def marcar_sessao(self, conta_id: int, saude: SaudeSessao,
                      falhas: int | None = None) -> None:
        """Escreve so as colunas de sessao, sem a linha inteira.

        `salvar_conta` grava a partir de uma copia carregada no inicio do passo. Com
        dois workers na mesma conta, um marca EXPIRADA e o outro ressuscita para VIVA
        com sua copia velha — e o pool volta a despachar para uma conta que exige
        login manual.
        """
        if falhas is None:
            self.con.execute("UPDATE conta SET saude=? WHERE id=?",
                             (saude.value, conta_id))
        else:
            self.con.execute("UPDATE conta SET saude=?, falhas=? WHERE id=?",
                             (saude.value, falhas, conta_id))
        self.con.commit()

    @staticmethod
    def _conta(r: sqlite3.Row) -> Conta:
        return Conta(
            id=r["id"], email=r["email"], tier=Tier(r["tier"]), perfil=r["perfil"],
            saldo=r["saldo"], saldo_lido_em=_dt(r["saldo_lido_em"]),
            saldo_confiavel=bool(r["saldo_confiavel"]), reset_em=_dt(r["reset_em"]),
            seca_ate=_dt(r["seca_ate"]), saude=SaudeSessao(r["saude"]),
            falhas=r["falhas"], ativa=bool(r["ativa"]),
        )

    # ---------- jobs ----------

    def criar_job(self, j: Job) -> int:
        agora = _txt(datetime.now(timezone.utc))
        cur = self.con.execute(
            """INSERT INTO job (modo, modelo, prompt, destino, nome, produto,
                   referencias, frame_inicial, frame_final, prioridade, estado,
                   custo_estimado, reavaliar_em, criado_em, atualizado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (j.modo.value, j.modelo.value, j.prompt, j.destino, j.nome, j.produto,
             json.dumps(j.referencias), j.frame_inicial, j.frame_final, j.prioridade,
             j.estado.value, j.custo_estimado, _txt(j.reavaliar_em), agora, agora),
        )
        self.con.commit()
        return int(cur.lastrowid)

    def job(self, id_: int) -> Job | None:
        r = self.con.execute("SELECT * FROM job WHERE id = ?", (id_,)).fetchone()
        return self._job(r) if r else None

    def jobs(self, estado: EstadoJob | None = None) -> list[Job]:
        if estado:
            rs = self.con.execute(
                "SELECT * FROM job WHERE estado = ? ORDER BY id DESC", (estado.value,)
            ).fetchall()
        else:
            rs = self.con.execute("SELECT * FROM job ORDER BY id DESC").fetchall()
        return [self._job(r) for r in rs]

    def reivindicar(self, agora: datetime) -> Job | None:
        """Pega o proximo pendente e ja o marca `rodando`, em UMA instrucao.

        Ler e depois marcar sao dois passos, e entre eles o job continua visivel para
        os outros workers: dois deles pegam o mesmo job, geram duas vezes, pagam duas
        vezes e um sobrescreve o arquivo do outro. O UPDATE ... RETURNING fecha a
        janela — quem perde a corrida nao ve mais o job.
        """
        ts = _txt(agora)
        cur = self.con.execute(
            """UPDATE job SET estado='rodando', atualizado_em=?
                WHERE id = (SELECT id FROM job
                             WHERE estado='pendente'
                               AND (reavaliar_em IS NULL OR reavaliar_em <= ?)
                             ORDER BY prioridade DESC, id ASC LIMIT 1)
             RETURNING *""",
            (ts, ts),
        )
        rs = cur.fetchall()
        self.con.commit()
        return self._job(rs[0]) if rs else None

    def salvar_job(self, j: Job) -> None:
        self.con.execute(
            """UPDATE job SET estado=?, conta_id=?, id_remoto=?, arquivo=?,
                   tentativas=?, custo_estimado=?, erro=?, reavaliar_em=?,
                   atualizado_em=?
               WHERE id=?""",
            (j.estado.value, j.conta_id, j.id_remoto, j.arquivo, j.tentativas,
             j.custo_estimado, j.erro, _txt(j.reavaliar_em),
             _txt(datetime.now(timezone.utc)), j.id),
        )
        self.con.commit()

    @staticmethod
    def _job(r: sqlite3.Row) -> Job:
        return Job(
            id=r["id"], modo=Modo(r["modo"]), modelo=Modelo(r["modelo"]),
            prompt=r["prompt"], destino=r["destino"], nome=r["nome"],
            produto=r["produto"], referencias=json.loads(r["referencias"]),
            frame_inicial=r["frame_inicial"], frame_final=r["frame_final"],
            prioridade=r["prioridade"], estado=EstadoJob(r["estado"]),
            conta_id=r["conta_id"], id_remoto=r["id_remoto"], arquivo=r["arquivo"],
            tentativas=r["tentativas"], custo_estimado=r["custo_estimado"],
            erro=r["erro"],
            reavaliar_em=_dt(r["reavaliar_em"]), criado_em=_dt(r["criado_em"]),
            atualizado_em=_dt(r["atualizado_em"]),
        )

    # ---------- projetos ----------

    def projeto_de(self, conta_id: int, produto: str) -> Projeto | None:
        """Projeto daquele produto NAQUELA conta.

        A chave e o par: projeto do Flow pertence a uma conta so, e a `g2` nao abre o
        projeto da `g1`. Procurar so pelo produto devolveria um id que a conta da vez
        nem consegue carregar.
        """
        r = self.con.execute(
            "SELECT * FROM projeto WHERE conta_id = ? AND produto = ?",
            (conta_id, produto),
        ).fetchone()
        return self._projeto(r) if r else None

    def registrar_projeto(self, conta_id: int, produto: str, id_remoto: str,
                          nome_remoto: str) -> int:
        """Grava o vinculo (conta, produto) -> projeto do Flow. Devolve o id local.

        Upsert em vez de INSERT puro: dois workers podem resolver o mesmo produto na
        mesma conta ao mesmo tempo, e o perdedor da corrida levantaria `IntegrityError`
        no meio de um job que ja pagou credito. O UNIQUE continua sendo a garantia de
        que nao ha duas linhas para o mesmo par.
        """
        cur = self.con.execute(
            """INSERT INTO projeto (conta_id, produto, id_remoto, nome_remoto, criado_em)
               VALUES (?,?,?,?,?)
               ON CONFLICT (conta_id, produto)
               DO UPDATE SET id_remoto = excluded.id_remoto,
                             nome_remoto = excluded.nome_remoto
               RETURNING id""",
            (conta_id, produto, id_remoto, nome_remoto,
             _txt(datetime.now(timezone.utc))),
        )
        id_ = int(cur.fetchone()["id"])
        self.con.commit()
        return id_

    def projetos(self, conta_id: int | None = None) -> list[Projeto]:
        if conta_id is None:
            rs = self.con.execute("SELECT * FROM projeto ORDER BY id").fetchall()
        else:
            rs = self.con.execute(
                "SELECT * FROM projeto WHERE conta_id = ? ORDER BY id", (conta_id,)
            ).fetchall()
        return [self._projeto(r) for r in rs]

    def esquecer_projeto(self, id_: int) -> None:
        """Apaga so o vinculo local. O projeto continua existindo no Flow."""
        self.con.execute("DELETE FROM projeto WHERE id = ?", (id_,))
        self.con.commit()

    @staticmethod
    def _projeto(r: sqlite3.Row) -> Projeto:
        return Projeto(
            id=r["id"], conta_id=r["conta_id"], produto=r["produto"],
            id_remoto=r["id_remoto"], nome_remoto=r["nome_remoto"],
            criado_em=_dt(r["criado_em"]),
        )

    # ---------- eventos ----------

    def registrar_evento(self, job_id: int | None, conta_id: int | None,
                         tipo: str, payload: str = "{}") -> None:
        self.con.execute(
            "INSERT INTO evento (job_id, conta_id, tipo, payload, em) VALUES (?,?,?,?,?)",
            (job_id, conta_id, tipo, payload, _txt(datetime.now(timezone.utc))),
        )
        self.con.commit()

    def eventos(self, job_id: int | None = None) -> list[dict]:
        if job_id is not None:
            rs = self.con.execute(
                "SELECT * FROM evento WHERE job_id = ? ORDER BY id", (job_id,)
            ).fetchall()
        else:
            rs = self.con.execute("SELECT * FROM evento ORDER BY id DESC LIMIT 200").fetchall()
        return [dict(r) for r in rs]
```

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_repo.py -q`
Expected: `22 passed`

- [ ] **Step 6: Commit**

```bash
git add flowpool/dados tests/test_repo.py
git commit -m "feat: esquema sqlite e repositorio"
```

---

### Task 6: Interface de transporte e transporte falso

**Files:**
- Create: `flowpool/transporte/base.py`, `flowpool/transporte/falso.py`
- Test: `tests/test_transporte_falso.py`

`base.py` **não define** a sua própria `SaldoInsuficiente`: importa a do núcleo (`flowpool.nucleo.erros`) e reexporta. `ErroTransporte`, `SessaoMorta` e `GeracaoFalhou` continuam sendo do transporte — só a de saldo é compartilhada, porque `nucleo/credito.py` também a levanta e o orquestrador precisa que os dois caminhos caiam no mesmo `except`. Consequência a lembrar: `SaldoInsuficiente` não é mais subclasse de `ErroTransporte`; quem quiser pegar as duas famílias escreve `except (ErroTransporte, SaldoInsuficiente)`.

O transporte falso precisa simular geração **assíncrona**, senão o motor nunca é exercitado nos caminhos que mais custam crédito. Três interruptores além dos originais:

- `passos_rodando`: as N primeiras chamadas de `consultar` devolvem `RODANDO`. É o caso normal do Flow — geração não fica pronta na primeira consulta.
- `nunca_termina`: a geração fica `RODANDO` para sempre, para exercitar o timeout do orquestrador sem esperar dez minutos de verdade.
- `morre_ao_gerar`: `gerar()` levanta `SessaoMorta`. É o que reproduz a sessão do Google que cai **no meio** da geração, e não só em `abrir_sessao`.

O transporte também ganha **três operações de projeto** — `listar_projetos`, `criar_projeto` e `abrir_projeto` — porque no Flow real toda geração acontece dentro de um projeto e o projeto tem id na URL (`labs.google/fx/tools/flow/[id]`). Sem elas, cada geração criaria um projeto novo e a conta viraria um lixão de projetos órfãos.

`ProjetoRemoto` (`id_remoto`, `nome`) é o projeto como o Flow o mostra. Toda leitura sai de **dentro de uma sessão**: não existe lista global de projetos, cada conta enxerga só os seus.

Dois interruptores novos no falso, os dois a serviço da mesma pergunta — "o app duplicou projeto?":

- `projetos`: nomes de projeto que já existem em cada conta, como se tivessem sido criados na mão pelo site. É o que exercita a **adoção** da Task 7.
- `criacoes_projeto`: quantas vezes `criar_projeto` foi chamado. É o contador que denuncia duplicação quando o reuso ou a adoção param de funcionar.

`abrir_projeto` do falso **levanta** para id de projeto de outra conta. Isso não é zelo: projeto pertence a uma conta só, e um falso permissivo deixaria passar no teste um motor que no Flow real tentaria abrir a URL de um projeto que a conta da vez nem consegue carregar.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_transporte_falso.py`:

```python
import pytest

from flowpool.nucleo.tipos import Conta, Modelo, Modo, Tier
from flowpool.transporte.base import (
    ErroTransporte, Estado, Pedido, ProjetoRemoto, SaldoInsuficiente, SessaoMorta,
)
from flowpool.transporte.falso import TransporteFalso


def conta(saldo=50):
    return Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil="p", saldo=saldo)


def pedido():
    return Pedido(modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="uma gata",
                  referencias=[], frame_inicial=None, frame_final=None)


async def test_gerar_devolve_id_remoto_e_fica_pronto():
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    id_remoto = await t.gerar(s, pedido())
    assert id_remoto
    assert await t.consultar(s, id_remoto) is Estado.PRONTO


async def test_gerar_debita_o_saldo_simulado():
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    await t.gerar(s, pedido())
    assert await t.saldo(s) == 30


async def test_saldo_insuficiente_levanta():
    t = TransporteFalso(saldos={1: 5})
    s = await t.abrir_sessao(conta(saldo=5))
    with pytest.raises(SaldoInsuficiente):
        await t.gerar(s, pedido())


async def test_sessao_morta_levanta_ao_abrir():
    t = TransporteFalso(saldos={1: 50}, mortas={1})
    with pytest.raises(SessaoMorta):
        await t.abrir_sessao(conta())


async def test_baixar_escreve_o_arquivo(tmp_path):
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    id_remoto = await t.gerar(s, pedido())
    destino = tmp_path / "c1.mp4"
    saida = await t.baixar(s, id_remoto, destino)
    assert saida == destino
    assert destino.read_bytes().startswith(b"FALSO")


async def test_falhar_em_conta_especifica():
    t = TransporteFalso(saldos={1: 50}, falha_em={1})
    s = await t.abrir_sessao(conta())
    id_remoto = await t.gerar(s, pedido())
    assert await t.consultar(s, id_remoto) is Estado.FALHOU


async def test_passos_rodando_atrasa_o_pronto():
    # geracao real nao fica pronta na primeira consulta; o motor precisa fazer polling
    t = TransporteFalso(saldos={1: 50}, passos_rodando=2)
    s = await t.abrir_sessao(conta())
    id_remoto = await t.gerar(s, pedido())
    assert await t.consultar(s, id_remoto) is Estado.RODANDO
    assert await t.consultar(s, id_remoto) is Estado.RODANDO
    assert await t.consultar(s, id_remoto) is Estado.PRONTO


async def test_nunca_termina_fica_rodando_para_sempre():
    t = TransporteFalso(saldos={1: 50}, nunca_termina={1})
    s = await t.abrir_sessao(conta())
    id_remoto = await t.gerar(s, pedido())
    for _ in range(5):
        assert await t.consultar(s, id_remoto) is Estado.RODANDO


async def test_morre_ao_gerar_levanta_sessao_morta_sem_cobrar():
    # a sessao do Google cai no meio de `gerar`, nao so em `abrir_sessao`
    t = TransporteFalso(saldos={1: 50}, morre_ao_gerar={1})
    s = await t.abrir_sessao(conta())
    with pytest.raises(SessaoMorta):
        await t.gerar(s, pedido())
    assert await t.saldo(s) == 50
    assert t.geracoes == {}


async def test_conta_nova_nao_tem_projeto_nenhum():
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    assert await t.listar_projetos(s) == []


async def test_criar_projeto_devolve_id_e_conta_as_chamadas():
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    assert await t.criar_projeto(s, "Panelas Monaco") == "proj-1"
    assert await t.criar_projeto(s, "Video Roupa") == "proj-2"
    assert t.criacoes_projeto == 2
    assert await t.listar_projetos(s) == [
        ProjetoRemoto(id_remoto="proj-1", nome="Panelas Monaco"),
        ProjetoRemoto(id_remoto="proj-2", nome="Video Roupa"),
    ]


async def test_projeto_de_uma_conta_nao_aparece_na_outra():
    # e a restricao que define o desenho: projeto pertence a UMA conta
    t = TransporteFalso(saldos={1: 50, 2: 50})
    g1 = await t.abrir_sessao(conta())
    g2 = await t.abrir_sessao(Conta(id=2, email="g2@b.com", tier=Tier.FREE, perfil="p"))
    await t.criar_projeto(g1, "Moda")
    assert await t.listar_projetos(g2) == []


async def test_abrir_projeto_registra_o_aberto():
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    id_remoto = await t.criar_projeto(s, "Moda")
    await t.abrir_projeto(s, id_remoto)
    assert t.abertos[1] == id_remoto


async def test_abrir_projeto_de_outra_conta_levanta():
    t = TransporteFalso(saldos={1: 50, 2: 50})
    g1 = await t.abrir_sessao(conta())
    g2 = await t.abrir_sessao(Conta(id=2, email="g2@b.com", tier=Tier.FREE, perfil="p"))
    alheio = await t.criar_projeto(g1, "Moda")
    with pytest.raises(ErroTransporte):
        await t.abrir_projeto(g2, alheio)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_transporte_falso.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.transporte.base'`

- [ ] **Step 3: Implementar a interface**

`flowpool/transporte/base.py`:

```python
"""Fronteira do sistema. Acima daqui ninguem sabe que o Flow existe."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from flowpool.nucleo.erros import SaldoInsuficiente
from flowpool.nucleo.tipos import Conta, Modelo, Modo

# `SaldoInsuficiente` mora no nucleo e e so reexportada aqui, para quem importa de
# `transporte.base` continuar funcionando. Duas classes com o mesmo nome fariam o
# `except` do orquestrador pegar uma e deixar a outra passar como falha de rede.
__all__ = [
    "Estado", "Pedido", "ProjetoRemoto", "Sessao", "FlowTransport",
    "ErroTransporte", "SaldoInsuficiente", "SessaoMorta", "GeracaoFalhou",
]


class Estado(str, Enum):
    RODANDO = "rodando"
    PRONTO = "pronto"
    FALHOU = "falhou"


@dataclass
class Pedido:
    modo: Modo
    modelo: Modelo
    prompt: str
    referencias: list[str] = field(default_factory=list)
    frame_inicial: str | None = None
    frame_final: str | None = None


@dataclass
class ProjetoRemoto:
    """Projeto como o Flow o mostra: um id na URL e um nome na lista.

    Sempre lido de DENTRO de uma sessao. Nao existe lista global de projetos: cada
    conta enxerga so os seus.
    """

    id_remoto: str
    nome: str


@dataclass
class Sessao:
    conta: Conta
    handle: Any = None


class ErroTransporte(Exception):
    """Base de tudo que o transporte sabe dar errado."""


class SessaoMorta(ErroTransporte):
    """O login caiu. Exige pessoa; nenhum retry resolve."""


class GeracaoFalhou(ErroTransporte):
    """O Flow aceitou e depois devolveu erro."""


class FlowTransport(Protocol):
    async def abrir_sessao(self, conta: Conta) -> Sessao: ...
    async def saldo(self, s: Sessao) -> int: ...
    # Toda geracao no Flow acontece DENTRO de um projeto, e o projeto tem id na URL
    # (`labs.google/fx/tools/flow/[id]`). Sem essas tres, cada geracao criaria um
    # projeto novo e a conta viraria um lixao de projetos orfaos.
    async def listar_projetos(self, s: Sessao) -> list[ProjetoRemoto]: ...
    async def criar_projeto(self, s: Sessao, nome: str) -> str: ...
    async def abrir_projeto(self, s: Sessao, id_remoto: str) -> None: ...
    async def gerar(self, s: Sessao, p: Pedido) -> str: ...
    async def consultar(self, s: Sessao, id_remoto: str) -> Estado: ...
    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path: ...
    async def fechar(self, s: Sessao) -> None: ...
```

- [ ] **Step 4: Implementar o transporte falso**

`flowpool/transporte/falso.py`:

```python
"""Transporte em memoria. E contra ele que o motor inteiro e testado."""

from __future__ import annotations

import asyncio
from pathlib import Path

from flowpool.nucleo.custos import custo_de
from flowpool.nucleo.tipos import Conta
from flowpool.transporte.base import (
    ErroTransporte, Estado, Pedido, ProjetoRemoto, SaldoInsuficiente, Sessao,
    SessaoMorta,
)


class TransporteFalso:
    """Simula os modos de falha reais do Flow.

    - `mortas`: `abrir_sessao` levanta `SessaoMorta` (login caiu antes de comecar).
    - `morre_ao_gerar`: `gerar` levanta `SessaoMorta` (o Chrome caiu no meio).
    - `falha_em`: a geracao e aceita e volta `FALHOU`.
    - `passos_rodando`: as N primeiras consultas de cada geracao voltam `RODANDO`.
    - `nunca_termina`: a geracao fica `RODANDO` para sempre, para exercitar timeout.
    - `projetos`: nomes de projeto que ja existem em cada conta, como se tivessem sido
      criados na mao pelo site. E o que exercita a adocao.
    """

    def __init__(self, saldos: dict[int, int], mortas: set[int] | None = None,
                 falha_em: set[int] | None = None, latencia: float = 0.0,
                 passos_rodando: int = 0, nunca_termina: set[int] | None = None,
                 morre_ao_gerar: set[int] | None = None,
                 projetos: dict[int, list[str]] | None = None) -> None:
        self.saldos = dict(saldos)
        self.mortas = mortas or set()
        self.falha_em = falha_em or set()
        self.latencia = latencia
        self.passos_rodando = passos_rodando
        self.nunca_termina = nunca_termina or set()
        self.morre_ao_gerar = morre_ao_gerar or set()
        self.geracoes: dict[str, tuple[int, Estado]] = {}
        self.consultas: dict[str, int] = {}
        self._seq = 0
        # projetos por conta: nenhuma conta enxerga os projetos da outra
        self.projetos: dict[int, list[ProjetoRemoto]] = {}
        self.abertos: dict[int, str] = {}
        # quantas vezes `criar_projeto` foi chamado; e o contador que denuncia
        # duplicacao quando o reuso ou a adocao param de funcionar
        self.criacoes_projeto = 0
        self._seq_projeto = 0
        for conta_id, nomes in (projetos or {}).items():
            for nome in nomes:
                self._novo_projeto(conta_id, nome)

    async def abrir_sessao(self, conta: Conta) -> Sessao:
        if conta.id in self.mortas:
            raise SessaoMorta(f"sessao da conta {conta.email} expirou")
        return Sessao(conta=conta)

    async def saldo(self, s: Sessao) -> int:
        return self.saldos.get(s.conta.id, 0)

    def _novo_projeto(self, conta_id: int, nome: str) -> ProjetoRemoto:
        self._seq_projeto += 1
        p = ProjetoRemoto(id_remoto=f"proj-{self._seq_projeto}", nome=nome)
        self.projetos.setdefault(conta_id, []).append(p)
        return p

    async def listar_projetos(self, s: Sessao) -> list[ProjetoRemoto]:
        return list(self.projetos.get(s.conta.id, []))

    async def criar_projeto(self, s: Sessao, nome: str) -> str:
        self.criacoes_projeto += 1
        return self._novo_projeto(s.conta.id, nome).id_remoto

    async def abrir_projeto(self, s: Sessao, id_remoto: str) -> None:
        # a conta so abre projeto seu: o Flow devolve erro para id de outra conta, e o
        # falso precisa reproduzir isso ou o motor passaria no teste e falharia no real
        meus = {p.id_remoto for p in self.projetos.get(s.conta.id, [])}
        if id_remoto not in meus:
            raise ErroTransporte(
                f"projeto {id_remoto} nao pertence a conta {s.conta.email}")
        self.abertos[s.conta.id] = id_remoto

    async def gerar(self, s: Sessao, p: Pedido) -> str:
        await asyncio.sleep(self.latencia)
        # antes de debitar: sessao que cai aqui nao chegou a fazer o Flow cobrar
        if s.conta.id in self.morre_ao_gerar:
            raise SessaoMorta(f"sessao da conta {s.conta.email} caiu ao gerar")
        custo = custo_de(p.modelo, s.conta.tier)
        if self.saldos.get(s.conta.id, 0) < custo:
            raise SaldoInsuficiente(f"conta {s.conta.email} sem credito")
        self.saldos[s.conta.id] -= custo
        self._seq += 1
        id_remoto = f"falso-{self._seq}"
        if s.conta.id in self.nunca_termina:
            final = Estado.RODANDO
        elif s.conta.id in self.falha_em:
            final = Estado.FALHOU
        else:
            final = Estado.PRONTO
        self.geracoes[id_remoto] = (s.conta.id, final)
        self.consultas[id_remoto] = 0
        return id_remoto

    async def consultar(self, s: Sessao, id_remoto: str) -> Estado:
        _, final = self.geracoes[id_remoto]
        vistas = self.consultas[id_remoto]
        self.consultas[id_remoto] = vistas + 1
        if final is Estado.RODANDO:
            return Estado.RODANDO
        return Estado.RODANDO if vistas < self.passos_rodando else final

    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"FALSO:" + id_remoto.encode())
        return destino

    async def fechar(self, s: Sessao) -> None:
        return None
```

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_transporte_falso.py -q`
Expected: `14 passed`

- [ ] **Step 6: Commit**

```bash
git add flowpool/transporte/base.py flowpool/transporte/falso.py tests/test_transporte_falso.py
git commit -m "feat: interface de transporte e implementacao falsa"
```

---

### Task 7: Orquestrador

**Files:**
- Create: `flowpool/motor/orquestrador.py`
- Test: `tests/test_orquestrador.py`

O coração do motor: puxa job, escolhe conta, reserva crédito, despacha, reconcilia, trata falha. Testado inteiro contra o transporte falso.

A regra que não pode escapar daqui: **toda reserva que não virou geração tem que ser estornada.** O orquestrador guarda em `job.custo_estimado` quanto reservou, chama `liberar` em todo caminho de falha anterior à confirmação da geração, e zera o campo depois de estornar (para não estornar duas vezes). Sem isso, três timeouts de rede numa conta de 50 créditos zeram o saldo local com os 50 créditos intactos no Flow, e a conta some do pool para sempre. No caminho de sucesso não há estorno: o crédito foi gasto de verdade, e a reconciliação com o saldo lido fecha a conta.

Isso vale para **todos** os ramos de falha, `SessaoMorta` inclusive. A sessão não morre só em `abrir_sessao`: no transporte real ela cai durante `gerar`, `consultar` ou `baixar`. Como `SessaoMorta` é o primeiro `except`, sem estorno ali o caso nunca chega ao ramo genérico e o crédito reservado some do saldo local para sempre. `_estornar` é no-op depois que o Flow aceitou (`id_remoto is not None`), então isso não infla saldo — e devolve `True` só quando crédito voltou de fato, que é como o ramo sabe se ainda precisa persistir a conta antes de `marcar_sessao`.

Oito outras decisões desta task, cada uma com um modo de falha real por trás:

**O projeto é resolvido antes de gerar, e a ordem tem três passos.** `_resolver_projeto` roda logo depois de `abrir_sessao`: (1) procura a dupla `(conta, produto)` no banco local e, se achou, só abre; (2) não achou — lista os projetos da conta e procura por **nome igual ao produto**; se achou, **adota**: grava no banco e abre, sem criar; (3) não achou nem remotamente — cria com o nome do produto, grava e abre. Cada caminho vira evento (`projeto_adotado`, `projeto_criado`).

O passo do meio é o que não pode ser cortado. Sem ele, apagar o `flowpool.db` ou criar um projeto na mão pelo site faz o app duplicar tudo — e projeto duplicado no Flow é bagunça que ninguém limpa depois, porque o app não apaga projeto nenhum. É também por isso que o teste de reuso afirma sobre `criacoes_projeto == 1`, e não só sobre o estado final do banco: o estado final não distingue "reusou" de "criou de novo e regravou por cima".

Job sem `produto` (criado direto pelo repo) não resolve projeto nenhum: criar um projeto chamado `""` seria pior do que não criar nada.

**`escolher` que levanta não pode deixar o job travado.** Com a reivindicação atômica, quando `passo()` chama `_conta_para` o job **já está `rodando`** no banco. Uma exceção subindo dali o deixava nesse estado até o próximo boot, e só `recuperar()` o destravava — o `try/except` de `rodar()` mantém a fila viva, mas não devolve o job. `ModeloSemCusto` é exatamente o caso que chega ali: `custo_de` roda dentro de `apta()`, para cada conta do pool. Agora a escolha vai dentro de `try/except Exception`, a falha vira evento `escolha_falhou` e o job volta para `pendente` **gastando uma tentativa** — é erro de configuração, e reprocessar para sempre um modelo sem custo cadastrado não conserta o cadastro.

**Recusa de saldo marca a conta seca com prazo.** No ramo `SaldoInsuficiente`: `conta.seca_ate = agora + ESPERA_RESET`, `saldo_confiavel = False`, `job.reavaliar_em = conta.seca_ate`. Sem prazo o job volta para a fila no mesmo instante, cai na mesma conta e o motor gira em laço quente — reproduzido em 50 iterações, 0 tentativas consumidas e 100 eventos gravados.

**O semáforo vem antes de reivindicar.** Reivindicar primeiro e esperar depois deixa o job puxado parado na fila do semáforo; na versão em que a marcação de `rodando` vinha depois do semáforo, ele ficava visível como `pendente` para todos os outros workers.

**`recuperar()` no boot, chamado pelo lifespan da API antes de `rodar()`.** Um crash entre `reservar` e `gerar` deixa o job em `rodando` para sempre — `reivindicar` só olha para `pendente`, então ele nunca é retomado nem falha — e o crédito reservado nunca volta. Sem isso, o `custo_estimado` persistido é um campo sem consumidor, o que contraria a justificativa que o spec dá para ele existir. Job sem `id_remoto` é estornado e volta para `pendente` sem gastar tentativa (não foi ele que falhou, foi a máquina); job com `id_remoto` volta preservando `id_remoto` e `conta_id`, sem estorno.

**O motor não morre em silêncio.** `rodar()` envolve `passo()` em `try/except`, registra `passo_falhou` e continua; `fechar()` ganha o seu próprio `try/except` dentro do `finally`. Três caminhos reais escapavam do `try` de `_executar` — `fechar()` de um browser já morto, `ModeloSemCusto` levantado por `escolher` (o erro criado justamente para não travar a fila, e que ninguém capturava) e falha de escrita no banco. Em qualquer um deles a task de `rodar()` terminava e ninguém observava: a API seguia respondendo HTTP normalmente e a fila nunca mais andava.

**Divergência vira evento, e falha de reconciliação também.** O `finally` guarda o retorno de `reconciliar` e grava `divergencia` com `{"delta", "lido"}` quando `abs(delta) >= LIMIAR_DIVERGENCIA`; o `except` do bloco grava `reconciliacao_falhou` em vez de engolir. O spec diz que é a reconciliação que responde se imagem custa crédito ou sai de graça — e o delta com sinal estava sendo calculado e jogado fora, sem chegar a lugar nenhum. O `except: pass` também engolia falha de `salvar_conta`, que era o silêncio que transformava conta seca em laço infinito.

**Timeout não pode pagar duas vezes.** `_devolver` preserva `id_remoto` e `conta_id` quando há geração em curso; `_executar` pula `gerar` se o job chega com `id_remoto` e a conta escolhida é a mesma; `_conta_para` tenta primeiro a conta anterior, e só cai na escolha normal se ela não servir mais. `TIMEOUT_GERACAO` e o intervalo de polling viram parâmetros do `__init__` (com os valores atuais como padrão) para o teste não esperar dez minutos. Antes de `_devolver`, todo ramo de falha registra o evento com `{"id_remoto", "tentativa"}` — é o que permite achar geração órfã no Flow depois. Sem nada disso, um timeout descarta o `id_remoto`, o job é gerado de novo e paga de novo enquanto a primeira geração termina no Flow sem ninguém para baixar: um Quality de 11 minutos custa 200 créditos e não entrega nada; na terceira tentativa, 300.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_orquestrador.py`:

```python
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import MAX_TENTATIVAS, Orquestrador
from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.erros import SaldoInsuficiente
from flowpool.nucleo.tipos import EstadoJob, Job, Modelo, Modo, SaudeSessao, Tier
from flowpool.transporte.falso import TransporteFalso


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path / "t.db")
    r.criar_esquema()
    return r


def job_fast(nome="c1", modelo=Modelo.VEO_FAST, produto=""):
    return Job(id=0, modo=Modo.T2V, modelo=modelo, prompt="uma gata",
               destino="clipes", nome=nome, produto=produto)


async def test_job_roda_e_fica_pronto(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await orq.passo()

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO
    assert j.conta_id == cid
    assert Path(j.arquivo).exists()


async def test_saldo_e_reconciliado_com_o_lido(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await orq.passo()

    assert repo.conta(cid).saldo == 30
    assert repo.conta(cid).saldo_confiavel is True


async def test_sem_conta_apta_o_job_volta_pra_fila_com_reavaliar(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 5; repo.salvar_conta(c)  # nao paga um Fast
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 5}), raiz=tmp_path)
    await orq.passo()

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.reavaliar_em is not None
    assert j.tentativas == 0  # esperar reset nao gasta tentativa


async def test_saldo_insuficiente_no_transporte_seca_a_conta_e_devolve_o_job(repo, tmp_path):
    # o banco acha que tem 50, o Flow diz que nao tem
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 0}), raiz=tmp_path)
    await orq.passo()

    assert repo.job(jid).estado is EstadoJob.PENDENTE
    assert repo.conta(cid).saldo == 0


async def test_sessao_morta_marca_conta_expirada(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}, mortas={cid}), raiz=tmp_path)
    await orq.passo()

    assert repo.conta(cid).saude is SaudeSessao.EXPIRADA
    assert repo.job(jid).estado is EstadoJob.PENDENTE


async def test_job_desiste_depois_do_teto_de_tentativas(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 500}, falha_em={cid}), raiz=tmp_path)
    for _ in range(MAX_TENTATIVAS):
        await orq.passo()

    j = repo.job(jid)
    assert j.estado is EstadoJob.FALHOU
    assert j.tentativas == MAX_TENTATIVAS
    assert j.erro


async def test_quality_vai_pra_conta_paga_mesmo_com_free_disponivel(repo, tmp_path):
    free = repo.criar_conta("f@b.com", Tier.FREE, "p/1")
    ultra = repo.criar_conta("u@b.com", Tier.ULTRA, "p/2")
    for cid, saldo in ((free, 50), (ultra, 9000)):
        c = repo.conta(cid); c.saldo = saldo; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast(modelo=Modelo.VEO_QUALITY))

    orq = Orquestrador(repo, TransporteFalso(saldos={free: 50, ultra: 9000}), raiz=tmp_path)
    await orq.passo()

    assert repo.job(jid).conta_id == ultra


async def test_falha_de_rede_estorna_a_reserva(repo, tmp_path):
    # uma falha de rede nao pode reduzir o saldo: o Flow nunca chegou a cobrar.
    # Sem estorno, tres timeouts secam uma conta de 50 e ela some do pool.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    async def rede_caiu(*a, **k):
        raise ConnectionError("rede caiu")

    t = TransporteFalso(saldos={cid: 50})
    t.gerar = rede_caiu
    t.saldo = rede_caiu  # a rede caiu inteira: nem a releitura de saldo salva o dia

    orq = Orquestrador(repo, t, raiz=tmp_path)
    await orq.passo()

    j = repo.job(jid)
    assert repo.conta(cid).saldo == 50    # 50 - 20 reservados + 20 estornados
    assert j.estado is EstadoJob.PENDENTE
    assert j.custo_estimado is None       # estornado, e uma vez so


async def test_passo_sem_job_nao_explode(repo, tmp_path):
    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path)
    assert await orq.passo() is False


async def test_arquivo_cai_no_destino_com_o_nome_pedido(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p",
                             destino="Video Roupa/clipes", nome="c3-take1"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await orq.passo()

    esperado = tmp_path / "Video Roupa" / "clipes" / "c3-take1.mp4"
    assert repo.job(jid).arquivo == str(esperado)
    assert esperado.exists()


async def test_job_de_imagem_sai_com_extensao_png(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="p", destino="imgs", nome="i1"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await orq.passo()

    assert repo.job(jid).arquivo.endswith("i1.png")


async def test_geracao_passa_por_rodando_antes_de_ficar_pronta(repo, tmp_path):
    # o caso normal do Flow: a geracao demora e o motor faz polling ate terminar
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 50}, passos_rodando=3)
    orq = Orquestrador(repo, t, raiz=tmp_path, intervalo_polling=0.001)
    await orq.passo()

    assert repo.job(jid).estado is EstadoJob.PRONTO
    assert len(t.geracoes) == 1


async def test_timeout_devolve_o_job_com_id_remoto_e_nao_gera_de_novo(repo, tmp_path):
    # descartar o id_remoto faria a segunda tentativa pagar de novo enquanto a
    # primeira geracao termina no Flow, orfa, sem ninguem para baixar
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 500}, nunca_termina={cid})
    orq = Orquestrador(repo, t, raiz=tmp_path,
                       timeout_geracao=timedelta(milliseconds=20),
                       intervalo_polling=0.001)
    await orq.passo()

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.id_remoto is not None
    assert j.conta_id == cid
    assert j.tentativas == 1
    primeiro = j.id_remoto

    await orq.passo()

    assert len(t.geracoes) == 1                       # a segunda tentativa nao gerou
    assert repo.job(jid).id_remoto == primeiro
    assert repo.job(jid).tentativas == 2


async def test_sessao_morta_durante_gerar_estorna_a_reserva(repo, tmp_path):
    # SessaoMorta e o primeiro `except`: sem estorno ali o credito reservado some do
    # saldo local para sempre, porque nunca chega no ramo generico
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    async def sem_leitura(*a, **k):
        raise ConnectionError("browser morto")

    t = TransporteFalso(saldos={cid: 50}, morre_ao_gerar={cid})
    t.saldo = sem_leitura   # navegador ja morreu: a reconciliacao nao salva o dia

    orq = Orquestrador(repo, t, raiz=tmp_path)
    await orq.passo()

    assert repo.conta(cid).saldo == 50            # 50 - 20 reservados + 20 estornados
    assert repo.conta(cid).saude is SaudeSessao.EXPIRADA
    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.custo_estimado is None


async def test_recusa_de_saldo_seca_a_conta_com_prazo_e_adia_o_job(repo, tmp_path):
    # o Flow mostra saldo e recusa gerar (cota diaria, throttle). Sem prazo o job
    # volta a fila na hora, para a mesma conta, e o motor vira laco quente.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    async def recusa(*a, **k):
        raise SaldoInsuficiente("cota diaria estourada")

    t = TransporteFalso(saldos={cid: 50})
    t.gerar = recusa

    orq = Orquestrador(repo, t, raiz=tmp_path)
    await orq.passo()

    conta = repo.conta(cid)
    agora = datetime.now(timezone.utc)
    assert conta.seca_ate is not None and conta.seca_ate > agora
    assert conta.saldo == 50                       # estornado e reconciliado
    assert escolher([conta], Modelo.VEO_FAST, agora) is None   # some da escolha

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.reavaliar_em == conta.seca_ate
    assert j.tentativas == 0
    assert repo.reivindicar(agora) is None         # nao volta na proxima iteracao


async def test_recuperar_estorna_job_preso_sem_geracao(repo, tmp_path):
    # crash entre `reservar` e `gerar`: sem recuperacao o job fica `rodando` para
    # sempre e o credito reservado nunca volta
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 30; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    j = repo.job(jid)
    j.estado = EstadoJob.RODANDO; j.conta_id = cid; j.custo_estimado = 20
    repo.salvar_job(j)

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    assert orq.recuperar() == 1

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.custo_estimado is None
    assert j.tentativas == 0                       # nao foi o job que falhou
    assert repo.conta(cid).saldo == 50
    assert [e["tipo"] for e in repo.eventos(jid)] == ["recuperado_sem_gerar"]


async def test_recuperar_preserva_geracao_ja_paga(repo, tmp_path):
    # ha geracao em curso no Flow, ja cobrada: estornar inflaria o saldo e perder o
    # id_remoto faria pagar de novo
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 30; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    j = repo.job(jid)
    j.estado = EstadoJob.RODANDO; j.conta_id = cid
    j.custo_estimado = 20; j.id_remoto = "falso-9"
    repo.salvar_job(j)

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    assert orq.recuperar() == 1

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.id_remoto == "falso-9"
    assert j.conta_id == cid
    assert repo.conta(cid).saldo == 30             # nao estornou
    ev = repo.eventos(jid)[0]
    assert ev["tipo"] == "recuperado_com_geracao"
    assert json.loads(ev["payload"])["id_remoto"] == "falso-9"


async def test_divergencia_acima_do_limiar_vira_evento_com_sinal(repo, tmp_path):
    # e esta medicao que responde se imagem custa credito ou sai de graca
    cid = repo.criar_conta("a@b.com", Tier.PRO, "p/1")
    c = repo.conta(cid); c.saldo = 100; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    async def cobrou_mais(*a, **k):
        return 60          # previsto 80 (100 - 20 da tabela); o Flow cobrou 40

    t = TransporteFalso(saldos={cid: 100})
    t.saldo = cobrou_mais

    orq = Orquestrador(repo, t, raiz=tmp_path)
    await orq.passo()

    div = [e for e in repo.eventos(jid) if e["tipo"] == "divergencia"]
    assert len(div) == 1
    payload = json.loads(div[0]["payload"])
    assert payload["delta"] == -20                 # negativo = faltou credito
    assert payload["lido"] == 60
    assert repo.conta(cid).saldo_confiavel is False


async def test_workers_simultaneos_nao_pegam_o_mesmo_job(repo, tmp_path):
    # dois jobs e tres workers com teto 1: tres geracoes seria pagar por um job duas
    # vezes, com o perdedor sobrescrevendo o arquivo do vencedor
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    j1 = repo.criar_job(job_fast(nome="c1"))
    j2 = repo.criar_job(job_fast(nome="c2"))

    t = TransporteFalso(saldos={cid: 500}, latencia=0.01)
    orq = Orquestrador(repo, t, raiz=tmp_path, max_simultaneos=1)
    await asyncio.gather(orq.passo(), orq.passo(), orq.passo())

    assert len(t.geracoes) == 2
    assert repo.job(j1).estado is EstadoJob.PRONTO
    assert repo.job(j2).estado is EstadoJob.PRONTO


async def test_projeto_e_criado_na_primeira_geracao_e_reusado_na_segunda(repo, tmp_path):
    # sem reuso, cada geracao criaria um projeto novo e a conta viraria um lixao
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    repo.criar_job(job_fast(nome="c1", produto="Panelas Monaco"))
    repo.criar_job(job_fast(nome="c2", produto="Panelas Monaco"))

    t = TransporteFalso(saldos={cid: 500})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await orq.passo()
    await orq.passo()

    assert t.criacoes_projeto == 1
    assert len(repo.projetos(cid)) == 1
    assert repo.projeto_de(cid, "Panelas Monaco").id_remoto == t.abertos[cid]


async def test_mesmo_produto_em_duas_contas_gera_dois_projetos(repo, tmp_path):
    # nao existe "o projeto do Vestido Vermelho": existe um em cada conta que gerou
    g1 = repo.criar_conta("g1@b.com", Tier.FREE, "p/1")
    g2 = repo.criar_conta("g2@b.com", Tier.FREE, "p/2")
    for cid in (g1, g2):
        c = repo.conta(cid); c.saldo = 20; repo.salvar_conta(c)   # cada uma paga 1 Fast
    repo.criar_job(job_fast(nome="c1", produto="Vestido Vermelho"))
    repo.criar_job(job_fast(nome="c2", produto="Vestido Vermelho"))

    t = TransporteFalso(saldos={g1: 20, g2: 20})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await orq.passo()
    await orq.passo()

    assert t.criacoes_projeto == 2
    remotos = {p.conta_id: p.id_remoto for p in repo.projetos()}
    assert set(remotos) == {g1, g2}
    assert remotos[g1] != remotos[g2]


async def test_projeto_que_existe_no_remoto_e_adotado_sem_criar(repo, tmp_path):
    # apagar o flowpool.db ou criar o projeto na mao pelo site nao pode duplicar tudo
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast(produto="Moda"))

    t = TransporteFalso(saldos={cid: 50}, projetos={cid: ["Moda"]})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await orq.passo()

    assert t.criacoes_projeto == 0
    assert repo.projeto_de(cid, "Moda").id_remoto == "proj-1"
    assert "projeto_adotado" in [e["tipo"] for e in repo.eventos(jid)]


async def test_job_sem_produto_nao_mexe_em_projeto(repo, tmp_path):
    # job criado direto pelo repo, sem produto: nao ha projeto a resolver
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await orq.passo()

    assert repo.job(jid).estado is EstadoJob.PRONTO
    assert t.criacoes_projeto == 0
    assert repo.projetos() == []


async def test_modelo_sem_custo_na_escolha_devolve_o_job_e_gasta_tentativa(
        repo, tmp_path, monkeypatch):
    # regressao: com a reivindicacao atomica o job ja esta `rodando` quando `escolher`
    # levanta. Sem tratar aqui ele so seria destravado no proximo boot.
    from flowpool.nucleo import custos

    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    monkeypatch.setattr(custos, "_CUSTO", {})   # nenhum modelo tem custo cadastrado

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    assert await orq.passo() is True

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.tentativas == 1          # erro de configuracao nao reprocessa para sempre
    assert "escolha_falhou" in [e["tipo"] for e in repo.eventos(jid)]
    assert repo.reivindicar(datetime.now(timezone.utc)) is not None   # ja destravado
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_orquestrador.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.motor.orquestrador'`

- [ ] **Step 3: Implementar**

`flowpool/motor/orquestrador.py`:

```python
"""Casa job com conta e conduz o ciclo de vida. Nao conhece Playwright."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flowpool.dados.repo import Repo
from flowpool.nucleo.credito import LIMIAR_DIVERGENCIA, liberar, reconciliar, reservar
from flowpool.nucleo.escolha import apta, escolher
from flowpool.nucleo.tipos import Conta, EstadoJob, Job, Modo, SaudeSessao
from flowpool.transporte.base import (
    Estado, FlowTransport, GeracaoFalhou, Pedido, SaldoInsuficiente, SessaoMorta,
)

MAX_TENTATIVAS = 3
ESPERA_RESET = timedelta(hours=1)
TIMEOUT_GERACAO = timedelta(minutes=10)
INTERVALO_POLLING = 5.0


def _extensao(modo: Modo) -> str:
    return ".png" if modo is Modo.IMAGEM else ".mp4"


class Orquestrador:
    def __init__(self, repo: Repo, transporte: FlowTransport, raiz: Path | str,
                 max_simultaneos: int = 3,
                 timeout_geracao: timedelta = TIMEOUT_GERACAO,
                 intervalo_polling: float = INTERVALO_POLLING) -> None:
        self.repo = repo
        self.transporte = transporte
        self.raiz = Path(raiz)
        self.sem = asyncio.Semaphore(max_simultaneos)
        # parametrizados para o teste nao ter que esperar dez minutos de verdade
        self.timeout_geracao = timeout_geracao
        self.intervalo_polling = intervalo_polling
        self._rodando = False

    def recuperar(self) -> int:
        """Resolve os jobs que um crash deixou presos em `rodando`. Roda no boot.

        `reivindicar` so olha para `pendente`, entao um job travado em `rodando` nunca
        seria retomado nem falharia — e o credito que ele reservou nunca voltaria ao
        saldo. Devolve quantos jobs foram tocados.

        Duas situacoes, e a diferenca e o `id_remoto`:
        - vazio: o Flow nunca aceitou o pedido, entao a reserva volta e o job recomeca
          do zero, sem gastar tentativa (nao foi ele que falhou, foi a maquina).
        - preenchido: existe geracao la fora, ja paga. Estornar inflaria o saldo, e
          perder o `id_remoto` faria pagar de novo — os dois sao preservados.
        """
        presos = self.repo.jobs(EstadoJob.RODANDO)
        for job in presos:
            conta_id = job.conta_id
            conta = self.repo.conta(conta_id) if conta_id is not None else None
            if job.id_remoto is None:
                if conta is not None and job.custo_estimado is not None:
                    liberar(conta, job.custo_estimado)
                    self.repo.salvar_conta(conta)
                job.custo_estimado = None
                job.conta_id = None
                job.estado = EstadoJob.PENDENTE
                self.repo.salvar_job(job)
                self.repo.registrar_evento(job.id, conta_id, "recuperado_sem_gerar")
            else:
                job.estado = EstadoJob.PENDENTE
                self.repo.salvar_job(job)
                self.repo.registrar_evento(
                    job.id, conta_id, "recuperado_com_geracao",
                    json.dumps({"id_remoto": job.id_remoto}))
        return len(presos)

    async def passo(self) -> bool:
        """Puxa um job e leva ate o fim. Devolve False se nao havia job.

        O semaforo e adquirido ANTES de reivindicar. Reivindicar primeiro deixaria o
        job puxado visivel como `pendente` enquanto o worker espera na fila do
        semaforo — e outro worker o pegaria, gerando e pagando duas vezes pelo mesmo
        job, com o perdedor sobrescrevendo o arquivo do vencedor.
        """
        async with self.sem:
            agora = datetime.now(timezone.utc)
            job = self.repo.reivindicar(agora)
            if job is None:
                return False

            try:
                conta = self._conta_para(job, agora)
            except Exception as e:
                # `reivindicar` ja marcou o job `rodando`. Deixar a excecao subir
                # daqui o deixaria travado nesse estado ate o proximo boot — e
                # `ModeloSemCusto`, que e erro de configuracao, e exatamente o caso
                # que chega aqui. Gasta tentativa: reprocessar para sempre um modelo
                # sem custo cadastrado nao conserta o cadastro.
                erro = f"{type(e).__name__}: {e}"
                self.repo.registrar_evento(job.id, None, "escolha_falhou",
                                           json.dumps({"erro": erro}))
                self._devolver(job, erro, gasta_tentativa=True)
                return True

            if conta is None:
                job.estado = EstadoJob.PENDENTE
                job.reavaliar_em = agora + ESPERA_RESET
                self.repo.salvar_job(job)
                self.repo.registrar_evento(job.id, None, "sem_conta_apta")
                return True

            await self._executar(job, conta)
        return True

    def _conta_para(self, job: Job, agora: datetime) -> Conta | None:
        """Conta em que este job deve rodar.

        Job que volta a fila com `id_remoto` tem geracao paga em curso no Flow, e ela
        so pode ser retomada de dentro da conta que a criou — por isso aquela conta
        tem preferencia. Se ela nao servir mais, a escolha normal vale e o job gera do
        zero.
        """
        contas = self.repo.contas()
        if job.id_remoto is not None and job.conta_id is not None:
            anterior = next((c for c in contas if c.id == job.conta_id), None)
            if anterior is not None and apta(anterior, job.modelo, agora):
                return anterior
        return escolher(contas, job.modelo, agora)

    async def _executar(self, job: Job, conta: Conta) -> None:
        # retomada: a geracao ja existe e e da mesma conta. Gerar de novo pagaria duas
        # vezes e deixaria a primeira orfa, terminando no Flow sem ninguem para baixar.
        retomando = job.id_remoto is not None and job.conta_id == conta.id
        job.estado = EstadoJob.RODANDO
        job.conta_id = conta.id
        self.repo.salvar_job(job)
        self.repo.registrar_evento(
            job.id, conta.id, "despachado",
            json.dumps({"modelo": job.modelo.value, "retomada": retomando}))

        sessao = None
        try:
            sessao = await self.transporte.abrir_sessao(conta)
            await self._resolver_projeto(sessao, job, conta)
            if not retomando:
                # a conta mudou: a geracao anterior nao serve mais, comeca do zero
                job.id_remoto = None
                # guardar o reservado no job e o que permite estornar depois —
                # inclusive depois de um restart, por isso vai para o banco na hora
                job.custo_estimado = reservar(conta, job.modelo)
                self.repo.salvar_conta(conta)
                self.repo.salvar_job(job)

                job.id_remoto = await self.transporte.gerar(sessao, self._pedido(job))
                self.repo.salvar_job(job)

            estado = await self._esperar(sessao, job.id_remoto)
            if estado is not Estado.PRONTO:
                raise GeracaoFalhou(f"Flow devolveu {estado.value}")

            destino = self.raiz / job.destino / f"{job.nome}{_extensao(job.modo)}"
            arquivo = await self.transporte.baixar(sessao, job.id_remoto, destino)

            job.arquivo = str(arquivo)
            job.estado = EstadoJob.PRONTO
            job.erro = None
            self.repo.salvar_job(job)
            self.repo.registrar_evento(job.id, conta.id, "pronto")

        except SessaoMorta as e:
            # A sessao nao morre so em `abrir_sessao`: no transporte real ela cai no
            # meio de `gerar`/`consultar`/`baixar`. Como este e o primeiro `except`,
            # sem estornar aqui o credito reservado sumiria do saldo local para
            # sempre. `_estornar` e no-op depois que o Flow aceitou, entao nao infla.
            if self._estornar(job, conta):
                self.repo.salvar_conta(conta)
            conta.saude = SaudeSessao.EXPIRADA
            # escrita de campo especifico: `salvar_conta` grava a linha inteira a
            # partir de uma copia velha, e outro worker ressuscitaria a sessao
            self.repo.marcar_sessao(conta.id, SaudeSessao.EXPIRADA)
            self._evento_de_falha(job, conta, "sessao_morta")
            self._devolver(job, str(e), gasta_tentativa=False)

        except SaldoInsuficiente as e:
            # Vem de duas fontes: o guarda local em `reservar` (a conta tem credito
            # real, so nao o bastante para este modelo) ou o proprio Flow. Estornar e
            # deixar a reconciliacao do `finally` adotar o numero verdadeiro — zerar o
            # saldo na mao apagaria credito que existe e tiraria a conta do pool.
            #
            # Marcar a conta seca COM PRAZO e o que impede o laco quente: se o Flow
            # mostra saldo mas recusa gerar (cota diaria, throttle), sem prazo o job
            # volta a fila na mesma hora, para a mesma conta, abrindo e fechando um
            # Chrome por iteracao, sem gastar tentativa e sem nunca desistir.
            self._estornar(job, conta)
            conta.seca_ate = datetime.now(timezone.utc) + ESPERA_RESET
            conta.saldo_confiavel = False
            self.repo.salvar_conta(conta)
            job.reavaliar_em = conta.seca_ate
            self._evento_de_falha(job, conta, "conta_seca")
            self._devolver(job, str(e), gasta_tentativa=False)

        except Exception as e:  # falha de rede, timeout, geracao errada
            # `_estornar` decide sozinho se devolve: so devolve enquanto o Flow ainda
            # nao aceitou o pedido. Falha de download ou geracao recusada acontece
            # depois da cobranca, e ali estornar inflaria o saldo.
            self._estornar(job, conta)
            conta.falhas += 1
            self.repo.salvar_conta(conta)
            self._evento_de_falha(job, conta, "falhou", {"erro": str(e)})
            self._devolver(job, f"{type(e).__name__}: {e}", gasta_tentativa=True)

        finally:
            if sessao is not None:
                try:
                    lido = await self.transporte.saldo(sessao)
                    delta = reconciliar(conta, lido, datetime.now(timezone.utc))
                    conta.falhas = 0 if job.estado is EstadoJob.PRONTO else conta.falhas
                    self.repo.salvar_conta(conta)
                    if abs(delta) >= LIMIAR_DIVERGENCIA:
                        # a medicao que responde se imagem custa credito ou sai de
                        # graca. Calcular o delta com sinal e joga-lo fora apagava o
                        # unico dado real que o projeto tem sobre custo.
                        self.repo.registrar_evento(
                            job.id, conta.id, "divergencia",
                            json.dumps({"delta": delta, "lido": lido}))
                except Exception as e:
                    # o `except: pass` daqui engolia tambem falha de `salvar_conta` —
                    # o silencio que transformava conta seca em laco infinito
                    self.repo.registrar_evento(job.id, conta.id, "reconciliacao_falhou",
                                               json.dumps({"erro": str(e)}))
                try:
                    # fechar um browser ja morto levanta, e isso escapava do `try` de
                    # cima: a task de `rodar()` terminava e a fila parava em silencio
                    await self.transporte.fechar(sessao)
                except Exception as e:
                    self.repo.registrar_evento(job.id, conta.id, "fechar_falhou",
                                               json.dumps({"erro": str(e)}))

    async def _resolver_projeto(self, sessao, job: Job, conta: Conta) -> None:
        """Deixa a sessao dentro do projeto certo antes de gerar.

        Toda geracao do Flow acontece dentro de um projeto, e o projeto tem id na URL.
        A politica e um projeto por produto EM CADA CONTA — projeto pertence a uma
        conta so, entao nao existe "o projeto do Vestido Vermelho", existe um em cada
        conta que gerou algo dele.

        Ordem: banco local, depois lista remota (adocao), depois criacao. O passo do
        meio existe porque sem ele apagar o `flowpool.db` ou criar um projeto na mao
        pelo site faria o app duplicar tudo, e projeto duplicado no Flow e bagunca que
        ninguem limpa depois.

        Job sem `produto` (criado direto pelo repo) nao tem projeto a resolver: criar
        um chamado "" seria pior do que nao criar nada.
        """
        if not job.produto:
            return

        conhecido = self.repo.projeto_de(conta.id, job.produto)
        if conhecido is not None:
            await self.transporte.abrir_projeto(sessao, conhecido.id_remoto)
            return

        remotos = await self.transporte.listar_projetos(sessao)
        achado = next((p for p in remotos if p.nome == job.produto), None)
        if achado is not None:
            id_remoto, nome, tipo = achado.id_remoto, achado.nome, "projeto_adotado"
        else:
            id_remoto = await self.transporte.criar_projeto(sessao, job.produto)
            nome, tipo = job.produto, "projeto_criado"

        self.repo.registrar_projeto(conta.id, job.produto, id_remoto, nome)
        self.repo.registrar_evento(
            job.id, conta.id, tipo,
            json.dumps({"produto": job.produto, "id_remoto": id_remoto}))
        await self.transporte.abrir_projeto(sessao, id_remoto)

    def _evento_de_falha(self, job: Job, conta: Conta, tipo: str,
                         extra: dict | None = None) -> None:
        """Registra a falha ANTES de `_devolver`, com o que a auditoria precisa.

        Depois de `_devolver` o job ja mudou de estado e pode ter perdido o vinculo
        com a conta. O `id_remoto` no evento e o que permite achar geracao orfa no
        Flow, e a tentativa e o que mostra quantas vezes aquele job ja custou credito.
        """
        payload = {"id_remoto": job.id_remoto, "tentativa": job.tentativas}
        if extra:
            payload.update(extra)
        self.repo.registrar_evento(job.id, conta.id, tipo, json.dumps(payload))

    def _estornar(self, job: Job, conta: Conta) -> bool:
        """Devolve ao saldo a reserva de um job que nao chegou a virar geracao.

        So estorna enquanto `id_remoto is None` — ou seja, enquanto o Flow ainda nao
        aceitou o pedido. Depois disso o credito provavelmente JA foi cobrado, e
        devolver inflaria o saldo local. Nao da para contar com a reconciliacao do
        `finally` para consertar: a falha que trouxe o codigo ate aqui costuma ser de
        rede, e a leitura de saldo falha junto. O saldo inflado sobreviveria, a conta
        seria escolhida de novo, o Flow recusaria, e o ciclo se repetiria.

        Idempotente: zera `custo_estimado` para nao estornar duas vezes. Quem chama
        salva o job logo em seguida (`_devolver`), entao o zero chega ao banco.

        Devolve True quando credito voltou de fato ao saldo em memoria — quem chamou
        usa isso para saber se ainda precisa persistir a conta.
        """
        if job.custo_estimado is None:
            return False
        devolveu = job.id_remoto is None
        if devolveu:
            liberar(conta, job.custo_estimado)
        job.custo_estimado = None
        return devolveu

    def _devolver(self, job: Job, erro: str, gasta_tentativa: bool) -> None:
        """Devolve o job a fila.

        Com `id_remoto` preenchido, `id_remoto` e `conta_id` sao PRESERVADOS: existe
        geracao paga em curso no Flow, e a proxima tentativa retoma aquela em vez de
        gerar (e pagar) de novo deixando a primeira orfa. Um Quality de 11 minutos
        custa 200 creditos e nao entrega nada; na terceira tentativa, 300.
        """
        job.erro = erro
        if gasta_tentativa:
            job.tentativas += 1
        if job.tentativas >= MAX_TENTATIVAS:
            job.estado = EstadoJob.FALHOU
        else:
            job.estado = EstadoJob.PENDENTE
            if job.id_remoto is None:
                job.conta_id = None
        self.repo.salvar_job(job)

    async def _esperar(self, sessao, id_remoto: str) -> Estado:
        limite = datetime.now(timezone.utc) + self.timeout_geracao
        while datetime.now(timezone.utc) < limite:
            estado = await self.transporte.consultar(sessao, id_remoto)
            if estado is not Estado.RODANDO:
                return estado
            await asyncio.sleep(self.intervalo_polling)
        raise TimeoutError(f"geracao {id_remoto} passou de {self.timeout_geracao}")

    @staticmethod
    def _pedido(job: Job) -> Pedido:
        return Pedido(modo=job.modo, modelo=job.modelo, prompt=job.prompt,
                      referencias=job.referencias, frame_inicial=job.frame_inicial,
                      frame_final=job.frame_final)

    async def rodar(self, intervalo: float = 2.0) -> None:
        """Laco eterno, usado pela API. Para com `parar()`.

        Tudo que escapa de `passo()` vira evento e o laco continua. Sem isso a task
        morre e ninguem observa: a API segue respondendo HTTP normalmente enquanto a
        fila nunca mais anda. Tres caminhos reais chegavam aqui — `fechar()` de um
        browser morto, `ModeloSemCusto` vindo de `escolher`, e falha de escrita no
        banco.
        """
        self._rodando = True
        while self._rodando:
            try:
                if not await self.passo():
                    await asyncio.sleep(intervalo)
            except Exception as e:
                self.repo.registrar_evento(
                    None, None, "passo_falhou",
                    json.dumps({"erro": f"{type(e).__name__}: {e}"}))
                await asyncio.sleep(intervalo)

    def parar(self) -> None:
        self._rodando = False
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_orquestrador.py -q`
Expected: `24 passed`

- [ ] **Step 5: Rodar a suíte inteira**

Run: `.venv/Scripts/pytest -q`
Expected: `119 passed` (8 tipos + 17 custos + 16 escolha + 15 credito + 3 pool + 22 repo + 14 transporte + 24 orquestrador)

- [ ] **Step 6: Commit**

```bash
git add flowpool/motor/orquestrador.py tests/test_orquestrador.py
git commit -m "feat: orquestrador de fila com troca de conta e retry"
```

---

### Task 8: API HTTP

**Files:**
- Create: `flowpool/api/esquemas.py`, `flowpool/api/app.py`
- Test: `tests/test_api.py`

`PedidoJob.produto` é **obrigatório** e `destino` passa a ser opcional: um validador preenche `destino = f"{produto}/clipes"` quando o pedido vem sem ele. Motivo: na pasta do usuário cada produto já é uma pasta (`Video Roupa`, `Panelas Monaco`, `Furadeira Pnoeoce`), então mandar `produto: "Panelas Monaco"` deve bastar e repetir o caminho é ruído. `destino` explícito continua aceito, para quando o arquivo deve cair em outro lugar.

`produto` é obrigatório e não opcional porque é ele que diz **em qual projeto do Flow** a geração acontece (Task 7). Um pedido sem produto não teria projeto a resolver, e cada geração abriria um projeto novo.

Duas rotas novas:

| método | rota | o que faz |
|---|---|---|
| `GET` | `/projetos` | lista os vínculos, com filtro opcional por `conta_id` e por `produto` |
| `DELETE` | `/projetos/{id}` | esquece o vínculo **local**; não apaga nada no Flow |

O `DELETE` diz isso na docstring da rota **e na resposta** (`aviso`), porque a leitura natural de um DELETE é a oposta. O projeto continua no Flow com tudo que já foi gerado dentro dele, e na próxima geração daquele produto naquela conta ele volta a ser adotado pelo nome.

O lifespan chama `orq.recuperar()` **antes** de `rodar()`. É o único ponto do sistema que roda uma vez por boot, e é onde um job deixado em `rodando` por um crash anterior — com crédito reservado que nunca voltou — precisa ser resolvido antes de o motor puxar qualquer coisa nova.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from flowpool.api.app import criar_app
from flowpool.dados.repo import Repo
from flowpool.nucleo.tipos import Tier
from flowpool.transporte.falso import TransporteFalso


@pytest.fixture
def cliente(tmp_path):
    repo = Repo(tmp_path / "t.db")
    repo.criar_esquema()
    app = criar_app(repo=repo, transporte=TransporteFalso(saldos={}), raiz=tmp_path)
    return TestClient(app), repo


def test_criar_job_devolve_id_e_estado_pendente(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "uma gata",
                              "produto": "Video Roupa", "nome": "c1"})
    assert r.status_code == 201
    assert r.json()["estado"] == "pendente"
    assert isinstance(r.json()["id"], int)


def test_consultar_job_inexistente_da_404(cliente):
    c, _ = cliente
    assert c.get("/jobs/999").status_code == 404


def test_lote_cria_varios_jobs(cliente):
    c, _ = cliente
    corpo = {"jobs": [
        {"modo": "t2v", "modelo": "veo-fast", "prompt": "a", "produto": "Moda", "nome": "c1"},
        {"modo": "t2v", "modelo": "veo-lite", "prompt": "b", "produto": "Moda", "nome": "c2"},
    ]}
    r = c.post("/jobs/lote", json=corpo)
    assert r.status_code == 201
    assert len(r.json()["ids"]) == 2


def test_ingredients2v_recusa_mais_de_tres_referencias(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "ingredients2v", "modelo": "veo-fast",
                              "prompt": "p", "produto": "Moda", "nome": "n",
                              "referencias": ["1.png", "2.png", "3.png", "4.png"]})
    assert r.status_code == 422


def test_frames2v_exige_frame_inicial(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "frames2v", "modelo": "veo-fast", "prompt": "p",
                              "produto": "Moda", "nome": "n"})
    assert r.status_code == 422


def test_produto_e_obrigatorio(cliente):
    # sem produto nao ha projeto a resolver: cada geracao criaria um projeto novo
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                              "destino": "d", "nome": "n"})
    assert r.status_code == 422


def test_destino_deriva_do_produto_quando_ausente(cliente):
    # na pasta do usuario cada produto ja e uma pasta; repetir o caminho e ruido
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "produto": "Panelas Monaco", "nome": "n"}).json()["id"]
    j = repo.job(jid)
    assert j.produto == "Panelas Monaco"
    assert j.destino == "Panelas Monaco/clipes"


def test_destino_explicito_e_respeitado(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "produto": "Panelas Monaco", "nome": "n",
                                "destino": "Benchmark Roupa/brutos"}).json()["id"]
    assert repo.job(jid).destino == "Benchmark Roupa/brutos"


def test_cancelar_job_pendente(cliente):
    c, _ = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
    assert c.delete(f"/jobs/{jid}").status_code == 200
    assert c.get(f"/jobs/{jid}").json()["estado"] == "cancelado"


def test_listar_contas_vazio(cliente):
    c, _ = cliente
    assert c.get("/contas").json() == []


def test_patch_conta_desativa(cliente):
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    r = c.patch(f"/contas/{cid}", json={"ativa": False})
    assert r.status_code == 200
    assert repo.conta(cid).ativa is False


def test_saude_resume_o_pool(cliente):
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    conta = repo.conta(cid); conta.saldo = 40; repo.salvar_conta(conta)
    c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                          "produto": "Moda", "nome": "n"})
    s = c.get("/saude").json()
    assert s["contas_vivas"] == 1
    assert s["credito_total"] == 40
    assert s["fila_pendente"] == 1


def test_listar_projetos_e_filtrar(cliente):
    c, repo = cliente
    g1 = repo.criar_conta("g1@b.com", Tier.FREE, "p/1")
    g2 = repo.criar_conta("g2@b.com", Tier.FREE, "p/2")
    repo.registrar_projeto(g1, "Moda", "rem-1", "Moda")
    repo.registrar_projeto(g1, "Vestir", "rem-2", "Vestir")
    repo.registrar_projeto(g2, "Moda", "rem-3", "Moda")

    assert len(c.get("/projetos").json()) == 3
    assert len(c.get("/projetos", params={"conta_id": g1}).json()) == 2
    por_produto = c.get("/projetos", params={"produto": "Moda"}).json()
    assert {p["id_remoto"] for p in por_produto} == {"rem-1", "rem-3"}


def test_esquecer_projeto_avisa_que_o_flow_nao_e_tocado(cliente):
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    pid = repo.registrar_projeto(cid, "Moda", "rem-1", "Moda")

    r = c.delete(f"/projetos/{pid}")
    assert r.status_code == 200
    assert "Flow" in r.json()["aviso"]
    assert repo.projeto_de(cid, "Moda") is None


def test_esquecer_projeto_inexistente_da_404(cliente):
    c, _ = cliente
    assert c.delete("/projetos/999").status_code == 404
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.api.app'`

- [ ] **Step 3: Implementar os esquemas**

`flowpool/api/esquemas.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from flowpool.nucleo.tipos import EstadoJob, Modelo, Modo, SaudeSessao, Tier


class PedidoJob(BaseModel):
    modo: Modo
    modelo: Modelo
    prompt: str
    # Nome da pasta do produto e, ao mesmo tempo, nome do projeto no Flow. Obrigatorio:
    # e ele que diz em qual projeto a geracao acontece.
    produto: str = Field(min_length=1)
    # Opcional porque na pasta do usuario cada produto ja e uma pasta. Continua aceito
    # explicito para quando o arquivo deve cair em outro lugar.
    destino: str | None = None
    nome: str
    referencias: list[str] = Field(default_factory=list, max_length=3)
    frame_inicial: str | None = None
    frame_final: str | None = None
    prioridade: int = 0

    @model_validator(mode="after")
    def destino_deriva_do_produto(self) -> PedidoJob:
        """`produto: "Panelas Monaco"` basta; repetir o caminho e ruido."""
        if not self.destino:
            self.destino = f"{self.produto}/clipes"
        return self

    @model_validator(mode="after")
    def coerente_com_o_modo(self) -> PedidoJob:
        if self.modo is Modo.FRAMES2V and not self.frame_inicial:
            raise ValueError("frames2v exige frame_inicial")
        if self.modo is Modo.INGREDIENTS2V and not self.referencias:
            raise ValueError("ingredients2v exige ao menos uma referencia")
        return self


class PedidoLote(BaseModel):
    jobs: list[PedidoJob]


class RespostaJob(BaseModel):
    id: int
    estado: EstadoJob
    modo: Modo
    modelo: Modelo
    nome: str
    produto: str = ""
    conta_id: int | None = None
    arquivo: str | None = None
    tentativas: int = 0
    erro: str | None = None


class RespostaProjeto(BaseModel):
    id: int
    conta_id: int
    produto: str
    id_remoto: str
    nome_remoto: str


class RespostaConta(BaseModel):
    id: int
    email: str
    tier: Tier
    saldo: int
    saldo_confiavel: bool
    saude: SaudeSessao
    ativa: bool
    falhas: int


class PatchConta(BaseModel):
    ativa: bool | None = None
    tier: Tier | None = None


class NovaConta(BaseModel):
    email: str
    tier: Tier = Tier.FREE


class Saude(BaseModel):
    contas_vivas: int
    credito_total: int
    fila_pendente: int
    contas_expiradas: list[str]
```

- [ ] **Step 4: Implementar as rotas**

`flowpool/api/app.py`:

```python
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException

from flowpool.api.esquemas import (
    NovaConta, PatchConta, PedidoJob, PedidoLote, RespostaConta, RespostaJob,
    RespostaProjeto, Saude,
)
from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import Orquestrador
from flowpool.nucleo.tipos import EstadoJob, Job, SaudeSessao
from flowpool.transporte.base import FlowTransport


def _job_de(p: PedidoJob) -> Job:
    # `destino` nunca chega None aqui: o validador de `PedidoJob` ja o derivou do
    # produto quando o pedido veio sem ele.
    return Job(id=0, modo=p.modo, modelo=p.modelo, prompt=p.prompt, destino=p.destino,
               nome=p.nome, produto=p.produto, referencias=p.referencias,
               frame_inicial=p.frame_inicial, frame_final=p.frame_final,
               prioridade=p.prioridade)


def _resposta(j: Job) -> RespostaJob:
    return RespostaJob(id=j.id, estado=j.estado, modo=j.modo, modelo=j.modelo,
                       nome=j.nome, produto=j.produto, conta_id=j.conta_id,
                       arquivo=j.arquivo, tentativas=j.tentativas, erro=j.erro)


def criar_app(repo: Repo, transporte: FlowTransport, raiz: Path | str,
              rodar_motor: bool = False) -> FastAPI:
    orq = Orquestrador(repo, transporte, raiz)

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        # antes de qualquer coisa: um crash anterior pode ter deixado job preso em
        # `rodando` com credito reservado que nunca voltou
        orq.recuperar()
        tarefa = asyncio.create_task(orq.rodar()) if rodar_motor else None
        yield
        if tarefa:
            orq.parar()
            tarefa.cancel()

    app = FastAPI(title="FlowPool", lifespan=ciclo)
    app.state.repo = repo
    app.state.orq = orq

    @app.post("/jobs", response_model=RespostaJob, status_code=201)
    def criar_job(p: PedidoJob) -> RespostaJob:
        return _resposta(repo.job(repo.criar_job(_job_de(p))))

    @app.post("/jobs/lote", status_code=201)
    def criar_lote(p: PedidoLote) -> dict:
        return {"ids": [repo.criar_job(_job_de(j)) for j in p.jobs]}

    @app.get("/jobs/{id_}", response_model=RespostaJob)
    def ver_job(id_: int) -> RespostaJob:
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        return _resposta(j)

    @app.get("/jobs", response_model=list[RespostaJob])
    def listar_jobs(estado: EstadoJob | None = None) -> list[RespostaJob]:
        return [_resposta(j) for j in repo.jobs(estado)]

    @app.delete("/jobs/{id_}", response_model=RespostaJob)
    def cancelar(id_: int) -> RespostaJob:
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        if j.estado is not EstadoJob.PENDENTE:
            raise HTTPException(409, f"job esta {j.estado.value}, nao da pra cancelar")
        j.estado = EstadoJob.CANCELADO
        repo.salvar_job(j)
        return _resposta(j)

    @app.get("/projetos", response_model=list[RespostaProjeto])
    def listar_projetos(conta_id: int | None = None,
                        produto: str | None = None) -> list[RespostaProjeto]:
        """Projetos do Flow ja vinculados a cada conta.

        Um produto aparece uma vez por conta que gerou algo dele: projeto pertence a
        uma conta so, e a `g2` nao abre o projeto da `g1`.
        """
        achados = repo.projetos(conta_id)
        if produto is not None:
            achados = [p for p in achados if p.produto == produto]
        return [RespostaProjeto(id=p.id, conta_id=p.conta_id, produto=p.produto,
                                id_remoto=p.id_remoto, nome_remoto=p.nome_remoto)
                for p in achados]

    @app.delete("/projetos/{id_}")
    def esquecer_projeto(id_: int) -> dict:
        """Esquece o vinculo LOCAL. NAO apaga o projeto no Flow.

        O projeto continua la, com tudo que ja foi gerado dentro dele. Na proxima
        geracao daquele produto naquela conta ele volta a ser adotado pelo nome.
        """
        alvo = next((p for p in repo.projetos() if p.id == id_), None)
        if alvo is None:
            raise HTTPException(404, "projeto nao existe")
        repo.esquecer_projeto(id_)
        return {"id": id_, "esquecido": True,
                "aviso": "o vinculo local foi apagado; o projeto continua existindo "
                         "no Flow e sera readotado pelo nome na proxima geracao"}

    @app.get("/contas", response_model=list[RespostaConta])
    def listar_contas() -> list[RespostaConta]:
        return [RespostaConta(id=c.id, email=c.email, tier=c.tier, saldo=c.saldo,
                              saldo_confiavel=c.saldo_confiavel, saude=c.saude,
                              ativa=c.ativa, falhas=c.falhas) for c in repo.contas()]

    @app.patch("/contas/{id_}", response_model=RespostaConta)
    def alterar_conta(id_: int, p: PatchConta) -> RespostaConta:
        c = repo.conta(id_)
        if c is None:
            raise HTTPException(404, "conta nao existe")
        if p.ativa is not None:
            c.ativa = p.ativa
        if p.tier is not None:
            c.tier = p.tier
        repo.salvar_conta(c)
        return RespostaConta(id=c.id, email=c.email, tier=c.tier, saldo=c.saldo,
                             saldo_confiavel=c.saldo_confiavel, saude=c.saude,
                             ativa=c.ativa, falhas=c.falhas)

    @app.get("/saude", response_model=Saude)
    def saude() -> Saude:
        contas = repo.contas()
        vivas = [c for c in contas if c.ativa and c.saude is SaudeSessao.VIVA]
        return Saude(
            contas_vivas=len(vivas),
            credito_total=sum(c.saldo for c in vivas),
            fila_pendente=len(repo.jobs(EstadoJob.PENDENTE)),
            contas_expiradas=[c.email for c in contas if c.saude is SaudeSessao.EXPIRADA],
        )

    return app
```

A rota `POST /contas` fica de fora deste task porque ela precisa abrir navegador — entra no Task 10, junto com a sessão real.

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_api.py -q`
Expected: `15 passed`

- [ ] **Step 6: Commit**

```bash
git add flowpool/api tests/test_api.py
git commit -m "feat: api http de jobs e contas"
```

---

### Task 9: Mapear os seletores do Flow

**Files:**
- Create: `flowpool/transporte/pw/seletores.py`, `flowpool/transporte/pw/mapear.py`

Este task é **manual e assistido** — não tem teste automatizado porque depende do site do Google. O produto dele é um arquivo de seletores preenchido com valores reais. Todos os seletores moram aqui e em nenhum outro lugar: quando o Google mexer no layout, só este arquivo muda.

Observação de campo (20/08): a app vive em `labs.google/fx/tools/flow/[id]` — o `[id]` é o **projeto**, e toda geração acontece dentro de um. Por isso o mapeamento também precisa cobrir a tela de projetos: lista, botão de novo projeto e campo de nome. Sem esses três, o transporte real não consegue cumprir o `listar_projetos`/`criar_projeto`/`abrir_projeto` que o motor já chama (Tasks 6 e 7), e cada geração abriria um projeto novo.

- [ ] **Step 1: Instalar o Chromium do Playwright e confirmar o Chrome real**

```bash
.venv/Scripts/playwright install-deps
```

O Chrome de verdade já está instalado na máquina (é o navegador do Davi). Confirme o caminho:

```bash
ls "/c/Program Files/Google/Chrome/Application/chrome.exe"
```

Expected: o arquivo existe. Se não existir, procure em `/c/Program Files (x86)/Google/Chrome/Application/`.

- [ ] **Step 2: Escrever o script de mapeamento**

`flowpool/transporte/pw/mapear.py`:

```python
"""Abre o Flow com Chrome real e um perfil dedicado, para login manual e inspecao.

Uso:
    python -m flowpool.transporte.pw.mapear perfis/mapeamento

Loga na mao, monta uma geracao na tela, e usa o inspector do Playwright para pegar
os seletores. Cole os valores encontrados em `seletores.py`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

FLOW = "https://labs.google/fx/tools/flow"


async def main(perfil: str) -> None:
    Path(perfil).mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=perfil,
            channel="chrome",      # o Chromium empacotado e recusado pelo auth do Google
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
        )
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(FLOW)
        print("Loge na mao. Quando terminar, deixe a janela aberta e volte aqui.")
        print("Para inspecionar: rode com PWDEBUG=1 para abrir o Inspector.")
        await pagina.pause()   # abre o Playwright Inspector
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "perfis/mapeamento"))
```

- [ ] **Step 3: Rodar e logar**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.mapear flowpool/perfis/mapeamento
```

Faça o login manual numa conta **grátis** (não a principal). Confirme que o Flow carrega o projeto.

- [ ] **Step 4: Coletar os seletores, um a um**

Com o Inspector aberto, use o modo *Pick locator* e anote o localizador de cada item abaixo. Anote também o texto exato dos rótulos, porque a interface pode estar em português:

1. Indicador de créditos no cabeçalho
2. Campo de prompt
3. Seletor de modelo (o dropdown com Veo / Nano Banana) e a opção de cada modelo
4. Botão de gerar
5. Botão/área de upload de imagem de referência (ingredients)
6. Botão/área de upload de primeiro frame e de último frame
7. O card de resultado enquanto está gerando (spinner) e quando termina
8. Botão de download do resultado
9. O aviso de "créditos insuficientes"
10. A tela de login (para detectar sessão caída)
11. A lista de projetos na tela inicial, e o card/link de **um** projeto dentro dela (o localizador precisa dar tanto o nome visível quanto o id/href)
12. O botão de **novo projeto**
13. O campo de **nome do projeto** e o botão que confirma a criação
14. O padrão da URL com o projeto aberto — anote uma URL real e confira onde o id cai

Nos itens 11 a 13, anote também **de onde sai o id do projeto**: do `href` do card, da URL após abrir, ou de um atributo. É esse id que vai para `projeto.id_remoto` no banco, e um id errado faz o app abrir o projeto de outro produto — ou, pior, tentar abrir um que a conta da vez nem enxerga.

- [ ] **Step 5: Preencher o arquivo de seletores**

`flowpool/transporte/pw/seletores.py` — substitua cada `...` pelo localizador real coletado no passo anterior. Não invente: use o que o Inspector mostrou.

```python
"""Todos os seletores da UI do Flow. Quando o Google mexer no layout, so este arquivo muda.

Preenchido por inspecao manual em <DATA DO MAPEAMENTO>. Cada constante e um seletor
do Playwright (CSS, texto ou role).
"""

URL_FLOW = "https://labs.google/fx/tools/flow"
URL_PROJETO = "https://labs.google/fx/tools/flow/project"
# A app vive dentro de um projeto: `labs.google/fx/tools/flow/[id]`. Formate com o
# id_remoto guardado no banco para abrir o projeto de um produto.
URL_PROJETO_ABERTO = "https://labs.google/fx/tools/flow/{id_remoto}"

# --- sessao ---
LOGIN_VISIVEL = "..."          # algo que so aparece deslogado, ex: botao "Sign in"
CREDITOS = "..."               # elemento com o numero de creditos

# --- prompt e modelo ---
CAMPO_PROMPT = "..."
SELETOR_MODELO = "..."
OPCAO_MODELO = {
    "veo-lite": "...",
    "veo-fast": "...",
    "veo-quality": "...",
    "nano-banana": "...",
}
BOTAO_GERAR = "..."

# --- uploads ---
UPLOAD_REFERENCIA = "..."      # input[type=file] dos ingredients
UPLOAD_FRAME_INICIAL = "..."
UPLOAD_FRAME_FINAL = "..."

# --- resultado ---
CARD_GERANDO = "..."
CARD_PRONTO = "..."
BOTAO_DOWNLOAD = "..."

# --- projetos ---
# Toda geracao acontece DENTRO de um projeto. Sem estes cinco, cada geracao criaria
# um projeto novo e a conta viraria um lixao de projetos orfaos.
LISTA_PROJETOS = "..."         # container da lista na tela inicial
CARD_PROJETO = "..."           # um item da lista; dele saem o nome e o id/href
BOTAO_NOVO_PROJETO = "..."
CAMPO_NOME_PROJETO = "..."
BOTAO_CRIAR_PROJETO = "..."    # o que confirma a criacao depois do nome

# --- erros ---
AVISO_SEM_CREDITO = "..."
```

Os cinco de projeto ficam `"..."` como os outros: só a sessão de mapeamento os preenche. O que **não** pode ficar em aberto é de onde sai o id — anote isso no comentário de `CARD_PROJETO` junto com o localizador.

- [ ] **Step 6: Commit**

```bash
git add flowpool/transporte/pw/seletores.py flowpool/transporte/pw/mapear.py
git commit -m "feat: mapa de seletores da ui do flow"
```

---

### Task 10: Sessão real e cadastro de conta

**Files:**
- Create: `flowpool/transporte/pw/sessao.py`
- Modify: `flowpool/api/app.py` (adicionar `POST /contas` e `POST /contas/{id}/revalidar`)
- Test: `tests/test_sessao.py`

Só o que dá para testar sem rede é testado: a montagem do caminho de perfil e a recusa quando o Chrome não existe.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_sessao.py`:

```python
import pytest

from flowpool.transporte.pw.sessao import ChromeAusente, caminho_perfil, checar_chrome


def test_caminho_de_perfil_e_derivado_do_email(tmp_path):
    p = caminho_perfil(tmp_path, "davi.teste+3@gmail.com")
    assert p.parent == tmp_path
    assert "@" not in p.name and "+" not in p.name
    assert p.name.startswith("davi")


def test_emails_diferentes_dao_perfis_diferentes(tmp_path):
    a = caminho_perfil(tmp_path, "a@gmail.com")
    b = caminho_perfil(tmp_path, "b@gmail.com")
    assert a != b


def test_mesmo_email_da_sempre_o_mesmo_perfil(tmp_path):
    assert caminho_perfil(tmp_path, "a@b.com") == caminho_perfil(tmp_path, "a@b.com")


def test_checar_chrome_levanta_quando_nao_acha(monkeypatch):
    monkeypatch.setattr("flowpool.transporte.pw.sessao._CANDIDATOS", [])
    with pytest.raises(ChromeAusente, match="Chrome"):
        checar_chrome()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_sessao.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.transporte.pw.sessao'`

- [ ] **Step 3: Implementar**

`flowpool/transporte/pw/sessao.py`:

```python
"""Abrir Chrome real com o perfil de uma conta. Login e sempre manual."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright

from flowpool.transporte.pw import seletores as sel

_CANDIDATOS = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


class ChromeAusente(RuntimeError):
    """O Google recusa o Chromium empacotado do Playwright; precisa do Chrome instalado."""


def checar_chrome() -> Path:
    for p in _CANDIDATOS:
        if p.exists():
            return p
    raise ChromeAusente(
        "Chrome nao encontrado. O Flow recusa o Chromium do Playwright — "
        "instale o Google Chrome ou ajuste _CANDIDATOS em sessao.py."
    )


def caminho_perfil(raiz: Path, email: str) -> Path:
    """Pasta de perfil estavel e legivel para um email."""
    slug = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-")
    digest = hashlib.sha1(email.encode()).hexdigest()[:8]
    return Path(raiz) / f"{slug}-{digest}"


async def abrir_contexto(perfil: Path, headless: bool = False) -> tuple[object, BrowserContext]:
    """Devolve (playwright, contexto). Quem chama fecha os dois."""
    checar_chrome()
    perfil.mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=str(perfil),
        channel="chrome",
        headless=headless,
        no_viewport=True,
        args=["--start-maximized"],
    )
    return pw, ctx


async def esta_logado(ctx: BrowserContext) -> bool:
    pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await pagina.goto(sel.URL_PROJETO, wait_until="domcontentloaded")
    return not await pagina.locator(sel.LOGIN_VISIVEL).is_visible(timeout=5000)


async def cadastrar(raiz_perfis: Path, email: str) -> Path:
    """Abre janela para login manual e so volta quando a sessao estiver de pe."""
    perfil = caminho_perfil(raiz_perfis, email)
    pw, ctx = await abrir_contexto(perfil, headless=False)
    try:
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(sel.URL_FLOW)
        # espera ate 5 min pelo login humano
        await pagina.locator(sel.CREDITOS).wait_for(timeout=300_000)
        return perfil
    finally:
        await ctx.close()
        await pw.stop()
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_sessao.py -q`
Expected: `4 passed`

- [ ] **Step 5: Adicionar as rotas de conta na API**

Em `flowpool/api/app.py`, adicione o import e as duas rotas, logo abaixo de `listar_contas`:

```python
from flowpool.transporte.pw.sessao import cadastrar, caminho_perfil
```

```python
    @app.post("/contas", response_model=RespostaConta, status_code=201)
    async def nova_conta(p: NovaConta) -> RespostaConta:
        raiz_perfis = Path(raiz) / "flowpool" / "perfis"
        perfil = await cadastrar(raiz_perfis, p.email)
        try:
            id_ = repo.criar_conta(p.email, p.tier, str(perfil))
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        c = repo.conta(id_)
        return RespostaConta(id=c.id, email=c.email, tier=c.tier, saldo=c.saldo,
                             saldo_confiavel=c.saldo_confiavel, saude=c.saude,
                             ativa=c.ativa, falhas=c.falhas)

    @app.post("/contas/{id_}/revalidar", response_model=RespostaConta)
    async def revalidar(id_: int) -> RespostaConta:
        c = repo.conta(id_)
        if c is None:
            raise HTTPException(404, "conta nao existe")
        await cadastrar(Path(c.perfil).parent, c.email)
        c.saude = SaudeSessao.VIVA
        c.falhas = 0
        repo.salvar_conta(c)
        return RespostaConta(id=c.id, email=c.email, tier=c.tier, saldo=c.saldo,
                             saldo_confiavel=c.saldo_confiavel, saude=c.saude,
                             ativa=c.ativa, falhas=c.falhas)
```

- [ ] **Step 6: Rodar a suíte**

Run: `.venv/Scripts/pytest -q`
Expected: `138 passed` (119 + 15 da api + 4 da sessão)

- [ ] **Step 7: Commit**

```bash
git add flowpool/transporte/pw/sessao.py flowpool/api/app.py tests/test_sessao.py
git commit -m "feat: sessao real com chrome e cadastro de conta"
```

---

### Task 11: Transporte Playwright — saldo e text-to-video

**Files:**
- Create: `flowpool/transporte/pw/transporte.py`
- Test: manual, contra o Flow

Os quatro modos compartilham o mesmo esqueleto: preencher prompt → escolher modelo → (subir arquivos) → gerar → esperar → baixar. Este task fecha o esqueleto e o modo mais simples.

Antes do esqueleto vem o projeto: `listar_projetos`, `criar_projeto` e `abrir_projeto` fecham o contrato que o motor já usa desde a Task 7. `abrir_projeto` é navegação pura (`URL_PROJETO_ABERTO.format(...)`); `criar_projeto` é botão → nome → confirmar, e **devolve o id lido da URL depois que o projeto abre** — é esse id que vai para o banco. Se a URL não mudar, a criação falhou: devolver um id inventado gravaria no banco um vínculo que nunca abre.

`gerar` passa a assumir que a página **já está dentro do projeto certo** — quem colocou foi `_resolver_projeto`, no orquestrador. O transporte não decide projeto: ele só lista, cria e abre.

- [ ] **Step 1: Implementar**

`flowpool/transporte/pw/transporte.py`:

```python
"""Implementacao real do FlowTransport. Unico modulo que importa Playwright
alem de sessao.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from flowpool.nucleo.tipos import Conta, Modo
from flowpool.transporte.base import (
    Estado, GeracaoFalhou, Pedido, ProjetoRemoto, SaldoInsuficiente, Sessao, SessaoMorta,
)
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.sessao import abrir_contexto


class TransportePlaywright:
    def __init__(self, timeout_ms: int = 600_000) -> None:
        self.timeout_ms = timeout_ms

    async def abrir_sessao(self, conta: Conta) -> Sessao:
        pw, ctx = await abrir_contexto(Path(conta.perfil), headless=False)
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(sel.URL_PROJETO, wait_until="domcontentloaded")
        if await pagina.locator(sel.LOGIN_VISIVEL).is_visible(timeout=5000):
            await ctx.close()
            await pw.stop()
            raise SessaoMorta(f"sessao de {conta.email} caiu")
        return Sessao(conta=conta, handle=(pw, ctx, pagina))

    async def saldo(self, s: Sessao) -> int:
        _, _, pagina = s.handle
        texto = await pagina.locator(sel.CREDITOS).inner_text(timeout=15_000)
        numeros = re.findall(r"\d[\d.,]*", texto)
        if not numeros:
            raise GeracaoFalhou(f"nao achei numero em {texto!r}")
        return int(re.sub(r"[.,]", "", numeros[0]))

    async def listar_projetos(self, s: Sessao) -> list[ProjetoRemoto]:
        """Projetos DESTA conta. Nao existe lista global: cada conta ve so os seus."""
        _, _, pagina = s.handle
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        await pagina.locator(sel.LISTA_PROJETOS).wait_for(timeout=30_000)
        achados = []
        for card in await pagina.locator(sel.CARD_PROJETO).all():
            href = await card.get_attribute("href") or ""
            nome = (await card.inner_text()).strip()
            id_remoto = href.rstrip("/").split("/")[-1]
            if id_remoto:
                achados.append(ProjetoRemoto(id_remoto=id_remoto, nome=nome))
        return achados

    async def criar_projeto(self, s: Sessao, nome: str) -> str:
        """Cria e devolve o id lido DA URL depois que o projeto abre.

        Inventar o id (ou devolver o nome) gravaria no banco um vinculo que nunca abre,
        e na geracao seguinte o app criaria outro projeto do mesmo produto.
        """
        _, _, pagina = s.handle
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        await pagina.locator(sel.BOTAO_NOVO_PROJETO).click()
        campo = pagina.locator(sel.CAMPO_NOME_PROJETO)
        await campo.wait_for(timeout=15_000)
        await campo.fill(nome)
        await pagina.locator(sel.BOTAO_CRIAR_PROJETO).click()
        await pagina.wait_for_url(re.compile(r"/flow/[^/]+$"), timeout=30_000)
        id_remoto = pagina.url.rstrip("/").split("/")[-1]
        if not id_remoto or id_remoto == "flow":
            raise GeracaoFalhou(f"projeto {nome!r} nao abriu depois de criado")
        return id_remoto

    async def abrir_projeto(self, s: Sessao, id_remoto: str) -> None:
        _, _, pagina = s.handle
        await pagina.goto(sel.URL_PROJETO_ABERTO.format(id_remoto=id_remoto),
                          wait_until="domcontentloaded")
        # projeto de outra conta nao carrega: cai no login ou em erro. Deixar passar
        # faria a geracao seguinte acontecer no projeto errado, ou em nenhum.
        if await pagina.locator(sel.LOGIN_VISIVEL).is_visible(timeout=5000):
            raise SessaoMorta(f"sessao de {s.conta.email} caiu ao abrir projeto")
        await pagina.locator(sel.CAMPO_PROMPT).wait_for(timeout=30_000)

    async def gerar(self, s: Sessao, p: Pedido) -> str:
        # a pagina ja esta dentro do projeto certo: quem colocou foi o orquestrador
        _, _, pagina = s.handle

        await pagina.locator(sel.SELETOR_MODELO).click()
        await pagina.locator(sel.OPCAO_MODELO[p.modelo.value]).click()

        await self._subir_arquivos(pagina, p)

        campo = pagina.locator(sel.CAMPO_PROMPT)
        await campo.click()
        await campo.fill(p.prompt)

        antes = await pagina.locator(sel.CARD_PRONTO).count()
        await pagina.locator(sel.BOTAO_GERAR).click()

        if await pagina.locator(sel.AVISO_SEM_CREDITO).is_visible(timeout=5000):
            raise SaldoInsuficiente(f"conta {s.conta.email} sem credito")

        await pagina.locator(sel.CARD_GERANDO).first.wait_for(timeout=30_000)
        # o "id remoto" aqui e o indice do card novo; o Flow nao expoe id na UI
        return f"card-{antes}"

    async def _subir_arquivos(self, pagina, p: Pedido) -> None:
        if p.modo is Modo.INGREDIENTS2V:
            await pagina.locator(sel.UPLOAD_REFERENCIA).set_input_files(
                [str(Path(r)) for r in p.referencias[:3]]
            )
        elif p.modo is Modo.FRAMES2V:
            await pagina.locator(sel.UPLOAD_FRAME_INICIAL).set_input_files(
                str(Path(p.frame_inicial))
            )
            if p.frame_final:
                await pagina.locator(sel.UPLOAD_FRAME_FINAL).set_input_files(
                    str(Path(p.frame_final))
                )

    async def consultar(self, s: Sessao, id_remoto: str) -> Estado:
        _, _, pagina = s.handle
        indice = int(id_remoto.split("-")[1])
        card = pagina.locator(sel.CARD_PRONTO).nth(indice)
        if await card.count() > 0 and await card.is_visible():
            return Estado.PRONTO
        if await pagina.locator(sel.CARD_GERANDO).count() > 0:
            return Estado.RODANDO
        return Estado.FALHOU

    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path:
        _, _, pagina = s.handle
        indice = int(id_remoto.split("-")[1])
        destino.parent.mkdir(parents=True, exist_ok=True)
        card = pagina.locator(sel.CARD_PRONTO).nth(indice)
        async with pagina.expect_download(timeout=self.timeout_ms) as espera:
            await card.locator(sel.BOTAO_DOWNLOAD).click()
        download = await espera.value
        await download.save_as(destino)
        return destino

    async def fechar(self, s: Sessao) -> None:
        pw, ctx, _ = s.handle
        await ctx.close()
        await pw.stop()
```

- [ ] **Step 2: Escrever o script de fumaça**

`flowpool/transporte/pw/fumaca.py`:

```python
"""Teste de fumaca contra o Flow de verdade. Gasta credito.

Uso:
    python -m flowpool.transporte.pw.fumaca <perfil> <modelo> "<prompt>"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from flowpool.nucleo.tipos import Conta, Modelo, Modo, Tier
from flowpool.transporte.base import Estado, Pedido, Sessao
from flowpool.transporte.pw.transporte import TransportePlaywright

PRODUTO_FUMACA = "Fumaca"


async def entrar_no_projeto(t: TransportePlaywright, s: Sessao, produto: str) -> str:
    """Adota o projeto pelo nome, e so cria se nao existir.

    E a mesma ordem do orquestrador (`_resolver_projeto`), de proposito: rodar a
    fumaca duas vezes nao pode deixar dois projetos "Fumaca" na conta.
    """
    achado = next((p for p in await t.listar_projetos(s) if p.nome == produto), None)
    id_remoto = achado.id_remoto if achado else await t.criar_projeto(s, produto)
    print("adotado" if achado else "criado", "projeto:", id_remoto)
    await t.abrir_projeto(s, id_remoto)
    return id_remoto


async def main(perfil: str, modelo: str, prompt: str) -> None:
    t = TransportePlaywright()
    conta = Conta(id=1, email="fumaca", tier=Tier.FREE, perfil=perfil)
    s = await t.abrir_sessao(conta)
    try:
        print("saldo antes:", await t.saldo(s))
        # a fumaca tambem passa pelo projeto: gerar fora de um nao existe no Flow
        await entrar_no_projeto(t, s, PRODUTO_FUMACA)
        id_remoto = await t.gerar(s, Pedido(modo=Modo.T2V, modelo=Modelo(modelo),
                                            prompt=prompt))
        print("gerando:", id_remoto)
        while await t.consultar(s, id_remoto) is Estado.RODANDO:
            await asyncio.sleep(5)
            print("...")
        destino = Path("scratch/fumaca.mp4")
        print("arquivo:", await t.baixar(s, id_remoto, destino))
        print("saldo depois:", await t.saldo(s))
    finally:
        await t.fechar(s)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))
```

- [ ] **Step 3: Rodar a fumaça com o modelo mais barato**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento veo-lite "a cat walking on a wooden table, cinematic"
```

Expected: imprime saldo antes, gera, imprime `scratch/fumaca.mp4` e um saldo 10 menor. Se algum seletor falhar, corrija **em `seletores.py`**, nunca no transporte.

- [ ] **Step 4: Anotar o custo real da imagem**

Rode a fumaça de novo com `nano-banana` e um prompt de imagem, e compare os saldos. Isso resolve a divergência anotada no spec (1–2 créditos vs zero). Registre o número medido num comentário em `flowpool/nucleo/custos.py` e ajuste `_CUSTO` se necessário.

- [ ] **Step 5: Commit**

```bash
git add flowpool/transporte/pw/transporte.py flowpool/transporte/pw/fumaca.py flowpool/nucleo/custos.py
git commit -m "feat: transporte playwright com saldo, t2v e download"
```

---

### Task 12: Fumaça dos outros três modos

**Files:**
- Modify: `flowpool/transporte/pw/fumaca.py`, `flowpool/transporte/pw/seletores.py` (correções)

- [ ] **Step 1: Estender o script de fumaça para aceitar modo e arquivos**

Substitua o `main` de `fumaca.py` por:

```python
async def main(perfil: str, modo: str, modelo: str, prompt: str,
               arquivos: list[str]) -> None:
    t = TransportePlaywright()
    conta = Conta(id=1, email="fumaca", tier=Tier.FREE, perfil=perfil)
    m = Modo(modo)
    pedido = Pedido(
        modo=m, modelo=Modelo(modelo), prompt=prompt,
        referencias=arquivos if m is Modo.INGREDIENTS2V else [],
        frame_inicial=arquivos[0] if m is Modo.FRAMES2V and arquivos else None,
        frame_final=arquivos[1] if m is Modo.FRAMES2V and len(arquivos) > 1 else None,
    )
    s = await t.abrir_sessao(conta)
    try:
        print("saldo antes:", await t.saldo(s))
        await entrar_no_projeto(t, s, PRODUTO_FUMACA)
        id_remoto = await t.gerar(s, pedido)
        while await t.consultar(s, id_remoto) is Estado.RODANDO:
            await asyncio.sleep(5)
            print("...")
        ext = ".png" if m is Modo.IMAGEM else ".mp4"
        print("arquivo:", await t.baixar(s, id_remoto, Path(f"scratch/fumaca{ext}")))
        print("saldo depois:", await t.saldo(s))
    finally:
        await t.fechar(s)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]))
```

- [ ] **Step 2: Fumaça de imagem**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento imagem nano-banana "a red ceramic mug on a linen tablecloth"
```

Expected: `scratch/fumaca.png` existe.

- [ ] **Step 3: Fumaça de frames-to-video**

Use uma imagem que já existe no projeto:

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento frames2v veo-lite "she slowly turns toward the mirror" "_Personagens/imagens/Clara/clara_01_frontal.jpg"
```

Expected: `scratch/fumaca.mp4` existe e o primeiro frame é a foto da Clara.

- [ ] **Step 4: Fumaça de ingredients-to-video**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento ingredients2v veo-lite "she walks toward the camera in a bright studio" "_Personagens/imagens/Clara/clara_01_frontal.jpg" "_Personagens/imagens/Clara/clara_02_tresquartos.jpg" "_Personagens/imagens/Clara/clara_03_perfil.jpg"
```

Expected: `scratch/fumaca.mp4` existe e o rosto é o da Clara. Três referências é o teto do Flow — o quarto arquivo é ignorado pelo `[:3]` no transporte, e a API já recusa com 422.

- [ ] **Step 5: Fumaça de projeto — reuso e adoção**

Rode qualquer uma das fumaças acima **duas vezes seguidas** e olhe a tela inicial do Flow depois.

Expected: a segunda execução imprime `adotado projeto: <mesmo id>`, e a lista de projetos da conta contém **um** único "Fumaca". Dois projetos com o mesmo nome significam que `listar_projetos` não está devolvendo nome ou id direito — corrija `CARD_PROJETO` em `seletores.py`, nunca o transporte. Apague o duplicado na mão antes de seguir: o app não apaga projeto nenhum, de propósito.

- [ ] **Step 6: Corrigir os seletores que falharam**

Toda correção vai em `seletores.py`. Se um modo exigir um passo extra na UI (por exemplo, abrir uma aba antes de aparecer o campo de upload), acrescente o seletor e o passo em `_subir_arquivos`, mantendo a regra: seletor no `seletores.py`, sequência no `transporte.py`.

- [ ] **Step 7: Commit**

```bash
git add flowpool/transporte/pw
git commit -m "feat: os quatro modos verificados contra o flow real"
```

---

### Task 13: Ponto de entrada e cliente do pipeline

**Files:**
- Create: `flowpool/__main__.py`, `flowpool/cliente.py`
- Test: `tests/test_cliente.py`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_cliente.py`:

```python
import pytest
from fastapi.testclient import TestClient

from flowpool.api.app import criar_app
from flowpool.cliente import FlowPool
from flowpool.dados.repo import Repo
from flowpool.transporte.falso import TransporteFalso


@pytest.fixture
def cliente(tmp_path):
    repo = Repo(tmp_path / "t.db")
    repo.criar_esquema()
    app = criar_app(repo=repo, transporte=TransporteFalso(saldos={}), raiz=tmp_path)
    return FlowPool(transporte_http=TestClient(app))


def test_gerar_devolve_o_id(cliente):
    assert isinstance(cliente.gerar(prompt="p", produto="Moda", nome="n"), int)


def test_lote_devolve_todos_os_ids(cliente):
    ids = cliente.lote([
        {"prompt": "a", "produto": "Moda", "nome": "c1"},
        {"prompt": "b", "produto": "Moda", "nome": "c2"},
    ])
    assert len(ids) == 2


def test_estado_devolve_o_dicionario_do_job(cliente):
    jid = cliente.gerar(prompt="p", produto="Moda", nome="n")
    assert cliente.estado(jid)["estado"] == "pendente"


def test_padroes_sao_t2v_e_veo_fast(cliente):
    jid = cliente.gerar(prompt="p", produto="Moda", nome="n")
    j = cliente.estado(jid)
    assert j["modo"] == "t2v"
    assert j["modelo"] == "veo-fast"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_cliente.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.cliente'`

- [ ] **Step 3: Implementar o cliente**

`flowpool/cliente.py`:

```python
"""Cliente fino para os scripts do pipeline (prompt_h3.py, frames.py) chamarem.

    from flowpool.cliente import FlowPool
    fp = FlowPool()
    jid = fp.gerar(prompt=texto, produto="Video Roupa", nome="c3-take1")
    caminho = fp.esperar(jid)
"""

from __future__ import annotations

import time
from typing import Any

import httpx

BASE = "http://127.0.0.1:8777"


class FlowPool:
    def __init__(self, base: str = BASE, transporte_http: Any = None) -> None:
        self.http = transporte_http or httpx.Client(base_url=base, timeout=30)

    def gerar(self, prompt: str, produto: str, nome: str, modo: str = "t2v",
              modelo: str = "veo-fast", destino: str | None = None,
              referencias: list[str] | None = None,
              frame_inicial: str | None = None, frame_final: str | None = None,
              prioridade: int = 0) -> int:
        # `destino` fica de fora quando e None: a API o deriva do produto
        # (`{produto}/clipes`), e mandar null explicito seria a mesma coisa com ruido.
        r = self.http.post("/jobs", json={
            "modo": modo, "modelo": modelo, "prompt": prompt, "produto": produto,
            "nome": nome, "referencias": referencias or [],
            "frame_inicial": frame_inicial, "frame_final": frame_final,
            "prioridade": prioridade,
            **({"destino": destino} if destino else {}),
        })
        r.raise_for_status()
        return r.json()["id"]

    def lote(self, jobs: list[dict]) -> list[int]:
        completos = [{"modo": "t2v", "modelo": "veo-fast", "referencias": [],
                      "prioridade": 0, **j} for j in jobs]
        r = self.http.post("/jobs/lote", json={"jobs": completos})
        r.raise_for_status()
        return r.json()["ids"]

    def estado(self, job_id: int) -> dict:
        r = self.http.get(f"/jobs/{job_id}")
        r.raise_for_status()
        return r.json()

    def esperar(self, job_id: int, intervalo: float = 10.0,
                teto_s: float = 3600.0) -> str:
        """Bloqueia ate o job terminar. Devolve o caminho do arquivo."""
        limite = time.monotonic() + teto_s
        while time.monotonic() < limite:
            j = self.estado(job_id)
            if j["estado"] == "pronto":
                return j["arquivo"]
            if j["estado"] in ("falhou", "cancelado"):
                raise RuntimeError(f"job {job_id} {j['estado']}: {j.get('erro')}")
            time.sleep(intervalo)
        raise TimeoutError(f"job {job_id} passou de {teto_s}s")

    def contas(self) -> list[dict]:
        return self.http.get("/contas").json()

    def saude(self) -> dict:
        return self.http.get("/saude").json()
```

- [ ] **Step 4: Implementar o ponto de entrada**

`flowpool/__main__.py`:

```python
"""Sobe o servico.

    python -m flowpool
"""

from __future__ import annotations

from pathlib import Path

import uvicorn

from flowpool.api.app import criar_app
from flowpool.dados.repo import Repo
from flowpool.transporte.pw.transporte import TransportePlaywright

RAIZ = Path(__file__).resolve().parent.parent   # a pasta "Tiktok Shop"
BANCO = RAIZ / "flowpool" / "flowpool.db"


def main() -> None:
    repo = Repo(BANCO)
    repo.criar_esquema()
    app = criar_app(repo=repo, transporte=TransportePlaywright(), raiz=RAIZ,
                    rodar_motor=True)
    uvicorn.run(app, host="127.0.0.1", port=8777)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_cliente.py -q`
Expected: `4 passed`

- [ ] **Step 6: Rodar a suíte inteira**

Run: `.venv/Scripts/pytest -q`
Expected: `142 passed` (138 + 4 do cliente)

- [ ] **Step 7: Commit**

```bash
git add flowpool/cliente.py flowpool/__main__.py tests/test_cliente.py
git commit -m "feat: ponto de entrada e cliente do pipeline"
```

---

### Task 14: Ponta a ponta com uma conta real

**Files:**
- Create: `flowpool/LEIA-ME.md`

- [ ] **Step 1: Subir o serviço**

```bash
.venv/Scripts/python -m flowpool
```

Expected: `Uvicorn running on http://127.0.0.1:8777`

- [ ] **Step 2: Cadastrar a conta de teste**

Em outro terminal:

```bash
curl -X POST http://127.0.0.1:8777/contas -H "Content-Type: application/json" -d "{\"email\":\"<a conta gratis de teste>\",\"tier\":\"free\"}"
```

Expected: abre uma janela do Chrome; após o login manual, o comando devolve a conta com `"saude":"viva"`.

- [ ] **Step 3: Disparar um lote de três clipes**

```bash
curl -X POST http://127.0.0.1:8777/jobs/lote -H "Content-Type: application/json" -d "{\"jobs\":[{\"modo\":\"t2v\",\"modelo\":\"veo-lite\",\"prompt\":\"a cat on a table\",\"produto\":\"Fumaca\",\"destino\":\"scratch\",\"nome\":\"e1\"},{\"modo\":\"t2v\",\"modelo\":\"veo-lite\",\"prompt\":\"a dog in a park\",\"produto\":\"Fumaca\",\"destino\":\"scratch\",\"nome\":\"e2\"},{\"modo\":\"t2v\",\"modelo\":\"veo-lite\",\"prompt\":\"rain on a window\",\"produto\":\"Fumaca\",\"destino\":\"scratch\",\"nome\":\"e3\"}]}"
```

Expected: `{"ids":[1,2,3]}`. Uma conta grátis com 50 créditos paga três Lite (30) e sobra 20.

- [ ] **Step 4: Acompanhar até o fim**

```bash
curl http://127.0.0.1:8777/saude
```

Expected, ao final: `fila_pendente: 0`, e os três arquivos em `scratch/e1.mp4`, `e2.mp4`, `e3.mp4`.

- [ ] **Step 5: Verificar a troca de conta**

Dispare um quarto e um quinto Lite. O quarto passa (sobram 20), o quinto encontra a conta sem crédito. Confirme em `GET /jobs?estado=pendente` que ele ficou pendente com `reavaliar_em` preenchido — **não** falhou. Esse é o comportamento que sustenta o pool inteiro.

- [ ] **Step 6: Escrever o LEIA-ME**

`flowpool/LEIA-ME.md`:

```markdown
# FlowPool

Serviço local que gera vídeo e imagem no Google Flow rodando um pool de contas Google.

## Subir

    python -m flowpool

Sobe em http://127.0.0.1:8777.

## Cadastrar conta

    curl -X POST http://127.0.0.1:8777/contas -H "Content-Type: application/json" \
      -d '{"email":"conta@gmail.com","tier":"free"}'

Abre uma janela do Chrome. Loga na mão. A sessão fica guardada em `perfis/`.
Precisa ser o **Chrome instalado** — o Chromium do Playwright é recusado pelo Google.

## Gerar

    from flowpool.cliente import FlowPool
    fp = FlowPool()
    jid = fp.gerar(prompt="...", produto="Video Roupa", nome="c3-take1",
                   modelo="veo-fast")
    caminho = fp.esperar(jid)

## O que saber

- Conta grátis: 50 créditos/dia. Lite 10, Fast 20. **Quality (100) nunca cabe** — vai
  para conta paga.
- Job sem conta apta não falha: espera o reset e volta sozinho.
- Sessão caída exige pessoa: `POST /contas/{id}/revalidar`.
- `produto` é obrigatório: é a pasta de destino (`{produto}/clipes`, se você não mandar
  `destino`) e o nome do projeto no Flow.
- Um projeto por produto **em cada conta** — projeto do Flow pertence a uma conta só.
  Veja em `GET /projetos`. `DELETE /projetos/{id}` esquece o vínculo local; o projeto
  continua no Flow e é readotado pelo nome na próxima geração.
- Quando o Google mexer no layout, o conserto é em `transporte/pw/seletores.py`.
```

- [ ] **Step 7: Commit**

```bash
git add flowpool/LEIA-ME.md
git commit -m "docs: leia-me do flowpool"
```

---

## Depois deste plano

O painel web é o **plano 2**: uma página servida pelo próprio FastAPI, consumindo `/contas`, `/jobs` e `/saude`, com botão de cadastrar conta e um formulário de lote. Não começa antes deste plano estar rodando ponta a ponta — tela para API que nunca gerou clipe é trabalho jogado fora.

Fica também pendente, e só depois de o A estar de pé: medir se a leitura de saldo e a geração de imagem funcionam por HTTP puro (rota C do spec). Se funcionarem, o `TransportePlaywright` ganha atalhos para esses dois casos e o teto de simultâneos sobe.
