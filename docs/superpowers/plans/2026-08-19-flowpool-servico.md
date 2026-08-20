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
| `flowpool/nucleo/escolha.py` | `escolher(contas, modelo) -> Conta | None`. Função pura. |
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
- Create: `flowpool/nucleo/tipos.py`
- Test: `tests/test_tipos.py`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_tipos.py`:

```python
from datetime import datetime, timezone

from flowpool.nucleo.tipos import Conta, EstadoJob, Job, Modelo, Modo, SaudeSessao, Tier


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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_tipos.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.nucleo.tipos'`

- [ ] **Step 3: Implementar**

`flowpool/nucleo/tipos.py`:

```python
"""Tipos do domínio. Sem I/O, sem dependências externas."""

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
    referencias: list[str] = field(default_factory=list)
    frame_inicial: str | None = None
    frame_final: str | None = None
    prioridade: int = 0
    estado: EstadoJob = EstadoJob.PENDENTE
    conta_id: int | None = None
    id_remoto: str | None = None
    arquivo: str | None = None
    tentativas: int = 0
    erro: str | None = None
    reavaliar_em: datetime | None = None
    criado_em: datetime | None = None
    atualizado_em: datetime | None = None
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_tipos.py -q`
Expected: `4 passed`

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
    return _CUSTO[(modelo, tier)]


def cabe_no_free(modelo: Modelo) -> bool:
    """Se uma conta gratuita consegue, no melhor dia possivel, rodar este modelo."""
    return custo_de(modelo, Tier.FREE) <= TETO_DIARIO_FREE
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_custos.py -q`
Expected: `16 passed`

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

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_escolha.py`:

```python
from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.tipos import Conta, Modelo, SaudeSessao, Tier


def conta(id, tier, saldo, saude=SaudeSessao.VIVA, ativa=True):
    return Conta(id=id, email=f"c{id}@x.com", tier=tier, perfil=f"p/{id}",
                 saldo=saldo, saude=saude, ativa=ativa)


def test_sem_contas_devolve_none():
    assert escolher([], Modelo.VEO_FAST) is None


def test_gasta_o_gratis_antes_do_pago():
    # credito de free morre em 24h; o de pago acumula. Aritmetica, nao preferencia.
    free = conta(1, Tier.FREE, 50)
    pro = conta(2, Tier.PRO, 1000)
    assert escolher([pro, free], Modelo.VEO_FAST).id == 1


def test_entre_free_pega_a_de_maior_saldo():
    # espalha o uso e evita sobras inuteis de 5 creditos pelo pool
    escolhida = escolher([conta(1, Tier.FREE, 20), conta(2, Tier.FREE, 50)], Modelo.VEO_LITE)
    assert escolhida.id == 2


def test_quality_ignora_todas_as_free_e_vai_pra_paga():
    contas = [conta(1, Tier.FREE, 50), conta(2, Tier.FREE, 50), conta(3, Tier.ULTRA, 9000)]
    assert escolher(contas, Modelo.VEO_QUALITY).id == 3


def test_quality_sem_conta_paga_devolve_none():
    assert escolher([conta(1, Tier.FREE, 50)], Modelo.VEO_QUALITY) is None


def test_pula_conta_com_saldo_insuficiente():
    # Fast custa 20 na free
    escolhida = escolher([conta(1, Tier.FREE, 10), conta(2, Tier.FREE, 40)], Modelo.VEO_FAST)
    assert escolhida.id == 2


def test_pula_conta_com_sessao_expirada():
    contas = [conta(1, Tier.FREE, 50, saude=SaudeSessao.EXPIRADA), conta(2, Tier.FREE, 30)]
    assert escolher(contas, Modelo.VEO_FAST).id == 2


def test_pula_conta_bloqueada():
    contas = [conta(1, Tier.FREE, 50, saude=SaudeSessao.BLOQUEADA), conta(2, Tier.PRO, 900)]
    assert escolher(contas, Modelo.VEO_FAST).id == 2


def test_pula_conta_desativada():
    contas = [conta(1, Tier.FREE, 50, ativa=False), conta(2, Tier.FREE, 30)]
    assert escolher(contas, Modelo.VEO_FAST).id == 2


def test_pool_todo_seco_devolve_none():
    assert escolher([conta(1, Tier.FREE, 5), conta(2, Tier.FREE, 0)], Modelo.VEO_FAST) is None


def test_ultra_paga_menos_e_por_isso_consegue_onde_pro_nao_conseguiria():
    # Fast custa 10 na ultra e 20 na pro. Com saldo 15, so a ultra da conta.
    contas = [conta(1, Tier.PRO, 15), conta(2, Tier.ULTRA, 15)]
    assert escolher(contas, Modelo.VEO_FAST).id == 2


def test_desempate_entre_pagas_e_por_saldo():
    contas = [conta(1, Tier.PRO, 300), conta(2, Tier.ULTRA, 900)]
    assert escolher(contas, Modelo.VEO_QUALITY).id == 2
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_escolha.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.nucleo.escolha'`

