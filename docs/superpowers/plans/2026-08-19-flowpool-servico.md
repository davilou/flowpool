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
| `flowpool/nucleo/escolha.py` | `escolher(contas, modelo, agora, quantidade) -> Conta | None`. Função pura. |
| `flowpool/nucleo/credito.py` | debitar previsto, reconciliar com o lido, decidir confiança |
| `flowpool/nucleo/proxy.py` | traduz a URL de proxy da conta para o Playwright e para o `chrome.exe`. Função pura. |
| `flowpool/dados/esquema.sql` | DDL das três tabelas |
| `flowpool/dados/repo.py` | leitura/escrita SQLite. Única parte que conhece SQL. |
| `flowpool/transporte/base.py` | `FlowTransport` (Protocol), `Pedido`, `Estado`, exceções |
| `flowpool/transporte/falso.py` | transporte em memória que simula latência, saldo e falhas |
| `flowpool/transporte/pw/seletores.py` | **todos** os seletores da UI do Flow, em um lugar só |
| `flowpool/transporte/pw/dom.py` | varredura de DOM sem `<script>` e mascaramento de token. Todo caminho de leitura passa por aqui. |
| `flowpool/transporte/pw/mapear.py` | sonda manual, para descobrir seletor novo |
| `flowpool/transporte/pw/conferir.py` | checa que cada seletor ainda casa. Primeira coisa a rodar quando o Google mexer no layout. |
| `flowpool/transporte/pw/sessao.py` | cadastro por `chrome.exe` comum, validação por Playwright, perfil da conta |
| `flowpool/transporte/pw/transporte.py` | implementação real do `FlowTransport` |
| `flowpool/transporte/pw/fumaca.py` | fumaça contra o Flow real, pelo transporte de verdade |
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
- Create: `flowpool/nucleo/tipos.py`, `flowpool/nucleo/erros.py`, `flowpool/nucleo/proxy.py`
- Test: `tests/test_tipos.py`, `tests/test_proxy.py`

- [ ] **Step 0: Criar os erros de domínio**

`flowpool/nucleo/erros.py` — vem antes de tudo porque a Task 2 já importa dele:

```python
"""Erros de dominio. Camadas de cima importam daqui; nucleo nao importa de ninguem."""


class SaldoInsuficiente(Exception):
    """A conta nao tem credito para o custo pedido. Nao e falha: e hora de trocar de conta."""


class ModeloSemCusto(Exception):
    """Modelo/tier sem custo cadastrado. Erro de configuracao, nao de execucao."""


class DuracaoSemCusto(Exception):
    """Duracao sem custo cadastrado. Irma de `ModeloSemCusto`, e pelo mesmo motivo.

    Chutar um preco para uma duracao que a tabela nao conhece mandaria reservar um
    numero inventado, e o erro so reapareceria mais tarde como conta seca — longe da
    causa. Erro de configuracao, nao de execucao.
    """


class ProxyInvalido(Exception):
    """A conta tem proxy configurado, mas a URL nao da para usar.

    Nao e o caso de nao haver proxy — esse e o comum, e devolve None. E o caso de haver
    um e ele estar errado, e ai falhar cedo e melhor do que abrir um navegador que sai
    pelo IP de casa sem ninguem perceber. O IP proprio por conta existe justamente para
    o Google nao correlacionar as contas entre si.
    """
```

Mora em `nucleo/` porque as camadas de cima dependem de `nucleo`, nunca o contrário. `transporte/base.py` (Task 6) reexporta `SaldoInsuficiente` daqui em vez de definir a sua própria — duas classes com o mesmo nome fariam o `except` do orquestrador pegar uma e deixar a outra passar como falha de rede.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_tipos.py`:

```python
from datetime import datetime, timezone

from flowpool.nucleo.tipos import (
    DURACAO_PADRAO, DURACOES, PROPORCOES, Conta, EstadoJob, Job, Modelo, Modo, Projeto,
    SaudeSessao, Tier, duracao_valida, quantidade_maxima,
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


def test_omni_flash_e_o_modelo_de_video_padrao_do_flow():
    # medido em campo: o dropdown de video do Flow abre em "Omni Flash", nao em Veo
    assert Modelo.OMNI_FLASH.value == "omni-flash"


def test_job_nasce_vertical():
    # o canal e TikTok: gerar ja em 9:16 evita o recorte manual depois
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p", destino="x", nome="c1")
    assert j.proporcao == "9:16"


def test_proporcao_explicita_sobrescreve_o_padrao():
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p", destino="x",
            nome="c1", proporcao="16:9")
    assert j.proporcao == "16:9"


def test_proporcoes_aceitas_sao_as_cinco_do_painel():
    assert PROPORCOES == ("16:9", "4:3", "1:1", "3:4", "9:16")


def test_job_nasce_com_uma_geracao_so():
    # quantidade multiplica o custo; o default nunca pode gastar mais do que o pedido
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p", destino="x", nome="c1")
    assert j.quantidade == 1


def test_imagem_aceita_lote_e_video_nao():
    # take de video e escolha do usuario, nao sorteio; variacao de imagem e barata
    assert quantidade_maxima(Modo.IMAGEM) == 4
    for modo in (Modo.T2V, Modo.FRAMES2V, Modo.INGREDIENTS2V):
        assert quantidade_maxima(modo) == 1


def test_conta_nasce_sem_proxy():
    # o campo e desenhado agora e fica vazio: enquanto nao ha IP contratado, o Davi
    # destrava com uma VPN de sistema, que o app nem enxerga
    c = Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil="perfis/1")
    assert c.proxy is None
    c.proxy = "http://1.2.3.4:8080"
    assert c.proxy == "http://1.2.3.4:8080"


def test_job_nasce_com_clipe_de_quatro_segundos():
    # 4s custa 7 creditos e 8s custa 12: fixar 8s desperdicava 70% do credito por
    # video. Numa conta gratuita de 50/dia isso e a diferenca entre 4 e 7 videos.
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p", destino="x", nome="c1")
    assert j.duracao == "4s"


def test_duracao_explicita_sobrescreve_o_padrao():
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p", destino="x",
            nome="c1", duracao="10s")
    assert j.duracao == "10s"


def test_duracoes_aceitas_sao_as_quatro_do_painel():
    assert DURACOES == ("4s", "6s", "8s", "10s")
    assert DURACAO_PADRAO == "4s"


def test_imagem_nao_aceita_duracao():
    # nao existe grupo de duracao no painel em modo imagem: pedir "4s" ali nao teria
    # onde ser clicado. `None` e o unico valor coerente.
    assert duracao_valida(Modo.IMAGEM, None) is True
    for d in DURACOES:
        assert duracao_valida(Modo.IMAGEM, d) is False


def test_video_aceita_so_as_quatro_duracoes():
    for modo in (Modo.T2V, Modo.FRAMES2V, Modo.INGREDIENTS2V):
        for d in DURACOES:
            assert duracao_valida(modo, d) is True
        assert duracao_valida(modo, "5s") is False
        assert duracao_valida(modo, None) is False
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
    # Omni Flash e o que o dropdown de video do Flow ja mostra por padrao. Nao e Veo:
    # transporte que nao trocar explicitamente gera nele.
    OMNI_FLASH = "omni-flash"
    VEO_LITE = "veo-lite"
    VEO_FAST = "veo-fast"
    VEO_QUALITY = "veo-quality"
    NANO_BANANA = "nano-banana"


# As cinco do painel de configuracao do Flow, nesta ordem. Valor fora da lista nao
# teria botao onde clicar.
PROPORCOES = ("16:9", "4:3", "1:1", "3:4", "9:16")

# Padrao vertical: o canal do Davi e TikTok, e gerar ja em 9:16 evita o recorte manual.
PROPORCAO_PADRAO = "9:16"

# Quantidade de saidas por geracao. O Flow oferece x1..x4, mas video e diferente de
# imagem: take de video e escolha do usuario, nao sorteio, e cada take extra custa
# cheio. Variacao de imagem e barata e serve justamente para escolher.
MAX_QUANTIDADE_IMAGEM = 4


def quantidade_maxima(modo: Modo) -> int:
    """Quantas saidas um job daquele modo pode pedir de uma vez."""
    return MAX_QUANTIDADE_IMAGEM if modo is Modo.IMAGEM else 1


# As quatro do grupo de duracao do chip, medidas ao vivo. Imagem nao tem grupo de
# duracao: nao existe botao onde clicar.
DURACOES = ("4s", "6s", "8s", "10s")

# Padrao 4s, e nao os 8s que o transporte fixava enquanto o campo nao existia. Sao os
# dois motivos:
#   1. PRECO. No Omni Flash, 4s custa 7 creditos e 8s custa 12 — fixar 8s desperdicava
#      70% do credito em todo video. Numa conta gratuita de 50 creditos por dia, e a
#      diferenca entre 4 e 7 videos.
#   2. USO. O pipeline do Davi monta os clipes em ~5s; o clipe curto e o caso comum,
#      nao a excecao.
# Continua sendo escolha por pedido — o padrao so precisa servir ao caso comum.
DURACAO_PADRAO = "4s"


def duracao_valida(modo: Modo, duracao: str | None) -> bool:
    """Se aquele modo aceita aquela duracao.

    `None` significa "sem duracao", e e o UNICO valor coerente em imagem: o painel do
    Flow nao oferece o grupo de duracao em modo imagem, entao "4s" ali nao teria onde
    ser clicado. Nos tres modos de video valem so os quatro valores de `DURACOES`.
    """
    if modo is Modo.IMAGEM:
        return duracao is None
    return duracao in DURACOES


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
    # URL do proxy pelo qual ESTA conta sai. Vazio e o normal hoje: sem proxy
    # contratado, o Davi destrava o Flow com uma VPN de sistema, que o app nem enxerga.
    # Existe por conta, e nao global, porque o ponto e o Google nao conseguir
    # correlacionar as contas entre si pelo IP. Formato: `http://host:porta`,
    # `http://usuario:senha@host:porta` ou `socks5://host:porta` — quem traduz e
    # `nucleo/proxy.py`.
    proxy: str | None = None


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
    # Formato da saida, um dos `PROPORCOES`. Vertical por padrao — ver a constante.
    proporcao: str = PROPORCAO_PADRAO
    # Saidas por geracao. MULTIPLICA O CUSTO: quatro imagens custam quatro vezes uma,
    # e por isso a quantidade atravessa `escolher` e `reservar`, nao so a reserva.
    quantidade: int = 1
    # Tamanho do clipe, um dos `DURACOES`. MUDA O CUSTO — 4s custa 7 creditos e 8s
    # custa 12 no Omni Flash —, e por isso atravessa `escolher` e `reservar` pelo mesmo
    # caminho da quantidade. Em modo imagem o campo fica com o padrao e NAO significa
    # nada: nao ha grupo de duracao no painel, o transporte nao o aplica e o custo o
    # ignora. Quem impede o usuario de pedir duracao em imagem e a API.
    duracao: str = DURACAO_PADRAO
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

`Modelo.OMNI_FLASH` existe porque o mapeamento de campo (20/08) mostrou que o dropdown
de vídeo do Flow **já vem em Omni Flash**, não em Veo. Um transporte que não troque o
modelo explicitamente gera nele — então ou o app o conhece, ou todo job "Veo" sairia no
modelo errado sem ninguém perceber. Por isso ele também virou o padrão de vídeo da API
(Task 8).

`Job.proporcao` e `Job.quantidade` são os outros dois grupos do painel de configuração
da geração (`tune Configurações`), ao lado dos dropdowns de modelo. O padrão de
proporção é **9:16** porque o canal do Davi é TikTok: gerar já em vertical evita o
recorte que ele faz hoje à mão. Continua sobrescrevível por pedido — os cinco valores
são exatamente os cinco botões do painel, e um valor fora da lista não teria onde
clicar.

`quantidade_maxima` mora aqui, e não no validador da API, porque é regra de domínio:
**vídeo sai sempre 1, imagem aceita até 4.** O motivo é do usuário — take de vídeo é
escolha dele, não sorteio, e cada take extra custa cheio; variação de imagem é barata e
serve justamente para escolher entre elas. A API só traduz isso em 422 (Task 8).

A consequência mais cara de `quantidade` está nas Tasks 3 e 4: ela **multiplica o
custo**, e por isso precisa atravessar `escolher` e `reservar`, não só a reserva.

`Job.produto` é o nome da pasta do produto e, ao mesmo tempo, o nome do projeto no
Flow. Tem default `""` só para não quebrar a ordem dos campos da dataclass — os campos
com default precisam vir depois dos sem default, e `produto` entra logo após `nome`. Na
prática a API exige (Task 8), e é dele que `destino` passa a derivar.

`Conta.proxy` existe porque **cada conta precisa sair por um IP próprio**: manter vinte
contas atrás de um único IP doméstico entrega ao Google exatamente a correlação que o
pool existe para evitar. A medição de campo que dimensiona o problema é de 20/08 — o
Flow **já recusa geração** a partir do IP de casa do Davi, e uma **VPN gratuita
destrava**. Isso diz duas coisas: o bloqueio é por reputação de IP, e a barra é baixa.
Não é preciso proxy residencial premium; é preciso **outro** IP.

O campo nasce **opcional e vazio**, e isso é a decisão, não uma pendência. Enquanto não
houver proxy contratado, o Davi destrava com uma VPN de sistema — que o app nem
enxerga, porque ela atua abaixo do processo. Quando ele contratar IPs (ISP estático, na
casa de US$ 0,27 por IP), basta preencher o campo por conta. Fica no `Conta` e não numa
configuração global justamente porque um proxy compartilhado por todo o pool não
resolveria nada: ele apenas mudaria o IP em que as vinte contas se encontram.

Ele é `str | None` e não um par host/porta porque o que o usuário tem em mãos, quando
compra um proxy, é uma linha única — `http://usuario:senha@host:porta`. Quebrá-la em
campos obrigaria o painel a pedir quatro coisas onde o fornecedor entrega uma, e a
tradução para o que o Playwright e o Chrome querem é código puro: mora em
`nucleo/proxy.py`, no Step 4.

- [ ] **Step 4: Escrever o teste que falha — a tradução do proxy**

`nucleo/proxy.py` fica no núcleo porque é lógica pura e precisa de teste sem rede. Três
funções, e a segunda é a que evita um bug silencioso caro.

`tests/test_proxy.py`:

```python
import pytest

from flowpool.nucleo.erros import ProxyInvalido
from flowpool.nucleo.proxy import analisar, argumento_chrome, mascarar

# --- analisar: o caso comum e nao haver proxy nenhum ---------------------------


def test_sem_proxy_devolve_none():
    # hoje o campo nasce vazio: o Davi destrava o Flow com uma VPN de sistema, que o
    # app nem enxerga. Devolver None e o caminho normal, nao a excecao.
    assert analisar(None) is None


def test_string_vazia_devolve_none():
    # o painel manda "" quando o campo fica em branco, e isso nao e erro de digitacao
    assert analisar("") is None
    assert analisar("   ") is None


def test_host_e_porta_viram_server():
    assert analisar("http://1.2.3.4:8080") == {"server": "http://1.2.3.4:8080"}


def test_proxy_sem_credencial_nao_traz_usuario_nem_senha():
    # o Playwright trata a chave presente com valor None como credencial vazia
    cfg = analisar("http://1.2.3.4:8080")
    assert "username" not in cfg
    assert "password" not in cfg


def test_credencial_sai_em_campos_separados_do_server():
    # o Playwright quer o usuario e a senha FORA da URL do servidor
    assert analisar("http://davi:segredo@1.2.3.4:8080") == {
        "server": "http://1.2.3.4:8080",
        "username": "davi",
        "password": "segredo",
    }


def test_credencial_percent_encoded_volta_decodificada():
    # senha com `@` ou `:` so cabe na URL escapada; mandar o escape cru para o
    # Playwright autenticaria com a senha errada e o proxy recusaria sem dizer por que
    cfg = analisar("http://u%40x:p%3As@h:1")
    assert cfg["username"] == "u@x"
    assert cfg["password"] == "p:s"


def test_socks5_e_aceito():
    assert analisar("socks5://1.2.3.4:1080") == {"server": "socks5://1.2.3.4:1080"}


def test_esquema_desconhecido_e_erro():
    # falhar cedo e melhor que abrir um navegador que sai pelo IP errado calado
    with pytest.raises(ProxyInvalido, match="esquema"):
        analisar("ftp://1.2.3.4:8080")


def test_url_sem_porta_e_erro():
    with pytest.raises(ProxyInvalido, match="porta"):
        analisar("http://1.2.3.4")


def test_url_sem_esquema_e_erro():
    # `1.2.3.4:8080` parece certo e nao e: o urlsplit le "1.2.3.4" como esquema
    with pytest.raises(ProxyInvalido, match="esquema"):
        analisar("1.2.3.4:8080")


def test_url_sem_host_e_erro():
    with pytest.raises(ProxyInvalido, match="host"):
        analisar("http://:8080")


def test_porta_fora_da_faixa_e_erro():
    # o urlsplit levanta ValueError so quando alguem le `.port`; sem traduzir, o erro
    # apareceria la dentro do transporte, longe do cadastro que o causou
    with pytest.raises(ProxyInvalido, match="porta"):
        analisar("http://h:99999")


# --- argumento_chrome: a funcao que evita o bug silencioso ---------------------


def test_argumento_sem_proxy_e_none():
    assert argumento_chrome(None) is None
    assert argumento_chrome("") is None


def test_argumento_traduz_host_e_porta():
    assert argumento_chrome("http://1.2.3.4:8080") == "--proxy-server=http://1.2.3.4:8080"


def test_argumento_aceita_socks5():
    assert argumento_chrome("socks5://1.2.3.4:1080") == "--proxy-server=socks5://1.2.3.4:1080"


def test_argumento_recusa_proxy_com_credencial():
    # o chrome.exe cru abre um dialogo pedindo usuario e senha, e o cadastro trava sem
    # mensagem nenhuma. Recusar aqui e o unico jeito de isso virar erro em vez de
    # janela parada esperando alguem digitar.
    with pytest.raises(ProxyInvalido):
        argumento_chrome("http://davi:segredo@1.2.3.4:8080")


def test_a_recusa_por_credencial_diz_o_que_fazer():
    # mensagem que so diz "nao da" manda a pessoa adivinhar. As duas saidas reais sao
    # autorizar por IP ou por um retransmissor local sem senha.
    with pytest.raises(ProxyInvalido) as e:
        argumento_chrome("http://davi:segredo@1.2.3.4:8080")
    texto = str(e.value).lower()
    assert "ip" in texto
    assert "retransmissor" in texto


def test_a_recusa_por_credencial_nao_repete_a_senha():
    # a mensagem vai para a resposta HTTP e para o log — e o mesmo motivo de a
    # resposta de conta mascarar o proxy (Task 8)
    with pytest.raises(ProxyInvalido) as e:
        argumento_chrome("http://davi:segredo@1.2.3.4:8080")
    assert "segredo" not in str(e.value)


def test_argumento_propaga_url_invalida():
    with pytest.raises(ProxyInvalido):
        argumento_chrome("ftp://1.2.3.4:8080")


# --- mascarar: a credencial nao passeia pela resposta da API -------------------


def test_mascarar_esconde_a_senha():
    assert mascarar("http://davi:segredo@1.2.3.4:8080") == "http://davi:***@1.2.3.4:8080"


def test_mascarar_sem_credencial_devolve_a_url_inteira():
    assert mascarar("http://1.2.3.4:8080") == "http://1.2.3.4:8080"


def test_mascarar_sem_proxy_e_none():
    assert mascarar(None) is None
    assert mascarar("") is None


def test_mascarar_nao_levanta_com_url_torta():
    # `mascarar` alimenta o `GET /contas`. Levantar aqui derrubaria a listagem inteira
    # justamente quando ha uma URL errada para o usuario ver e corrigir.
    assert "segredo" not in mascarar("nao-e-url://davi:segredo@")
```

