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


# Teto de referencias de uma geracao. E o mesmo nos dois modos que aceitam: tres.
MAX_REFERENCIAS = 3


def max_referencias(modo: Modo) -> int:
    """Quantas imagens de referencia aquele modo aceita.

    IMAGEM ACEITA. MEDIDO 2026-08-20: com o chip em modo imagem a pagina tem um
    `input[type=file]` e um botao de anexar — o Flow gera imagem a partir de uma foto,
    e e o Nano Banana fazendo isso. Enquanto isto valia zero, o transporte descartava a
    referencia SEM AVISO: a pessoa colava a imagem, trocava para modo imagem e a imagem
    sumia. E nao era um canto raro do app — e a PRIMEIRA etapa do pipeline do usuario,
    "foto da roupa -> imagem da modelo vestida".

    Video por texto e video por frames nao aceitam: la o arquivo tem outro papel (ou
    papel nenhum), e anexar seria o mesmo descarte silencioso pela outra porta.
    """
    if modo in (Modo.INGREDIENTS2V, Modo.IMAGEM):
        return MAX_REFERENCIAS
    return 0


def aceita_referencia(modo: Modo) -> bool:
    return max_referencias(modo) > 0


def exige_arquivo(modo: Modo) -> bool:
    """Se o modo nao GERA sem um arquivo — diferente de apenas aceitar um.

    `ingredients2v` sem referencia e `frames2v` sem frame inicial nao tem o que gerar.
    Imagem sem referencia e o caso comum: geracao por texto puro. Confundir as duas
    coisas transformaria toda geracao de imagem em erro.
    """
    return modo in (Modo.INGREDIENTS2V, Modo.FRAMES2V)


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