- [ ] **Step 3: Implementar**

`flowpool/nucleo/escolha.py`:

```python
"""Escolha de conta. Funcao pura: sem I/O, sem relogio, sem banco."""

from __future__ import annotations

from collections.abc import Sequence

from flowpool.nucleo.custos import cabe_no_free, custo_de
from flowpool.nucleo.tipos import Conta, Modelo, SaudeSessao, Tier

# Free primeiro: seu credito expira em 24h, o das pagas acumula ate o fim do mes.
_ORDEM_TIER = {Tier.FREE: 1, Tier.PRO: 0, Tier.ULTRA: 0}


def apta(c: Conta, modelo: Modelo) -> bool:
    if not c.ativa or c.saude is not SaudeSessao.VIVA:
        return False
    if c.tier is Tier.FREE and not cabe_no_free(modelo):
        return False
    return custo_de(modelo, c.tier) <= c.saldo


def escolher(contas: Sequence[Conta], modelo: Modelo) -> Conta | None:
    """Melhor conta para rodar `modelo` agora, ou None se nenhuma serve."""
    aptas = [c for c in contas if apta(c, modelo)]
    if not aptas:
        return None
    return max(aptas, key=lambda c: (_ORDEM_TIER[c.tier], c.saldo, -c.id))
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_escolha.py -q`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add flowpool/nucleo/escolha.py tests/test_escolha.py
git commit -m "feat: politica de escolha de conta"
```

---

### Task 4: Contabilidade de crédito

**Files:**
- Create: `flowpool/nucleo/credito.py`
- Test: `tests/test_credito.py`

O ponto que o spec insiste: descontar o previsto ao despachar (para dois jobs simultâneos não estourarem o mesmo saldo) e reconciliar com a leitura real ao terminar.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_credito.py`:

```python
from datetime import datetime, timezone

from flowpool.nucleo.credito import LIMIAR_DIVERGENCIA, reconciliar, reservar
from flowpool.nucleo.tipos import Conta, Modelo, Tier


def conta(saldo, tier=Tier.FREE):
    return Conta(id=1, email="a@b.com", tier=tier, perfil="p", saldo=saldo,
                 saldo_confiavel=True)


AGORA = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def test_reservar_desconta_o_custo_previsto():
    c = conta(50)
    reservar(c, Modelo.VEO_FAST)
    assert c.saldo == 30


def test_reservar_nunca_deixa_saldo_negativo():
    c = conta(5)
    reservar(c, Modelo.VEO_FAST)
    assert c.saldo == 0


def test_reconciliar_com_leitura_igual_mantem_confianca():
    c = conta(30)
    div = reconciliar(c, lido=30, agora=AGORA)
    assert div == 0
    assert c.saldo == 30
    assert c.saldo_confiavel is True
    assert c.saldo_lido_em == AGORA


def test_reconciliar_sempre_adota_o_valor_lido():
    # a tela do Flow e a verdade; o previsto e so estimativa
    c = conta(30)
    reconciliar(c, lido=42, agora=AGORA)
    assert c.saldo == 42


def test_divergencia_pequena_nao_derruba_confianca():
    c = conta(30)
    div = reconciliar(c, lido=32, agora=AGORA)
    assert div == 2
    assert c.saldo_confiavel is True


def test_divergencia_grande_derruba_confianca():
    c = conta(30)
    div = reconciliar(c, lido=30 + LIMIAR_DIVERGENCIA + 1, agora=AGORA)
    assert div > LIMIAR_DIVERGENCIA
    assert c.saldo_confiavel is False


def test_divergencia_e_em_modulo():
    c = conta(100)
    div = reconciliar(c, lido=100 - LIMIAR_DIVERGENCIA - 1, agora=AGORA)
    assert div > LIMIAR_DIVERGENCIA
    assert c.saldo_confiavel is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_credito.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.nucleo.credito'`