- [ ] **Step 5: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_proxy.py -q`
Expected: FAIL — `ImportError: cannot import name 'ProxyInvalido'`

- [ ] **Step 6: Implementar `nucleo/proxy.py`**

```python
"""Traducao da URL de proxy de uma conta. Puro: sem rede, sem I/O, sem navegador.

Cada conta do pool sai por um IP proprio para o Google nao correlacionar as contas
entre si. Medicao de campo (20/08): o Flow ja recusa geracao a partir do IP domestico
do Davi e uma VPN gratuita destrava — ou seja, o bloqueio e por reputacao de IP e a
barra e baixa. Nao e preciso proxy residencial premium; e preciso OUTRO IP.

Isto mora no nucleo porque e logica pura e precisa de teste sem rede. O transporte le
o resultado; a decisao inteira sobre o que e uma URL usavel acontece aqui.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from flowpool.nucleo.erros import ProxyInvalido

# O que o Chrome e o Playwright sabem falar. `https` entra porque existe proxy que
# atende em TLS; `socks5h` e o socks5 que resolve DNS do lado do proxy — sem ele, o
# DNS vazaria pelo IP de casa, que e metade do que este arquivo existe para evitar.
ESQUEMAS = ("http", "https", "socks5", "socks5h")

# O que substitui a senha na resposta da API. Nao e a senha truncada: e um carimbo.
SENHA_MASCARADA = "***"


def _partes(url: str):
    """`urlsplit` com os erros dele traduzidos para `ProxyInvalido`.

    `.port` levanta `ValueError` para porta fora da faixa, e so na hora em que alguem
    le o atributo. Sem traduzir aqui, o estouro apareceria dentro do transporte, longe
    do cadastro que o causou.
    """
    if "://" not in url:
        raise ProxyInvalido(
            f"proxy {url!r} sem esquema; use http://host:porta, "
            f"http://usuario:senha@host:porta ou socks5://host:porta")
    partes = urlsplit(url)
    if partes.scheme not in ESQUEMAS:
        raise ProxyInvalido(
            f"esquema de proxy {partes.scheme!r} desconhecido; use um de {list(ESQUEMAS)}")
    if not partes.hostname:
        raise ProxyInvalido(f"proxy {url!r} sem host")
    try:
        porta = partes.port
    except ValueError as e:
        raise ProxyInvalido(f"proxy {url!r} com porta invalida: {e}") from e
    if porta is None:
        raise ProxyInvalido(
            f"proxy {url!r} sem porta; proxy sem porta explicita nao se conecta")
    return partes, porta


def analisar(url: str | None) -> dict | None:
    """Converte a URL de proxy no formato que o Playwright espera.

    Devolve None quando nao ha proxy (o caso comum hoje).
    Levanta ProxyInvalido quando a URL existe mas nao da para usar — falhar cedo e
    melhor que abrir um navegador que vai sair pelo IP errado sem ninguem perceber.

    `username` e `password` so aparecem quando a URL os traz: o Playwright trata a
    chave presente com valor None como credencial vazia e tenta autenticar assim.
    """
    if url is None or not url.strip():
        return None
    partes, porta = _partes(url.strip())

    cfg: dict = {"server": f"{partes.scheme}://{partes.hostname}:{porta}"}
    # a credencial so cabe na URL escapada quando tem `@` ou `:`; mandar o escape cru
    # para o Playwright autenticaria com a senha errada e o proxy recusaria calado
    if partes.username:
        cfg["username"] = unquote(partes.username)
    if partes.password:
        cfg["password"] = unquote(partes.password)
    return cfg


def argumento_chrome(url: str | None) -> str | None:
    """Traduz o proxy para a linha de comando do chrome.exe do cadastro.

    O Chrome cru aceita `--proxy-server=host:porta` mas NAO aceita credencial: ele
    abre um dialogo pedindo usuario e senha, que trava o cadastro sem mensagem util.
    Por isso, proxy COM credencial e recusado aqui com ProxyInvalido — e a mensagem
    precisa dizer o que fazer: usar proxy autorizado por IP, ou por um retransmissor
    local sem senha.

    O cadastro tambem precisa do proxy, e nao so a operacao: se a conta nasce no IP de
    casa e depois passa a operar por proxy, o Google ve a mudanca — que e exatamente o
    sinal que o proxy existe para evitar. O login tem que sair do MESMO IP do trabalho.
    """
    cfg = analisar(url)
    if cfg is None:
        return None
    if "username" in cfg or "password" in cfg:
        raise ProxyInvalido(
            "o Chrome do cadastro nao aceita proxy com usuario e senha: ele abre um "
            "dialogo de autenticacao e o cadastro fica parado sem erro nenhum. Use um "
            "proxy autorizado por IP, ou aponte esta conta para um retransmissor local "
            "sem senha (ex.: http://127.0.0.1:PORTA) que carregue a credencial por voce."
        )
    return f"--proxy-server={cfg['server']}"


def mascarar(url: str | None) -> str | None:
    """Esconde a senha do proxy antes de a URL sair na resposta da API.

    A resposta vai para o painel e para log; credencial de proxy nao precisa passear
    por ali. Nao levanta de proposito: `mascarar` alimenta o `GET /contas`, e derrubar
    a listagem inteira por causa de uma URL torta esconderia justamente a URL que a
    pessoa precisa ver para consertar. O que ele nao faz e deixar passar o que nao
    entendeu — havendo `@` no meio, tudo antes dele some.
    """
    if url is None or not url.strip():
        return None
    bruto = url.strip()
    try:
        partes = urlsplit(bruto)
        usuario, host, porta = partes.username, partes.hostname, partes.port
    except ValueError:
        usuario = host = porta = None
    if usuario and host:
        sufixo = f":{porta}" if porta else ""
        return f"{partes.scheme}://{unquote(usuario)}:{SENHA_MASCARADA}@{host}{sufixo}"
    if host:
        return bruto
    return re.sub(r"(//)[^/@]*@", rf"\1{SENHA_MASCARADA}@", bruto)
```

**Por que `argumento_chrome` é uma função separada, e não um `if` dentro do transporte.**
O login do cadastro não passa por Playwright (Task 10) — sobe um `chrome.exe` comum por
`subprocess`. E o login **tem que sair do mesmo IP do trabalho**: se a conta nasce no IP
de casa e só depois passa a operar por proxy, o Google vê a mudança, que é exatamente o
sinal que o proxy existe para evitar. Então o `chrome.exe` do cadastro também precisa do
proxy — por outra sintaxe, e com uma incompatibilidade que **precisa ser erro**.

O Chrome cru aceita `--proxy-server=` mas **não** aceita credencial embutida: ele abre um
diálogo de autenticação. Num cadastro dirigido por `subprocess`, esse diálogo é uma
janela parada que ninguém preenche, e o `proc.wait()` fica pendurado até o teto de 15
minutos — sem erro, sem log, sem pista. Recusar antes de abrir a janela transforma isso
numa mensagem que diz o que fazer.

`mascarar` fica aqui, e não no `api/app.py`, pelo mesmo motivo das outras duas: é
função pura de texto e ganha teste sem subir servidor. Quem a consome é a Task 8.

- [ ] **Step 7: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_tipos.py tests/test_proxy.py -q`
Expected: `43 passed` (20 tipos + 23 proxy)

- [ ] **Step 8: Commit**

```bash
git add flowpool/nucleo/tipos.py flowpool/nucleo/erros.py flowpool/nucleo/proxy.py \
        tests/test_tipos.py tests/test_proxy.py
git commit -m "feat: tipos do dominio do flowpool"
```

---

### Task 2: Tabela de custos

**Files:**
- Create: `flowpool/nucleo/custos.py`
- Test: `tests/test_custos.py`

O custo depende de (modelo, tier, duração): conta Ultra paga menos por Lite e Fast, e clipe curto custa menos que clipe longo. O teto diário do free é o que impede Quality de rodar em conta grátis.

> **Nota de revisão (2026-08-20).** Este task já foi executado com os números **estimados**. A medição ao vivo desempatou duas linhas: **imagem custa 0** (saldo 50 → 50, e o Flow nem pede confirmação) e **Omni Flash custa 12** em conta grátis a 720p/8s/9:16/x1 (saldo 50 → 38). A correção do código chegou no **Step 6 da Task 11**, junto da medição que a justifica; naquele momento a **contagem de casos não mudou** — só os valores. Os listings abaixo, tanto o do código quanto o dos testes, estão na forma **final**, já com a duração da Task 15.
>
> **Nota de revisão 2 (2026-08-20) — duração.** A limitação que a medição expôs (o custo de vídeo depende da **configuração**, não só do modelo) deixou de ser limitação: a **Task 15** acrescentou `duracao` ao domínio, e a tabela de vídeo passou a ser por **(modelo, tier, duração)**. O listing abaixo já está na forma final. Os dois pontos conhecidos são `8s = 12` (MEDIDO por saldo, 50 → 38) e `4s = 7` (ANUNCIADO pelo agente na confirmação); 6s, 10s e os três Veo são **extrapolados** por proporção. Continua valendo o de sempre: a tabela é **reserva** anterior ao despacho, e a reconciliação corrige.
>
> O que isso muda no contrato: `custo_de(modelo, tier)` **sem duração** passa a valer o custo do clipe **padrão (4s)**, não mais o de 8s. Os testes que conferem número de crédito dizem agora, explicitamente, de qual clipe falam.

`custo_de` recusa par desconhecido com `ModeloSemCusto` em vez de deixar vazar um `KeyError`. Motivo: `custo_de` roda dentro de `apta()`, que roda para CADA conta dentro de `escolher()` — um `KeyError` cru não derruba um job, derruba a avaliação do pool inteiro e trava a fila. E o spec já promete acomodar um modelo novo (Gemini Omni) sem migração, então o dia em que alguém acrescenta um modelo e esquece o custo vai chegar. `ModeloSemCusto` vem de `flowpool/nucleo/erros.py`, criado no Step 0 da Task 1.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_custos.py`:

```python
import pytest

from flowpool.nucleo.custos import TETO_DIARIO_FREE, cabe_no_free, custo_de
from flowpool.nucleo.erros import DuracaoSemCusto
from flowpool.nucleo.tipos import DURACAO_PADRAO, DURACOES, Modelo, Tier

# Os quatro modelos que tem duracao. Imagem nao entra: nao ha grupo de duracao no
# painel do Flow em modo imagem.
VIDEO = [Modelo.OMNI_FLASH, Modelo.VEO_LITE, Modelo.VEO_FAST, Modelo.VEO_QUALITY]


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
    ],
)
def test_custo_de_video_por_modelo_e_tier_em_oito_segundos(modelo, tier, esperado):
    # 8s e a duracao de referencia da tabela: e nela que esta o unico ponto MEDIDO
    # (Omni Flash, 12). Estes tres Veo continuam sendo ESTIMATIVA.
    assert custo_de(modelo, tier, "8s") == esperado


@pytest.mark.parametrize("tier", [Tier.FREE, Tier.PRO, Tier.ULTRA])
def test_imagem_custa_zero_em_qualquer_tier(tier):
    assert custo_de(Modelo.NANO_BANANA, tier) == 0


def test_tabela_cobre_todos_os_pares():
    # um modelo novo sem custo cadastrado travaria a fila inteira, nao so um job
    for m in Modelo:
        for t in Tier:
            assert isinstance(custo_de(m, t), int)


def test_tabela_cobre_todas_as_duracoes_dos_modelos_de_video():
    # duracao sem custo cadastrado tem o mesmo efeito de modelo sem custo: o job morre
    # na escolha de conta, nao na geracao
    for m in VIDEO:
        for t in Tier:
            for d in DURACOES:
                assert isinstance(custo_de(m, t, d), int)


def test_quality_nao_cabe_em_conta_gratis():
    # 59 creditos ja no clipe mais curto, contra teto diario de 50 — nunca cabe
    assert cabe_no_free(Modelo.VEO_QUALITY) is False


@pytest.mark.parametrize(
    "modelo", [Modelo.VEO_LITE, Modelo.VEO_FAST, Modelo.NANO_BANANA, Modelo.OMNI_FLASH]
)
def test_o_resto_cabe_em_conta_gratis(modelo):
    assert cabe_no_free(modelo) is True


# --- custo por duracao ---

def test_omni_flash_de_oito_segundos_custa_doze():
    # MEDIDO 2026-08-20, conta FREE, 720p / 8s / 9:16 / x1: saldo 50 -> 38
    assert custo_de(Modelo.OMNI_FLASH, Tier.FREE, "8s") == 12
    assert custo_de(Modelo.OMNI_FLASH, Tier.PRO, "8s") == 12
    assert custo_de(Modelo.OMNI_FLASH, Tier.ULTRA, "8s") == 6


def test_omni_flash_de_quatro_segundos_custa_sete():
    # ANUNCIADO pelo agente conversacional do Flow na confirmacao do pedido. NAO foi
    # medido por saldo, ao contrario dos 12 do clipe de 8s.
    assert custo_de(Modelo.OMNI_FLASH, Tier.FREE, "4s") == 7
    assert custo_de(Modelo.OMNI_FLASH, Tier.PRO, "4s") == 7


def test_quatro_segundos_custa_menos_que_oito_em_todo_modelo_de_video():
    # e o numero que justifica a mudanca inteira: fixar 8s pagava a mais em todo job
    for m in VIDEO:
        for t in Tier:
            assert custo_de(m, t, "4s") < custo_de(m, t, "8s")


def test_custo_nunca_cai_quando_a_duracao_cresce():
    # nao da para exigir crescimento ESTRITO: nos tiers baratos o arredondamento
    # empata dois degraus vizinhos (Veo Lite ultra: 3, 5, 5, 7)
    for m in VIDEO:
        for t in Tier:
            valores = [custo_de(m, t, d) for d in DURACOES]
            assert valores == sorted(valores)


def test_duracao_omitida_vale_o_padrao_do_dominio():
    # o default nao e o 8s da tabela: e o 4s que todo job nasce pedindo. Um default
    # diferente do padrao do dominio faria a reserva de um job comum sair errada.
    assert DURACAO_PADRAO == "4s"
    assert custo_de(Modelo.OMNI_FLASH, Tier.FREE) == 7
    assert custo_de(Modelo.OMNI_FLASH, Tier.FREE) == custo_de(
        Modelo.OMNI_FLASH, Tier.FREE, DURACAO_PADRAO)


def test_imagem_ignora_a_duracao():
    # imagem nao tem duracao. O parametro existe para as chamadas de video; passa-lo
    # numa de imagem nao pode mudar preco nenhum, hoje que e zero nem no dia em que
    # deixar de ser.
    for d in DURACOES:
        assert custo_de(Modelo.NANO_BANANA, Tier.FREE, d) == 0


def test_duracao_fora_da_lista_e_erro_de_configuracao():
    # mesma familia de `ModeloSemCusto`: chutar um preco para "5s" mandaria reservar
    # um numero inventado, e o erro so apareceria como conta seca depois
    with pytest.raises(DuracaoSemCusto):
        custo_de(Modelo.OMNI_FLASH, Tier.FREE, "5s")


# --- teto do free ---

def test_quatro_imagens_ainda_cabem_no_teto_do_free():
    # imagem MEDIDA em zero: 4 x 0 = 0 contra teto de 50 — cabe, mas o calculo tem
    # que ser explicito, porque o dia em que a imagem passar a custar nao avisa
    assert cabe_no_free(Modelo.NANO_BANANA, quantidade=4) is True


def test_lote_que_estoura_o_teto_diario_nao_cabe_no_free():
    # 3 x 20 = 60 contra teto de 50, no clipe de 8s
    assert cabe_no_free(Modelo.VEO_FAST, quantidade=3, duracao="8s") is False


def test_o_mesmo_lote_cabe_no_free_quando_o_clipe_e_de_quatro_segundos():
    # 3 x 12 = 36 contra os mesmos 50. A duracao tem que entrar aqui tambem, senao o
    # teto do free recusaria um lote que o Flow aceitaria.
    assert cabe_no_free(Modelo.VEO_FAST, quantidade=3, duracao="4s") is True
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_custos.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.nucleo.custos'`

- [ ] **Step 3: Implementar**

`flowpool/nucleo/custos.py`:

```python
"""Custo em creditos por (modelo, tier, duracao), e o teto diario da conta gratuita.

Os numeros vem do suporte do Flow, de medicao publica e de duas medicoes proprias
(ver o spec). Nada aqui e preco final: e uma ESTIMATIVA para reservar saldo antes de
despachar — quem manda e a leitura real, reconciliada em `nucleo/credito.py`.

