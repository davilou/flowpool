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
    # Formato da saida, um dos `PROPORCOES`. Vertical por padrao — ver a constante.
    proporcao: str = PROPORCAO_PADRAO
    # Saidas por geracao. MULTIPLICA O CUSTO: quatro imagens custam quatro vezes uma,
    # e por isso a quantidade atravessa `escolher` e `reservar`, nao so a reserva.
    quantidade: int = 1
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
