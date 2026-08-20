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
    custo_estimado: int | None = None
    erro: str | None = None
    reavaliar_em: datetime | None = None
    criado_em: datetime | None = None
    atualizado_em: datetime | None = None