Video tem tres dimensoes (modelo, tier, duracao); imagem tem duas. Duracao nao entra
em imagem porque nao existe grupo de duracao no painel do Flow em modo imagem.
"""

from __future__ import annotations

from flowpool.nucleo.erros import DuracaoSemCusto, ModeloSemCusto
from flowpool.nucleo.tipos import DURACAO_PADRAO, Modelo, Tier

TETO_DIARIO_FREE = 50

# Duracao de referencia da tabela de video: e nela que esta o unico numero MEDIDO por
# saldo. Todas as outras saem daqui reescaladas por `_ESCALA_DURACAO`.
_DURACAO_BASE = "8s"

# Custo de UM clipe de 8s, por (modelo, tier).
_CUSTO_VIDEO: dict[tuple[Modelo, Tier], int] = {
    # MEDIDO 2026-08-20, conta FREE, 720p / 8s / 9:16 / x1: saldo 50 -> 38.
    (Modelo.OMNI_FLASH, Tier.FREE): 12,
    (Modelo.OMNI_FLASH, Tier.PRO): 12,
    (Modelo.OMNI_FLASH, Tier.ULTRA): 6,   # EXTRAPOLADO: Ultra paga metade em Lite/Fast
    # ESTIMATIVA — nenhum Veo foi medido ainda, em duracao nenhuma.
    (Modelo.VEO_LITE, Tier.FREE): 10,
    (Modelo.VEO_LITE, Tier.PRO): 10,
    (Modelo.VEO_LITE, Tier.ULTRA): 5,
    (Modelo.VEO_FAST, Tier.FREE): 20,
    (Modelo.VEO_FAST, Tier.PRO): 20,
    (Modelo.VEO_FAST, Tier.ULTRA): 10,
    (Modelo.VEO_QUALITY, Tier.FREE): 100,
    (Modelo.VEO_QUALITY, Tier.PRO): 100,
    (Modelo.VEO_QUALITY, Tier.ULTRA): 100,
}

# Imagem. Duracao nao entra: o painel nao tem o grupo, e o transporte nao o toca.
# MEDIDO 2026-08-20: imagem custa ZERO. Saldo 50 antes e 50 depois, e o Flow nem pede
# confirmacao. Desempata a divergencia que o spec carregava (1-2 vs zero).
_CUSTO_IMAGEM: dict[tuple[Modelo, Tier], int] = {
    (Modelo.NANO_BANANA, Tier.FREE): 0,
    (Modelo.NANO_BANANA, Tier.PRO): 0,
    (Modelo.NANO_BANANA, Tier.ULTRA): 0,
}

# Escala do custo por duracao, na moeda do Omni Flash free/pro — que e onde estao os
# unicos dois pontos conhecidos:
#
#   8s = 12   MEDIDO 2026-08-20 por saldo (50 -> 38), 720p / 9:16 / x1.
#   4s =  7   ANUNCIADO pelo agente conversacional do Flow na confirmacao do pedido.
#             NAO foi medido por saldo — e um numero que o Flow disse, nao um que o
#             app viu sair da conta.
#
# 6s e 10s sao EXTRAPOLADOS: a reta que passa pelos dois pontos e 2 + 1,25 por segundo,
# o que da 9,5 e 14,5, arredondados para cima em 10 e 15. O 15 tem um apoio a mais —
# e o outro numero que o agente ja anunciou para esta familia de pedido.
#
# Os tres Veo herdam a MESMA forma a partir do seu proprio 8s, e ali a extrapolacao e
# dupla: nenhum Veo foi medido em duracao nenhuma.
#
# Arredondamento para CIMA, sempre. Reserva curta demais aprova uma conta que nao paga
# o job: o Flow recusa, o job volta pra fila e a conta e marcada seca por erro de
# calculo. Reserva folgada so custa um pouco de saldo ate a reconciliacao — que e
# quem, no fim, corrige tudo isto.
_ESCALA_DURACAO = {"4s": 7, "6s": 10, "8s": 12, "10s": 15}


def _escalar(custo_base: int, duracao: str) -> int:
    """Custo de `duracao` a partir do custo de 8s. Arredonda para cima."""
    try:
        fator = _ESCALA_DURACAO[duracao]
    except KeyError:
        raise DuracaoSemCusto(
            f"sem custo cadastrado para duracao {duracao!r}; "
            f"use uma de {list(_ESCALA_DURACAO)}") from None
    base = _ESCALA_DURACAO[_DURACAO_BASE]
    return -(-custo_base * fator // base)   # divisao inteira com teto


def custo_de(modelo: Modelo, tier: Tier, duracao: str | None = None) -> int:
    """Custo estimado em creditos de UMA geracao.

    `duracao` so tem efeito nos modelos de video. `None` vale `DURACAO_PADRAO`, que e
    o mesmo padrao com que todo `Job` nasce — nao o 8s da tabela. Um default divergente
    do padrao do dominio faria a reserva do job comum sair errada em silencio.

    O default existe tambem para as chamadas de IMAGEM, que nao tem duracao nenhuma
    para informar.
    """
    if (modelo, tier) in _CUSTO_IMAGEM:
        return _CUSTO_IMAGEM[(modelo, tier)]
    try:
        base = _CUSTO_VIDEO[(modelo, tier)]
    except KeyError:
        raise ModeloSemCusto(f"sem custo cadastrado para {modelo.value} em {tier.value}") from None
    return _escalar(base, duracao or DURACAO_PADRAO)


def cabe_no_free(modelo: Modelo, quantidade: int = 1,
                 duracao: str | None = None) -> bool:
    """Se uma conta gratuita consegue, no melhor dia possivel, rodar este pedido.

    Quantidade e duracao entram no calculo porque as duas mexem no custo total. Um
    lote que estoura o teto diario nunca vai caber em conta gratuita, e descobrir isso
    so na hora de gerar custaria uma geracao recusada. A duracao entra pelo mesmo
    motivo, na direcao oposta: sem ela, o teto recusaria um lote de clipes curtos que
    o Flow aceitaria de bom grado.
    """
    return custo_de(modelo, Tier.FREE, duracao) * quantidade <= TETO_DIARIO_FREE
```

Vídeo e imagem moram em **dicionários separados** de propósito: vídeo tem a dimensão
duração, imagem não tem — não existe grupo de duração no painel em modo imagem. A
estrutura da tabela diz isso sozinha, em vez de depender de um `if` escondido.

A escala por duração é ancorada em dois pontos e **extrapolada** no resto, e o código
marca qual é qual. O arredondamento é sempre **para cima**: reserva curta demais aprova
uma conta que não paga o job, o Flow recusa, o job volta pra fila e a conta é marcada
seca por erro de cálculo. Reserva folgada só custa um pouco de saldo até a
reconciliação. `test_tabela_cobre_todos_os_pares` garante que nenhum modelo novo escape
sem custo, e `test_tabela_cobre_todas_as_duracoes_dos_modelos_de_video` faz o mesmo pelas
quatro durações.

`cabe_no_free` recebe `quantidade` **e** `duracao` porque o teto diário é sobre o
**total**: quatro imagens ainda cabem em 50 créditos, e três clipes de 4s (36) cabem
onde três de 8s (60) não caberiam. Isso precisa ser conta feita, não sorte.

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_custos.py -q`
Expected: `30 passed`

- [ ] **Step 5: Commit**

```bash
git add flowpool/nucleo/custos.py tests/test_custos.py
git commit -m "feat: tabela de custo por modelo e tier"
```

---

### Task 3: Escolha de conta

> **Nota de revisão (Task 15) — duração.** `apta` e `escolher` ganharam o parâmetro `duracao`, que segue exatamente o caminho de `quantidade` e existe pelo mesmo motivo: as duas mexem no custo **total** do pedido, e uma escolha que não sabe o custo total aprova uma conta que o Flow depois recusa. O listing do `escolha.py` abaixo já está na forma final. O de `tests/test_escolha.py` **não**: ele é o da primeira passada (imagem ainda a 2 créditos, corrigida na Task 11) e não traz os 4 casos de duração — os principais são `test_conta_que_paga_um_clipe_de_4s_nao_e_escolhida_para_o_de_8s` e `test_apta_olha_a_duracao`, descritos na Task 15.

**Files:**
- Create: `flowpool/nucleo/escolha.py`
- Test: `tests/test_escolha.py`

Esta é a peça central e é função pura — merece teste de tabela cobrindo os casos reais do pool do Davi.

Os enums são comparados com `==`, nunca com `is`. `Tier` e `SaudeSessao` herdam de `str, Enum`: se em qualquer caminho futuro um valor chegar como string crua vinda do SQLite, o `is` falha em silêncio — e falha nas duas direções erradas, descartando conta viva ou aprovando conta grátis para Veo Quality, que é a única regra que o spec chama de intransponível. O `==` custa zero e elimina o modo de falha.

A assinatura é `escolher(contas, modelo, agora, quantidade=1)` e `apta(c, modelo, agora, quantidade=1)`: **o relógio entra por parâmetro.** Chamar `datetime.now()` aqui dentro tiraria a pureza justamente da peça que precisa ser testável sem navegador e sem congelar o relógio do processo — e é o núcleo inteiro que se apoia nessa garantia.

O `agora` serve para uma decisão só, mas é uma decisão que não dá para adiar: conta com `seca_ate` no futuro é descartada. Sem esse descarte, a conta que o Flow acabou de recusar volta a ser escolhida na iteração seguinte e o motor entra em laço quente (ver Task 1 e Task 7).

A `quantidade` entra pelo mesmo motivo de pureza, e resolve um problema que só aparece com lote: **ela multiplica o custo.** Se apenas a reserva (Task 4) multiplicasse, `escolher` aprovaria uma conta que comporta *uma* geração para um job de quatro — o Flow recusaria na hora de gerar, o job voltaria para a fila e a conta seria marcada seca por um problema que era de cálculo, não de crédito. Por isso o filtro de saldo aqui é sobre o **total**, e `cabe_no_free` também recebe a quantidade.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_escolha.py`:

```python
from datetime import datetime, timedelta, timezone

from flowpool.nucleo.escolha import apta, escolher
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


def test_conta_que_paga_uma_imagem_nao_e_escolhida_para_um_lote_de_quatro():
    # Se so a reserva multiplicasse, o app escolheria esta conta, tentaria gerar quatro,
    # o Flow recusaria, o job voltaria pra fila e a conta seria marcada seca — por um
    # problema que era de calculo, nao de credito.
    magra = conta(1, Tier.FREE, 2)   # paga exatamente uma imagem
    assert escolher([magra], Modelo.NANO_BANANA, AGORA) is not None
    assert escolher([magra], Modelo.NANO_BANANA, AGORA, quantidade=4) is None


def test_lote_grande_pula_a_free_e_cai_na_paga():
    # com quantidade 1 a free vence pela regra do credito que expira antes;
    # com quantidade 4 ela nao comporta o total e sai da escolha
    free = conta(1, Tier.FREE, 6)    # paga tres imagens, nao quatro
    pro = conta(2, Tier.PRO, 1000)
    assert escolher([free, pro], Modelo.NANO_BANANA, AGORA).id == 1
    assert escolher([free, pro], Modelo.NANO_BANANA, AGORA, quantidade=4).id == 2


def test_apta_avalia_o_custo_total_do_lote():
    c = conta(1, Tier.FREE, 6)
    assert apta(c, Modelo.NANO_BANANA, AGORA, quantidade=3) is True
    assert apta(c, Modelo.NANO_BANANA, AGORA, quantidade=4) is False


def test_omni_flash_cabe_em_conta_gratis():
    assert escolher([conta(1, Tier.FREE, 20)], Modelo.OMNI_FLASH, AGORA).id == 1
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


def apta(c: Conta, modelo: Modelo, agora: datetime, quantidade: int = 1,
         duracao: str | None = None) -> bool:
    # Comparacao por valor, nao por identidade: `Tier` e `SaudeSessao` herdam de
    # `str, Enum`, e um valor cru vindo do SQLite quebraria `is` em silencio.
    if not c.ativa or c.saude != SaudeSessao.VIVA:
        return False
    # Conta seca: o Flow ja recusou gerar apesar do saldo aparente. Insistir antes do
    # prazo devolve o job a fila na hora e o motor vira laco quente.
    if c.seca_ate is not None and c.seca_ate > agora:
        return False
    if c.tier == Tier.FREE and not cabe_no_free(modelo, quantidade, duracao):
        return False
    # Custo TOTAL do pedido, nao de uma geracao. Se so a reserva multiplicasse, o app
    # escolheria uma conta que comporta uma geracao e tentaria gerar quatro: o Flow
    # recusa, o job volta pra fila e a conta e marcada seca por um problema que era de
    # calculo.
    return custo_de(modelo, c.tier, duracao) * quantidade <= c.saldo


def escolher(contas: Sequence[Conta], modelo: Modelo, agora: datetime,
             quantidade: int = 1, duracao: str | None = None) -> Conta | None:
    """Melhor conta para rodar `quantidade` geracoes de `modelo` em `agora`.

    None se nenhuma serve.
    """
    aptas = [c for c in contas if apta(c, modelo, agora, quantidade, duracao)]
    if not aptas:
        return None
    return max(aptas, key=lambda c: (_ORDEM_TIER[c.tier], c.saldo, -c.id))
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_escolha.py -q`
Expected: `27 passed`

- [ ] **Step 5: Commit**

```bash
git add flowpool/nucleo/escolha.py tests/test_escolha.py
git commit -m "feat: politica de escolha de conta"
```

---

### Task 4: Contabilidade de crédito

> **Nota de revisão (Task 15) — duração.** `reservar` ganhou o parâmetro `duracao`. Reservar sempre o clipe de 8s tirava do saldo local crédito que o Flow não ia cobrar, e a conta saía do pool antes da hora. O listing do `credito.py` abaixo já está na forma final; o de `tests/test_credito.py` é o da primeira passada, sem os 3 casos de duração da Task 15.

**Files:**
- Create: `flowpool/nucleo/credito.py`
- Usa: `flowpool/nucleo/erros.py` (criado na Task 1)
- Test: `tests/test_credito.py`, `tests/test_pool.py`

O ponto que o spec insiste: descontar o previsto ao despachar (para dois jobs simultâneos não estourarem o mesmo saldo) e reconciliar com a leitura real ao terminar.

Três coisas que o desenho ingênuo erra e esta task corrige de saída:

1. **Reserva recusa, não zera.** Chegar em `reservar` sem saldo significa que quem chamou ignorou `escolher`; zerar silenciosamente apagaria crédito real que ainda daria outra geração mais barata.
2. **Existe estorno (`liberar`).** Sem ele, três timeouts de rede numa conta de 50 créditos zeram o saldo local com os 50 créditos intactos no Flow — e a conta some do pool para sempre, porque ninguém despacha job para conta que parece seca.
3. **A divergência é medida com sinal.** Sobrar crédito é notícia boa (é o que responde se a imagem custa ou sai de graça); só faltar crédito derruba a confiança. E o reset diário da conta grátis não é divergência nenhuma: é a conta voltando ao teto.
4. **A reserva é do lote inteiro.** `reservar(conta, modelo, quantidade=1)` desconta `custo_de(...) * quantidade`. Reservar só uma geração quando o pedido tem quatro deixaria o saldo local acreditando em crédito que o Flow já cobrou — e a escolha seguinte mandaria job para uma conta mais magra do que o app pensa. O par disso está na Task 3: `escolher` avalia o mesmo total, senão o app escolhe uma conta que comporta uma geração e tenta gerar quatro.

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


# --- quantidade ---

def test_reservar_multiplica_pela_quantidade():
    # quatro imagens custam quatro vezes uma; reservar so uma deixaria o Flow cobrar
    # o que o saldo local nao previu
    c = conta(50)
    assert reservar(c, Modelo.NANO_BANANA, quantidade=4) == 8
    assert c.saldo == 42


def test_reservar_recusa_lote_que_o_saldo_nao_paga_inteiro():
    # a conta paga UMA geracao, nao o lote. Reservar so uma faria o app despachar um
    # pedido que o Flow recusa, e a conta seria marcada seca por erro de calculo.
    c = conta(30)
    with pytest.raises(SaldoInsuficiente):
        reservar(c, Modelo.VEO_FAST, quantidade=2)
    assert c.saldo == 30


def test_reservar_sem_quantidade_continua_reservando_uma():
    c = conta(50)
    assert reservar(c, Modelo.VEO_FAST) == 20
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


def reservar(conta: Conta, modelo: Modelo, quantidade: int = 1,
             duracao: str | None = None) -> int:
    """Desconta o custo previsto antes de despachar. Devolve o custo reservado.

    Recusa em vez de zerar: chegar aqui sem saldo significa que quem chamou ignorou
    `escolher`, e silenciar isso apagaria credito real que ainda daria outra geracao.

    O custo e o de UMA geracao vezes a quantidade pedida. Reservar so uma quando o
    pedido tem quatro deixaria o saldo local acreditando em credito que o Flow ja
    cobrou — e a escolha seguinte mandaria job para uma conta mais magra do que o app
    pensa.
    """
    custo = custo_de(modelo, conta.tier, duracao) * quantidade
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
Expected: `21 passed`

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
Expected: `122 passed` (20 tipos + 23 proxy + 30 custos + 25 escolha + 21 credito + 3 pool)

- [ ] **Step 8: Commit**

```bash
git add flowpool/nucleo/erros.py flowpool/nucleo/credito.py tests/test_credito.py tests/test_pool.py
git commit -m "feat: reserva, estorno e reconciliacao de credito"
```

---

### Task 5: Esquema e repositório SQLite

> **Nota de revisão (Task 15) — duração.** A tabela `job` ganhou `duracao TEXT NOT NULL DEFAULT '4s'`, lida e gravada pelo repo. Como em `proporcao` e `quantidade`, **não há migração**: o esquema é `CREATE TABLE IF NOT EXISTS`, e um `flowpool.db` anterior à coluna precisa ser recriado à mão. Os listings de código abaixo já estão na forma final; `tests/test_repo.py` ganhou 2 casos de ida e volta descritos na Task 15.

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

`conta.proxy` entra como `TEXT` **anulável, sem default** — e a ausência de `NOT NULL` é a decisão. Não ter proxy é o estado normal hoje: o campo fica vazio até haver IP contratado, e nesse meio-tempo o Davi destrava o Flow com uma VPN de sistema, que o app nem enxerga. Um `NOT NULL DEFAULT ''` transformaria "sem proxy" em string vazia e obrigaria cada leitor a lembrar de tratar os dois valores como a mesma coisa; `NULL` diz sozinho o que quer dizer, e `nucleo.proxy.analisar(None)` já devolve `None` sem reclamar.

Ele é gravado por `salvar_conta` e lido por `_conta`, e **não** entra em `criar_conta`: quem cria conta pela API é o `POST /contas` (Task 10), que precisa do proxy antes de abrir o Chrome de login e o aplica lá — a coluna é só onde o valor descansa entre uma sessão e outra. Se ele não sobrevivesse ao restart, a conta voltaria a sair pelo IP de casa na primeira geração depois de o serviço subir, sem nada no log dizendo isso.

`job.proporcao` e `job.quantidade` entram como `TEXT NOT NULL DEFAULT '9:16'` e `INTEGER NOT NULL DEFAULT 1`. Os defaults vivem em dois lugares de propósito — na dataclass (Task 1) e na coluna — porque o job criado direto pelo repo, sem passar pela API, também precisa nascer vertical e com uma geração só. Um `NULL` aqui viraria `quantidade=None` multiplicando o custo, e o erro apareceria como `TypeError` dentro de `reservar`, longe da causa.

Nenhum dos dois entra em `salvar_job`: são do pedido, imutáveis depois da criação, como `prompt` e `modo`. `salvar_job` grava só o que muda ao longo da vida do job (estado, conta, id remoto, arquivo, tentativas, custo, erro, reavaliação).

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


def test_proxy_da_conta_sobrevive_ao_banco(repo):
    # cada conta sai por um IP proprio para o Google nao correlacionar as contas; um
    # proxy que nao sobrevive ao restart faria a conta voltar a sair pelo IP de casa
    id_ = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    assert repo.conta(id_).proxy is None
    c = repo.conta(id_)
    c.proxy = "http://davi:segredo@1.2.3.4:8080"
    repo.salvar_conta(c)
    assert repo.conta(id_).proxy == "http://davi:segredo@1.2.3.4:8080"


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


def test_job_gravado_sem_proporcao_sai_vertical(repo):
    # o default e do canal do Davi (TikTok), e precisa valer tambem para quem cria job
    # direto pelo repo, sem passar pela API
    id_ = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH,
                             prompt="p", destino="d", nome="n"))
    j = repo.job(id_)
    assert j.proporcao == "9:16"
    assert j.quantidade == 1


def test_proporcao_e_quantidade_sobrevivem_ao_banco(repo):
    id_ = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="p", destino="d", nome="n",
                             proporcao="16:9", quantidade=4))
    j = repo.job(id_)
    assert j.proporcao == "16:9"
    assert j.quantidade == 4


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
    ativa           INTEGER NOT NULL DEFAULT 1,
    proxy           TEXT
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
    proporcao      TEXT    NOT NULL DEFAULT '9:16',
    quantidade     INTEGER NOT NULL DEFAULT 1,
    duracao        TEXT    NOT NULL DEFAULT '4s',
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
                   saldo_confiavel=?, reset_em=?, seca_ate=?, saude=?, falhas=?,
                   ativa=?, proxy=?
               WHERE id=?""",
            (c.tier.value, c.perfil, c.saldo, _txt(c.saldo_lido_em),
             int(c.saldo_confiavel), _txt(c.reset_em), _txt(c.seca_ate),
             c.saude.value, c.falhas, int(c.ativa), c.proxy, c.id),
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
            falhas=r["falhas"], ativa=bool(r["ativa"]), proxy=r["proxy"],
        )

    # ---------- jobs ----------

    def criar_job(self, j: Job) -> int:
        agora = _txt(datetime.now(timezone.utc))
        cur = self.con.execute(
            """INSERT INTO job (modo, modelo, prompt, destino, nome, produto,
                   referencias, frame_inicial, frame_final, proporcao, quantidade,
                   prioridade, estado, custo_estimado, reavaliar_em, criado_em,
                   atualizado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (j.modo.value, j.modelo.value, j.prompt, j.destino, j.nome, j.produto,
             json.dumps(j.referencias), j.frame_inicial, j.frame_final, j.proporcao,
             j.quantidade, j.prioridade, j.estado.value, j.custo_estimado,
             _txt(j.reavaliar_em), agora, agora),
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
            proporcao=r["proporcao"], quantidade=r["quantidade"],
            duracao=r["duracao"],
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
Expected: `25 passed`

- [ ] **Step 6: Commit**

```bash
git add flowpool/dados tests/test_repo.py
git commit -m "feat: esquema sqlite e repositorio"
```

---

### Task 6: Interface de transporte e transporte falso

> **Nota de revisão (Task 15) — duração.** `Pedido` ganhou `duracao: str = DURACAO_PADRAO`, e o `TransporteFalso` passou a cobrar por (modelo, tier, **duração**) — se ele não cobrasse igual ao Flow, o motor passaria no teste com uma contabilidade que o real desmente. Os listings de código abaixo já estão na forma final; `tests/test_transporte_falso.py` ganhou 2 casos e teve o helper `pedido()` fixado em `8s`, para os testes que conferem número de crédito dizerem de qual clipe falam.

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

`Pedido` carrega **`proporcao` e `quantidade`** além do prompt e das referências. São configurações do painel de geração do Flow (`tune Configurações`), e é aqui que elas atravessam a fronteira: sem isso o transporte real geraria sempre no default da UI e o pedido do usuário seria ignorado em silêncio — o pior tipo de bug neste app, porque a saída existe e parece certa.

Pelo mesmo motivo, `gerar` do falso cobra `custo_de(...) * quantidade`. O Flow cobra por geração; um falso que cobrasse sempre uma deixaria o motor passar no teste com uma contabilidade que o real desmente.

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


async def test_lote_de_quatro_debita_quatro_vezes():
    # o Flow cobra por geracao; o falso precisa cobrar igual, senao o motor passa no
    # teste com uma contabilidade que o real desmente
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    await t.gerar(s, Pedido(modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA, prompt="p",
                            quantidade=4))
    assert t.saldos[1] == 42


async def test_lote_que_nao_cabe_no_saldo_e_recusado():
    t = TransporteFalso(saldos={1: 30})
    s = await t.abrir_sessao(conta(30))
    with pytest.raises(SaldoInsuficiente):
        await t.gerar(s, Pedido(modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p",
                                quantidade=2))


async def test_pedido_carrega_proporcao_e_quantidade():
    # sao configuracoes do painel do Flow: sem atravessar a fronteira, o transporte
    # real geraria sempre no default da UI e o pedido do usuario seria ignorado
    p = Pedido(modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p")
    assert p.proporcao == "9:16"
    assert p.quantidade == 1


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
from flowpool.nucleo.tipos import PROPORCAO_PADRAO, Conta, Modelo, Modo

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
    # Duas configuracoes do painel de geracao do Flow. Precisam atravessar a fronteira
    # ou o transporte real geraria sempre no default da UI e o pedido do usuario seria
    # ignorado em silencio — que e o pior tipo de bug aqui, porque a saida existe e
    # parece certa.
    proporcao: str = PROPORCAO_PADRAO
    quantidade: int = 1
    # So vale em video: em imagem o painel nao tem grupo de duracao, e o transporte
    # nao toca nele.
    duracao: str = DURACAO_PADRAO


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
        # o Flow cobra por geracao: um lote de quatro custa quatro vezes
        custo = custo_de(p.modelo, s.conta.tier, p.duracao) * p.quantidade
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
Expected: `19 passed`

- [ ] **Step 6: Commit**

```bash
git add flowpool/transporte/base.py flowpool/transporte/falso.py tests/test_transporte_falso.py
git commit -m "feat: interface de transporte e implementacao falsa"
```

---

### Task 7: Orquestrador

> **Nota de revisão (Task 16) — semeadura de saldo.** `passo()` ganhou um segundo caminho: quando `_conta_para` devolve `None`, o motor tenta ler o saldo de uma conta viva sem `saldo_confiavel` (`_semear_uma_conta` → `semear_saldo`) e **reescolhe** antes de adiar o job. Sem isso o pool nascia morto — ver a Task 16 para o impasse inteiro. Os listings de código abaixo são anteriores a essa mudança nesse ponto; tudo o mais continua valendo.

> **Nota de revisão (Task 15) — duração.** `_conta_para` passa `job.duracao` a `apta` e `escolher`, `_executar` a passa a `reservar`, e `_pedido` a leva ao transporte. É o mesmo trio de pontos por onde `quantidade` já passava. Os listings de código abaixo já estão na forma final; `tests/test_orquestrador.py` ganhou 4 casos e teve o helper `job_fast()` fixado em `8s`, pelo mesmo motivo do task anterior.

**Files:**
- Create: `flowpool/motor/orquestrador.py`
- Test: `tests/test_orquestrador.py`

O coração do motor: puxa job, escolhe conta, reserva crédito, despacha, reconcilia, trata falha. Testado inteiro contra o transporte falso.

A regra que não pode escapar daqui: **toda reserva que não virou geração tem que ser estornada.** O orquestrador guarda em `job.custo_estimado` quanto reservou, chama `liberar` em todo caminho de falha anterior à confirmação da geração, e zera o campo depois de estornar (para não estornar duas vezes). Sem isso, três timeouts de rede numa conta de 50 créditos zeram o saldo local com os 50 créditos intactos no Flow, e a conta some do pool para sempre. No caminho de sucesso não há estorno: o crédito foi gasto de verdade, e a reconciliação com o saldo lido fecha a conta.

Isso vale para **todos** os ramos de falha, `SessaoMorta` inclusive. A sessão não morre só em `abrir_sessao`: no transporte real ela cai durante `gerar`, `consultar` ou `baixar`. Como `SessaoMorta` é o primeiro `except`, sem estorno ali o caso nunca chega ao ramo genérico e o crédito reservado some do saldo local para sempre. `_estornar` é no-op depois que o Flow aceitou (`id_remoto is not None`), então isso não infla saldo — e devolve `True` só quando crédito voltou de fato, que é como o ramo sabe se ainda precisa persistir a conta antes de `marcar_sessao`.

Nove outras decisões desta task, cada uma com um modo de falha real por trás:

**A quantidade atravessa a escolha, não só a reserva.** `passo()` passa `job.quantidade` para `escolher`, `_conta_para` a passa para `apta`, e `_executar` a passa para `reservar`. Se só a reserva multiplicasse, o motor escolheria uma conta que comporta *uma* geração para um job de quatro: o Flow recusaria, o job voltaria para a fila e a conta seria marcada seca — por um problema que era de cálculo, não de crédito. `job.custo_estimado` guarda o **total** reservado, e por isso o estorno continua correto sem mudança nenhuma.

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
from flowpool.nucleo.custos import custo_de
from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.erros import SaldoInsuficiente
from flowpool.nucleo.tipos import EstadoJob, Job, Modelo, Modo, SaudeSessao, Tier
from flowpool.transporte.base import ErroTransporte
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


async def test_lote_de_imagens_reserva_o_custo_multiplicado(repo, tmp_path):
    # 4 x 2 = 8. Reservar so 2 deixaria o saldo local acreditando em credito que o
    # Flow ja cobrou, e a proxima escolha mandaria job pra uma conta mais magra do
    # que o app pensa.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="p", destino="imgs", nome="i1", quantidade=4))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await orq.passo()

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO
    assert j.custo_estimado == 4 * custo_de(Modelo.NANO_BANANA, Tier.FREE)


async def test_lote_pula_a_conta_que_so_comporta_uma_geracao(repo, tmp_path):
    # a free vence pela regra do credito que expira antes, mas nao paga o lote inteiro.
    # Se `passo` nao passasse a quantidade para `escolher`, ela seria escolhida, o Flow
    # recusaria e a conta seria marcada seca por erro de calculo.
    magra = repo.criar_conta("magra@b.com", Tier.FREE, "p/1")
    c = repo.conta(magra); c.saldo = 6; repo.salvar_conta(c)
    gorda = repo.criar_conta("gorda@b.com", Tier.PRO, "p/2")
    c = repo.conta(gorda); c.saldo = 1000; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="p", destino="imgs", nome="i1", quantidade=4))

    orq = Orquestrador(repo, TransporteFalso(saldos={magra: 6, gorda: 1000}),
                       raiz=tmp_path)
    await orq.passo()

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO
    assert j.conta_id == gorda


async def test_pedido_leva_proporcao_e_quantidade_ao_transporte(repo, tmp_path):
    # sao configuracoes do painel do Flow: se nao atravessarem a fronteira, o
    # transporte real gera no default da UI e o pedido do usuario e ignorado
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA, prompt="p",
                       destino="imgs", nome="i1", proporcao="1:1", quantidade=3))

    vistos = []
    t = TransporteFalso(saldos={cid: 50})
    gerar_original = t.gerar

    async def espiar(s, p):
        vistos.append(p)
        return await gerar_original(s, p)

    t.gerar = espiar
    await Orquestrador(repo, t, raiz=tmp_path).passo()

    assert vistos[0].proporcao == "1:1"
    assert vistos[0].quantidade == 3


async def test_proxy_morto_nao_marca_a_conta_expirada_nem_seca(repo, tmp_path):
    # proxy fora do ar e falha de INFRAESTRUTURA, nao da conta. Marcar `expirada`
    # pediria um login manual que ninguem precisa fazer; marcar seca tiraria do pool
    # uma conta que esta boa e cheia de credito. Nos dois casos o pool encolheria por
    # um problema que se conserta trocando de IP, nao mexendo na conta.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid)
    c.saldo = 50
    c.proxy = "http://1.2.3.4:8080"
    repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 50})

    async def proxy_fora_do_ar(conta):
        raise ErroTransporte("net::ERR_PROXY_CONNECTION_FAILED em http://1.2.3.4:8080")

    t.abrir_sessao = proxy_fora_do_ar
    await Orquestrador(repo, t, raiz=tmp_path).passo()

    conta = repo.conta(cid)
    assert conta.saude is SaudeSessao.VIVA
    assert conta.seca_ate is None
    assert conta.saldo == 50          # a reserva voltou: o Flow nunca chegou a cobrar

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.tentativas == 1          # gasta tentativa, como toda falha de rede
    assert "PROXY" in j.erro
```

O último teste não pede código novo — o ramo genérico de `_executar` já faz a coisa
certa. Ele existe para **prender** esse comportamento, e a diferença entre ter e não ter
o teste aparece no dia em que alguém, caçando outro bug, acrescentar um
`conta.saude = EXPIRADA` ali. Proxy fora do ar é falha de infraestrutura: gasta
tentativa, como toda falha de rede, mas a conta continua boa. Marcá-la `expirada` pediria
um login manual que ninguém precisa fazer; marcá-la seca tiraria do pool uma conta cheia
de crédito. Ver *Fila e execução* no spec.

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
            if anterior is not None and apta(anterior, job.modelo, agora,
                                             job.quantidade):
                return anterior
        return escolher(contas, job.modelo, agora, job.quantidade, job.duracao)

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
                # o total do lote, nao o de uma geracao: e este numero que o estorno
                # devolve depois
                job.custo_estimado = reservar(conta, job.modelo, job.quantidade,
                                              job.duracao)
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
                      frame_final=job.frame_final, proporcao=job.proporcao,
                      quantidade=job.quantidade, duracao=job.duracao)

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
Expected: `38 passed`

- [ ] **Step 5: Rodar a suíte inteira**

Run: `.venv/Scripts/pytest -q`
Expected: `200 passed` (20 tipos + 23 proxy + 30 custos + 25 escolha + 21 credito + 3 pool + 27 repo + 19 transporte + 32 orquestrador)

- [ ] **Step 6: Commit**

```bash
git add flowpool/motor/orquestrador.py tests/test_orquestrador.py
git commit -m "feat: orquestrador de fila com troca de conta e retry"
```

---

### Task 8: API HTTP

> **Nota de revisão (Task 16) — o saldo nasce lido.** `POST /contas` e `POST /contas/{id}/revalidar` passaram a **ler o saldo** depois de validar a sessão, e a resposta traz o número real. Entrou também `POST /contas/{id}/saldo`, para reler sob demanda. Os listings abaixo são anteriores a isso.

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

`PedidoJob.modelo` passa a ser **opcional**, com padrão por modo: os três modos de vídeo herdam `omni-flash`, `imagem` herda `nano-banana`. O padrão de vídeo é Omni Flash porque é nele que o dropdown do Flow já abre — assumir `veo-fast` seria o app escolhendo por conta própria um modelo que custa diferente e sai diferente do que a interface faria. Modelo explícito continua valendo para tudo.

`PedidoJob.proporcao` tem padrão `"9:16"` e é validada contra `PROPORCOES`: valor fora da lista é **422**, porque o painel do Flow tem cinco botões e um sexto valor não teria onde clicar. O padrão vertical é do canal do Davi (TikTok) — gerar já em vertical evita o recorte manual —, e continua sobrescrevível por pedido.

`PedidoJob.quantidade` tem padrão `1`, mínimo `1`, e teto que depende do modo (`quantidade_maxima`, Task 1): **vídeo aceita só 1, imagem aceita até 4.** Pedido de vídeo com `quantidade > 1` é 422. O motivo é do usuário: take de vídeo é escolha dele, não sorteio, e cada take extra custa cheio; variação de imagem é barata e serve justamente para escolher entre elas. Recusar aqui é melhor do que gerar quatro vídeos que ninguém pediu — e é a mesma quantidade que multiplica o custo em `escolher` e `reservar` (Tasks 3 e 4).

`NovaConta` e `PatchConta` ganham `proxy: str | None = None`, validado por `nucleo.proxy.analisar` — URL que não dá para usar é **422**, não 500. A tradução é explícita porque `ProxyInvalido` não herda de `ValueError`, e só `ValueError` o Pydantic converte em 422: sem ela, um campo mal digitado viraria "erro interno" no painel.

No `PATCH`, `proxy` distingue dois sentidos que os outros campos não precisam distinguir: **campo ausente é "não mexe"; string vazia é "tira o proxy"**. Por isso o teste é `"proxy" in p.model_fields_set`, e não `p.proxy is not None` como em `ativa` e `tier` — com o teste de `None` não haveria como devolver uma conta ao estado sem proxy, que é justamente o estado em que todas nascem.

`RespostaConta.proxy` sai **mascarado** (`http://usuario:***@host:porta`), por `nucleo.proxy.mascarar`. A resposta vai para o painel e para log, e credencial de proxy não precisa passear por ali. A construção da resposta de conta vira uma função única (`_resposta_conta`) pelo mesmo motivo: repetida em cada rota, bastava uma delas esquecer o `mascarar` para a senha sair inteira no JSON — e ninguém notaria até ela aparecer num log.

O que a API **não** faz é recusar proxy com credencial. Ele é perfeitamente usável pelo Playwright, que é quem opera; quem não o aceita é o `chrome.exe` cru do cadastro, e é lá que a recusa acontece (Task 10), com a reserva ainda intacta e uma mensagem que diz o que fazer.

O lifespan chama `orq.recuperar()` **antes** de `rodar()`. É o único ponto do sistema que roda uma vez por boot, e é onde um job deixado em `rodando` por um crash anterior — com crédito reservado que nunca voltou — precisa ser resolvido antes de o motor puxar qualquer coisa nova.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from flowpool.api.app import criar_app
from flowpool.dados.repo import Repo
from flowpool.nucleo.tipos import Modelo, Tier
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


def test_patch_grava_o_proxy_da_conta(cliente):
    # cada conta sai por um IP proprio: e o que impede o Google de correlacionar as
    # contas entre si
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    r = c.patch(f"/contas/{cid}", json={"proxy": "http://1.2.3.4:8080"})
    assert r.status_code == 200
    assert repo.conta(cid).proxy == "http://1.2.3.4:8080"
    assert r.json()["proxy"] == "http://1.2.3.4:8080"


def test_patch_recusa_proxy_que_nao_da_para_usar(cliente):
    # falhar no cadastro e melhor do que abrir um navegador que sai pelo IP errado
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    r = c.patch(f"/contas/{cid}", json={"proxy": "1.2.3.4:8080"})
    assert r.status_code == 422
    assert repo.conta(cid).proxy is None


def test_patch_apaga_o_proxy_com_string_vazia(cliente):
    # sem isso nao ha como VOLTAR para "sem proxy" — `None` no PATCH significa
    # "nao mexe", entao tirar o proxy seria impossivel pela API
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    con = repo.conta(cid); con.proxy = "http://1.2.3.4:8080"; repo.salvar_conta(con)
    r = c.patch(f"/contas/{cid}", json={"proxy": ""})
    assert r.status_code == 200
    assert repo.conta(cid).proxy is None


def test_resposta_de_conta_mascara_a_senha_do_proxy(cliente):
    # a resposta vai para o painel e para log; credencial de proxy nao precisa
    # passear por ali
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    con = repo.conta(cid)
    con.proxy = "http://davi:segredo@1.2.3.4:8080"
    repo.salvar_conta(con)

    corpo = c.get("/contas").json()[0]
    assert corpo["proxy"] == "http://davi:***@1.2.3.4:8080"
    assert "segredo" not in c.get("/contas").text


def test_conta_sem_proxy_responde_null(cliente):
    c, repo = cliente
    repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    assert c.get("/contas").json()[0]["proxy"] is None


def test_nova_conta_aceita_proxy_e_recusa_url_torta():
    # a rota `POST /contas` entra no Task 10 (precisa abrir navegador); o esquema
    # existe antes dela para que o cadastro ja nasca no IP certo — conta que nasce no
    # IP de casa e depois muda para proxy denuncia exatamente o que o proxy evita
    from flowpool.api.esquemas import NovaConta

    assert NovaConta(email="a@b.com").proxy is None
    assert NovaConta(email="a@b.com", proxy="http://1.2.3.4:8080").proxy == \
        "http://1.2.3.4:8080"
    with pytest.raises(ValueError):
        NovaConta(email="a@b.com", proxy="ftp://1.2.3.4:8080")


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


# --- proporcao ---

def test_proporcao_padrao_e_vertical(cliente):
    # o canal do Davi e TikTok: gerar ja em 9:16 evita o recorte manual depois
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
    assert repo.job(jid).proporcao == "9:16"


def test_proporcao_explicita_e_respeitada(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n",
                                "proporcao": "16:9"}).json()["id"]
    assert repo.job(jid).proporcao == "16:9"


def test_proporcao_fora_da_lista_da_422(cliente):
    # o painel do Flow so tem cinco botoes; qualquer outro valor nao teria onde clicar
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                              "produto": "Moda", "nome": "n", "proporcao": "21:9"})
    assert r.status_code == 422


# --- quantidade ---

def test_quantidade_padrao_e_uma(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
    assert repo.job(jid).quantidade == 1


def test_video_com_quantidade_maior_que_um_da_422(cliente):
    # take de video e escolha do usuario, nao sorteio — e cada take extra custa cheio
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                              "produto": "Moda", "nome": "n", "quantidade": 2})
    assert r.status_code == 422


def test_imagem_aceita_lote_de_quatro(cliente):
    # variacao de imagem e barata e serve justamente para escolher
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                                "produto": "Moda", "nome": "n",
                                "quantidade": 4}).json()["id"]
    assert repo.job(jid).quantidade == 4


def test_imagem_acima_de_quatro_da_422(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                              "produto": "Moda", "nome": "n", "quantidade": 5})
    assert r.status_code == 422


def test_quantidade_zero_da_422(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                              "produto": "Moda", "nome": "n", "quantidade": 0})
    assert r.status_code == 422


# --- duracao ---

def test_duracao_padrao_e_de_quatro_segundos(cliente):
    # 4s custa 7 creditos e 8s custa 12: fixar 8s desperdicava 70% do credito por
    # video, e numa conta gratuita de 50/dia isso e a diferenca entre 4 e 7 videos
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
    assert repo.job(jid).duracao == "4s"


def test_duracao_explicita_e_respeitada(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n",
                                "duracao": "10s"}).json()["id"]
    assert repo.job(jid).duracao == "10s"


def test_duracao_fora_da_lista_da_422(cliente):
    # o painel do Flow so tem quatro botoes de duracao; "5s" nao teria onde clicar
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                              "produto": "Moda", "nome": "n", "duracao": "5s"})
    assert r.status_code == 422


def test_imagem_com_duracao_da_422(cliente):
    # nao existe grupo de duracao no painel em modo imagem: aceitar o campo seria
    # prometer uma configuracao que o transporte nao tem onde aplicar
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                              "produto": "Moda", "nome": "n", "duracao": "4s"})
    assert r.status_code == 422
    assert "duracao" in r.text or "duração" in r.text


def test_imagem_sem_duracao_passa(cliente):
    # o campo tem default no dominio ("4s"); se a recusa olhasse o VALOR em vez de o
    # campo ter vindo no corpo, todo pedido de imagem seria recusado
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                              "produto": "Moda", "nome": "n"})
    assert r.status_code == 201


def test_resposta_do_job_mostra_a_duracao(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                              "produto": "Moda", "nome": "n", "duracao": "6s"})
    assert r.json()["duracao"] == "6s"


# --- modelo padrao ---

def test_job_de_video_sem_modelo_sai_com_omni_flash(cliente):
    # e o que o dropdown de video do Flow ja mostra; pedir Veo sem dizer seria mentira
    c, repo = cliente
    r = c.post("/jobs", json={"modo": "t2v", "prompt": "p", "produto": "Moda", "nome": "n"})
    assert r.status_code == 201
    assert repo.job(r.json()["id"]).modelo is Modelo.OMNI_FLASH


def test_job_de_imagem_sem_modelo_sai_com_nano_banana(cliente):
    c, repo = cliente
    r = c.post("/jobs", json={"modo": "imagem", "prompt": "p", "produto": "Moda", "nome": "n"})
    assert r.status_code == 201
    assert repo.job(r.json()["id"]).modelo is Modelo.NANO_BANANA


def test_modelo_explicito_continua_valendo(cliente):
    c, repo = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-quality", "prompt": "p",
                              "produto": "Moda", "nome": "n"})
    assert repo.job(r.json()["id"]).modelo is Modelo.VEO_QUALITY
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.api.app'`

- [ ] **Step 3: Implementar os esquemas**

`flowpool/api/esquemas.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from flowpool.nucleo.erros import ProxyInvalido
from flowpool.nucleo.proxy import analisar
from flowpool.nucleo.tipos import (
    DURACAO_PADRAO, DURACOES, PROPORCAO_PADRAO, PROPORCOES, EstadoJob, Modelo, Modo,
    SaudeSessao, Tier, duracao_valida, quantidade_maxima,
)


def _proxy_usavel(v: str | None) -> str | None:
    """Recusa no cadastro a URL de proxy que nao da para usar.

    `analisar` levanta `ProxyInvalido`, que nao e `ValueError` — o Pydantic so
    converte `ValueError` em 422, entao sem esta traducao a URL torta subiria como 500
    e o painel diria "erro interno" para um campo mal digitado. Vazio continua valendo
    e significa "sem proxy": e assim que se VOLTA para o IP de casa (ou para a VPN de
    sistema, que o app nem enxerga).
    """
    if v is None:
        return None
    if not v.strip():
        return None
    try:
        analisar(v)
    except ProxyInvalido as e:
        raise ValueError(str(e)) from e
    return v.strip()

# O que cada modo usa quando o pedido nao diz o modelo. Video vai em Omni Flash porque
# e nele que o dropdown do Flow ja abre — assumir Veo seria escolher por conta propria
# um modelo que custa diferente e sai diferente.
_MODELO_PADRAO = {
    Modo.T2V: Modelo.OMNI_FLASH,
    Modo.FRAMES2V: Modelo.OMNI_FLASH,
    Modo.INGREDIENTS2V: Modelo.OMNI_FLASH,
    Modo.IMAGEM: Modelo.NANO_BANANA,
}


class PedidoJob(BaseModel):
    modo: Modo
    # Opcional: sem ele vale o padrao do modo (`_MODELO_PADRAO`).
    modelo: Modelo | None = None
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
    # Vertical por padrao: o canal e TikTok. Sobrescrevivel por pedido.
    proporcao: str = PROPORCAO_PADRAO
    # Saidas por geracao. MULTIPLICA O CUSTO — o teto depende do modo, ver
    # `quantidade_coerente_com_o_modo`.
    quantidade: int = Field(default=1, ge=1)
    # Tamanho do clipe. MUDA O CUSTO: 7 creditos em 4s contra 12 em 8s no Omni Flash.
    # So existe em video — ver `duracao_coerente_com_o_modo`.
    duracao: str = DURACAO_PADRAO
    prioridade: int = 0

    @field_validator("proporcao")
    @classmethod
    def proporcao_existe_no_painel(cls, v: str) -> str:
        """O painel do Flow tem cinco botoes; valor fora da lista nao teria onde clicar."""
        if v not in PROPORCOES:
            raise ValueError(f"proporcao {v!r} nao existe; use uma de {list(PROPORCOES)}")
        return v

    @model_validator(mode="after")
    def modelo_padrao_do_modo(self) -> PedidoJob:
        """Pedido sem modelo herda o padrao do modo em vez de ser recusado."""
        if self.modelo is None:
            self.modelo = _MODELO_PADRAO[self.modo]
        return self

    @model_validator(mode="after")
    def quantidade_coerente_com_o_modo(self) -> PedidoJob:
        """Video sai sempre 1; imagem aceita ate 4.

        Take de video e escolha do usuario, nao sorteio — e cada take extra custa
        cheio. Variacao de imagem e barata e serve justamente para escolher entre
        elas. Recusar aqui e melhor do que gerar quatro videos que ninguem pediu.
        """
        teto = quantidade_maxima(self.modo)
        if self.quantidade > teto:
            raise ValueError(
                f"modo {self.modo.value} aceita no maximo {teto} por job, "
                f"pedido {self.quantidade}")
        return self

    @model_validator(mode="after")
    def duracao_coerente_com_o_modo(self) -> PedidoJob:
        """Imagem nao tem duracao; video so aceita os quatro valores do painel.

        O teste do lado da imagem e pelo campo TER VINDO no corpo (`model_fields_set`),
        e nao pelo valor: o padrao do dominio e "4s", entao comparar valor recusaria
        todo pedido de imagem que nao falasse de duracao — ou seja, todos eles. E o
        mesmo criterio que `PatchConta` usa para o proxy, e pelo mesmo motivo: ausencia
        e valor sao coisas diferentes.

        Recusar aqui e melhor do que aceitar calado: nao ha grupo de duracao no painel
        em modo imagem, entao a configuracao nunca seria aplicada e o usuario receberia
        o arquivo achando que foi.
        """
        if self.modo is Modo.IMAGEM:
            pedida = self.duracao if "duracao" in self.model_fields_set else None
            if not duracao_valida(self.modo, pedida):
                raise ValueError(
                    f"modo imagem nao tem duracao: o painel do Flow nao oferece o "
                    f"grupo em imagem, e {pedida!r} nao teria onde ser clicado")
            return self
        if not duracao_valida(self.modo, self.duracao):
            raise ValueError(
                f"duracao {self.duracao!r} nao existe; use uma de {list(DURACOES)}")
        return self

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
    proporcao: str = PROPORCAO_PADRAO
    quantidade: int = 1
    duracao: str = DURACAO_PADRAO
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
    # SEMPRE mascarado por `nucleo.proxy.mascarar` antes de chegar aqui. A resposta vai
    # para o painel e para log, e credencial de proxy nao precisa passear por ali.
    proxy: str | None = None


class PatchConta(BaseModel):
    ativa: bool | None = None
    tier: Tier | None = None
    # `None` significa "nao mexe"; string vazia significa "tira o proxy". Sem os dois
    # sentidos separados nao haveria como voltar uma conta para "sem proxy".
    proxy: str | None = None

    @field_validator("proxy")
    @classmethod
    def proxy_da_para_usar(cls, v: str | None) -> str | None:
        return _proxy_usavel(v)


class NovaConta(BaseModel):
    email: str
    tier: Tier = Tier.FREE
    # O cadastro ja nasce com o proxy porque o LOGIN tem que sair do mesmo IP do
    # trabalho: conta que nasce no IP de casa e depois passa a operar por proxy mostra
    # ao Google exatamente a mudanca que o proxy existe para evitar. Quem consome e o
    # `POST /contas` da Task 10, que abre o chrome.exe do cadastro.
    proxy: str | None = None

    @field_validator("proxy")
    @classmethod
    def proxy_da_para_usar(cls, v: str | None) -> str | None:
        return _proxy_usavel(v)


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
from flowpool.nucleo.proxy import mascarar
from flowpool.nucleo.tipos import Conta, EstadoJob, Job, SaudeSessao
from flowpool.transporte.base import FlowTransport


def _job_de(p: PedidoJob) -> Job:
    # `destino` e `modelo` nunca chegam None aqui: os validadores de `PedidoJob` ja
    # derivaram um do produto e o outro do modo quando o pedido veio sem eles.
    return Job(id=0, modo=p.modo, modelo=p.modelo, prompt=p.prompt, destino=p.destino,
               nome=p.nome, produto=p.produto, referencias=p.referencias,
               frame_inicial=p.frame_inicial, frame_final=p.frame_final,
               proporcao=p.proporcao, quantidade=p.quantidade, duracao=p.duracao,
               prioridade=p.prioridade)


def _resposta(j: Job) -> RespostaJob:
    return RespostaJob(id=j.id, estado=j.estado, modo=j.modo, modelo=j.modelo,
                       nome=j.nome, produto=j.produto, proporcao=j.proporcao,
                       quantidade=j.quantidade, duracao=j.duracao,
                       conta_id=j.conta_id,
                       arquivo=j.arquivo, tentativas=j.tentativas, erro=j.erro)


def _resposta_conta(c: Conta) -> RespostaConta:
    """Conta como a API a mostra — com a senha do proxy fora.

    Existe como funcao unica de proposito: com a construcao repetida em cada rota,
    bastava uma delas esquecer o `mascarar` para a credencial sair inteira no JSON, e
    ninguem notaria ate ela aparecer num log.
    """
    return RespostaConta(id=c.id, email=c.email, tier=c.tier, saldo=c.saldo,
                         saldo_confiavel=c.saldo_confiavel, saude=c.saude,
                         ativa=c.ativa, falhas=c.falhas, proxy=mascarar(c.proxy))


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
        return [_resposta_conta(c) for c in repo.contas()]

    @app.patch("/contas/{id_}", response_model=RespostaConta)
    def alterar_conta(id_: int, p: PatchConta) -> RespostaConta:
        c = repo.conta(id_)
        if c is None:
            raise HTTPException(404, "conta nao existe")
        if p.ativa is not None:
            c.ativa = p.ativa
        if p.tier is not None:
            c.tier = p.tier
        if "proxy" in p.model_fields_set:
            # aqui o teste e pelo campo TER VINDO no corpo, nao por ele ser `None`
            # como nos outros dois. O validador transforma "" em None, e string vazia
            # e justamente como se TIRA o proxy de uma conta — com o teste de `None`
            # nao haveria como voltar uma conta para "sem proxy".
            c.proxy = p.proxy
        repo.salvar_conta(c)
        return _resposta_conta(c)

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
Expected: `32 passed`

- [ ] **Step 6: Commit**

```bash
git add flowpool/api tests/test_api.py
git commit -m "feat: api http de jobs e contas"
```

---

### Task 9: Completar e validar o mapa de seletores

**Files:**
- Modify: `flowpool/transporte/pw/seletores.py`, `flowpool/transporte/pw/mapear.py`, `flowpool/transporte/pw/dom.py` (existe, ainda não commitado)
- Create: `flowpool/transporte/pw/conferir.py`

**Este task não cria o mapa do zero — ele já existe.** O mapeamento ao vivo aconteceu em 2026-08-20, numa conta gratuita, locale pt, e o resultado está em `flowpool/transporte/pw/seletores.py`. O que falta é: (a) trazer para o arquivo o **chip de configuração**, que foi descoberto depois e hoje mora solto em `fumaca.py`; (b) medir os seis itens que ainda estão em aberto; (c) escrever o script que **confere** se cada seletor ainda casa — é a primeira coisa a rodar no dia em que o Google mexer no layout, e sem ele o sintoma de um deploy do Google é um job que falha três vezes sem dizer o porquê.

As seis regras do cabeçalho de `seletores.py` continuam valendo e são a referência viva. Duas ganharam consequência prática desde então e viram código neste task:

- **Regra 2 (não varrer `<script>`) virou módulo.** O `__NEXT_DATA__` carrega o access_token OAuth em texto puro. Isso vazou **duas vezes** no mapeamento — a segunda depois de a regra já estar escrita num comentário. Comentário não basta: o filtro precisa ser código que todo caminho de leitura importa. É o `dom.py`.
- **Regra 1 (nada de classe) ganhou uma irmã:** casar sempre pela **ligadura do ícone** (`videocam`, `crop_free`, `add_2`), nunca pelo rótulo acentuado. "Vídeo" impresso num console cp1252 vira "V?deo", e um seletor montado a partir daí nunca casa — o erro aparece como "botão não existe" e manda a pessoa procurar no lugar errado.

#### O que já está medido e não se mexe

| grupo | estado |
|---|---|
| URLs, locale `pt`, padrão do projeto | medido |
| lista de projetos, card, "Novo projeto" | medido |
| onboarding (duas telas de boas-vindas) | medido |
| campo de prompt, enviar, anexar | medido |
| menu do avatar e regex de créditos | medido |
| menu do projeto (Renomear / Lixeira / Excluir) | medido |
| menu do card (imagem e vídeo) e submenu de download | medido |
| banner de alta demanda | medido |
| confirmação do agente e o custo no texto | medido |

#### A descoberta que reorganiza o arquivo: o chip

O painel de geração **não** é o `tune Configurações`. `tune` é onde mora a política de confirmação do agente ("Sempre"/"Nunca") — e ele de fato só aparece depois do primeiro prompt. A configuração da geração está num **chip** na barra do prompt, que mostra o estado atual em texto (`Vídeo · 720p · 8s crop_9_16 x1`) e, ao ser clicado, abre um painel com **tudo**: modo, proporção, duração, quantidade e o dropdown de modelo.

Isso desfaz três suposições das versões anteriores deste plano:

1. **Não existem "dois dropdowns de modelo", um de imagem e um de vídeo.** Existe **um** dropdown, e a lista dentro dele muda conforme o modo selecionado: em vídeo lista Omni Flash / Veo 3.1 Lite / Fast / Quality; em imagem lista Nano Banana Pro / 2 / 2 Lite.
2. **A armadilha de ordem do `tune` some do caminho da geração.** O chip existe desde o primeiro instante do projeto. A sequência "enviar prompt → esperar o agente → abrir tune" era uma volta desnecessária e frágil.
3. **A confirmação do agente deixa de ser obstáculo.** Configurando pelo chip, o Flow **não pergunta nada**: debita e gera. O radio "Sempre"/"Nunca" só governa o agente conversacional, que o transporte não usa. Ver o trade-off na Task 11.

Os quatro modos do spec são os quatro do chip, e cada um tem uma ligadura própria: `image Imagem`, `videocam Vídeo`, `crop_free Frames`, `chrome_extension Elementos`.

- [ ] **Step 1: Criar o módulo de leitura segura de DOM**

`flowpool/transporte/pw/dom.py`:

```python
"""Leitura segura do DOM do Flow.

Existe por um motivo concreto: o `__NEXT_DATA__` da pagina carrega o access_token
OAuth em texto puro, junto com nome e e-mail. Uma varredura ingenua
(`[...document.querySelectorAll('*')].filter(e => !e.children.length)`) INCLUI
`<script>`, porque script nao tem filhos *elemento* — e o token vaza para o log.

Isso ja aconteceu duas vezes durante o mapeamento. A segunda foi depois de a regra
estar escrita num comentario, o que prova que comentario nao basta: o filtro precisa
ser codigo compartilhado que todo caminho de leitura usa.

Use `TEXTOS_VISIVEIS` em vez de escrever a varredura a mao.
"""

from __future__ import annotations

import re
import unicodedata

# Devolve o texto de todos os nos-folha visiveis, ja sem script/style/noscript.
TEXTOS_VISIVEIS = r"""
() => {
  const PROIBIDO = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','IFRAME']);
  return [...document.querySelectorAll('body *')]
    .filter(e => !e.children.length && !PROIBIDO.has(e.tagName))
    .map(e => (e.innerText || '').trim())
    .filter(Boolean);
}
"""

# Mesma varredura, mas ja aplicando um limite de tamanho — texto legitimo de interface
# e curto. Um "texto" de 3 KB e quase sempre payload que escapou do filtro.
TEXTOS_CURTOS = r"""
(limite) => {
  const PROIBIDO = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','IFRAME']);
  return [...document.querySelectorAll('body *')]
    .filter(e => !e.children.length && !PROIBIDO.has(e.tagName))
    .map(e => (e.innerText || '').trim())
    .filter(t => t && t.length <= limite);
}
"""


def sem_segredo(texto: str) -> str:
    """Ultima linha de defesa antes de qualquer log ou arquivo.

    Mesmo com o filtro de tags, um texto pode carregar token por outro caminho
    (atributo, JSON embutido num data-*). Mascarar e barato; vazar nao.
    """
    texto = re.sub(r"ya29\.[A-Za-z0-9_\-]+", "ya29.<MASCARADO>", texto)
    texto = re.sub(r'"access_token"\s*:\s*"[^"]+"', '"access_token":"<MASCARADO>"', texto)
    return texto


def sem_acento(texto: str) -> str:
    """Minusculas e sem acento, para comparar e para IMPRIMIR.

    O texto do chip vem acentuado ("Video" com acento). Comparar acentuado em Python
    funciona — o arquivo e UTF-8 —, mas IMPRIMIR acentuado num console cp1252 troca a
    letra por '?', e a pessoa que le o log monta o seletor a partir do texto trocado.
    Normalizar dos dois lados elimina a classe inteira de erro.
    """
    plano = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in plano if not unicodedata.combining(c)).lower()


def seguro(texto: str) -> str:
    """O que se imprime: sem segredo e sem acento, nessa ordem."""
    return sem_acento(sem_segredo(texto))
```

- [ ] **Step 2: Fazer o `mapear.py` passar tudo pelo filtro antes de gravar**

`mapear.py` grava `sonda.json` **em disco**. Hoje ele só varre `button`, `a`, `input` e `[contenteditable]`, então não pega `<script>` — mas grava `aria-label` e `innerText` crus, e foi exatamente uma gravação em disco que vazou token na primeira passada. Acrescente o import e troque a gravação:

```python
from flowpool.transporte.pw.dom import sem_segredo
```

```python
            bruto = json.dumps(dados, ensure_ascii=False, indent=2)
            # regra 2, aplicada onde dói: sonda.json vai para disco e sobrevive a
            # sessao. Mascarar aqui e o que impede o vazamento de virar arquivo.
            saida.write_text(sem_segredo(bruto), encoding="utf-8")
```

- [ ] **Step 3: Trazer o chip para dentro de `seletores.py`**

Hoje o chip está espalhado em literais dentro de `fumaca.py` (`ICONE_MODO`, `'button:has-text("crop_"), button:has-text("720p")'`, os `get_by_role` de duração e quantidade). Isso quebra a única regra estrutural do módulo: **seletor mora em `seletores.py`, sequência mora no transporte**. Um seletor duplicado em dois arquivos é um seletor que vai ser corrigido em um só no dia do deploy do Google.

Substitua a seção `--- painel de configuracao da geracao ---` de `seletores.py` pelo bloco abaixo. Note as trocas de `:has-text` para `:text-is` — e o porquê, que é concreto:

```python
# --- chip de configuracao da geracao (A PECA CENTRAL) -------------------
# Na barra do prompt ha um botao que MOSTRA o estado atual em texto:
#     "Video · 720p · 8s crop_9_16 x1"
# Clicar nele abre um painel com modo, proporcao, duracao, quantidade e o dropdown de
# modelo. E por aqui que a geracao se configura — NAO pelo `tune Configuracoes`, que
# so guarda a politica de confirmacao do agente conversacional.
#
# O chip serve tambem de VERIFICACAO: depois de configurar, le-se o texto dele e
# confere-se que bate com o pedido. Ver a regra dura na Task 11.
CHIP_CONFIG = 'button:has-text("crop_")'

# O `720p` do chip NAO e configuravel: nao ha grupo de resolucao no painel. Sai
# derivado do modelo. Vale registrar no evento, nao tentar mudar.

# Container do painel aberto. TUDO que se clica no painel e escopado por ele: sem
# escopo, `crop_9_16` casa tambem o proprio CHIP_CONFIG (o texto do chip contem a
# ligadura), e o clique fecharia o painel em vez de escolher a proporcao.
PAINEL_CHIP = '[role=dialog], [role=menu]'   # CONFIRMAR no Step 5

# Modos: casar pela LIGADURA, nunca pelo rotulo. "Video" acentuado nao sobrevive a um
# console cp1252, e um seletor montado a partir do texto trocado nunca casa.
# As chaves sao os valores de `Modo` (nucleo/tipos.py) — este arquivo nao importa
# dominio, so espelha os nomes.
MODO_ICONE = {
    "imagem": "image",
    "t2v": "videocam",
    "frames2v": "crop_free",            # primeiro e (opcional) ultimo frame
    "ingredients2v": "chrome_extension",  # referencias de personagem/produto
}
BOTAO_MODO = {k: f'button:has-text("{lig}")' for k, lig in MODO_ICONE.items()}

# O rotulo que o CHIP mostra para cada modo, ja normalizado (dom.sem_acento). E o que
# a verificacao procura no texto do chip depois de configurar.
ROTULO_MODO_NO_CHIP = {
    "imagem": "imagem", "t2v": "video",
    "frames2v": "frames", "ingredients2v": "elementos",
}

# Proporcao. `:text-is` e exato de proposito: `:has-text("crop_9_16")` casaria tambem
# o proprio chip, cujo texto TERMINA em "crop_9_16 x1".
PROPORCAO = {
    "16:9": 'button:text-is("crop_16_9")',
    "4:3": 'button:text-is("crop_landscape")',
    "1:1": 'button:text-is("crop_square")',
    "3:4": 'button:text-is("crop_portrait")',
    "9:16": 'button:text-is("crop_9_16")',
}
# MEDIDO: em modo VIDEO o painel oferece so 9:16 e 16:9. Em modo IMAGEM, as cinco.
PROPORCOES_POR_MODO = {
    "imagem": ("16:9", "4:3", "1:1", "3:4", "9:16"),
    "t2v": ("9:16", "16:9"),
    "frames2v": ("9:16", "16:9"),
    "ingredients2v": ("9:16", "16:9"),
}

# Duracao — so existe em modo video.
DURACAO = {d: f'button:text-is("{d}")' for d in ("4s", "6s", "8s", "10s")}

# Quantidade. `:text-is` pelo mesmo motivo da proporcao: o chip termina em "x1", entao
# `:has-text("x1")` casaria o chip e o clique fecharia o painel.
QUANTIDADE = {n: f'button:text-is("x{n}")' for n in (1, 2, 3, 4)}

# UM dropdown de modelo, nao dois. A lista dentro dele muda conforme o modo escolhido.
DROPDOWN_MODELO = 'button:has-text("arrow_drop_down")'

# Rotulos exatos, medidos em 2026-08-20 numa conta gratuita.
OPCAO_MODELO = {
    # video
    "omni-flash": '[role=menuitem]:text-is("Omni Flash")',
    "veo-lite": '[role=menuitem]:text-is("Veo 3.1 - Lite")',
    "veo-fast": '[role=menuitem]:text-is("Veo 3.1 - Fast")',
    "veo-quality": '[role=menuitem]:text-is("Veo 3.1 - Quality")',
    # imagem
    "nano-banana-pro": '[role=menuitem]:text-is("Nano Banana Pro")',
    "nano-banana-2": '[role=menuitem]:text-is("Nano Banana 2")',
    "nano-banana": '[role=menuitem]:text-is("Nano Banana 2 Lite")',
}
# `:text-is` aqui nao e capricho: "Nano Banana 2" e prefixo de "Nano Banana 2 Lite", e
# com `:has-text` pedir o 2 escolheria o Lite — mais barato, mais feio, sem erro.
```

E **apague** do arquivo o bloco `--- confirmacao do agente (ARMADILHA) ---` com `CONFIG_AGENTE`/`AGENTE_SEMPRE`/`AGENTE_NUNCA`, mais o `BOTAO_CONFIGURACOES` e o `BOTAO_SALVAR_CONFIG`. Eles descrevem um caminho que o transporte não vai percorrer, e seletor que ninguém usa é seletor que ninguém conserta — no dia do deploy do Google, o `conferir.py` acusaria falha em algo irrelevante e mandaria a pessoa investigar o lugar errado. O texto sobre o agente conversacional continua útil, mas como **nota de trade-off no spec**, não como seletor.

Acrescente também, na seção de URLs, o host que falta para montar a chamada de sessão da Task 10:

```python
URL_BASE = "https://labs.google"    # URL_SESSAO e um caminho; a Task 10 concatena
```

- [ ] **Step 4: Registrar o que a grade e o download exigem**

Substitua a seção `--- card de midia na grade ---` por:

```python
# --- card de midia na grade ---------------------------------------------
# A grade e a fonte de verdade do estado, NAO a conversa: o agente so responde quando
# perguntado; a grade atualiza sozinha.
#
# NAO contar elementos <video> soltos. Da falso positivo: o Flow usa <video> em outros
# lugares da tela (previa, cabecalho de cena), e um contador de <video> declara
# "pronto" antes de existir card nenhum. Conta-se CARD DA GRADE.
CARD_GRADE = "..."          # MEDIR no Step 5 — o container clicavel de um item
CARD_MIDIA = 'img[src*="media.getMedia"], img[src*="trpc"]'   # a miniatura dentro dele
CARD_FALHA = 'text="Ops! Algo deu errado"'
BOTAO_REPETIR_CARD = 'button:has-text("redo")'   # repete a geracao SEM cobrar de novo

# Onde o card recem-nascido entra na grade. MEDIR no Step 5 — errar isso NAO da erro:
# o app baixa um clipe antigo, grava o caminho no banco e marca o job como pronto.
CARDS_NOVOS_PRIMEIRO = True   # CONFIRMAR

# Menu do card: clique DIREITO. O <video> em si nao e clicavel (e miniatura); o alvo e
# o centro do container, por coordenada da bounding box.
MENU_CARD = '[role=menu]'
MENU_ITEM = '[role=menuitem]'

# Itens medidos. Imagem: favorite, redo, `motion_blur Animar`, add, `download Baixar`,
# Renomear, share, photo_library, flag, `delete Mover para a lixeira`.
# Video: os mesmos, mais `split_scene` e `smart_display`.
MENU_ANIMAR = '[role=menuitem]:has-text("Animar")'      # imagem -> video (i2v)
MENU_BAIXAR = '[role=menuitem]:has-text("Baixar")'
MENU_LIXEIRA_CARD = '[role=menuitem]:has-text("Mover para a lixeira")'

# "Baixar" abre SUBMENU de resolucao.
#   imagem: "1K Tamanho original", "2K Aumentada", "4K" (exige plano pago)
#   video:  "270p", "720p", "1080p", "4K"
#
# OS ROTULOS TEM QUEBRA DE LINHA: "270p\nGIF animado". Passar o rotulo inteiro para um
# seletor CSS estoura o parser. Casar SO A PRIMEIRA LINHA.
BAIXAR = {
    "1k": '[role=menuitem]:has-text("Tamanho original")',
    "2k": '[role=menuitem]:has-text("2K")',
    "270p": '[role=menuitem]:has-text("270p")',
    "720p": '[role=menuitem]:has-text("720p")',
    "1080p": '[role=menuitem]:has-text("1080p")',
}
# 4K fica FORA do dicionario de proposito: exige plano pago, e um item que existe no
# menu mas falha ao clicar e pior do que um item que nao existe no mapa.

# REGRA DURA: NUNCA escolher item de menu por posicao. Um `.last` mandou um video do
# usuario para a lixeira, porque o ultimo item do menu e "Mover para a lixeira".
# Sempre por texto; e quando o texto casar com mais de um candidato, ERRO — nao
# "pega o primeiro". A Task 11 tem o helper `_unico` que faz valer isso.

# O Flow nomeia o arquivo pelo conteudo e escolhe a extensao sozinho:
#   Red_ceramic_mug_on_table_202608201311.mp4   (video)
#   Red_ceramic_mug_on_table_202608201158.jpeg  (imagem — JPEG, nao PNG)
# Respeitar `download.suggested_filename` em vez de assumir extensao.
```

- [ ] **Step 5: Medir os sete itens em aberto**

Suba o Chrome do perfil de mapeamento — **por `subprocess`, não pelo Playwright** (regra 5; o comando exato está na Task 10, Step 6) — ou rode `mapear.py` num perfil já logado, com a **janela visível de verdade** (regra 4). Depois meça, nesta ordem:

1. **`PAINEL_CHIP`** — abra o chip e leia o `role` do container que aparece. É `dialog`, `menu`, ou nenhum dos dois? Anote o seletor real. **Bloqueia a Task 11**: sem escopo, todo clique no painel corre o risco de acertar o próprio chip.
2. **`CARD_GRADE`** — o container clicável de um item da grade. Precisa ser o elemento que responde ao clique direito e que tem `bounding_box`. Confirme contando: com N gerações no projeto, `count()` tem que dar N — não N+2 por causa de `<video>` de prévia.
3. **`AVISO_SEM_CREDITO`** — só aparece com a conta realmente em zero. É o último a medir, e a hora natural é o Step 5 da Task 14, que esvazia a conta de propósito. Até lá fica `"..."`, e o `conferir.py` o reporta como **pendente**, não como quebrado.
4. **Ordem da grade** — o card novo entra no **início** ou no **fim**? Gere uma vez, conte, gere de novo e veja onde o novo apareceu. Grave em `CARDS_NOVOS_PRIMEIRO: bool`. Errar isso baixa o clipe errado, e o arquivo existe e parece certo — o pior modo de falha deste app.
5. **Texto do chip em modo Imagem** — o âncora `button:has-text("crop_")` foi medido em modo vídeo. Confirme que em modo imagem o chip continua trazendo uma ligadura `crop_*`. Se não trouxer, o âncora precisa de um segundo termo.
6. **Rótulo do modelo dentro do painel** — o chip **não** mostra o modelo. A verificação do modelo tem que acontecer com o painel ainda aberto, lendo o texto do próprio dropdown. Anote de onde esse texto sai (`innerText` do `DROPDOWN_MODELO`, ou um irmão dele).
7. **`src` da miniatura carrega id estável?** Anote um `src` real de um card pronto. Se houver um id de mídia ali, ele vira o `id_remoto` do job e a retomada depois de restart passa a funcionar. Se não houver, a retomada fica limitada — ver a pendência declarada na Task 11.

- [ ] **Step 6: Escrever o conferidor**

`flowpool/transporte/pw/conferir.py`:

```python
"""Confere que cada seletor de `seletores.py` ainda casa com o Flow de hoje.

E a PRIMEIRA coisa a rodar quando alguma coisa parar de funcionar. Sem ele, o sintoma
de um deploy do Google e um job que falha tres vezes, troca de conta, falha de novo e
morre com "TimeoutError" — que nao diz qual seletor morreu. Com ele, o diagnostico e
uma linha.

Nao gera nada e nao gasta credito: so navega, abre paineis e conta.

Uso:
    python -m flowpool.transporte.pw.conferir <perfil> [<uuid-do-projeto>]

Sem o uuid, confere so a lista de projetos e o menu da conta.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from flowpool.transporte.pw import dom
from flowpool.transporte.pw import seletores as sel

PENDENTE = "..."   # marcador dos seletores que a Step 5 ainda nao mediu


class Placar:
    def __init__(self) -> None:
        self.ok = 0
        self.faltando: list[str] = []
        self.pendentes: list[str] = []

    async def checar(self, escopo, contexto: str, nome: str, seletor: str,
                     minimo: int = 1) -> None:
        if not seletor or seletor == PENDENTE:
            self.pendentes.append(f"{contexto}.{nome}")
            print(f"[{contexto:9}] {nome:24} PENDENTE (nunca medido)")
            return
        try:
            n = await escopo.locator(seletor).count()
        except Exception as e:                       # seletor malformado
            self.faltando.append(f"{contexto}.{nome}")
            print(f"[{contexto:9}] {nome:24} INVALIDO ({type(e).__name__})")
            return
        if n >= minimo:
            self.ok += 1
            print(f"[{contexto:9}] {nome:24} OK ({n})")
        else:
            self.faltando.append(f"{contexto}.{nome}")
            print(f"[{contexto:9}] {nome:24} FALTA")


async def _candidatos(pagina) -> None:
    """Imprime o texto curto e visivel da tela, para achar o rotulo que mudou.

    Passa por `dom.seguro`: sem segredo (regra 2) e sem acento (console cp1252).
    """
    textos = await pagina.evaluate(dom.TEXTOS_CURTOS, 40)
    vistos = sorted({dom.seguro(t) for t in textos})
    print("\ncandidatos visiveis na tela:")
    for t in vistos:
        print("   ", t)


async def main(perfil: str, uuid: str | None = None) -> int:
    p = Placar()
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(Path(perfil).resolve()), channel="chrome",
            headless=False, no_viewport=True, args=["--start-maximized"])
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        await pagina.wait_for_selector(sel.CARD_PROJETO, timeout=30_000)

        await p.checar(pagina, "lista", "CARD_PROJETO", sel.CARD_PROJETO)
        await p.checar(pagina, "lista", "BOTAO_NOVO_PROJETO", sel.BOTAO_NOVO_PROJETO)
        await p.checar(pagina, "lista", "AVATAR_CONTA", sel.AVATAR_CONTA)

        # saldo: abre e fecha o menu do avatar. Dois cliques por leitura — e por isso
        # que o transporte le saldo uma vez por job, e nao a cada polling.
        await pagina.locator(sel.AVATAR_CONTA).first.click()
        await pagina.wait_for_timeout(1500)
        textos = await pagina.evaluate(dom.TEXTOS_CURTOS, 60)
        import re
        achou = any(re.search(sel.PADRAO_CREDITOS, t) for t in textos)
        print(f"[{'conta':9}] {'PADRAO_CREDITOS':24} {'OK' if achou else 'FALTA'}")
        if achou:
            p.ok += 1
        else:
            p.faltando.append("conta.PADRAO_CREDITOS")
        await pagina.keyboard.press("Escape")

        if uuid:
            # regra 3: entra-se CLICANDO no card, nunca por goto direto
            await pagina.locator(f'a[href*="{uuid}"]').first.click()
            await pagina.wait_for_timeout(8000)

            await p.checar(pagina, "projeto", "CAMPO_PROMPT", sel.CAMPO_PROMPT)
            await p.checar(pagina, "projeto", "BOTAO_ENVIAR", sel.BOTAO_ENVIAR)
            await p.checar(pagina, "projeto", "BOTAO_ANEXAR", sel.BOTAO_ANEXAR)
            await p.checar(pagina, "projeto", "BOTAO_MAIS_OPCOES", sel.BOTAO_MAIS_OPCOES)
            await p.checar(pagina, "projeto", "CHIP_CONFIG", sel.CHIP_CONFIG)
            await p.checar(pagina, "projeto", "CARD_GRADE", sel.CARD_GRADE, minimo=0)

            await pagina.locator(sel.CHIP_CONFIG).first.click()
            await pagina.wait_for_timeout(2000)
            painel = pagina.locator(sel.PAINEL_CHIP).first
            await p.checar(pagina, "chip", "PAINEL_CHIP", sel.PAINEL_CHIP)
            for modo, seletor in sel.BOTAO_MODO.items():
                await p.checar(painel, "chip", f"MODO[{modo}]", seletor)
            for razao, seletor in sel.PROPORCAO.items():
                await p.checar(painel, "chip", f"PROPORCAO[{razao}]", seletor)
            for d, seletor in sel.DURACAO.items():
                await p.checar(painel, "chip", f"DURACAO[{d}]", seletor)
            for n, seletor in sel.QUANTIDADE.items():
                await p.checar(painel, "chip", f"QUANTIDADE[x{n}]", seletor)
            await p.checar(painel, "chip", "DROPDOWN_MODELO", sel.DROPDOWN_MODELO)

            await painel.locator(sel.DROPDOWN_MODELO).first.click()
            await pagina.wait_for_timeout(1500)
            for chave, seletor in sel.OPCAO_MODELO.items():
                # o dropdown lista so os modelos DO MODO ATUAL: os de imagem faltarem
                # aqui e esperado, nao quebra
                await p.checar(pagina, "modelos", chave, seletor, minimo=0)
            await pagina.keyboard.press("Escape")
            await pagina.keyboard.press("Escape")

        await p.checar(pagina, "pendente", "AVISO_SEM_CREDITO", sel.AVISO_SEM_CREDITO)

        print(f"\nresumo: {p.ok} ok, {len(p.faltando)} faltando, "
              f"{len(p.pendentes)} pendentes")
        if p.faltando:
            print("faltando:", ", ".join(p.faltando))
            await _candidatos(pagina)
        await ctx.close()
    return 1 if p.faltando else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)))