- [ ] **Step 3: Implementar**

`flowpool/nucleo/credito.py`:

```python
"""Contabilidade de credito: reservar o previsto, reconciliar com o lido.

Descontar so localmente acumula erro (geracao que falha depois de debitar, custo que
o Google muda, imagem que as vezes sai a zero). Em uma semana o app manda job pra
conta seca e a fila trava sem explicacao. Por isso a leitura real sempre vence.
"""

from __future__ import annotations

from datetime import datetime

from flowpool.nucleo.custos import custo_de
from flowpool.nucleo.tipos import Conta, Modelo

# Acima disso, o saldo deixa de ser confiavel ate a proxima leitura.
LIMIAR_DIVERGENCIA = 5


def reservar(conta: Conta, modelo: Modelo) -> int:
    """Desconta o custo previsto antes de despachar. Devolve o custo reservado."""
    custo = custo_de(modelo, conta.tier)
    conta.saldo = max(0, conta.saldo - custo)
    return custo


def reconciliar(conta: Conta, lido: int, agora: datetime) -> int:
    """Adota o saldo lido do Flow. Devolve a divergencia absoluta."""
    divergencia = abs(lido - conta.saldo)
    conta.saldo = lido
    conta.saldo_lido_em = agora
    conta.saldo_confiavel = divergencia <= LIMIAR_DIVERGENCIA
    return divergencia
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_credito.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add flowpool/nucleo/credito.py tests/test_credito.py
git commit -m "feat: reserva e reconciliacao de credito"
```

---

### Task 5: Esquema e repositório SQLite

**Files:**
- Create: `flowpool/dados/esquema.sql`, `flowpool/dados/repo.py`
- Test: `tests/test_repo.py`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_repo.py`:

```python
from datetime import datetime, timezone

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


def test_referencias_sobrevivem_ao_banco(repo):
    id_ = repo.criar_job(Job(id=0, modo=Modo.INGREDIENTS2V, modelo=Modelo.VEO_FAST,
                             prompt="p", destino="d", nome="n",
                             referencias=["a/1.png", "a/2.png", "a/3.png"]))
    assert repo.job(id_).referencias == ["a/1.png", "a/2.png", "a/3.png"]


def test_proximo_pendente_respeita_prioridade(repo):
    repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                       prompt="baixa", destino="d", nome="n1", prioridade=0))
    repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                       prompt="alta", destino="d", nome="n2", prioridade=9))
    assert repo.proximo_pendente(agora=datetime.now(timezone.utc)).prompt == "alta"


def test_proximo_pendente_ignora_job_esperando_reset(repo):
    futuro = datetime(2099, 1, 1, tzinfo=timezone.utc)
    j = Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="d", nome="n")
    j.reavaliar_em = futuro
    repo.criar_job(j)
    assert repo.proximo_pendente(agora=datetime.now(timezone.utc)) is None


def test_registrar_evento_e_listar_por_job(repo):
    jid = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                             prompt="p", destino="d", nome="n"))
    repo.registrar_evento(job_id=jid, conta_id=None, tipo="despachado", payload="{}")
    eventos = repo.eventos(job_id=jid)
    assert len(eventos) == 1
    assert eventos[0]["tipo"] == "despachado"
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
    referencias    TEXT    NOT NULL DEFAULT '[]',
    frame_inicial  TEXT,
    frame_final    TEXT,
    prioridade     INTEGER NOT NULL DEFAULT 0,
    estado         TEXT    NOT NULL DEFAULT 'pendente',
    conta_id       INTEGER REFERENCES conta(id),
    id_remoto      TEXT,
    arquivo        TEXT,
    tentativas     INTEGER NOT NULL DEFAULT 0,
    erro           TEXT,
    reavaliar_em   TEXT,
    criado_em      TEXT    NOT NULL,
    atualizado_em  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_fila ON job (estado, prioridade DESC, id);

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

