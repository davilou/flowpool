from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from flowpool.nucleo.tipos import (
    PROPORCAO_PADRAO, PROPORCOES, EstadoJob, Modelo, Modo, SaudeSessao, Tier,
    quantidade_maxima,
)

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