```

- [ ] **Step 7: Rodar o conferidor**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.conferir flowpool/perfis/mapeamento <uuid-do-projeto>
```

Expected — o formato, não os números exatos:

```
[lista    ] CARD_PROJETO             OK (3)
[lista    ] BOTAO_NOVO_PROJETO       OK (1)
[lista    ] AVATAR_CONTA             OK (1)
[conta    ] PADRAO_CREDITOS          OK
[projeto  ] CAMPO_PROMPT             OK (1)
[projeto  ] BOTAO_ENVIAR             OK (1)
[projeto  ] CHIP_CONFIG              OK (1)
[chip     ] PAINEL_CHIP              OK (1)
[chip     ] MODO[t2v]                OK (1)
[chip     ] PROPORCAO[9:16]          OK (1)
[chip     ] DURACAO[4s]              OK (1)
[chip     ] QUANTIDADE[x1]           OK (1)
[modelos  ] omni-flash               OK (1)
[modelos  ] nano-banana              OK (0)
[pendente ] AVISO_SEM_CREDITO        PENDENTE (nunca medido)

resumo: 27 ok, 0 faltando, 1 pendentes
```

`AVISO_SEM_CREDITO` **pendente** é o estado correto até a Task 14. `faltando` em zero é o critério de saída deste task; o processo devolve 1 se houver qualquer `FALTA`, o que o torna usável num script.