from flowpool.nucleo.tipos import Conta, EstadoJob, Job, Modelo, Modo, SaudeSessao, Tier

_ESQUEMA = Path(__file__).parent / "esquema.sql"


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _txt(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


class Repo:
    def __init__(self, caminho: Path | str) -> None:
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.caminho, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")

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
                   saldo_confiavel=?, reset_em=?, saude=?, falhas=?, ativa=?
               WHERE id=?""",
            (c.tier.value, c.perfil, c.saldo, _txt(c.saldo_lido_em),
             int(c.saldo_confiavel), _txt(c.reset_em), c.saude.value, c.falhas,
             int(c.ativa), c.id),
        )
        self.con.commit()

    @staticmethod
    def _conta(r: sqlite3.Row) -> Conta:
        return Conta(
            id=r["id"], email=r["email"], tier=Tier(r["tier"]), perfil=r["perfil"],
            saldo=r["saldo"], saldo_lido_em=_dt(r["saldo_lido_em"]),
            saldo_confiavel=bool(r["saldo_confiavel"]), reset_em=_dt(r["reset_em"]),
            saude=SaudeSessao(r["saude"]), falhas=r["falhas"], ativa=bool(r["ativa"]),
        )

    # ---------- jobs ----------

    def criar_job(self, j: Job) -> int:
        agora = datetime.now(timezone.utc).isoformat()
        cur = self.con.execute(
            """INSERT INTO job (modo, modelo, prompt, destino, nome, referencias,
                   frame_inicial, frame_final, prioridade, estado, reavaliar_em,
                   criado_em, atualizado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (j.modo.value, j.modelo.value, j.prompt, j.destino, j.nome,
             json.dumps(j.referencias), j.frame_inicial, j.frame_final, j.prioridade,
             j.estado.value, _txt(j.reavaliar_em), agora, agora),
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

    def proximo_pendente(self, agora: datetime) -> Job | None:
        r = self.con.execute(
            """SELECT * FROM job
               WHERE estado = 'pendente'
                 AND (reavaliar_em IS NULL OR reavaliar_em <= ?)
               ORDER BY prioridade DESC, id ASC LIMIT 1""",
            (agora.isoformat(),),
        ).fetchone()
        return self._job(r) if r else None

    def salvar_job(self, j: Job) -> None:
        self.con.execute(
            """UPDATE job SET estado=?, conta_id=?, id_remoto=?, arquivo=?,
                   tentativas=?, erro=?, reavaliar_em=?, atualizado_em=?
               WHERE id=?""",
            (j.estado.value, j.conta_id, j.id_remoto, j.arquivo, j.tentativas, j.erro,
             _txt(j.reavaliar_em), datetime.now(timezone.utc).isoformat(), j.id),
        )
        self.con.commit()

    @staticmethod
    def _job(r: sqlite3.Row) -> Job:
        return Job(
            id=r["id"], modo=Modo(r["modo"]), modelo=Modelo(r["modelo"]),
            prompt=r["prompt"], destino=r["destino"], nome=r["nome"],
            referencias=json.loads(r["referencias"]),
            frame_inicial=r["frame_inicial"], frame_final=r["frame_final"],
            prioridade=r["prioridade"], estado=EstadoJob(r["estado"]),
            conta_id=r["conta_id"], id_remoto=r["id_remoto"], arquivo=r["arquivo"],
            tentativas=r["tentativas"], erro=r["erro"],
            reavaliar_em=_dt(r["reavaliar_em"]), criado_em=_dt(r["criado_em"]),
            atualizado_em=_dt(r["atualizado_em"]),
        )

    # ---------- eventos ----------

    def registrar_evento(self, job_id: int | None, conta_id: int | None,
                         tipo: str, payload: str = "{}") -> None:
        self.con.execute(
            "INSERT INTO evento (job_id, conta_id, tipo, payload, em) VALUES (?,?,?,?,?)",
            (job_id, conta_id, tipo, payload, datetime.now(timezone.utc).isoformat()),
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
Expected: `9 passed`

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

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_transporte_falso.py`:

```python
import pytest

from flowpool.nucleo.tipos import Conta, Modelo, Modo, Tier
from flowpool.transporte.base import Pedido, SaldoInsuficiente, SessaoMorta, Estado
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

from flowpool.nucleo.tipos import Conta, Modelo, Modo


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
class Sessao:
    conta: Conta
    handle: Any = None


class ErroTransporte(Exception):
    """Base de tudo que o transporte sabe dar errado."""


class SaldoInsuficiente(ErroTransporte):
    """A conta nao tem credito. Nao e erro: e hora de trocar de conta."""


class SessaoMorta(ErroTransporte):
    """O login caiu. Exige pessoa; nenhum retry resolve."""


class GeracaoFalhou(ErroTransporte):
    """O Flow aceitou e depois devolveu erro."""


class FlowTransport(Protocol):
    async def abrir_sessao(self, conta: Conta) -> Sessao: ...
    async def saldo(self, s: Sessao) -> int: ...
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
    Estado, Pedido, SaldoInsuficiente, Sessao, SessaoMorta,
)


class TransporteFalso:
    def __init__(self, saldos: dict[int, int], mortas: set[int] | None = None,
                 falha_em: set[int] | None = None, latencia: float = 0.0) -> None:
        self.saldos = dict(saldos)
        self.mortas = mortas or set()
        self.falha_em = falha_em or set()
        self.latencia = latencia
        self.geracoes: dict[str, tuple[int, Estado]] = {}
        self._seq = 0

    async def abrir_sessao(self, conta: Conta) -> Sessao:
        if conta.id in self.mortas:
            raise SessaoMorta(f"sessao da conta {conta.email} expirou")
        return Sessao(conta=conta)

    async def saldo(self, s: Sessao) -> int:
        return self.saldos.get(s.conta.id, 0)

    async def gerar(self, s: Sessao, p: Pedido) -> str:
        await asyncio.sleep(self.latencia)
        custo = custo_de(p.modelo, s.conta.tier)
        if self.saldos.get(s.conta.id, 0) < custo:
            raise SaldoInsuficiente(f"conta {s.conta.email} sem credito")
        self.saldos[s.conta.id] -= custo
        self._seq += 1
        id_remoto = f"falso-{self._seq}"
        estado = Estado.FALHOU if s.conta.id in self.falha_em else Estado.PRONTO
        self.geracoes[id_remoto] = (s.conta.id, estado)
        return id_remoto

    async def consultar(self, s: Sessao, id_remoto: str) -> Estado:
        return self.geracoes[id_remoto][1]

    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"FALSO:" + id_remoto.encode())
        return destino

    async def fechar(self, s: Sessao) -> None:
        return None
```

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_transporte_falso.py -q`
Expected: `6 passed`

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

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_orquestrador.py`:

```python
from pathlib import Path

import pytest

from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import MAX_TENTATIVAS, Orquestrador
from flowpool.nucleo.tipos import EstadoJob, Job, Modelo, Modo, SaudeSessao, Tier
from flowpool.transporte.falso import TransporteFalso


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path / "t.db")
    r.criar_esquema()
    return r


