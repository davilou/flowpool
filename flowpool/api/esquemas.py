from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from flowpool.nucleo.custos import CUSTO_DURACAO_BASE
from flowpool.nucleo.erros import ProxyInvalido
from flowpool.nucleo.proxy import analisar
from flowpool.nucleo.tipos import (
    DURACAO_PADRAO, DURACOES, PROPORCAO_PADRAO, PROPORCOES, SEM_DURACAO, EstadoJob,
    Modelo, Modo, SaudeSessao, Tier, duracao_valida, max_referencias, modelo_valido,
    modelos_do_modo, quantidade_maxima,
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
    # frames2v NAO herda Omni Flash: o modo so funciona em Veo Lite, e o padrao tem que
    # ser o unico modelo que o modo aceita. Ver `MODELOS_DO_MODO` no nucleo.
    Modo.FRAMES2V: Modelo.VEO_LITE,
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
    # Ate tres, e QUAIS modos aceitam sai de `max_referencias` — ver
    # `referencias_coerentes_com_o_modo`. Imagem aceita: o Flow gera imagem a partir de
    # uma foto, e e por ai que o pipeline do usuario comeca.
    referencias: list[str] = Field(default_factory=list)
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
        if self.modo in SEM_DURACAO:
            pedida = self.duracao if "duracao" in self.model_fields_set else None
            if not duracao_valida(self.modo, pedida):
                raise ValueError(
                    f"modo {self.modo.value} nao tem duracao: o painel do Flow nao "
                    f"oferece o grupo nele, e {pedida!r} nao teria onde ser clicado")
            # O CAMPO NAO PODE FICAR NO PADRAO "4s" NUM MODO SEM DURACAO, e o motivo e
            # o CUSTO. `custo_de` escala o preco por duracao, e "4s" vale ~58% da
            # tabela: em `frames2v` isso reservaria 6 creditos onde o Flow cobra 10, e
            # reserva curta demais aprova uma conta que nao paga o job — o Flow recusa,
            # o job volta pra fila e a conta e marcada seca por erro de conta.
            #
            # Vale a duracao BASE da tabela de custo. MEDIDO indiretamente: o painel em
            # Frames com Veo 3.1 Fast anunciou "A geracao vai usar 20 creditos", que e
            # exatamente o valor de base do Fast — ou seja, o modo cobra o preco cheio,
            # nao um preco escalado. Para Veo Lite isso da 10.
            #
            # `None` seria mais honesto, mas a coluna `job.duracao` e NOT NULL e mudar
            # isso pede reconstruir a tabela. Fica registrado como divida: o painel vai
            # mostrar "8s" num modo que nao tem duracao.
            self.duracao = CUSTO_DURACAO_BASE
            return self
        if not duracao_valida(self.modo, self.duracao):
            raise ValueError(
                f"duracao {self.duracao!r} nao existe; use uma de {list(DURACOES)}")
        return self

    @model_validator(mode="after")
    def modelo_coerente_com_o_modo(self) -> PedidoJob:
        """Recusa modelo que aquele modo nao aceita, em vez de gastar credito para
        descobrir.

        O caso que motivou: `frames2v` so funciona em Veo Lite. O painel do Flow DEIXA
        escolher Veo Fast em modo Frames — o print de 2026-08-21 mostra exatamente
        isso, anunciando 20 creditos —, entao quem so olha a tela nao ve impedimento
        nenhum. Ve o custo dobrado e um video que ignora o frame final.
        """
        if not modelo_valido(self.modo, self.modelo):
            aceitos = [m.value for m in modelos_do_modo(self.modo)]
            raise ValueError(
                f"modo {self.modo.value} nao aceita o modelo {self.modelo.value}; "
                f"use um de {aceitos}")
        return self

    @model_validator(mode="after")
    def destino_deriva_do_produto(self) -> PedidoJob:
        """`produto: "Panelas Monaco"` basta; repetir o caminho e ruido."""
        if not self.destino:
            self.destino = f"{self.produto}/clipes"
        return self

    @model_validator(mode="after")
    def referencias_coerentes_com_o_modo(self) -> PedidoJob:
        """Quem aceita referencia, quantas, e quem nao aceita nenhuma.

        IMAGEM ACEITA — e sempre aceitou, do lado do Flow. Quem recusava era o
        transporte, em silencio: a pessoa colava a foto, trocava para modo imagem e a
        foto sumia. Nao e um canto raro do app; e a primeira etapa do pipeline do
        usuario, "foto da roupa -> imagem da modelo vestida".

        Nos modos que NAO aceitam a recusa e explicita, e nao um descarte calado la na
        frente: mandar referencia em `t2v` so pode ser engano de quem chama, e um 422
        aqui custa menos do que um clipe gerado sem a referencia que o pedido trazia.
        """
        teto = max_referencias(self.modo)
        if self.referencias and not teto:
            raise ValueError(
                f"modo {self.modo.value} nao aceita referencia: o arquivo seria "
                f"descartado sem chegar ao Flow. Use ingredients2v (video) ou imagem")
        if len(self.referencias) > teto:
            raise ValueError(
                f"modo {self.modo.value} aceita no maximo {teto} referencias, "
                f"pedidas {len(self.referencias)}")
        return self

    @model_validator(mode="after")
    def frames_sao_exclusivos_do_frames2v(self) -> PedidoJob:
        """Frame inicial e final so existem em `frames2v`.

        Fora dele o painel do Flow nao tem onde colocar esses arquivos, e o transporte
        nao os anexa — aceitar aqui seria prometer uma coisa e entregar outra. Referencia
        de imagem tem campo proprio (`referencias`), e e por ele que a foto entra.
        """
        if self.modo is not Modo.FRAMES2V and (self.frame_inicial or self.frame_final):
            raise ValueError(
                f"frame_inicial/frame_final so valem em frames2v; em "
                f"{self.modo.value} use referencias")
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
    # O que entrou na geracao. O detalhe do job mostra as miniaturas: e assim que se
    # confere, sem abrir o Flow, que a referencia certa foi usada.
    referencias: list[str] = Field(default_factory=list)
    frame_inicial: str | None = None
    frame_final: str | None = None
    prioridade: int = 0
    # Quando o motor volta a olhar um job devolvido a fila. `None` e "na proxima volta".
    reavaliar_em: datetime | None = None
    # Sem estas duas datas nao ha tabela de jobs nem historico que se ordene por tempo
    # — so por id, que nao diz ha quanto tempo um job esta rodando.
    criado_em: datetime | None = None
    atualizado_em: datetime | None = None


class RespostaEvento(BaseModel):
    """Uma linha da tabela de eventos, com o payload ja aberto.

    E o diagnostico de um job ou de uma conta: `despachado`, `download_falhou`,
    `sem_conta_apta`, `divergencia`, `saldo_semeado`... O painel mostra a linha do tempo
    quando algo deu errado, em vez de so a ultima mensagem de erro.
    """

    id: int
    job_id: int | None = None
    conta_id: int | None = None
    tipo: str
    em: datetime
    payload: dict = Field(default_factory=dict)


class Info(BaseModel):
    """O que o painel precisa saber do servico e das regras — lido, nao espelhado.

    Antes o JS repetia a tabela de custos, os modelos por modo e os tetos; cada copia
    envelhecia por conta propria. Aqui a fonte e o nucleo.
    """

    raiz: str
    max_simultaneos: int
    teto_diario_free: int
    # custos[modelo][tier][duracao] -> creditos. Imagem nao tem duracao: a chave e "-".
    custos: dict[str, dict[str, dict[str, int]]]
    modelos_do_modo: dict[str, list[str]]
    modelo_padrao: dict[str, str]
    proporcoes: list[str]
    duracoes: list[str]
    sem_duracao: list[str]
    # A duracao que a tabela de custo cobra nos modos SEM grupo de duracao (frames2v).
    duracao_base: str
    max_referencias: dict[str, int]
    quantidade_maxima: dict[str, int]


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
    # Quando o saldo foi lido pela ultima vez no Flow. Conta gratuita reseta todo dia,
    # entao "50 creditos" lido ha 30 horas vale menos que o numero sugere.
    saldo_lido_em: datetime | None = None
    # Ate quando o Flow recusou gerar apesar do saldo. No futuro = conta fora da escolha.
    seca_ate: datetime | None = None


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


class PedidoColarUrl(BaseModel):
    # O texto vem de um Ctrl+V: quando a pessoa usa "Copiar endereco da imagem" em vez
    # de "Copiar imagem" (duas opcoes vizinhas no menu do navegador), e isto que chega.
    # A validacao de verdade — esquema e endereco de rede — mora em `colar.validar_url`,
    # que e funcao pura e testada a parte; aqui so se limita o tamanho do texto.
    url: str = Field(min_length=1, max_length=4096)


class RespostaColagem(BaseModel):
    nome: str
    # Relativo a raiz e com barra normal, igual ao que o seletor devolve: e o mesmo
    # texto que o job guarda em `referencias`, entao o painel nao traduz nada no meio.
    caminho: str
    tamanho: int


class Saude(BaseModel):
    contas_vivas: int
    # Quantas geracoes estao em curso AGORA e qual e o teto. Com o pool rodando varias
    # janelas, "a fila esta parada" e "as cinco janelas estao cheias" sao estados
    # completamente diferentes e ate agora indistinguiveis no painel.
    gerando: int
    max_simultaneos: int
    # Contas com navegador aberto. Passa de `gerando` por um quando alguma leitura de
    # saldo esta acontecendo — ela tambem abre navegador e tambem toma a conta.
    contas_ocupadas: int
    credito_total: int
    fila_pendente: int
    contas_expiradas: list[str]