- [ ] **Step 8: Commit**

```bash
git add flowpool/transporte/pw/seletores.py flowpool/transporte/pw/dom.py \
        flowpool/transporte/pw/mapear.py flowpool/transporte/pw/conferir.py
git commit -m "feat: chip de configuracao no mapa e conferidor de seletores"
```

---

### Task 10: Cadastro de conta — Chrome por subprocess, validação por Playwright

**Files:**
- Create: `flowpool/transporte/pw/sessao.py`
- Modify: `flowpool/api/app.py` (adicionar `POST /contas` e `POST /contas/{id}/revalidar`)
- Test: `tests/test_sessao.py`

**O login não acontece sob Playwright.** Não é questão de flag, de `channel="chrome"` nem de user-agent: o Google detecta o CDP e recusa com *"esse navegador ou app pode não ser seguro"*. Qualquer versão deste task que abra o navegador de login pelo Playwright está errada por construção.

O fluxo validado em 2026-08-20 tem quatro partes, e cada uma existe por um motivo medido:

1. **`subprocess.Popen` de `chrome.exe`** com `--user-data-dir=<perfil>`, `--no-first-run`, `--no-default-browser-check`, apontando para a URL do Flow. Chrome comum, sem CDP, sem Playwright. É o único jeito de o login passar.
2. **A pessoa loga e passa duas telas de boas-vindas.** Consentimento (duas caixas — **deixar desmarcadas** — e botão "Avançar") e aviso de privacidade (botão "Continuar", que **só habilita depois de o fim do texto entrar em viewport de verdade**). Esse portão é `IntersectionObserver`: janela minimizada, fora de tela ou ocluída conta como oculta, e o botão nunca habilita. É a regra 4 aparecendo já no cadastro.
3. **A pessoa fecha o Chrome, e o app espera o perfil ficar livre.** Dois Chromes no mesmo `--user-data-dir` fazem o segundo entregar a URL para o primeiro e morrer; o Playwright que vier depois falha com `TargetClosedError` — um erro que não menciona perfil nem Chrome e manda procurar no lugar errado.
4. **Validação por Playwright:** abrir o perfil e conferir que `GET /fx/api/auth/session` devolve `access_token`. É a única prova de que a sessão está de pé. E o token **nunca** sai da função — o que volta é a data de expiração.

**Medido: a sessão expira em ~48 h** (`expires` dois dias à frente). A revalidação é exatamente o mesmo fluxo do cadastro, então `revalidar` reusa `cadastrar` e a rota da API é a mesma função com outro nome.

**O Chrome de login sai pelo proxy da conta, e isso não é opcional.** Se a conta nasce no IP de casa e só depois passa a operar por proxy, o Google vê a mudança de IP entre o login e o uso — que é exatamente o sinal que o proxy existe para evitar. O login tem que sair do **mesmo IP do trabalho**. Por isso `argumentos_chrome` recebe o proxy e `argumento_chrome` (de `nucleo/proxy.py`, Task 1) entra na linha de comando.

E há uma incompatibilidade que **precisa ser erro, não silêncio**: o `chrome.exe` cru aceita `--proxy-server=` mas **não** aceita credencial embutida — ele abre um diálogo pedindo usuário e senha. Num cadastro dirigido por `subprocess`, esse diálogo é uma janela parada que ninguém preenche, e o `proc.wait()` fica pendurado até o teto de 15 minutos, sem erro, sem log e sem pista. `argumento_chrome` recusa antes de abrir a janela, com uma mensagem que diz as duas saídas reais: proxy autorizado por IP, ou um retransmissor local sem senha. A recusa acontece **antes** do `Popen`, para não deixar Chrome órfão nem perfil ocupado.

Testável sem rede: o caminho do perfil, a recusa quando o Chrome não existe, a montagem dos argumentos, a detecção de perfil ocupado e a interpretação da resposta de sessão. O que sobra — Chrome subindo, humano logando — não se testa, se roda.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_sessao.py`:

```python
from datetime import datetime, timezone

import pytest

from flowpool.nucleo.erros import ProxyInvalido
from flowpool.transporte.base import SessaoMorta
from flowpool.transporte.pw.sessao import (
    ChromeAusente, argumentos_chrome, caminho_perfil, checar_chrome,
    interpretar_sessao, perfil_em_uso,
)

TOKEN = "ya29.a0AfB_byQFAKE_TOKEN_DE_TESTE"


def test_caminho_de_perfil_e_derivado_do_email(tmp_path):
    p = caminho_perfil(tmp_path, "davi.teste+3@gmail.com")
    assert p.parent == tmp_path
    assert "@" not in p.name and "+" not in p.name
    assert p.name.startswith("davi")


def test_emails_diferentes_dao_perfis_diferentes(tmp_path):
    assert caminho_perfil(tmp_path, "a@gmail.com") != caminho_perfil(tmp_path, "b@gmail.com")


def test_mesmo_email_da_sempre_o_mesmo_perfil(tmp_path):
    assert caminho_perfil(tmp_path, "a@b.com") == caminho_perfil(tmp_path, "a@b.com")


def test_checar_chrome_levanta_quando_nao_acha(monkeypatch):
    monkeypatch.setattr("flowpool.transporte.pw.sessao._CANDIDATOS", [])
    with pytest.raises(ChromeAusente, match="Chrome"):
        checar_chrome()


def test_argumentos_do_chrome_levam_perfil_e_url(tmp_path):
    args = argumentos_chrome(tmp_path / "chrome.exe", tmp_path / "perfil", "https://x/y")
    assert args[0].endswith("chrome.exe")
    assert any(a.startswith("--user-data-dir=") and "perfil" in a for a in args)
    assert args[-1] == "https://x/y"


def test_argumentos_do_chrome_nao_pedem_porta_de_depuracao(tmp_path):
    # --remote-debugging-port e o que faz o Google recusar o login. Uma flag dessas
    # entrando aqui por engano reproduziria exatamente o bug que este task existe para
    # evitar, e o sintoma seria "o Google nao deixa logar", nao "flag errada".
    args = argumentos_chrome(tmp_path / "chrome.exe", tmp_path / "perfil", "https://x")
    assert not any("remote-debugging" in a or "--headless" in a for a in args)


def test_sem_proxy_a_linha_de_comando_nao_ganha_flag_nenhuma(tmp_path):
    # o caso comum hoje: campo vazio, e o Chrome sai pelo IP da maquina (ou pela VPN
    # de sistema, que ele nem enxerga)
    args = argumentos_chrome(tmp_path / "chrome.exe", tmp_path / "perfil", "https://x")
    assert not any("proxy" in a for a in args)


def test_o_chrome_de_login_sai_pelo_proxy_da_conta(tmp_path):
    # o login tem que sair do MESMO IP do trabalho: conta que nasce no IP de casa e
    # depois opera por proxy mostra ao Google a mudanca que o proxy evita
    args = argumentos_chrome(tmp_path / "chrome.exe", tmp_path / "perfil", "https://x",
                             proxy="http://1.2.3.4:8080")
    assert "--proxy-server=http://1.2.3.4:8080" in args
    assert args[-1] == "https://x"      # a URL continua sendo o ultimo argumento


def test_o_chrome_de_login_recusa_proxy_com_credencial(tmp_path):
    # o Chrome cru abre um dialogo de usuario e senha, e o `proc.wait()` fica
    # pendurado ate o teto de 15 minutos sem erro nenhum. Recusar antes do Popen e o
    # que transforma isso numa mensagem em vez de numa janela parada.
    with pytest.raises(ProxyInvalido, match="retransmissor"):
        argumentos_chrome(tmp_path / "chrome.exe", tmp_path / "perfil", "https://x",
                          proxy="http://davi:segredo@1.2.3.4:8080")


def test_perfil_livre_quando_nenhum_chrome_usa_a_pasta(tmp_path, monkeypatch):
    monkeypatch.setattr("flowpool.transporte.pw.sessao._linhas_de_comando",
                        lambda: ['chrome.exe --user-data-dir="C:\\outro"'])
    assert perfil_em_uso(tmp_path / "perfil") is False


def test_perfil_ocupado_quando_um_chrome_usa_a_pasta(tmp_path, monkeypatch):
    alvo = (tmp_path / "perfil").resolve()
    monkeypatch.setattr("flowpool.transporte.pw.sessao._linhas_de_comando",
                        lambda: [f'chrome.exe --user-data-dir={alvo}'])
    assert perfil_em_uso(tmp_path / "perfil") is True


def test_sessao_valida_devolve_a_expiracao():
    quando = interpretar_sessao(
        {"access_token": TOKEN, "expires": "2026-08-22T15:04:05.000Z"})
    assert quando == datetime(2026, 8, 22, 15, 4, 5, tzinfo=timezone.utc)


def test_sessao_morta_quando_falta_access_token():
    with pytest.raises(SessaoMorta):
        interpretar_sessao({"user": {"email": "a@b.com"}})


def test_sessao_morta_quando_a_resposta_vem_vazia():
    # deslogado, o Flow devolve `{}` com 200 — nao um erro HTTP. Tratar so o status
    # deixaria uma sessao morta passar por viva ate a primeira geracao falhar.
    with pytest.raises(SessaoMorta):
        interpretar_sessao({})


def test_interpretar_sessao_nao_devolve_o_token():
    # o valor de retorno vai para log e para evento. Se o token puder sair por aqui,
    # a regra 2 nao adianta nada: o vazamento so muda de porta.
    quando = interpretar_sessao({"access_token": TOKEN, "expires": "2026-08-22T15:04:05Z"})
    assert TOKEN not in repr(quando)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/pytest tests/test_sessao.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowpool.transporte.pw.sessao'`

- [ ] **Step 3: Implementar**

`flowpool/transporte/pw/sessao.py`:

```python
"""Perfil de conta: cadastro (Chrome comum) e validacao (Playwright).

A divisao nao e estilistica. MEDIDO em 2026-08-20: o Google recusa o login em qualquer
Chrome dirigido por CDP — "esse navegador ou app pode nao ser seguro" — e nao adianta
`channel="chrome"`, user-agent nem flag nenhuma. Entao:

    login    -> chrome.exe comum, por subprocess, humano na frente
    operacao -> Playwright reaproveitando o MESMO perfil

Uma vez autenticado, o Playwright dirige a sessao sem reclamacao. A checagem e
exclusiva do fluxo de login.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from flowpool.nucleo.proxy import analisar, argumento_chrome
from flowpool.transporte.base import SessaoMorta
from flowpool.transporte.pw import seletores as sel

_CANDIDATOS = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]

# Quanto tempo esperar a pessoa logar e passar as duas telas de boas-vindas.
TETO_LOGIN_S = 900.0
# Quanto esperar o perfil ficar livre depois que a janela fecha. Chrome deixa processo
# filho vivo por alguns segundos depois de a janela sumir.
TETO_LIBERAR_S = 120.0

INSTRUCOES = """
Uma janela do Chrome abriu no perfil desta conta.

  1. Faca o login na conta Google.
  2. Tela de consentimento: DEIXE AS DUAS CAIXAS DESMARCADAS e clique em "Avancar".
  3. Aviso de privacidade: ROLE ATE O FIM DO TEXTO — o botao "Continuar" so habilita
     depois que o fim do texto entra na tela de verdade. Janela minimizada ou coberta
     por outra nao conta: o portao e IntersectionObserver.
  4. Confirme que a lista de projetos do Flow carregou.
  5. FECHE A JANELA DO CHROME. O cadastro continua sozinho a partir dai.

Nao abra este mesmo perfil em outro Chrome enquanto isso: o segundo entrega a URL
para o primeiro e morre, e a validacao falharia com TargetClosedError.
"""


class ChromeAusente(RuntimeError):
    """O Google recusa o Chromium empacotado do Playwright; precisa do Chrome instalado."""


class PerfilOcupado(RuntimeError):
    """Ja existe um Chrome usando este --user-data-dir."""


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


def argumentos_chrome(exe: Path, perfil: Path, url: str,
                      proxy: str | None = None) -> list[str]:
    """A linha de comando do Chrome de LOGIN.

    Nenhuma flag de automacao entra aqui. `--remote-debugging-port` e o que faz o
    Google recusar; `--headless` mata o IntersectionObserver que libera o aviso de
    privacidade. As tres que ficam sao so para o perfil novo nao abrir assistente.

    O proxy entra porque o LOGIN tem que sair do mesmo IP do trabalho: conta que nasce
    no IP de casa e depois passa a operar por proxy mostra ao Google exatamente a
    mudanca que o proxy existe para evitar. `argumento_chrome` levanta `ProxyInvalido`
    quando a URL traz credencial — o Chrome cru abriria um dialogo de autenticacao, e
    a janela ficaria parada ate o teto de login sem erro nenhum.
    """
    args = [
        str(exe),
        f"--user-data-dir={Path(perfil).resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    flag = argumento_chrome(proxy)
    if flag:
        args.append(flag)
    # a URL fica sempre por ultimo: e assim que o Chrome a distingue de uma flag
    args.append(url)
    return args


def _linhas_de_comando() -> list[str]:
    """CommandLine de cada chrome.exe vivo. Isolado para o teste poder trocar."""
    consulta = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                "ForEach-Object { $_.CommandLine }")
    saida = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", consulta],
        capture_output=True, text=True, timeout=60)
    return [l for l in saida.stdout.splitlines() if l.strip()]


def perfil_em_uso(perfil: Path) -> bool:
    """Se algum Chrome vivo esta com este --user-data-dir.

    Nao da para usar arquivo de lock: `SingletonLock` e coisa de POSIX, e o
    `Default/LOCK` do Windows e do LevelDB e existe com o Chrome fechado. Sobra olhar
    a linha de comando dos processos.
    """
    alvo = str(Path(perfil).resolve()).lower()
    try:
        return any(alvo in linha.lower() for linha in _linhas_de_comando())
    except Exception:
        # sem conseguir consultar, o seguro e assumir livre e deixar o erro real
        # aparecer na validacao — travar o cadastro por causa da consulta seria pior
        return False


def esperar_perfil_livre(perfil: Path, teto_s: float = TETO_LIBERAR_S) -> None:
    limite = time.monotonic() + teto_s
    while time.monotonic() < limite:
        if not perfil_em_uso(perfil):
            return
        time.sleep(2.0)
    raise PerfilOcupado(
        f"ainda ha Chrome usando {perfil}. Feche todas as janelas desse perfil.")


def interpretar_sessao(dados: dict) -> datetime:
    """Le a resposta de `/fx/api/auth/session` e devolve SO a data de expiracao.

    O token NAO sai daqui. A resposta inteira carrega o access_token OAuth em texto
    puro, e o valor de retorno desta funcao vai para log e para evento — devolver o
    dicionario seria abrir a mesma porta que a regra 2 fecha no DOM.

    Deslogado, o Flow devolve `{}` com HTTP 200. Conferir so o status deixaria uma
    sessao morta passar por viva ate a primeira geracao falhar, tres tentativas
    depois, com a conta marcada por um erro que nao era dela.
    """
    if not dados.get("access_token"):
        raise SessaoMorta("sessao sem access_token — a conta nao esta logada")
    bruto = str(dados.get("expires") or "")
    if not bruto:
        raise SessaoMorta("sessao sem campo `expires`")
    return datetime.fromisoformat(bruto.replace("Z", "+00:00"))


async def abrir_contexto(perfil: Path, proxy: str | None = None,
                         headless: bool = False):
    """Devolve (playwright, contexto). Quem chama fecha os dois.

    `headless` existe so na assinatura, e o default e o unico valor sensato: sem
    janela visivel o IntersectionObserver nao dispara, a lista de projetos nao termina
    de renderizar e nada disso da erro — so nao aparece.

    `proxy` e a URL da conta. `analisar` devolve None quando nao ha proxy (o caso
    comum hoje), e `launch_persistent_context` com `proxy=None` e o mesmo que nao
    passar nada. Diferente do Chrome de login, aqui credencial e aceita: o Playwright
    autentica sozinho, sem dialogo.
    """
    checar_chrome()
    Path(perfil).mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=str(Path(perfil).resolve()),
        channel="chrome",              # o Chromium empacotado e recusado pelo auth
        headless=headless,
        no_viewport=True,
        args=["--start-maximized"],
        accept_downloads=True,
        proxy=analisar(proxy),
    )
    return pw, ctx


async def validar_perfil(perfil: Path, proxy: str | None = None) -> datetime:
    """Abre o perfil com Playwright e prova que a sessao esta viva.

    Valida pelo MESMO proxy por que a conta vai operar. Validar pelo IP de casa e
    depois gerar por outro IP e a divergencia que este campo existe para nao criar.
    """
    pw, ctx = await abrir_contexto(perfil, proxy)
    try:
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        resposta = await pagina.request.get(sel.URL_BASE + sel.URL_SESSAO)
        return interpretar_sessao(await resposta.json())
    finally:
        await ctx.close()
        await pw.stop()


async def cadastrar(raiz_perfis: Path, email: str,
                    proxy: str | None = None) -> tuple[Path, datetime]:
    """Sobe o Chrome de login, espera a pessoa, e valida. Devolve (perfil, expira_em).

    Revalidar sessao caida e a MESMA coisa: mesmo perfil, mesmo fluxo. Por isso nao ha
    duas funcoes.

    O login sai pelo proxy da conta, e a montagem dos argumentos vem ANTES do `Popen`:
    proxy com credencial levanta `ProxyInvalido` ali, com o Chrome ainda nao aberto e
    o perfil ainda livre. Levantar depois deixaria uma janela orfa segurando o perfil,
    e a proxima tentativa morreria em `PerfilOcupado` — um erro que aponta para o lugar
    errado.
    """
    perfil = caminho_perfil(raiz_perfis, email)
    perfil.mkdir(parents=True, exist_ok=True)
    exe = checar_chrome()
    args = argumentos_chrome(exe, perfil, sel.URL_FLOW, proxy)
    if perfil_em_uso(perfil):
        raise PerfilOcupado(f"{perfil} ja esta aberto em outro Chrome — feche antes")

    proc = subprocess.Popen(args)
    print(INSTRUCOES)

    # `to_thread` nao e detalhe: `cadastrar` e aguardado dentro de uma rota do FastAPI,
    # e o motor roda no MESMO event loop. Um `proc.wait()` direto congelaria a fila
    # inteira pelos minutos que a pessoa levar para logar.
    await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=TETO_LOGIN_S)
    if perfil_em_uso(perfil):
        # o Chrome que subimos morreu na hora porque outro ja tinha o perfil e ficou
        # com a janela. Continuar daria TargetClosedError na validacao.
        raise PerfilOcupado(
            f"{perfil} continua em uso depois de o Chrome de login sair — "
            "outro Chrome pegou a janela. Feche tudo e repita.")
    await asyncio.to_thread(esperar_perfil_livre, perfil)
    return perfil, await validar_perfil(perfil, proxy)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_sessao.py -q`
Expected: `15 passed`

- [ ] **Step 5: Adicionar as rotas de conta na API**

Em `flowpool/api/app.py`, os imports (o `json` ainda não existe lá) e as duas rotas, logo abaixo de `listar_contas`:

```python
import json

from flowpool.nucleo.erros import ProxyInvalido
from flowpool.transporte.base import SessaoMorta
from flowpool.transporte.pw.sessao import ChromeAusente, PerfilOcupado, cadastrar
```

```python
    @app.post("/contas", response_model=RespostaConta, status_code=201)
    async def nova_conta(p: NovaConta) -> RespostaConta:
        raiz_perfis = Path(raiz) / "flowpool" / "perfis"
        try:
            # o proxy entra JA no cadastro: o login tem que sair do mesmo IP por onde
            # a conta vai operar, senao o Google ve a mudanca de IP entre o login e o
            # uso — que e o sinal que o proxy existe para evitar
            perfil, expira_em = await cadastrar(raiz_perfis, p.email, p.proxy)
        except ProxyInvalido as e:
            # 409, nao 500: proxy com credencial no chrome.exe do cadastro e uma
            # combinacao que nao existe, e a mensagem ja diz as duas saidas reais
            raise HTTPException(409, str(e)) from e
        except (ChromeAusente, PerfilOcupado) as e:
            raise HTTPException(409, str(e)) from e
        except SessaoMorta as e:
            # a pessoa fechou a janela antes de terminar o login. 409, nao 500: nao e
            # defeito do servico, e passo faltando — e a mensagem tem que dizer isso.
            raise HTTPException(409, f"login nao concluiu: {e}") from e
        try:
            id_ = repo.criar_conta(p.email, p.tier, str(perfil))
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        c = repo.conta(id_)
        # `criar_conta` nao recebe proxy — a coluna e so onde o valor descansa entre
        # uma sessao e outra. Sem esta gravacao, o cadastro sairia pelo proxy e todas
        # as geracoes seguintes pelo IP de casa, que e o pior dos dois mundos.
        c.proxy = p.proxy
        repo.salvar_conta(c)
        # a expiracao vai para evento, nao para coluna: a sessao vive ~48h e quem
        # descobre que caiu e a propria geracao, marcando `expirada`. Guardar a data
        # numa coluna exigiria migracao para uma informacao que ninguem consulta.
        repo.registrar_evento(None, c.id, "sessao_validada",
                              json.dumps({"expira_em": expira_em.isoformat()}))
        return _resposta_conta(c)

    @app.post("/contas/{id_}/revalidar", response_model=RespostaConta)
    async def revalidar(id_: int) -> RespostaConta:
        c = repo.conta(id_)
        if c is None:
            raise HTTPException(404, "conta nao existe")
        # revalidar E cadastrar: mesmo perfil, mesmo Chrome, mesmas duas telas. A
        # sessao expira em ~48h medidas, entao isto vira rotina, nao excecao. E pelo
        # MESMO proxy: revalidar por outro IP e o unico jeito de a conta trocar de IP
        # sem ninguem pedir.
        _, expira_em = await cadastrar(Path(c.perfil).parent, c.email, c.proxy)
        c.saude = SaudeSessao.VIVA
        c.falhas = 0
        repo.salvar_conta(c)
        repo.registrar_evento(None, c.id, "sessao_revalidada",
                              json.dumps({"expira_em": expira_em.isoformat()}))
        return _resposta_conta(c)
```