def job_fast(nome="c1", modelo=Modelo.VEO_FAST):
    return Job(id=0, modo=Modo.T2V, modelo=modelo, prompt="uma gata",
               destino="clipes", nome=nome)


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
from flowpool.nucleo.credito import reconciliar, reservar
from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.tipos import Conta, EstadoJob, Job, Modo, SaudeSessao
from flowpool.transporte.base import (
    Estado, FlowTransport, GeracaoFalhou, Pedido, SaldoInsuficiente, SessaoMorta,
)

MAX_TENTATIVAS = 3
ESPERA_RESET = timedelta(hours=1)
TIMEOUT_GERACAO = timedelta(minutes=10)


def _extensao(modo: Modo) -> str:
    return ".png" if modo is Modo.IMAGEM else ".mp4"


class Orquestrador:
    def __init__(self, repo: Repo, transporte: FlowTransport, raiz: Path | str,
                 max_simultaneos: int = 3) -> None:
        self.repo = repo
        self.transporte = transporte
        self.raiz = Path(raiz)
        self.sem = asyncio.Semaphore(max_simultaneos)
        self._rodando = False

    async def passo(self) -> bool:
        """Puxa um job e leva ate o fim. Devolve False se nao havia job."""
        agora = datetime.now(timezone.utc)
        job = self.repo.proximo_pendente(agora)
        if job is None:
            return False

        conta = escolher(self.repo.contas(), job.modelo)
        if conta is None:
            job.reavaliar_em = agora + ESPERA_RESET
            self.repo.salvar_job(job)
            self.repo.registrar_evento(job.id, None, "sem_conta_apta")
            return True

        async with self.sem:
            await self._executar(job, conta)
        return True

    async def _executar(self, job: Job, conta: Conta) -> None:
        job.estado = EstadoJob.RODANDO
        job.conta_id = conta.id
        self.repo.salvar_job(job)
        self.repo.registrar_evento(job.id, conta.id, "despachado",
                                   json.dumps({"modelo": job.modelo.value}))

        sessao = None
        try:
            sessao = await self.transporte.abrir_sessao(conta)
            reservar(conta, job.modelo)
            self.repo.salvar_conta(conta)

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
            conta.saude = SaudeSessao.EXPIRADA
            self.repo.salvar_conta(conta)
            self._devolver(job, str(e), gasta_tentativa=False)
            self.repo.registrar_evento(job.id, conta.id, "sessao_morta")

        except SaldoInsuficiente as e:
            conta.saldo = 0
            conta.saldo_confiavel = False
            self.repo.salvar_conta(conta)
            self._devolver(job, str(e), gasta_tentativa=False)
            self.repo.registrar_evento(job.id, conta.id, "conta_seca")

        except Exception as e:  # falha de rede, timeout, geracao errada
            conta.falhas += 1
            self.repo.salvar_conta(conta)
            self._devolver(job, f"{type(e).__name__}: {e}", gasta_tentativa=True)
            self.repo.registrar_evento(job.id, conta.id, "falhou",
                                       json.dumps({"erro": str(e)}))

        finally:
            if sessao is not None:
                try:
                    lido = await self.transporte.saldo(sessao)
                    reconciliar(conta, lido, datetime.now(timezone.utc))
                    conta.falhas = 0 if job.estado is EstadoJob.PRONTO else conta.falhas
                    self.repo.salvar_conta(conta)
                except Exception:
                    pass
                await self.transporte.fechar(sessao)

    def _devolver(self, job: Job, erro: str, gasta_tentativa: bool) -> None:
        job.erro = erro
        if gasta_tentativa:
            job.tentativas += 1
        if job.tentativas >= MAX_TENTATIVAS:
            job.estado = EstadoJob.FALHOU
        else:
            job.estado = EstadoJob.PENDENTE
            job.conta_id = None
            job.id_remoto = None
        self.repo.salvar_job(job)

    async def _esperar(self, sessao, id_remoto: str) -> Estado:
        limite = datetime.now(timezone.utc) + TIMEOUT_GERACAO
        while datetime.now(timezone.utc) < limite:
            estado = await self.transporte.consultar(sessao, id_remoto)
            if estado is not Estado.RODANDO:
                return estado
            await asyncio.sleep(5)
        raise TimeoutError(f"geracao {id_remoto} passou de {TIMEOUT_GERACAO}")

    @staticmethod
    def _pedido(job: Job) -> Pedido:
        return Pedido(modo=job.modo, modelo=job.modelo, prompt=job.prompt,
                      referencias=job.referencias, frame_inicial=job.frame_inicial,
                      frame_final=job.frame_final)

    async def rodar(self, intervalo: float = 2.0) -> None:
        """Laco eterno, usado pela API. Para com `parar()`."""
        self._rodando = True
        while self._rodando:
            if not await self.passo():
                await asyncio.sleep(intervalo)

    def parar(self) -> None:
        self._rodando = False
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_orquestrador.py -q`
Expected: `10 passed`

- [ ] **Step 5: Rodar a suíte inteira**

Run: `.venv/Scripts/pytest -q`
Expected: `64 passed` (4 tipos + 16 custos + 12 escolha + 7 credito + 9 repo + 6 transporte + 10 orquestrador)

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
                              "destino": "clipes", "nome": "c1"})
    assert r.status_code == 201
    assert r.json()["estado"] == "pendente"
    assert isinstance(r.json()["id"], int)


def test_consultar_job_inexistente_da_404(cliente):
    c, _ = cliente
    assert c.get("/jobs/999").status_code == 404


def test_lote_cria_varios_jobs(cliente):
    c, _ = cliente
    corpo = {"jobs": [
        {"modo": "t2v", "modelo": "veo-fast", "prompt": "a", "destino": "d", "nome": "c1"},
        {"modo": "t2v", "modelo": "veo-lite", "prompt": "b", "destino": "d", "nome": "c2"},
    ]}
    r = c.post("/jobs/lote", json=corpo)
    assert r.status_code == 201
    assert len(r.json()["ids"]) == 2


def test_ingredients2v_recusa_mais_de_tres_referencias(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "ingredients2v", "modelo": "veo-fast",
                              "prompt": "p", "destino": "d", "nome": "n",
                              "referencias": ["1.png", "2.png", "3.png", "4.png"]})
    assert r.status_code == 422


def test_frames2v_exige_frame_inicial(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "frames2v", "modelo": "veo-fast", "prompt": "p",
                              "destino": "d", "nome": "n"})
    assert r.status_code == 422


def test_cancelar_job_pendente(cliente):
    c, _ = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "destino": "d", "nome": "n"}).json()["id"]
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
                          "destino": "d", "nome": "n"})
    s = c.get("/saude").json()
    assert s["contas_vivas"] == 1
    assert s["credito_total"] == 40
    assert s["fila_pendente"] == 1
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
    destino: str
    nome: str
    referencias: list[str] = Field(default_factory=list, max_length=3)
    frame_inicial: str | None = None
    frame_final: str | None = None
    prioridade: int = 0

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
    conta_id: int | None = None
    arquivo: str | None = None
    tentativas: int = 0
    erro: str | None = None


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
    NovaConta, PatchConta, PedidoJob, PedidoLote, RespostaConta, RespostaJob, Saude,
)
from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import Orquestrador
from flowpool.nucleo.tipos import EstadoJob, Job, SaudeSessao
from flowpool.transporte.base import FlowTransport


def _job_de(p: PedidoJob) -> Job:
    return Job(id=0, modo=p.modo, modelo=p.modelo, prompt=p.prompt, destino=p.destino,
               nome=p.nome, referencias=p.referencias, frame_inicial=p.frame_inicial,
               frame_final=p.frame_final, prioridade=p.prioridade)


def _resposta(j: Job) -> RespostaJob:
    return RespostaJob(id=j.id, estado=j.estado, modo=j.modo, modelo=j.modelo,
                       nome=j.nome, conta_id=j.conta_id, arquivo=j.arquivo,
                       tentativas=j.tentativas, erro=j.erro)


def criar_app(repo: Repo, transporte: FlowTransport, raiz: Path | str,
              rodar_motor: bool = False) -> FastAPI:
    orq = Orquestrador(repo, transporte, raiz)

    @asynccontextmanager
    async def ciclo(app: FastAPI):
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
Expected: `9 passed`

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

- [ ] **Step 5: Preencher o arquivo de seletores**

`flowpool/transporte/pw/seletores.py` — substitua cada `...` pelo localizador real coletado no passo anterior. Não invente: use o que o Inspector mostrou.

```python
"""Todos os seletores da UI do Flow. Quando o Google mexer no layout, so este arquivo muda.

Preenchido por inspecao manual em <DATA DO MAPEAMENTO>. Cada constante e um seletor
do Playwright (CSS, texto ou role).
"""

