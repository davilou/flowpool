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
    # O prompt inteiro. A galeria mostra o comeco no card e o texto completo no
    # visualizador: e por ele que a pessoa reconhece o que gerou, muito antes do nome.
    prompt: str = ""
    proporcao: str = PROPORCAO_PADRAO
    quantidade: int = 1
    duracao: str = DURACAO_PADRAO
    conta_id: int | None = None
    # ABSOLUTO, como o motor gravou. Fica na resposta porque e o caminho que a pessoa
    # copia para abrir o arquivo no editor de video.
    arquivo: str | None = None
    # O MESMO arquivo, relativo a raiz do projeto. E este que as rotas de midia e de
    # download aceitam: `resolver` recusa caminho absoluto, de proposito.
    caminho: str | None = None
    # Creditos reservados para esta geracao. `None` enquanto o job nao foi despachado.
    custo_estimado: int | None = None
    tentativas: int = 0
    erro: str | None = None


class PatchJob(BaseModel):
    """O que da para mudar num job depois de criado. Hoje: so o nome.

    O nome vira o nome do arquivo em disco, entao mudar so faz sentido ENQUANTO o
    arquivo nao existe — quem impede depois disso e a rota, com 409.
    """

    nome: str = Field(min_length=1)

    @field_validator("nome")
    @classmethod
    def nome_nao_e_so_espaco(cls, v: str) -> str:
        """Nome vira NOME DE ARQUIVO: " " produziria um ` .mp4` em disco."""
        if not v.strip():
            raise ValueError("o nome nao pode ser so espaco")
        return v.strip()


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


class ItemPasta(BaseModel):
    nome: str
    # Sempre relativo a raiz do projeto e sempre com barra normal: e o mesmo texto que
    # volta na querystring da navegacao e que o job guarda em `referencias`.
    caminho: str


class ItemImagem(BaseModel):
    nome: str
    caminho: str
    tamanho: int


class RespostaArquivos(BaseModel):
    # "" e a raiz do projeto.
    caminho: str
    # `None` na raiz — nao ha para onde voltar. "" e um pai valido (a propria raiz).
    pai: str | None = None
    pastas: list[ItemPasta] = Field(default_factory=list)
    arquivos: list[ItemImagem] = Field(default_factory=list)


class Saude(BaseModel):
    contas_vivas: int
    credito_total: int
    fila_pendente: int
    contas_expiradas: list[str]