- [ ] **Step 6: Cadastrar o perfil de mapeamento à mão, uma vez**

O mesmo comando que `cadastrar()` monta, para ter um perfil logado antes de a API existir — é o perfil que a Task 9 e a Task 11 usam:

```bash
"/c/Program Files/Google/Chrome/Application/chrome.exe" \
  --user-data-dir="$(pwd)/flowpool/perfis/mapeamento" \
  --no-first-run --no-default-browser-check \
  "https://labs.google/fx/pt/tools/flow"
```

Logue numa conta **grátis** (não a principal), passe as duas telas de boas-vindas como descrito em `INSTRUCOES`, confirme que a lista de projetos carrega, e **feche a janela**.

- [ ] **Step 7: Provar que o perfil vale**

```bash
.venv/Scripts/python -c "import asyncio,pathlib;from flowpool.transporte.pw.sessao import validar_perfil;print(asyncio.run(validar_perfil(pathlib.Path('flowpool/perfis/mapeamento'))))"
```

(Sem proxy de propósito: o perfil de mapeamento é ferramenta de desenvolvimento, não
conta de produção. Para provar um proxy contratado, acrescente-o como segundo argumento
de `validar_perfil` e confira o IP de saída em `https://ifconfig.me` antes de cadastrar
a conta de verdade — um proxy que só falha na primeira geração custa um login manual.)

Expected: uma data ~48 h à frente, por exemplo `2026-08-22 15:04:05+00:00`. Se sair `SessaoMorta`, o login não completou — refaça o Step 6 e confirme que a lista de projetos apareceu antes de fechar.

- [ ] **Step 8: Rodar a suíte**

Run: `.venv/Scripts/pytest -q`
Expected: `253 passed` (238 das Tasks 0–8 + 15 da sessão)

- [ ] **Step 9: Commit**

```bash
git add flowpool/transporte/pw/sessao.py flowpool/api/app.py tests/test_sessao.py
git commit -m "feat: cadastro de conta por chrome comum e validacao por playwright"
```

---

### Task 11: Transporte Playwright — chip, geração e download

**Files:**
- Create: `flowpool/transporte/pw/transporte.py`
- Test: manual, contra o Flow

Este é o transporte real. Ele satisfaz o `FlowTransport` da Task 6 sem mudar nem uma linha do orquestrador, e a sequência de cada método vem do que foi medido em 2026-08-20.

#### A decisão que organiza o task: modo direto, não agente

O Flow tem dois caminhos para gerar:

| caminho | pergunta antes? | diz o preço? | determinístico? |
|---|---|---|---|
| **chip** (modo direto) | não — debita e gera | não | sim |
| **agente conversacional** | sim, com Aprovar / Rejeitar | **sim, o custo exato vem no texto** | não — depende de interpretação |

O padrão do transporte é o **modo direto**. O motivo é o modo de falha: o agente pode responder qualquer coisa, inclusive pedir esclarecimento, e um robô esperando "Aprovar" fica pendurado até o timeout, queima as três tentativas e marca o job como falho sem dizer o porquê. O chip é uma máquina de estados pequena e legível.

O preço disso é ser **cego ao preço**: o modo direto não anuncia quanto vai custar. Quem cobre esse buraco é a reconciliação de saldo que já existe (`nucleo/credito.py`, evento `divergencia`) — leitura real depois de cada job, e a tabela de custos serve só para reservar antes. O trade-off está registrado no spec.

#### A regra dura: configurou, confere

**Depois de configurar, ler o texto do chip e conferir que bate com o pedido.** Se não bater, é **erro**, não aviso.

Isso não é zelo abstrato. A versão anterior de `fumaca.py` tinha um `ajustar()` tolerante — `except Exception: print("sem efeito — seguindo")` — pensado para o caso benigno de "essa opção já estava assim". O que ele fez de verdade foi engolir um clique que não pegou: uma geração pedida em **4s saiu em 8s**, com saldo debitado, arquivo na pasta e ninguém percebendo. Passo tolerante em configuração de geração é o pior padrão possível neste app, porque a saída existe e parece certa.

O modelo **não aparece no texto do chip** (`Vídeo · 720p · 8s crop_9_16 x1`), então a verificação acontece em dois momentos: o modelo é conferido com o painel ainda aberto, lendo o rótulo do dropdown; modo, proporção, duração e quantidade são conferidos no chip depois de fechar.

#### Proxy: o erro que NÃO é da conta

`abrir_sessao` passa `conta.proxy` para `abrir_contexto`, e é o que faz cada conta sair
por um IP próprio. O que precisa ficar registrado aqui é o que acontece quando o proxy
**cai**.

Proxy fora do ar aparece como `net::ERR_PROXY_CONNECTION_FAILED` — uma exceção comum do
Playwright, que chega ao orquestrador pelo ramo genérico de `_executar`. E é exatamente
onde ela deve chegar: **falha de proxy é falha de rede.** Gasta tentativa, estorna a
reserva e devolve o job à fila, mas **não** marca a conta `expirada` nem seca. Marcar
seria errado duas vezes — a conta está boa e cheia de crédito, e ela sumiria do pool por
um problema de infraestrutura que se conserta trocando de IP, não mexendo na conta. Com
vinte contas atrás de proxies do mesmo fornecedor, uma queda do fornecedor marcaria o
pool inteiro `expirado` e cobraria vinte logins manuais por nada.

Não há código novo para isso: o ramo genérico já se comporta assim. O que existe é o
teste que o prende (`test_proxy_morto_nao_marca_a_conta_expirada_nem_seca`, Task 7), e
ele foi conferido mordendo — acrescentar um `conta.saude = EXPIRADA` naquele ramo o faz
falhar.

Uma consequência de operação que vale dizer em voz alta: **o proxy tem que estar de pé
antes do cadastro**, e o mesmo daí em diante. Conta que loga por um IP e opera por outro
mostra ao Google a mudança que o proxy existe para evitar, e trocar o proxy de uma conta
já cadastrada é uma mudança de IP como qualquer outra — não é erro do app, mas também
não é de graça.

#### Pendências declaradas

Três coisas ficam de fora **de propósito**, e ficam escritas aqui para não virarem surpresa:

1. ~~**`Pedido` não tem `duracao`.**~~ **RESOLVIDO na Task 15.** Enquanto durou, o transporte fixava `DURACAO_PADRAO = "8s"` e apenas **verificava** — e o preço disso só ficou visível quando a duração entrou no custo: 8s custa 12 créditos e 4s custa 7, ou seja, todo vídeo saía **70% mais caro** do que precisava. Numa conta grátis de 50 créditos/dia, a diferença entre 4 e 7 vídeos por dia. A constante saiu de `transporte/pw/transporte.py`; o lugar dela é `p.duracao`.
2. **`id_remoto` é posicional** (`card-{n}`), porque a grade não expõe id na UI. Consequência honesta: a **retomada depois de restart** que o orquestrador implementa (`recuperado_com_geracao`) só funciona enquanto o processo vive. O Step 5 da Task 9 mede se o `src` da miniatura carrega um id estável; se carregar, trocar `_identificar` é uma mudança de dez linhas e a retomada passa a valer de verdade.
3. **Vídeo só aceita 9:16 e 16:9**, e a API aceita as cinco proporções para qualquer modo. O transporte recusa cedo (antes de tocar no chip, com a reserva ainda estornável), mas o lugar certo de recusar é o 422 do `POST /jobs`. Fica para o mesmo task do item 1.

- [ ] **Step 1: Implementar**

`flowpool/transporte/pw/transporte.py`:

```python
"""Implementacao real do FlowTransport.

Sequencias medidas ao vivo em 2026-08-20. Seletor NENHUM mora aqui: quando o Google
mexer no layout, o conserto e em `seletores.py` e o diagnostico e `conferir.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

from flowpool.nucleo.tipos import Conta, Modo
from flowpool.transporte.base import (
    Estado, GeracaoFalhou, Pedido, ProjetoRemoto, SaldoInsuficiente, Sessao, SessaoMorta,
)
from flowpool.transporte.pw import dom
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.sessao import abrir_contexto, interpretar_sessao

# Chave de `sel.BAIXAR` por modo. 4K fica de fora dos dois: exige plano pago.
RESOLUCAO = {
    Modo.IMAGEM: "1k",          # "1K Tamanho original"
    Modo.T2V: "720p",
    Modo.FRAMES2V: "720p",
    Modo.INGREDIENTS2V: "720p",
}
# Quantas vezes repetir um card falho pelo proprio botao `redo`, que NAO cobra de novo.
MAX_REPETICOES = 2


class TransportePlaywright:
    def __init__(self, timeout_ms: int = 600_000) -> None:
        self.timeout_ms = timeout_ms
        self._repeticoes: dict[str, int] = {}
        # `baixar` recebe so o id_remoto, e o menu de resolucao e diferente entre
        # imagem e video. Guardar o modo no momento de gerar e mais barato do que
        # adivinhar pelo DOM — e adivinhar errado baixaria a resolucao errada calado.
        self._modo_do_card: dict[str, Modo] = {}

    # ------------------------------------------------------------------ sessao
    async def abrir_sessao(self, conta: Conta) -> Sessao:
        # o proxy e DA CONTA, nao do processo: e assim que cada conta sai por um IP
        # proprio e o Google nao consegue correlacionar as vinte entre si. Vazio e o
        # normal hoje, e `abrir_contexto` trata None como "sem proxy".
        pw, ctx = await abrir_contexto(Path(conta.perfil), conta.proxy)
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        try:
            # a prova de sessao viva e a API, nao a tela: deslogado o Flow devolve {}
            # com HTTP 200, e a tela leva segundos para trocar para o botao de login
            resposta = await pagina.request.get(sel.URL_BASE + sel.URL_SESSAO)
            interpretar_sessao(await resposta.json())
        except SessaoMorta:
            await ctx.close()
            await pw.stop()
            raise SessaoMorta(f"sessao de {conta.email} caiu")
        return Sessao(conta=conta, handle=(pw, ctx, pagina))

    async def fechar(self, s: Sessao) -> None:
        pw, ctx, _ = s.handle
        await ctx.close()
        await pw.stop()

    # ------------------------------------------------------------------- saldo
    async def saldo(self, s: Sessao) -> int:
        """O saldo NAO fica na tela do projeto: mora no menu do avatar.

        Custa dois cliques (abrir e fechar). Por isso o orquestrador le uma vez por
        job, na reconciliacao, e nao a cada volta do polling.
        """
        _, _, pagina = s.handle
        await pagina.locator(sel.AVATAR_CONTA).first.click(timeout=15_000)
        try:
            await pagina.wait_for_timeout(1200)
            for texto in await pagina.evaluate(dom.TEXTOS_CURTOS, 60):
                achado = re.search(sel.PADRAO_CREDITOS, texto)
                if achado:
                    return int(re.sub(r"[.,]", "", achado.group(1)))
            raise GeracaoFalhou("nao achei o saldo no menu da conta")
        finally:
            # sem fechar, o menu fica por cima do chip e do prompt na proxima geracao
            await pagina.keyboard.press("Escape")

    # ---------------------------------------------------------------- projetos
    async def listar_projetos(self, s: Sessao) -> list[ProjetoRemoto]:
        _, _, pagina = s.handle
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        await pagina.locator(sel.CARD_PROJETO).first.wait_for(timeout=30_000)
        achados: list[ProjetoRemoto] = []
        for card in await pagina.locator(sel.CARD_PROJETO).all():
            href = await card.get_attribute("href") or ""
            id_remoto = href.rstrip("/").split("/")[-1]
            nome = dom.sem_segredo((await card.inner_text()).strip()).splitlines()[0]
            if id_remoto:
                achados.append(ProjetoRemoto(id_remoto=id_remoto, nome=nome))
        return achados

    async def criar_projeto(self, s: Sessao, nome: str) -> str:
        """SAO DUAS ETAPAS. "Novo projeto" cria e ENTRA direto, sem pedir nome.

        Renomear e depois, por "Mais opcoes" -> "Renomear". Quem tratar isso como uma
        etapa so acaba com uma conta cheia de "Projeto sem titulo", e a adocao por nome
        (Task 7) para de funcionar — a proxima geracao do mesmo produto cria outro.
        """
        _, _, pagina = s.handle
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        await pagina.locator(sel.BOTAO_NOVO_PROJETO).first.click(timeout=30_000)
        await pagina.wait_for_url(re.compile(re.escape(sel.PADRAO_PROJETO)), timeout=30_000)
        id_remoto = pagina.url.rstrip("/").split("/")[-1]
        if not id_remoto or sel.PADRAO_PROJETO not in pagina.url:
            raise GeracaoFalhou(f"projeto {nome!r} nao abriu depois de criado")

        await pagina.locator(sel.BOTAO_MAIS_OPCOES).first.click(timeout=15_000)
        await pagina.locator(sel.MENU_RENOMEAR).first.click(timeout=15_000)
        campo = pagina.locator(sel.CAMPO_NOME_PROJETO).first
        await campo.wait_for(timeout=15_000)
        await campo.fill(nome)
        await pagina.locator(sel.BOTAO_CONCLUIR_NOME).first.click(timeout=15_000)
        await pagina.wait_for_timeout(1500)
        return id_remoto

    async def abrir_projeto(self, s: Sessao, id_remoto: str) -> None:
        """NUNCA por goto. Deep link derruba a app com "Application error".

        O Next espera chegar por navegacao interna. Vai-se a lista e CLICA-SE no card.
        """
        _, _, pagina = s.handle
        if sel.PADRAO_PROJETO + id_remoto in pagina.url:
            return
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        card = pagina.locator(f'a[href*="{id_remoto}"]')
        if await card.count() == 0:
            # projeto de outra conta simplesmente nao aparece na lista. Seguir daqui
            # geraria no projeto errado — ou em nenhum.
            raise GeracaoFalhou(
                f"projeto {id_remoto} nao esta na lista de {s.conta.email}")
        await card.first.click()
        await pagina.locator(sel.CAMPO_PROMPT).first.wait_for(timeout=30_000)

    # -------------------------------------------------------------------- chip
    def _painel(self, pagina):
        return pagina.locator(sel.PAINEL_CHIP).first

    async def _texto_do_chip(self, pagina) -> str:
        bruto = await pagina.locator(sel.CHIP_CONFIG).first.inner_text(timeout=15_000)
        return dom.seguro(bruto.replace("\n", " "))

    async def _clicar_unico(self, escopo, seletor: str, o_que: str) -> None:
        """Clica so quando ha EXATAMENTE um candidato.

        E aqui que a regra "nunca por posicao" vira codigo. Um `.first`/`.last` num
        menu ambiguo ja mandou um video do usuario para a lixeira, porque o ultimo item
        do menu do card e "Mover para a lixeira". Ambiguidade e erro, nao escolha.
        """
        loc = escopo.locator(seletor)
        n = await loc.count()
        if n != 1:
            raise GeracaoFalhou(f"{o_que}: esperava 1 candidato para {seletor!r}, achei {n}")
        await loc.click(timeout=15_000)

    async def _configurar(self, pagina, p: Pedido) -> str:
        """Abre o chip, ajusta tudo e CONFERE. Devolve o texto final do chip.

        Nada de passo tolerante. Um `except: segue` engoliu um clique de duracao que
        nao pegou e entregou 8s onde foram pedidos 4s — com credito debitado, arquivo
        na pasta e ninguem percebendo. Se o chip nao confirmar o estado, e erro.
        """
        modo = p.modo.value
        permitidas = sel.PROPORCOES_POR_MODO[modo]
        if p.proporcao not in permitidas:
            raise GeracaoFalhou(
                f"modo {modo} aceita so {permitidas}, e o job pediu {p.proporcao}")

        await pagina.locator(sel.CHIP_CONFIG).first.click(timeout=15_000)
        painel = self._painel(pagina)
        await painel.wait_for(timeout=15_000)

        # ordem importa: o modo redefine as listas de proporcao, duracao e modelo
        await self._clicar_unico(painel, sel.BOTAO_MODO[modo], "modo")
        await pagina.wait_for_timeout(1200)

        await self._clicar_unico(painel, sel.DROPDOWN_MODELO, "dropdown de modelo")
        await pagina.wait_for_timeout(1000)
        await self._clicar_unico(pagina, sel.OPCAO_MODELO[p.modelo.value], "modelo")
        await pagina.wait_for_timeout(1000)

        # o modelo NAO aparece no texto do chip: confere-se aqui, com o painel aberto
        rotulo = dom.seguro(await painel.locator(sel.DROPDOWN_MODELO).first.inner_text())
        esperado = dom.sem_acento(sel.OPCAO_MODELO[p.modelo.value].split('"')[1])
        if esperado not in rotulo:
            raise GeracaoFalhou(f"modelo nao trocou: dropdown diz {rotulo!r}, "
                                f"esperado {esperado!r}")

        await self._clicar_unico(painel, sel.PROPORCAO[p.proporcao], "proporcao")
        # `None` em imagem: o painel nao tem grupo de duracao naquele modo, e procurar
        # o botao ali derrubaria toda geracao de imagem. Nos tres modos de video vale a
        # duracao DO PEDIDO — ate a Task 15 era uma constante de 8s aqui, e todo video
        # saia 70% mais caro do que o de 4s sem ninguem ver.
        duracao = None if p.modo is Modo.IMAGEM else p.duracao
        if duracao:
            await self._clicar_unico(painel, sel.DURACAO[duracao], "duracao")
        await self._clicar_unico(painel, sel.QUANTIDADE[p.quantidade], "quantidade")

        await pagina.keyboard.press("Escape")
        await pagina.wait_for_timeout(1200)

        chip = await self._texto_do_chip(pagina)
        faltando = [
            alvo for alvo in (
                sel.ROTULO_MODO_NO_CHIP[modo],
                sel.PROPORCAO[p.proporcao].split('"')[1],
                f"x{p.quantidade}",
                *(() if duracao is None else (duracao,)),
            ) if alvo not in chip
        ]
        if faltando:
            raise GeracaoFalhou(
                f"chip nao confirma a configuracao: diz {chip!r}, faltou {faltando}")
        return chip

    # ------------------------------------------------------------------- gerar
    async def gerar(self, s: Sessao, p: Pedido) -> str:
        _, _, pagina = s.handle
        antes = await pagina.locator(sel.CARD_GRADE).count()

        await self._configurar(pagina, p)
        await self._anexar(pagina, p)

        # ARMADILHA: `input[aria-label="Texto editavel"]` e o NOME DO PROJETO. Escrever
        # ali renomeia o projeto em vez de gerar, e o sintoma e "nada acontece" — que e
        # o pior tipo de bug aqui. O campo certo e o unico contenteditable visivel.
        campo = pagina.locator(sel.CAMPO_PROMPT).first
        await campo.click(timeout=15_000)
        await campo.fill("")
        await campo.type(p.prompt, delay=8)

        # dois botoes contem a palavra "Criar": anexar e enviar. So a ligadura separa.
        await pagina.locator(sel.BOTAO_ENVIAR).first.click(timeout=15_000)

        if await pagina.locator(sel.AVISO_SEM_CREDITO).count() > 0:
            raise SaldoInsuficiente(f"conta {s.conta.email} sem credito")

        # o card novo aparecendo e a UNICA confirmacao de que o Flow aceitou o pedido
        await self._esperar_card_novo(pagina, antes)
        indice = 0 if sel.CARDS_NOVOS_PRIMEIRO else antes
        id_remoto = f"card-{indice}"
        self._repeticoes[id_remoto] = 0
        self._modo_do_card[id_remoto] = p.modo
        return id_remoto

    async def _esperar_card_novo(self, pagina, antes: int, teto_ms: int = 120_000) -> None:
        gasto = 0
        while gasto < teto_ms:
            if await pagina.locator(sel.CARD_GRADE).count() > antes:
                return
            await pagina.wait_for_timeout(2000)
            gasto += 2000
        raise GeracaoFalhou("o Flow nao criou card novo depois do envio")

    async def _anexar(self, pagina, p: Pedido) -> None:
        """Um unico `input[type=file]` (multiple, accept=image/*) serve a tudo.

        Quem decide o que cada arquivo significa e o MODO ja escolhido no chip, nao o
        seletor: em `crop_free` os arquivos viram frame inicial e final, em
        `chrome_extension` viram referencias. Por isso `_configurar` roda ANTES daqui.
        """
        if p.modo is Modo.INGREDIENTS2V:
            arquivos = [str(Path(r)) for r in p.referencias[:3]]
        elif p.modo is Modo.FRAMES2V:
            arquivos = [str(Path(x)) for x in (p.frame_inicial, p.frame_final) if x]
        else:
            return
        if not arquivos:
            raise GeracaoFalhou(f"modo {p.modo.value} sem arquivo para anexar")
        await pagina.locator(sel.UPLOAD).first.set_input_files(arquivos)
        await pagina.wait_for_timeout(3000)

    # --------------------------------------------------------------- consultar
    async def _card(self, pagina, id_remoto: str):
        indice = int(id_remoto.split("-")[1])
        cards = pagina.locator(sel.CARD_GRADE)
        if await cards.count() <= indice:
            raise GeracaoFalhou(f"{id_remoto} sumiu da grade")
        return cards.nth(indice)

    async def consultar(self, s: Sessao, id_remoto: str) -> Estado:
        """Le o estado pela GRADE, nunca pela conversa.

        O agente so responde quando perguntado; a grade atualiza sozinha. E nao se
        contam elementos <video> soltos: o Flow usa <video> em outros lugares da tela,
        e o contador declararia "pronto" antes de existir card.
        """
        _, _, pagina = s.handle
        card = await self._card(pagina, id_remoto)

        if await card.locator(sel.CARD_FALHA).count() > 0:
            vezes = self._repeticoes.get(id_remoto, 0)
            if vezes < MAX_REPETICOES:
                # `redo` repete SEM cobrar de novo. Repetir aqui, dentro de `consultar`,
                # e o que faz o orquestrador nem tomar conhecimento: `_esperar` ve
                # RODANDO e continua o polling, sem gastar tentativa do job nem
                # reabrir navegador. O usuario decidiu que nao quer notificacao — entao
                # a repeticao tem que ser invisivel e barata.
                self._repeticoes[id_remoto] = vezes + 1
                await self._clicar_unico(card, sel.BOTAO_REPETIR_CARD,
                                         f"repetir {id_remoto}")
                await pagina.wait_for_timeout(3000)
                return Estado.RODANDO
            # Sob o banner de alta demanda a falha e do Google, nao da conta. Levantar
            # SaldoInsuficiente ou SessaoMorta aqui marcaria a conta seca ou expirada e
            # um pico do Google derrubaria o pool inteiro. GeracaoFalhou nao marca nada.
            if await pagina.locator(sel.AVISO_ALTA_DEMANDA).count() > 0:
                raise GeracaoFalhou(
                    "Flow em alta demanda: falha do Google, a conta esta boa")
            return Estado.FALHOU

        if await card.locator(sel.CARD_MIDIA).count() > 0:
            return Estado.PRONTO
        return Estado.RODANDO

    # ------------------------------------------------------------------ baixar
    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path:
        _, _, pagina = s.handle
        card = await self._card(pagina, id_remoto)

        # o <video> nao e clicavel (e miniatura). O alvo e o centro do container.
        caixa = await card.bounding_box()
        if caixa is None:
            raise GeracaoFalhou(f"{id_remoto} sem caixa na tela — nao da para clicar")
        await pagina.mouse.click(caixa["x"] + caixa["width"] / 2,
                                 caixa["y"] + caixa["height"] / 2, button="right")
        menu = pagina.locator(sel.MENU_CARD).last
        await menu.wait_for(timeout=15_000)
        await self._logar_menu(pagina, menu)

        await self._clicar_unico(menu, sel.MENU_BAIXAR, "item Baixar")
        await pagina.wait_for_timeout(1500)

        # assumir VIDEO quando o modo nao e conhecido e o default seguro: pedir "720p"
        # num menu de imagem da zero candidatos e o `_clicar_unico` grita, enquanto
        # pedir "Tamanho original" num menu de video baixaria em silencio o errado.
        chave = RESOLUCAO[self._modo_do_card.get(id_remoto, Modo.T2V)]
        async with pagina.expect_download(timeout=self.timeout_ms) as espera:
            # os rotulos do submenu tem QUEBRA DE LINHA ("270p\nGIF animado"): passar o
            # rotulo inteiro para um seletor CSS estoura o parser. `sel.BAIXAR` ja
            # guarda so a primeira linha.
            await self._clicar_unico(pagina, sel.BAIXAR[chave], f"resolucao {chave}")
        download = await espera.value

        # o Flow escolhe a extensao: jpeg para imagem, mp4 para video. O orquestrador
        # montou `destino` com .png/.mp4 a partir do modo — se salvassemos ali, o banco
        # apontaria para um .png que na verdade e JPEG. Devolver o caminho REAL faz
        # `job.arquivo` ficar verdadeiro sem tocar na Task 7.
        sufixo = Path(download.suggested_filename).suffix or destino.suffix
        real = destino.with_suffix(sufixo)
        real.parent.mkdir(parents=True, exist_ok=True)
        await download.save_as(real)
        return real

    async def _logar_menu(self, pagina, menu) -> None:
        """Imprime os candidatos ANTES de clicar. Barato, e ja teria evitado um
        clipe do usuario indo para a lixeira."""
        itens = [dom.seguro(t) for t in
                 await menu.locator(sel.MENU_ITEM).all_inner_texts()]
        print("menu do card:", [i.splitlines()[0] for i in itens if i])