URL_FLOW = "https://labs.google/fx/tools/flow"
URL_PROJETO = "https://labs.google/fx/tools/flow/project"

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

# --- erros ---
AVISO_SEM_CREDITO = "..."
```

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
Expected: `77 passed` (64 + 9 da api + 4 da sessao)

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
    Estado, GeracaoFalhou, Pedido, SaldoInsuficiente, Sessao, SessaoMorta,
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

    async def gerar(self, s: Sessao, p: Pedido) -> str:
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
from flowpool.transporte.base import Estado, Pedido
from flowpool.transporte.pw.transporte import TransportePlaywright


async def main(perfil: str, modelo: str, prompt: str) -> None:
    t = TransportePlaywright()
    conta = Conta(id=1, email="fumaca", tier=Tier.FREE, perfil=perfil)
    s = await t.abrir_sessao(conta)
    try:
        print("saldo antes:", await t.saldo(s))
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

- [ ] **Step 5: Corrigir os seletores que falharam**

Toda correção vai em `seletores.py`. Se um modo exigir um passo extra na UI (por exemplo, abrir uma aba antes de aparecer o campo de upload), acrescente o seletor e o passo em `_subir_arquivos`, mantendo a regra: seletor no `seletores.py`, sequência no `transporte.py`.

- [ ] **Step 6: Commit**

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
    assert isinstance(cliente.gerar(prompt="p", destino="d", nome="n"), int)


def test_lote_devolve_todos_os_ids(cliente):
    ids = cliente.lote([
        {"prompt": "a", "destino": "d", "nome": "c1"},
        {"prompt": "b", "destino": "d", "nome": "c2"},
    ])
    assert len(ids) == 2


def test_estado_devolve_o_dicionario_do_job(cliente):
    jid = cliente.gerar(prompt="p", destino="d", nome="n")
    assert cliente.estado(jid)["estado"] == "pendente"


def test_padroes_sao_t2v_e_veo_fast(cliente):
    jid = cliente.gerar(prompt="p", destino="d", nome="n")
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
    jid = fp.gerar(prompt=texto, destino="Video Roupa/clipes", nome="c3-take1")
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

    def gerar(self, prompt: str, destino: str, nome: str, modo: str = "t2v",
              modelo: str = "veo-fast", referencias: list[str] | None = None,
              frame_inicial: str | None = None, frame_final: str | None = None,
              prioridade: int = 0) -> int:
        r = self.http.post("/jobs", json={
            "modo": modo, "modelo": modelo, "prompt": prompt, "destino": destino,
            "nome": nome, "referencias": referencias or [],
            "frame_inicial": frame_inicial, "frame_final": frame_final,
            "prioridade": prioridade,
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
Expected: `81 passed` (77 + 4 do cliente)

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
curl -X POST http://127.0.0.1:8777/jobs/lote -H "Content-Type: application/json" -d "{\"jobs\":[{\"modo\":\"t2v\",\"modelo\":\"veo-lite\",\"prompt\":\"a cat on a table\",\"destino\":\"scratch\",\"nome\":\"e1\"},{\"modo\":\"t2v\",\"modelo\":\"veo-lite\",\"prompt\":\"a dog in a park\",\"destino\":\"scratch\",\"nome\":\"e2\"},{\"modo\":\"t2v\",\"modelo\":\"veo-lite\",\"prompt\":\"rain on a window\",\"destino\":\"scratch\",\"nome\":\"e3\"}]}"
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
    jid = fp.gerar(prompt="...", destino="Video Roupa/clipes", nome="c3-take1",
                   modelo="veo-fast")
    caminho = fp.esperar(jid)

## O que saber

- Conta grátis: 50 créditos/dia. Lite 10, Fast 20. **Quality (100) nunca cabe** — vai
  para conta paga.
- Job sem conta apta não falha: espera o reset e volta sozinho.
- Sessão caída exige pessoa: `POST /contas/{id}/revalidar`.
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
