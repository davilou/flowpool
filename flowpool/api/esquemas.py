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