```

Uma nota sobre `menu = pagina.locator(sel.MENU_CARD).last`: aqui `.last` **não** viola a regra do "nunca por posição". A regra é sobre escolher **item** de menu — foi um `.last` sobre itens que mandou um clipe para a lixeira. `.last` sobre o conjunto de menus abertos escolhe o submenu que acabou de nascer, e o item dentro dele continua sendo escolhido por texto, com `_clicar_unico` recusando ambiguidade.

- [ ] **Step 2: Reescrever a fumaça em cima do transporte**

A `fumaca.py` de hoje é um script solto que fala com o Flow direto — foi assim que o chip foi descoberto, e cumpriu o papel. A partir daqui ela passa a exercitar o **transporte**, senão são dois códigos concorrentes e só um é o que roda em produção.

`flowpool/transporte/pw/fumaca.py`:

```python
"""Fumaca contra o Flow real, pelo transporte de verdade.

GASTA CREDITO em video. Imagem e gratuita (MEDIDO em 2026-08-20: saldo 50 -> 50).

Uso:
    python -m flowpool.transporte.pw.fumaca <perfil> <modo> <modelo> "<prompt>" \
        [arquivos...] [--proporcao 9:16] [--quantidade 1] [--duracao 4s]
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from flowpool.nucleo.tipos import Conta, Modelo, Modo, Tier
from flowpool.transporte.base import Estado, Pedido, Sessao
from flowpool.transporte.pw.transporte import TransportePlaywright

PRODUTO_FUMACA = "Fumaca"


async def entrar_no_projeto(t: TransportePlaywright, s: Sessao, produto: str) -> str:
    """Adota pelo nome e so cria se nao existir — a MESMA ordem do orquestrador.

    De proposito: rodar a fumaca duas vezes nao pode deixar dois "Fumaca" na conta, e
    e essa a unica forma de a fumaca provar a adocao.
    """
    achado = next((p for p in await t.listar_projetos(s) if p.nome == produto), None)
    id_remoto = achado.id_remoto if achado else await t.criar_projeto(s, produto)
    print("adotado" if achado else "criado", "projeto:", id_remoto)
    await t.abrir_projeto(s, id_remoto)
    return id_remoto


async def main(a: argparse.Namespace) -> None:
    t = TransportePlaywright()
    conta = Conta(id=1, email="fumaca", tier=Tier.FREE, perfil=a.perfil)
    modo = Modo(a.modo)
    pedido = Pedido(
        modo=modo, modelo=Modelo(a.modelo), prompt=a.prompt,
        referencias=a.arquivos if modo is Modo.INGREDIENTS2V else [],
        frame_inicial=a.arquivos[0] if modo is Modo.FRAMES2V and a.arquivos else None,
        frame_final=a.arquivos[1] if modo is Modo.FRAMES2V and len(a.arquivos) > 1 else None,
        proporcao=a.proporcao, quantidade=a.quantidade,
    )
    s = await t.abrir_sessao(conta)
    try:
        antes = await t.saldo(s)
        print("saldo antes:", antes)
        await entrar_no_projeto(t, s, PRODUTO_FUMACA)
        id_remoto = await t.gerar(s, pedido)
        print("gerando:", id_remoto)
        while await t.consultar(s, id_remoto) is Estado.RODANDO:
            await asyncio.sleep(10)
            print("...")
        # extensao de mentira de proposito: o transporte troca pela real do Flow
        print("arquivo:", await t.baixar(s, id_remoto, Path("scratch/fumaca.mp4")))
        depois = await t.saldo(s)
        print(f"saldo depois: {depois}  (custo real: {antes - depois})")
    finally:
        await t.fechar(s)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("perfil")
    p.add_argument("modo", choices=[m.value for m in Modo])
    p.add_argument("modelo", choices=[m.value for m in Modelo])
    p.add_argument("prompt")
    p.add_argument("arquivos", nargs="*")
    p.add_argument("--proporcao", default="9:16")
    p.add_argument("--quantidade", type=int, default=1)
    # a fumaca GASTA CREDITO em video, e a duracao e o que mais mexe na conta: 7
    # creditos em 4s contra 12 em 8s no Omni Flash. Default no padrao do dominio.
    p.add_argument("--duracao", choices=list(DURACOES), default=DURACAO_PADRAO)
    asyncio.run(main(p.parse_args()))
```

- [ ] **Step 3: Fumaça de imagem primeiro — é grátis**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento \
  imagem nano-banana "a red ceramic mug on a linen tablecloth"
```

Expected:

```
saldo antes: 50
criado projeto: 6f0c...-...
gerando: card-0
...
arquivo: scratch\fumaca.jpeg
saldo depois: 50  (custo real: 0)
```

Repare em duas coisas: a extensão saiu **`.jpeg`**, não o `.mp4` que o comando pediu — é o transporte respeitando `suggested_filename`; e o custo saiu **0**, confirmando a medição. Se a extensão vier `.mp4`, `download.suggested_filename` não foi respeitado e o banco vai apontar para um arquivo mentiroso.

- [ ] **Step 4: Fumaça de vídeo — a que gasta**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento \
  t2v omni-flash "a slow push-in on a ceramic mug, soft window light"
```

Expected: `adotado projeto:` com o **mesmo** id do Step 3 (a adoção funcionando), `scratch/fumaca.mp4`, e `custo real: 7` — sete, e não doze, porque desde a Task 15 o padrão é o clipe de **4s**. Se sair 12, a duração não chegou ao chip: confira que `_configurar` usa `p.duracao` e não uma constante. Para medir o de 8s de propósito, `--duracao 8s`. O vídeo tem que sair **vertical** — abra o arquivo e confirme. Saída em 16:9 significa que o clique na proporção não pegou e o `_configurar` não pegou o erro; nesse caso o defeito está na verificação, não só no clique.

- [ ] **Step 5: Provar que a verificação do chip morde**

Peça uma configuração que o modo não aceita e confirme que o transporte **recusa antes de gastar**:

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento \
  t2v omni-flash "teste" --proporcao 1:1
```

Expected: `GeracaoFalhou: modo t2v aceita so ('9:16', '16:9'), e o job pediu 1:1`, **sem** o Chrome chegar a clicar no chip e **sem** crédito gasto. Esse passo é o que separa este plano do anterior: o comportamento antigo era clicar, não achar o botão, seguir em frente e gerar em 9:16 sem avisar.

- [ ] **Step 6: Corrigir a tabela de custos com o que foi medido**

A Task 2 rodou com os números **estimados** e o texto dela já foi corrigido para os medidos — este step é onde a correção chega ao código. Duas linhas mudam em `flowpool/nucleo/custos.py`:

- **Omni Flash 20/20/10 → 12/12/6.** MEDIDO 2026-08-20, conta FREE, 720p / 8s / 9:16 / x1: saldo 50 → 38.
- **Nano Banana 2/2/1 → 0/0/0.** MEDIDO: imagem custa ZERO — saldo 50 antes e 50 depois, e o Flow nem pede confirmação. Desempata a divergência que o spec carregava (1-2 vs zero).

E em `tests/test_custos.py`, os mesmos números — **a contagem de casos não muda**, só os valores esperados.

`cabe_no_free` continua verdadeiro em todos os casos testados (0 × 4 = 0 ≤ 50; 12 ≤ 50), então nenhum outro teste muda.

**Consequência de domínio, e ela é boa:** com imagem a **zero**, a escolha de conta deixa de discriminar por saldo em job de imagem — qualquer conta viva serve. Era exatamente o que o spec previa em *Notas de custo*, e agora está confirmado.

> **Nota de revisão (Task 15).** O que este step **não** resolveu: a medição deu 12 créditos para uma configuração de **8s**, e o agente conversacional anunciou **7** para a mesma família em **4s**. Enquanto a tabela era por (modelo, tier), esses dois números não cabiam nela — e o app reservava 12 para todo vídeo, inclusive os que sairiam por 7. A Task 15 fecha isso: a tabela de vídeo passa a ser por **(modelo, tier, duração)**, e o listing final está na Task 2.

- [ ] **Step 7: Rodar a suíte**

Run: `.venv/Scripts/pytest -q`
Expected: `253 passed` — o mesmo total do Task 10. Valores mudaram, casos não.

- [ ] **Step 8: Commit**

```bash
git add flowpool/transporte/pw/transporte.py flowpool/transporte/pw/fumaca.py \
        flowpool/nucleo/custos.py tests/test_custos.py
git commit -m "feat: transporte playwright pelo chip, com verificacao e custos medidos"
```

---

### Task 12: Fumaça dos quatro modos do chip

**Files:**
- Modify: `flowpool/transporte/pw/seletores.py` (correções e os slots de upload), `flowpool/transporte/pw/transporte.py` (só se um modo exigir passo extra)

Os quatro modos do spec são os quatro do **chip** — `image Imagem`, `videocam Vídeo`, `crop_free Frames`, `chrome_extension Elementos`. Não existem três `input[type=file]` distintos para referência, frame inicial e frame final: existe **um** input (`multiple`, `accept="image/*"`), e quem dá significado aos arquivos é o **modo já selecionado no chip**. Por isso `_configurar` roda antes de `_anexar` no transporte, e não o contrário.

O que ainda não está medido dentro de cada modo são os **slots**: em `crop_free`, como a UI separa "primeiro frame" de "último frame"; em `chrome_extension`, como ela lista as até três referências. Este task mede isso gerando de verdade.

- [ ] **Step 1: Mapear os slots dos modos Frames e Elementos**

Com o `conferir.py` da Task 9 aberto no projeto, troque o chip para `crop_free` e depois para `chrome_extension`, e anote em cada um: quantos `input[type=file]` a página passa a ter, e que rótulo/ligadura identifica cada slot. Registre em `seletores.py`:

```python
# --- slots de upload por modo -------------------------------------------
# UM input serve a tudo (multiple, accept=image/*). O MODO decide o significado:
#   crop_free          -> primeiro e ultimo frame, na ordem em que sao enviados
#   chrome_extension   -> ate 3 referencias de personagem/produto
# Se a UI mostrar slots nomeados, eles entram aqui e `_anexar` passa a enviar um a um.
SLOT_FRAME_INICIAL = "..."   # MEDIR neste task
SLOT_FRAME_FINAL = "..."     # MEDIR neste task
SLOT_REFERENCIA = "..."      # MEDIR neste task
```

Se os três continuarem `"..."` depois da medição porque a UI realmente usa um input só e ordem posicional, **deixe assim e escreva isso no comentário** — um seletor inventado é pior do que um campo vazio, porque o `conferir.py` passa a acusar quebra em algo que nunca existiu.

- [ ] **Step 2: Fumaça do modo Imagem (`image`)**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento \
  imagem nano-banana "a red ceramic mug on a linen tablecloth"
```

Expected: `scratch/fumaca.jpeg`, `custo real: 0`, e o chip confirmado com `imagem`, `crop_9_16`, `x1` — sem duração nenhuma, porque imagem não tem grupo de duração no painel. E `POST /jobs` com `modo=imagem` e `duracao` no corpo é **422** desde a Task 15.

- [ ] **Step 3: Fumaça do modo Frames (`crop_free`)**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento \
  frames2v omni-flash "she slowly turns toward the mirror" \
  "_Personagens/imagens/Clara/clara_01_frontal.jpg"
```

Expected: `scratch/fumaca.mp4` e o **primeiro frame é a foto da Clara**. Se o vídeo começar em outra coisa, o arquivo foi para o slot errado (ou o modo não trocou) — volte ao Step 1.

- [ ] **Step 4: Fumaça do modo Elementos (`chrome_extension`)**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento \
  ingredients2v omni-flash "she walks toward the camera in a bright studio" \
  "_Personagens/imagens/Clara/clara_01_frontal.jpg" \
  "_Personagens/imagens/Clara/clara_02_tresquartos.jpg" \
  "_Personagens/imagens/Clara/clara_03_perfil.jpg"
```

Expected: `scratch/fumaca.mp4` com o rosto da Clara. Três referências é o teto do Flow — o quarto arquivo é cortado pelo `[:3]` no transporte, e a API já recusa com 422.

- [ ] **Step 5: Fumaça do modo Vídeo (`videocam`) em lote de imagem — a prova da quantidade**

```bash
# quatro variacoes de imagem: a grade tem que ganhar QUATRO cards
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento \
  imagem nano-banana "a red ceramic mug on linen" --quantidade 4
```

Expected: quatro cards novos na grade e o chip confirmando `x4`. Com imagem a zero crédito, a prova aqui **não** é o saldo — é a contagem de cards. Um único card significa que o clique em `QUANTIDADE` não pegou, e é isso que faria o app reservar 4× o custo de um modelo pago e gastar 1× (ou o contrário) sem ninguém notar.

- [ ] **Step 6: Confirmar a ordem da grade**

Depois do Step 5 a grade tem vários cards de idades diferentes. Confirme onde o card novo entrou e ajuste `sel.CARDS_NOVOS_PRIMEIRO`. O teste é direto: gere mais um, e veja se `card-0` é o recém-nascido ou o mais velho.

Errar isso é o modo de falha mais caro do transporte inteiro, porque **não dá erro**: o app baixa um clipe antigo, grava o caminho no banco, marca o job como pronto, e o vídeo errado entra no vídeo final.

- [ ] **Step 7: Fumaça de projeto — reuso e adoção**

Rode qualquer uma das fumaças acima **duas vezes seguidas** e olhe a tela inicial do Flow.

Expected: a segunda execução imprime `adotado projeto: <mesmo id>` e a lista da conta tem **um** único "Fumaca". Dois "Fumaca" significam que `listar_projetos` não devolveu nome ou id direito — ou que `criar_projeto` parou na primeira etapa e deixou o projeto com o nome padrão. Corrija em `seletores.py`; apague o duplicado à mão antes de seguir, porque o app não apaga projeto nenhum, de propósito.

- [ ] **Step 8: Rodar o conferidor de novo**

```bash
.venv/Scripts/python -m flowpool.transporte.pw.conferir flowpool/perfis/mapeamento <uuid>
```

Expected: `0 faltando`. Depois de quatro modos exercitados, um seletor faltando aqui é seletor que só existia na cabeça de quem escreveu.

- [ ] **Step 9: Commit**

```bash
git add flowpool/transporte/pw
git commit -m "feat: os quatro modos do chip verificados contra o flow real"
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


def test_padroes_sao_t2v_omni_flash_e_vertical(cliente):
    # omni-flash porque e o padrao de video do Flow; 9:16 porque o canal e TikTok.
    # 9:16 tambem e uma das DUAS unicas proporcoes que o chip aceita em video — as
    # outras tres do painel so existem em modo imagem.
    jid = cliente.gerar(prompt="p", produto="Moda", nome="n")
    j = cliente.estado(jid)
    assert j["modo"] == "t2v"
    assert j["modelo"] == "omni-flash"
    assert j["proporcao"] == "9:16"
    assert j["quantidade"] == 1
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
              modelo: str = "omni-flash", destino: str | None = None,
              referencias: list[str] | None = None,
              frame_inicial: str | None = None, frame_final: str | None = None,
              proporcao: str = "9:16", quantidade: int = 1,
              duracao: str | None = None, prioridade: int = 0) -> int:
        # `destino` fica de fora quando e None: a API o deriva do produto
        # (`{produto}/clipes`), e mandar null explicito seria a mesma coisa com ruido.
        #
        # `duracao` fica de fora pelo motivo OPOSTO, e ele importa: em `modo=imagem` a
        # API devolve 422 se o campo vier, porque nao existe grupo de duracao no painel
        # do Flow em imagem. Mandar sempre "4s" faria todo pedido de imagem falhar. Sem
        # o campo, video herda o padrao do dominio, que ja e 4s.
        #
        # `modelo="omni-flash"` e o padrao de video do Flow, nao Veo. `proporcao="9:16"`
        # porque o canal e TikTok — e porque video so aceita 9:16 e 16:9 no chip.
        # `quantidade=1` porque video sai sempre um; mais de um em video e 422.
        r = self.http.post("/jobs", json={
            "modo": modo, "modelo": modelo, "prompt": prompt, "produto": produto,
            "nome": nome, "referencias": referencias or [],
            "frame_inicial": frame_inicial, "frame_final": frame_final,
            "proporcao": proporcao, "quantidade": quantidade,
            "prioridade": prioridade,
            **({"destino": destino} if destino else {}),
            **({"duracao": duracao} if duracao else {}),
        })
        r.raise_for_status()
        return r.json()["id"]

    def lote(self, jobs: list[dict]) -> list[int]:
        completos = [{"modo": "t2v", "modelo": "omni-flash", "referencias": [],
                      "proporcao": "9:16", "quantidade": 1,
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
        """Bloqueia ate o job terminar. Devolve o caminho do arquivo.

        O caminho pode NAO ter a extensao que voce esperava: o Flow entrega imagem em
        JPEG, e o transporte respeita `download.suggested_filename` em vez de assumir.
        Use o valor devolvido, nao monte o caminho por conta propria.
        """
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

# UM por enquanto, e o motivo nao e RAM.
#
# Cada worker precisa de uma janela de Chrome VISIVEL: o IntersectionObserver que
# renderiza a lista de projetos e libera o aviso de privacidade nao dispara com
# `visibilityState == 'hidden'`, e o Chrome no Windows marca como oculta a janela que
# esta totalmente coberta por outra. Duas janelas empilhadas numa tela so sao uma
# janela funcionando e outra travada sem erro nenhum.
#
# Alem disso, duas sessoes no mesmo perfil sao impossiveis: dois Chromes com o mesmo
# --user-data-dir fazem o segundo entregar para o primeiro. O teto so pode subir com
# contas distintas E espaco de tela — medir na Task 14, Step 6.
MAX_SIMULTANEOS = 1


def main() -> None:
    repo = Repo(BANCO)
    repo.criar_esquema()
    app = criar_app(repo=repo, transporte=TransportePlaywright(), raiz=RAIZ,
                    rodar_motor=True, max_simultaneos=MAX_SIMULTANEOS)
    uvicorn.run(app, host="127.0.0.1", port=8777)


if __name__ == "__main__":
    main()
```

`criar_app` ainda não aceita `max_simultaneos` — o `Orquestrador` aceita desde a Task 7, mas a fábrica não repassa. Abra `flowpool/api/app.py` e mude duas linhas:

```python
def criar_app(repo: Repo, transporte: FlowTransport, raiz: Path | str,
              rodar_motor: bool = False, max_simultaneos: int = 3) -> FastAPI:
    orq = Orquestrador(repo, transporte, raiz, max_simultaneos=max_simultaneos)
```

O default fica em 3 de propósito: mudá-lo aqui alteraria o comportamento dos testes da API, que rodam com transporte falso e nenhuma janela. Quem sabe do custo de tela é o `__main__`, e é ele que passa 1.

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/Scripts/pytest tests/test_cliente.py -q`
Expected: `4 passed`

- [ ] **Step 6: Rodar a suíte inteira**

Run: `.venv/Scripts/pytest -q`
Expected: `257 passed` (253 + 4 do cliente)

> **Nota (2026-08-20).** Esses totais somam só os arquivos de teste que este plano lista. `tests/test_transporte_pw.py` (37 casos) nasceu na execução ao vivo da Task 11, junto de `analise.py`, e nunca entrou nas somas acima. Com ele, e já com a Task 15 fechada, a suíte real é de **290**.

- [ ] **Step 7: Commit**

```bash
git add flowpool/cliente.py flowpool/__main__.py tests/test_cliente.py
git commit -m "feat: ponto de entrada e cliente do pipeline"
```

---

### Task 14: Ponta a ponta com uma conta real

**Files:**
- Create: `flowpool/LEIA-ME.md`
- Modify: `flowpool/transporte/pw/seletores.py` (`AVISO_SEM_CREDITO`, medido no Step 5)

- [ ] **Step 1: Subir o serviço**

```bash
.venv/Scripts/python -m flowpool
```

Expected: `Uvicorn running on http://127.0.0.1:8777`

- [ ] **Step 2: Cadastrar a conta de teste**

Em outro terminal:

```bash
curl -X POST http://127.0.0.1:8777/contas -H "Content-Type: application/json" \
  -d "{\"email\":\"<a conta gratis de teste>\",\"tier\":\"free\"}"
```

Expected: no terminal do serviço aparecem as `INSTRUCOES` e uma janela do Chrome abre — **um Chrome comum, não o do Playwright**. Siga os cinco passos impressos: logar, consentimento com as **duas caixas desmarcadas** → "Avançar", aviso de privacidade **rolado até o fim** → "Continuar", conferir a lista de projetos, **fechar a janela**. Aí o `curl` devolve a conta com `"saude":"viva"`.

Se o `curl` voltar 409 com "ja esta aberto em outro Chrome", há uma janela sobrando naquele perfil — feche-a e repita. Se voltar 409 com "login nao concluiu", a janela foi fechada antes de a sessão existir.

**Confirme que a fila não congelou durante o login:** com o serviço no ar e o Chrome aberto, `GET /saude` tem que responder na hora. Se demorar, o `asyncio.to_thread` do `cadastrar` não está no lugar e o motor inteiro fica parado pelos minutos do login.

- [ ] **Step 3: Disparar um lote que a conta paga inteiro**

Omni Flash custa **12** em conta grátis (medido). Quatro cabem em 50 e sobram 2:

```bash
curl -X POST http://127.0.0.1:8777/jobs/lote -H "Content-Type: application/json" -d "{\"jobs\":[{\"modo\":\"t2v\",\"modelo\":\"omni-flash\",\"prompt\":\"a cat on a table\",\"produto\":\"Fumaca\",\"destino\":\"scratch\",\"nome\":\"e1\"},{\"modo\":\"t2v\",\"modelo\":\"omni-flash\",\"prompt\":\"a dog in a park\",\"produto\":\"Fumaca\",\"destino\":\"scratch\",\"nome\":\"e2\"},{\"modo\":\"t2v\",\"modelo\":\"omni-flash\",\"prompt\":\"rain on a window\",\"produto\":\"Fumaca\",\"destino\":\"scratch\",\"nome\":\"e3\"},{\"modo\":\"t2v\",\"modelo\":\"omni-flash\",\"prompt\":\"steam over a mug\",\"produto\":\"Fumaca\",\"destino\":\"scratch\",\"nome\":\"e4\"}]}"
```

Expected: `{"ids":[1,2,3,4]}`.

- [ ] **Step 4: Acompanhar até o fim**

```bash
curl http://127.0.0.1:8777/saude
```

Expected, ao final: `fila_pendente: 0`, `credito_total` perto de 2, e `scratch/e1.mp4` … `scratch/e4.mp4` no disco. Todos os quatro no **mesmo** projeto "Fumaca" da conta — um `projeto_criado` e três `projeto_adotado` na tabela de eventos.

- [ ] **Step 5: Esvaziar a conta e medir `AVISO_SEM_CREDITO`**

Um quinto job com 2 créditos na conta é o único jeito honesto de medir o aviso de saldo insuficiente — é o último item aberto do mapa de seletores.

```bash
curl -X POST http://127.0.0.1:8777/jobs -H "Content-Type: application/json" \
  -d "{\"modo\":\"t2v\",\"modelo\":\"omni-flash\",\"prompt\":\"a bird on a wire\",\"produto\":\"Fumaca\",\"destino\":\"scratch\",\"nome\":\"e5\"}"
```

Expected: o job **fica pendente** com `reavaliar_em` preenchido — **não** falha. Como o guarda local (`reservar`) já barra 12 contra 2, o job nem chega ao Flow, e é esse comportamento que sustenta o pool: sem conta apta, o job espera o reset em vez de queimar tentativas.

Para ver o aviso da própria UI, abra o projeto no Chrome do perfil e mande gerar à mão. Anote o texto exato e preencha:

```python
AVISO_SEM_CREDITO = 'text=/<o texto medido>/i'
```

Rode `conferir.py` de novo — o resumo tem que fechar em `0 faltando, 0 pendentes`.

- [ ] **Step 6: Medir o teto de simultâneos**

Com **duas** contas cadastradas e `MAX_SIMULTANEOS = 2`, dispare dois jobs de produtos diferentes e observe as duas janelas do Chrome lado a lado, **sem sobreposição**. Se ambas concluírem, o teto 2 é real nesta máquina; se a janela de trás travar sem erro, o Chrome a marcou como ocluída e o teto continua 1.

Anote o número medido em `MAX_SIMULTANEOS` e no LEIA-ME. Este é o número que o spec deixou explicitamente em aberto — deixar 3 chutado ali era o que transformaria "não cabe na tela" num job travado sem mensagem.

- [ ] **Step 7: Escrever o LEIA-ME**

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

Abre uma janela do **Chrome comum** (não o do Playwright: o Google recusa o login em
navegador dirigido por automação). O terminal do serviço imprime os cinco passos:

1. Logar na conta Google.
2. Consentimento: **deixar as duas caixas desmarcadas** → "Avançar".
3. Aviso de privacidade: **rolar até o fim do texto** → "Continuar". O botão só habilita
   quando o fim do texto entra na tela de verdade.
4. Conferir que a lista de projetos carregou.
5. **Fechar a janela.** O cadastro continua sozinho.

A sessão fica guardada em `perfis/` e vive **cerca de 48 horas**. Quando cair, a conta
aparece em `GET /saude` como expirada e o conserto é `POST /contas/{id}/revalidar` —
que é o mesmo fluxo, com a mesma janela.

## Gerar

    from flowpool.cliente import FlowPool
    fp = FlowPool()
    jid = fp.gerar(prompt="...", produto="Video Roupa", nome="c3-take1")
    caminho = fp.esperar(jid)

Sem dizer o modelo, vídeo sai em **Omni Flash** (é o padrão do próprio Flow) e imagem
em Nano Banana. Sem dizer a proporção, sai em **9:16**. Sem dizer a duração, o clipe
sai com **4s** — o mais barato, e o que o pipeline usa.

    # clipe mais longo, quando a cena pede: custa 12 em vez de 7
    jid = fp.gerar(prompt="...", produto="Panelas Monaco", nome="c1", duracao="8s")

    # quatro variações de imagem para escolher (e imagem não custa crédito)
    jid = fp.gerar(prompt="...", produto="Panelas Monaco", nome="capa",
                   modo="imagem", quantidade=4)

Use o caminho que `esperar` devolve — imagem sai em **.jpeg**, não em .png.

## O que saber

- Conta grátis: 50 créditos/dia. **Medido**: Omni Flash 12 (720p, 8s, 9:16, x1),
  **imagem 0**. Veo Lite 10 / Fast 20 / Quality 100 continuam estimativa.
  **Quality nunca cabe em conta grátis** — vai para conta paga.
- O custo do vídeo depende da configuração, não só do modelo. A tabela serve para
  reservar antes de despachar; o número verdadeiro vem da reconciliação de saldo.
- **Vídeo só aceita 9:16 e 16:9.** As cinco proporções existem, mas as outras três só
  em modo imagem. Pedir 1:1 num vídeo é recusado antes de gastar crédito.
- Duração de vídeo: **4s por padrão**, e o chip aceita `4s`, `6s`, `8s`, `10s`. É
  `duracao` no `POST /jobs`. **Muda o preço**: no Omni Flash, 4s custa 7 créditos e 8s
  custa 12 — numa conta grátis de 50/dia, a diferença entre 7 e 4 vídeos. Imagem não
  tem duração: mandar o campo em `modo=imagem` é 422.
- Vídeo sai sempre **uma** geração por job; `quantidade` acima de 1 só em `modo=imagem`
  (até 4) e é 422 no resto.
- Job sem conta apta não falha: espera o reset e volta sozinho.
- Geração que falha é **repetida sozinha** pelo botão `redo` do próprio card, que não
  cobra de novo. Não há notificação, de propósito.
- Instabilidade do Google ("Flow is currently experiencing high demand") **não** marca
  a conta como seca nem expirada — é falha do Flow, não da conta.
- Uma janela de Chrome **visível** por worker. Janela coberta por outra conta como
  oculta e a geração trava sem erro. Por isso `MAX_SIMULTANEOS` começa em 1.
- `produto` é obrigatório: é a pasta de destino (`{produto}/clipes`, se você não mandar
  `destino`) e o nome do projeto no Flow.
- Um projeto por produto **em cada conta** — projeto do Flow pertence a uma conta só.
  Veja em `GET /projetos`. `DELETE /projetos/{id}` esquece o vínculo local; o projeto
  continua no Flow e é readotado pelo nome na próxima geração.
- Quando o Google mexer no layout: rode
  `python -m flowpool.transporte.pw.conferir <perfil> <uuid>` **antes** de investigar
  qualquer outra coisa. Ele diz qual seletor morreu. O conserto é sempre em
  `transporte/pw/seletores.py`, nunca no transporte.
```

- [ ] **Step 8: Commit**

```bash
git add flowpool/LEIA-ME.md flowpool/transporte/pw/seletores.py
git commit -m "docs: leia-me do flowpool e aviso de saldo medido"
```

---

### Task 15: Duração de vídeo — o campo que faltava, e o crédito que ele economiza

**Files:**
- Modify: `flowpool/nucleo/tipos.py`, `flowpool/nucleo/erros.py`, `flowpool/nucleo/custos.py`, `flowpool/nucleo/escolha.py`, `flowpool/nucleo/credito.py`, `flowpool/dados/esquema.sql`, `flowpool/dados/repo.py`, `flowpool/transporte/base.py`, `flowpool/transporte/falso.py`, `flowpool/transporte/pw/transporte.py`, `flowpool/transporte/pw/fumaca.py`, `flowpool/motor/orquestrador.py`, `flowpool/api/esquemas.py`, `flowpool/api/app.py`
- Test: `tests/test_tipos.py`, `tests/test_custos.py`, `tests/test_escolha.py`, `tests/test_credito.py`, `tests/test_pool.py`, `tests/test_repo.py`, `tests/test_transporte_falso.py`, `tests/test_orquestrador.py`, `tests/test_api.py`

Fecha o item 1 das *Pendências declaradas* da Task 11. O chip do Flow oferece **4s, 6s, 8s e 10s** (medido ao vivo; imagem não tem duração), e o domínio não modelava isso: `Pedido` não tinha o campo e o transporte fixava `DURACAO_PADRAO = "8s"`.

**Por que isso não era detalhe.** O custo depende da duração, e a diferença é grande:

| configuração | créditos | origem |
|---|---|---|
| Omni Flash, 8s, 720p, 9:16, x1 | **12** | **MEDIDO** — saldo 50 → 38 |
| Omni Flash, 4s | **7** | **ANUNCIADO** pelo agente na confirmação; não medido por saldo |

Fixar 8s desperdiçava **70% do crédito em todo vídeo**. Numa conta grátis de 50 créditos por dia, é a diferença entre **4 e 7 vídeos**. E o usuário pediu clipes de 4 segundos explicitamente — o pipeline dele monta cenas de ~5s.

- [ ] **Step 1: `duracao` no domínio**

`flowpool/nucleo/tipos.py` ganha `DURACOES = ("4s", "6s", "8s", "10s")`, `DURACAO_PADRAO = "4s"`, a função pura `duracao_valida(modo, duracao)` e o campo `Job.duracao: str = DURACAO_PADRAO`. `flowpool/nucleo/erros.py` ganha `DuracaoSemCusto`. Os listings finais estão na **Task 1**.

O padrão é **4s** porque é o mais barato e porque é o caso comum do usuário. Duração continua sendo escolha por pedido — o padrão só precisa servir a quem não escolhe.

`duracao_valida` trata `None` como "sem duração", e `None` é o **único** valor válido em imagem. Isso é deliberado: o campo do `Job` sempre tem valor, e é o **modo** que decide se ele significa alguma coisa — o mesmo arranjo de `frame_inicial`, que só quer dizer algo em `frames2v`. Em imagem, o valor fica no padrão e ninguém o lê: o transporte anula, o custo ignora, e a API recusa que o usuário o informe.

- [ ] **Step 2: coluna no banco**

`duracao TEXT NOT NULL DEFAULT '4s'` em `job`, logo depois de `quantidade`, lida e gravada no `repo.py`. Os testes de ida e volta ficam em `tests/test_repo.py` (`test_job_gravado_sem_duracao_sai_com_quatro_segundos` e `test_duracao_sobrevive_ao_banco`) — sem eles, um restart no meio da fila reescreveria todo job pendente para o padrão e o usuário receberia clipes de outro tamanho.

Como nas colunas `proporcao` e `quantidade`, não há passo de migração: o esquema é `CREATE TABLE IF NOT EXISTS`, e um `flowpool.db` anterior à coluna precisa ser recriado à mão. Vale dizer em voz alta, em vez de deixar descobrir por `sqlite3.OperationalError`.

- [ ] **Step 3: `Pedido.duracao` e a constante que sai do transporte**

`transporte/base.py` ganha `duracao: str = DURACAO_PADRAO`, e `transporte/pw/transporte.py` **perde** o `DURACAO_PADRAO = "8s"` e passa a usar `p.duracao`. A verificação do chip (`faltando_no_chip`) já recebia a duração — agora recebe a real, e não a fixa, o que é justamente o que a torna capaz de pegar um clique que não pegou.

`fumaca.py` ganha `--duracao`, com default no padrão do domínio.

- [ ] **Step 4: o custo passa a depender da duração**

É a parte que mais importa. A tabela de vídeo vira `(modelo, tier)` → custo **do clipe de 8s**, e a duração reescala. Vídeo e imagem passam a morar em **dicionários separados**: imagem não tem a dimensão. O listing final está na **Task 2**.

A escala sai dos dois pontos conhecidos do Omni Flash free/pro (`8s = 12` medido, `4s = 7` anunciado). A reta que passa pelos dois é `2 + 1,25` por segundo, o que dá 9,5 em 6s e 14,5 em 10s — arredondados para **cima**, 10 e 15. O 15 tem apoio extra: é o outro número que o agente já anunciou para essa mesma família de pedido. **6s, 10s e os três Veo são EXTRAPOLADOS**, e o código diz isso. Quem corrige é a reconciliação — a tabela nunca foi preço, é reserva.

O arredondamento é sempre para cima, e não é preciosismo: reserva curta demais aprova uma conta que não paga o job, o Flow recusa, o job volta pra fila e a conta é marcada seca por erro de cálculo.

**A duração propaga por `custo_de`, `cabe_no_free`, `apta`, `escolher` e `reservar`** — o mesmo caminho que `quantidade` já percorria, e pelo mesmo motivo: se só a reserva soubesse a duração, a escolha de conta aprovaria uma conta que não paga o job.

> **Contrato substituído conscientemente.** `custo_de(modelo, tier)` **sem duração** passa a valer o custo do clipe **padrão (4s)**, não mais o de 8s. O default tinha que ser o padrão do domínio: um default divergente (8s) faria a reserva do job comum sair errada em silêncio. Consequência: os testes que conferem número de crédito passaram a dizer explicitamente de qual clipe falam — `custo_de(VEO_FAST, FREE, "8s") == 20`, `reservar(c, VEO_FAST, duracao="8s") == 20`, e assim por diante em `test_custos`, `test_escolha`, `test_credito`, `test_pool`, `test_transporte_falso` e `test_orquestrador`. Nenhum caso foi apagado; todos foram tornados explícitos.

- [ ] **Step 5: API**

`PedidoJob.duracao: str = DURACAO_PADRAO`, validado por `duracao_coerente_com_o_modo`: fora da lista → 422; duração em `modo=imagem` → 422, com a mensagem dizendo que imagem não tem duração. `RespostaJob` passa a mostrar o campo. Listings finais na **Task 8**.

A recusa em imagem olha `model_fields_set`, e não o valor — o padrão do domínio é `"4s"`, então comparar valor recusaria **todo** pedido de imagem. É o mesmo critério que `PatchConta` já usa para o proxy, e pelo mesmo motivo: ausência e valor são coisas diferentes.

- [ ] **Step 6: Rodar a suíte**

Run: `.venv/Scripts/pytest -q`
Expected: `290 passed` (257 → 290; +33 casos).

> **Correção de total (Task 16).** O painel web (o "plano 2" citado no fim deste arquivo) foi implementado sem entrar aqui como task, e trouxe **3 casos** de `tests/test_api.py`. A partir daí a suíte estava em **293**, e é desse número que a Task 16 parte.

Os que provam a mudança, e não só a acompanham:

- `test_conta_que_paga_um_clipe_de_4s_nao_e_escolhida_para_o_de_8s` — com saldo 10, a conta paga o de 4s (7) e não paga o de 8s (12). É o teste que prova que a duração atravessou **`escolher`**, e não só `reservar`.
- `test_job_longo_pula_a_conta_que_so_paga_o_curto` — o mesmo, ponta a ponta pelo orquestrador.
- `test_quatro_segundos_custa_menos_que_oito_em_todo_modelo_de_video` — o número que justifica a mudança inteira.
- `test_imagem_com_duracao_da_422` e `test_imagem_sem_duracao_passa` — os dois lados do `model_fields_set`.
- `test_pedido_leva_a_duracao_ao_transporte` — sem isso, o transporte real cairia no default da UI e o pedido do usuário seria ignorado em silêncio.
- `test_duracao_que_nao_pegou_e_apontada` (já existia em `test_transporte_pw.py`) — continua valendo, agora com o campo real por trás.

- [ ] **Step 7: Commit**

```bash
git add flowpool tests
git commit -m "feat: duracao de video no dominio, no custo e na escolha de conta"
```

- [ ] **Step 8: Medir de verdade, quando houver crédito para gastar**

Os 7 créditos do clipe de 4s são **anunciados**, não medidos. Vale fechar a medição por saldo assim que houver crédito sobrando:

```bash
.venv/Scripts/python -m flowpool.transporte.pw.fumaca flowpool/perfis/mapeamento \
  t2v omni-flash "a slow push-in on a ceramic mug" --duracao 4s
```

Expected: `custo real: 7`. Se sair diferente, o número da tabela muda e a escala inteira se move junto — é ela que sustenta 6s, 10s e os três Veo. **Não rodar sem decidir gastar o crédito**: a geração é real.

---

### Task 16: O pool nascia morto — o saldo passa a nascer lido

**Files:**
- Modify: `flowpool/motor/orquestrador.py`, `flowpool/api/app.py`, `flowpool/api/painel.js`, `flowpool/transporte/falso.py`
- Test: `tests/test_orquestrador.py`, `tests/test_api.py`

**O defeito.** `Conta.saldo` nasce em `0`. `POST /contas` validava a sessão e **não lia o saldo**. O saldo só era escrito na reconciliação, que roda no `finally` de `_executar` — ou seja, **depois** de um job. Mas `escolher` exige `custo <= conta.saldo`, então com saldo `0` nenhuma conta é apta.

O ciclo fechava sobre si mesmo: **o saldo só atualiza rodando job, e o job só roda se houver saldo.**

Reproduzido com uma conta de 50 créditos reais no Flow e um transporte falso sabendo disso:

```
conta recem-cadastrada -> saldo=0  confiavel=False
  volta 1: job=pendente tentativas=0 conta=None reavaliar=sim
  volta 2: job=pendente tentativas=0 conta=None reavaliar=sim
  volta 3: job=pendente tentativas=0 conta=None reavaliar=sim
saldo real no Flow: 50   |   saldo no banco: 0
VEREDITO: IMPASSE — o job nunca roda
```

**A gravidade está no silêncio.** Nada quebrava e nada aparecia: sem exceção, sem evento útil, sem alerta no painel. O job ficava `pendente` para sempre. O usuário cadastrava a primeira conta, enfileirava um job, e o app simplesmente não fazia nada — que é o pior modo de falha possível para quem está começando a usar o serviço.

- [ ] **Step 1: Escrever o teste do impasse, e vê-lo falhar**

Este vem primeiro, e é o mais importante da task. Em `tests/test_orquestrador.py`:

```python
async def test_conta_recem_cadastrada_com_saldo_zero_nao_trava_a_fila(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")   # saldo 0, confiavel False
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    for _ in range(3):
        await orq.passo()
        if repo.job(jid).estado is EstadoJob.PRONTO:
            break

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO, "o job ficou pendente para sempre"
    assert j.conta_id == cid
    assert repo.conta(cid).saldo_confiavel is True
```

Antes da correção ele falha exatamente como o defeito se manifesta: `AssertionError: o job ficou pendente para sempre`.

A asserção tem que ser **sobre o job ter andado**, não sobre o saldo ter mudado. Um teste que só conferisse o saldo passaria com a correção pela metade — o saldo lido e o job continuando parado.

O job é de **vídeo**, e isso não é escolha à toa: imagem custa **zero** desde a medição de 20/08, e com custo zero qualquer conta com saldo zero já é apta. O impasse não existe em imagem, e um teste em modo imagem passaria antes e depois da correção sem provar nada.

- [ ] **Step 2: `semear_saldo` no orquestrador**

Um método público, porque a API também o usa:

```python
async def semear_saldo(self, conta: Conta) -> bool:
    """Abre sessao SO para ler o saldo e reconciliar. Devolve True se leu."""
```

Abre sessão, lê o saldo, `reconciliar`, salva a conta, registra `saldo_semeado` com `{"antes", "lido"}`, fecha a sessão no `finally`. `SessaoMorta` marca a conta `expirada` **campo a campo** (`marcar_sessao`, como no resto do motor) e registra `saldo_nao_semeado`; qualquer outra falha só registra `saldo_nao_semeado`. **Nunca propaga**: quem chama está no meio de um passo do motor ou de uma rota de cadastro, e nos dois lugares travar seria pior do que ficar sem o número.

O evento não é enfeite. É uma abertura de navegador **sem job associado** — sem registro, quem auditasse o banco depois procuraria a geração que a pagou e não acharia. E vale dizer o que ele significa: ler saldo **não gasta crédito**, é abrir o menu do avatar.

- [ ] **Step 3: o motor se cura sozinho — a correção que importa**

Em `passo()`, quando `_conta_para` devolve `None`, o motor semeia **uma** conta e reescolhe:

```python
conta = self._conta_para(job, agora)
if conta is None and await self._semear_uma_conta(agora):
    conta = self._conta_para(job, agora)
```

Ler o saldo no cadastro (Step 4) sozinho **não basta**. Qualquer caminho que deixe uma conta com saldo desatualizado reintroduz o impasse numa variante: um `flowpool.db` recriado, uma conta inserida direto no banco, um reset diário que ninguém observou. Só a cura no motor cobre o conjunto.

`_proxima_a_semear` decide a candidata, e cada regra dela evita um estrago:

- **Só conta ativa, com sessão viva e `saldo_confiavel` falso.** O corte por `saldo_confiavel` é o contrapeso obrigatório: sem ele, um pool **legitimamente seco** viraria um laço de abrir e fechar navegador a cada volta do motor.
- **Fora a conta seca com prazo em aberto** (`seca_ate > agora`). O Flow já recusou gerar nela apesar do saldo; reler o número não muda isso até o reset, e relê-lo a cada volta é o mesmo laço quente que `seca_ate` existe para evitar.
- **Uma por passo**, a de **leitura mais antiga** primeiro (nunca lida antes de todas). Abrir navegador é caro e o motor roda em laço; semear o pool inteiro abriria vinte janelas para despachar um job.

E a propriedade que fecha o desenho: isso **não vira laço quente por construção**, porque a semeadura só acontece com um job **já reivindicado**, e o job que não acha conta sai da fila por `ESPERA_RESET`.

A semeadura fica **dentro** do `try` que já existia, mas não pode disparar o ramo `escolha_falhou`: falha de leitura de saldo não é erro de configuração e não pode gastar tentativa do job. É por isso que `semear_saldo` engole as próprias exceções em vez de deixá-las subir.

- [ ] **Step 4: a conta nasce com o saldo lido**

`POST /contas` e `POST /contas/{id}/revalidar` chamam `await orq.semear_saldo(c)` **depois** de validar a sessão e antes de responder. A resposta passa a trazer o saldo real, que é o que o painel mostra.

O ponto delicado: **se a leitura falhar e a sessão estiver viva, a conta é cadastrada mesmo assim**, com `saldo_confiavel = False`. Recusar o cadastro por causa do saldo seria pior — a sessão é a parte cara (login manual, pessoa na frente do Chrome, até 900s de espera) e o saldo é recuperável por três caminhos diferentes: o motor, o botão do painel e a próxima revalidação.

Na revalidação há um agravante que justifica a leitura ainda mais: a sessão caiu horas ou dias atrás, e o saldo guardado envelheceu junto — conta gratuita reseta todo dia.

- [ ] **Step 5: `POST /contas/{id}/saldo` e o botão do painel**

A mesma leitura, avulsa, para quem quer conferir sem esperar a fila andar. `404` para conta que não existe.

Diferente das duas rotas do Step 4, esta **recusa** quando não consegue ler: `409` com o e-mail da conta e a sugestão de revalidar. O botão existe justamente para conferir, e devolver `200` com o número velho como se fosse fresco seria pior do que não ter botão.

No `painel.js`: botão **"Ler saldo"** na linha da conta, ao lado de "Revalidar", com o teto de sessão (`TETO_SESSAO_MS`) porque abre navegador do lado do servidor. A mensagem de sucesso do cadastro passa a olhar `saldo_confiavel` — sem isso, uma leitura falha faria a tela dizer "entrou no pool com 0 créditos", e o usuário concluiria que a conta está seca quando o que faltou foi só a leitura.

- [ ] **Step 6: o contador que prova que o navegador não abriu**

`TransporteFalso` ganha `self.aberturas`, incrementado em `abrir_sessao`. É o mesmo raciocínio do `criacoes_projeto` da Task 7: o estado final do banco **não distingue** "não abriu navegador" de "abriu e não adiantou", e é exatamente essa diferença que o teste do pool seco precisa afirmar.

- [ ] **Step 7: Rodar a suíte**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `308 passed` (293 → 308; +15 casos — 8 em `test_orquestrador.py`, 7 em `test_api.py`).

Os que provam a mudança, e não só a acompanham:

- `test_conta_recem_cadastrada_com_saldo_zero_nao_trava_a_fila` — **o teste do impasse**. Falhava antes da correção.
- `test_pool_seco_de_verdade_nao_abre_navegador` — o contrapeso. Conta com saldo `5`, confiável, e um job que custa 20: `t.aberturas == 0`. Sem ele, a correção do impasse trocaria um pool parado por um laço de abrir e fechar Chrome.
- `test_conta_seca_nao_e_relida_antes_do_prazo` — o mesmo, pela outra porta (`seca_ate` no futuro).
- `test_motor_semeia_uma_conta_por_passo` e `test_semeadura_prefere_a_conta_de_leitura_mais_antiga` — as duas regras de custo da semeadura.
- `test_semeadura_com_sessao_morta_expira_a_conta_e_nao_trava_o_laco` — falha de leitura não pode gastar tentativa do job nem parar o passo.
- `test_semear_saldo_sozinho_nao_gera_nada` — ler saldo não gasta crédito, e é isso que torna aceitável abrir um navegador só para isso.
- `test_cadastro_devolve_o_saldo_lido_do_flow` e `test_conta_cadastrada_ja_pode_rodar_job` — o impasse visto da porta de entrada.
- `test_cadastro_sobrevive_a_leitura_de_saldo_quebrada` — a conta entra mesmo sem o número.
- `test_rota_de_saldo_que_nao_conseguiu_ler_avisa_em_vez_de_mentir` — o `409`.

- [ ] **Step 8: Commit**

Três commits separados: código+testes, painel, docs.

---

### Task 17: Dois defeitos medidos ao vivo — o submodo que não existia e a referência descartada

Os dois vieram do uso real, não de leitura de código, e nenhum dos dois é cosmético.

#### Defeito 1 — `Frames` e `Elementos` são SUBMODOS de Vídeo

Um job real falhou **três vezes** com:

```
GeracaoFalhou: modo: esperava 1 candidato para 'button:has-text("chrome_extension")', achei 0
```

Medido: **a lista de modos do painel depende do modo atual.**

```
chip em VÍDEO  -> image Imagem | videocam Vídeo | crop_free Frames | chrome_extension Elementos
chip em IMAGEM -> image Imagem | videocam Vídeo
```

`Frames` e `Elementos` não existem enquanto o chip estiver em imagem. O `_configurar` clicava no alvo direto, então vindo de imagem ele clicava no vazio.

- [ ] **Step 1: a hierarquia vira dado, em `seletores.py`**

```python
MODO_PAI = {"frames2v": "t2v", "ingredients2v": "t2v"}
```

Mora ali, e não numa condição escondida na sequência, porque é um **fato da interface**: se o Google promover Frames a modo de topo, ou criar um terceiro submodo, o conserto é uma linha deste dicionário.

- [ ] **Step 2: a decisão é pura e tem teste — `analise.etapas_ate_o_modo`**

`(modo, visivel) -> ["t2v", "frames2v"]` ou `["frames2v"]`. **Não forçar dois passos quando um basta:** se o alvo já está no painel, clica-se direto — cada clique extra é mais uma animação e mais uma chance de o painel redesenhar no meio. Modo de topo ausente é **erro**, não caminho inventado.

- [ ] **Step 3: `_escolher_modo` no transporte**

Espera o `videocam` (que existe nos **dois** estados do painel, e por isso serve de âncora), conta o alvo, percorre as etapas e espera o painel redesenhar entre elas. Contar antes de o painel renderizar diria "não está visível" e forçaria dois cliques onde um bastava — é o mesmo engano do `count()` sem espera que já custou três execuções.

- [ ] **Step 4: o rótulo do submodo no chip — pendência declarada, não chute**

**Não está medido** se o chip escreve "Frames"/"Elementos" nos submodos ou se continua dizendo só "Vídeo". `ROTULO_MODO_NO_CHIP` passa a guardar uma **tupla** de marcadores aceitos, e qualquer um confirma:

```python
"frames2v": ("frames", "video"), "ingredients2v": ("elementos", "video"),
```

Exigir só o marcador próprio daria **falso negativo**: bloquearia o modo inteiro depois de a troca ter dado certo. Aceitar o rótulo do pai não afrouxa o que importa — o chip que continuou em **imagem**, que era o defeito real, segue reprovando. E a tolerância não fica calada: `analise.confirmacao_fraca` devolve a linha que a corrida imprime, e essa linha é a medição que falta.

- [ ] **Step 5: `conferir.py` mede sem gastar crédito**

Percorre os quatro modos pela hierarquia, imprime o texto do chip em cada um e dá o veredito sobre `ROTULO_MODO_NO_CHIP`. De quebra conserta o próprio diagnóstico: ele acusava `FALTA` nos dois submodos só porque o painel estava em imagem — o mesmo engano, dito pela ferramenta que existe para achá-lo. Termina deixando o painel em vídeo, que é o modo com duração e com o dropdown de vídeo.

#### Defeito 2 — o modo imagem ACEITA referência, e o app descartava em silêncio

Medido: com o chip em modo imagem a página tem **1 `input[type=file]` e 1 botão de anexar**. O Flow aceita imagem com referência — é o Nano Banana gerando a partir de uma foto.

O `_anexar` retornava cedo em `Modo.IMAGEM`, então a referência era **jogada fora sem aviso**. O usuário relatou exatamente isso: cola a imagem, troca para modo imagem, a imagem some.

**Por que é grave:** o pipeline dele **começa** em "foto da roupa → imagem da modelo vestida". Isso é imagem com referência. A primeira etapa do trabalho não estava contemplada.

- [ ] **Step 6: o domínio diz quem aceita — `tipos.max_referencias`**

Três em `ingredients2v` e em `imagem`, zero nos outros dois. E `tipos.exige_arquivo` separa **aceitar** de **exigir**: imagem sem referência é geração por texto puro, o caso comum, e confundir as duas coisas transformaria toda geração de imagem em erro.

- [ ] **Step 7: o transporte anexa em imagem**

`frame_inicial`/`frame_final` continuam **exclusivos de `frames2v`**; o resto sai de `max_referencias`, então não há um segundo lugar dizendo quem aceita o quê.

- [ ] **Step 8: a API valida por modo**

O teto sai do campo (`max_length=3`) e vira validação por modo, com a mensagem dizendo o modo e o número. Modo que **não** aceita referência passa a recusar com `422`, em vez de deixar o transporte descartar o arquivo lá na frente — é a mesma regra do passo tolerante, pela outra porta. E `frame_inicial`/`frame_final` fora de `frames2v` viram `422`, que até agora era acordo tácito.

Cuidado com a regra de quantidade: **imagem aceita `x1..x4` e isso continua valendo com referência.** São duas regras independentes, e amarrar uma na outra tiraria as variações justamente do caso em que elas mais servem.

- [ ] **Step 9: o painel para de sequestrar o modo**

O vaivém que o usuário descreveu tinha causa aqui: colar forçava `ingredients2v`; ele trocava para imagem, o teto de anexos naquele modo era zero e a referência caía fora; colava de novo, voltava para vídeo. **Duas regras discordando na mesma barra.**

Agora o modo só troca quando o modo atual não aceita arquivo — hoje, só `t2v`. Imagem entra no mapa de papéis com o mesmo teto de três, o `corpoDoJob` manda `referencias` em imagem, o atalho das três da pasta aparece, e `exigeAnexo()` espelha `tipos.exige_arquivo` para que imagem sem referência não vire erro antes de sair.

- [ ] **Step 10: Rodar a suíte**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `461 passed` (432 → 461; +29 casos — 18 em `test_transporte_pw.py`, 5 em `test_tipos.py`, 6 em `test_api.py`).

Os que provam a mudança, e não só a acompanham:

- `test_submodo_ausente_passa_pelo_video_primeiro` — **o teste do defeito 1**.
- `test_modo_visivel_no_painel_e_um_clique_so` — o contrapeso: a correção não pode virar "sempre dois cliques".
- `test_modo_de_topo_ausente_e_erro_e_nao_caminho_inventado` — `imagem` e `t2v` sumirem é deploy do Google, não caminho a adivinhar.
- `test_chip_que_so_diz_video_ainda_confirma_o_submodo` e `test_chip_que_continuou_em_imagem_reprova_o_submodo` — o par que define a tolerância: ela cobre o que não está medido e **não** cobre o defeito real.
- `test_imagem_com_referencia_anexa_em_vez_de_descartar` — **o teste do defeito 2**.
- `test_imagem_sem_referencia_continua_sendo_texto_puro` — o contrapeso, e o que impede a correção de quebrar o caso comum.
- `test_aceitar_arquivo_nao_e_o_mesmo_que_exigir` — a distinção em uma linha.
- `test_imagem_com_referencia_continua_aceitando_quatro_variacoes` — quantidade e referência são independentes.
- `test_t2v_recusa_referencia_em_vez_de_descartar_calado` — o descarte silencioso fechado pela outra porta.

- [ ] **Step 11: Fumaça — o que rodar, e nesta ordem**

Nenhuma das duas correções se prova com teste: são sequência contra o Google.

1. **Sem gastar nada** (mede a hierarquia e o rótulo do chip):
   `python -m flowpool.transporte.pw.conferir perfis/g1 <uuid-do-projeto>`
2. **Imagem com referência** (imagem é gratuita — medido: saldo 50 → 50), exercita as duas correções de uma vez, porque sai de vídeo para imagem **e** anexa:
   `python -m flowpool.transporte.pw.fumaca perfis/g1 imagem nano-banana "a modelo vestindo a roupa da referência" "Moda/roupa.png"`
3. **Elementos** (gasta crédito), o modo do erro original:
   `python -m flowpool.transporte.pw.fumaca perfis/g1 ingredients2v omni-flash "a modelo andando" "Moda/roupa.png" --duracao 4s`

- [ ] **Step 12: Commit**

Quatro commits separados: domínio/API, transporte+testes, painel, docs.

---

## Depois deste plano

O painel web é o **plano 2**: uma página servida pelo próprio FastAPI, consumindo `/contas`, `/jobs` e `/saude`, com botão de cadastrar conta e um formulário de lote. Não começa antes deste plano estar rodando ponta a ponta — tela para API que nunca gerou clipe é trabalho jogado fora.

Fica também pendente, e só depois de o A estar de pé: medir se a leitura de saldo e a geração de imagem funcionam por HTTP puro (rota C do spec). Se funcionarem, o `TransportePlaywright` ganha atalhos para esses dois casos e o teto de simultâneos sobe.
