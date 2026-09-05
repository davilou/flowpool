"""Auto-cura de seletores: tudo o que acontece EM VOLTA da chamada ao modelo.

A chamada em si nao entra na suite — ela sobe 20 GB de modelo numa GPU e o resultado
nao e deterministico. O que entra e o resto, que e onde mora o risco:

* seletor da lista de NAO CURAVEIS nem chega a perguntar   -> um `.last` num menu de
  card ja mandou um video do usuario para a lixeira
* proposta que casa 0 ou 2 elementos e recusada            -> modelo local erra mais
* teto de curas por execucao                               -> dez seletores quebrados
  nao e seletor obsoleto, e mudanca grande
* sem modelo respondendo, o erro original sobe             -> degradacao, nao estouro
* os curados sobrepoem `seletores.py`, e apagar o JSON reverte

O curador falso satisfaz o mesmo Protocol que o Ollama e o Anthropic satisfazem, entao
o que a suite exercita e o caminho de producao inteiro menos o transporte HTTP.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowpool.transporte.base import GeracaoFalhou
from flowpool.transporte.pw import cura
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.cura import AutoCura, Proposta
from flowpool.transporte.pw.transporte import TransportePlaywright


# --- dublês -------------------------------------------------------------


class CuradorFalso:
    """Mesmo Protocol do `CuradorOllama` e do `CuradorAnthropic`."""

    def __init__(self, *propostas: Proposta) -> None:
        self.propostas = list(propostas)
        self.chamadas: list[tuple] = []

    async def propor(self, descricao, imagem, resumo):
        self.chamadas.append((descricao, imagem, resumo))
        return self.propostas.pop(0) if self.propostas else None


class CuradorQueCai:
    """O Ollama fora do ar, ou o modelo que nao coube na GPU."""

    def __init__(self) -> None:
        self.chamadas = 0

    async def propor(self, descricao, imagem, resumo):
        self.chamadas += 1
        raise ConnectionError("nada escutando em localhost:11434")


class _Loc:
    def __init__(self, quantos: int) -> None:
        self.quantos = quantos
        self.clicado = False

    @property
    def first(self):
        return self

    async def count(self):
        return self.quantos

    async def click(self, timeout=None):
        self.clicado = True

    async def wait_for(self, timeout=None, state=None):
        if not self.quantos:
            raise TimeoutError("nao apareceu")


class _Pagina:
    """So o que a cura toca: contar candidatos, varrer o DOM e tirar print."""

    def __init__(self, mapa: dict[str, int]) -> None:
        self.mapa = mapa
        self.locs: dict[str, _Loc] = {}
        self.prints = 0

    def locator(self, seletor):
        return self.locs.setdefault(seletor, _Loc(self.mapa.get(seletor, 0)))

    async def evaluate(self, js, *args):
        return ['<button aria-label="Enviar">arrow_forward']

    async def screenshot(self, path=None):
        # nao escreve nada: print que nao existe faz a cura seguir sem imagem, que e
        # justamente o caminho degradado que interessa exercitar
        self.prints += 1

    async def wait_for_timeout(self, ms):
        pass


@pytest.fixture(autouse=True)
def seletores_intactos():
    """A cura MUTA `seletores` em memoria. Sem isto, um teste contamina o proximo."""
    textos = {n: v for n, v in vars(sel).items() if n.isupper() and isinstance(v, str)}
    mapas = {n: dict(v) for n, v in vars(sel).items() if n.isupper() and isinstance(v, dict)}
    yield
    for nome, valor in textos.items():
        setattr(sel, nome, valor)
    for nome, valor in mapas.items():
        getattr(sel, nome).clear()
        getattr(sel, nome).update(valor)
    sel._ORIGINAIS.clear()


def _transporte(auto: AutoCura) -> TransportePlaywright:
    return TransportePlaywright(cura=auto)


# --- o caminho feliz: propos, casou, gravou, repetiu --------------------


async def test_proposta_que_casa_um_elemento_e_gravada_e_a_acao_repete(tmp_path):
    novo = 'button:has-text("expand_more")'
    pagina = _Pagina({sel.DROPDOWN_MODELO: 0, novo: 1})
    falso = CuradorFalso(Proposta(seletores=[novo], porque="o icone virou expand_more"))
    arquivo = tmp_path / "curados.json"

    await _transporte(AutoCura(curador=falso, arquivo=arquivo))._clicar_unico(
        pagina, sel.DROPDOWN_MODELO, "dropdown de modelo")

    # repetiu a acao com o seletor curado — nao com o quebrado
    assert pagina.locator(novo).clicado is True
    # gravou fora de `seletores.py`, com o que a auditoria precisa
    entrada = json.loads(arquivo.read_text(encoding="utf-8"))["DROPDOWN_MODELO"]
    assert entrada["valor"] == novo
    assert entrada["antigo"] == 'button:has-text("arrow_drop_down")'
    assert entrada["porque"] == "o icone virou expand_more"
    assert entrada["em"]
    # e passou a valer na leitura, sem reiniciar o processo
    assert sel.DROPDOWN_MODELO == novo


async def test_alternativas_sao_validadas_na_ordem(tmp_path):
    """Modelo local erra mais, entao ele propoe ate tres e a validacao escolhe.

    A primeira que casar SOZINHA vence; a que casa zero e descartada em silencio, que
    e diferente de a cura inteira falhar.
    """
    ruim, bom = 'button:has-text("nada")', 'button:has-text("expand_more")'
    pagina = _Pagina({sel.DROPDOWN_MODELO: 0, ruim: 0, bom: 1})
    falso = CuradorFalso(Proposta(seletores=[ruim, bom], porque="duas hipoteses"))

    await _transporte(AutoCura(curador=falso, arquivo=tmp_path / "c.json"))._clicar_unico(
        pagina, sel.DROPDOWN_MODELO, "dropdown de modelo")

    assert pagina.locator(bom).clicado is True
    assert sel.DROPDOWN_MODELO == bom


async def test_cura_vale_para_seletor_de_dentro_de_um_mapa(tmp_path):
    """`BOTAO_MODO['ingredients2v']` e o seletor que quebrou ao vivo em 20/08.

    Ele nao e um nome solto do modulo: mora num dicionario. Se so nome solto fosse
    curavel, justamente o caso medido ficaria de fora.
    """
    velho = sel.BOTAO_MODO["ingredients2v"]
    novo = 'button:has-text("extension")'
    pagina = _Pagina({velho: 0, novo: 1})
    falso = CuradorFalso(Proposta(seletores=[novo], porque="a ligadura mudou"))
    arquivo = tmp_path / "curados.json"

    await _transporte(AutoCura(curador=falso, arquivo=arquivo))._clicar_unico(
        pagina, velho, "modo")

    assert sel.BOTAO_MODO["ingredients2v"] == novo
    assert "BOTAO_MODO[ingredients2v]" in json.loads(arquivo.read_text(encoding="utf-8"))


# --- a validacao: 0 ou 2 e recusa, e o erro original sobe ---------------


@pytest.mark.parametrize("quantos", [0, 2])
async def test_proposta_que_nao_casa_exatamente_um_e_recusada(tmp_path, quantos):
    novo = 'button:has-text("expand_more")'
    pagina = _Pagina({sel.DROPDOWN_MODELO: 0, novo: quantos})
    falso = CuradorFalso(Proposta(seletores=[novo], porque="chutei"))
    arquivo = tmp_path / "curados.json"

    with pytest.raises(GeracaoFalhou) as erro:
        await _transporte(AutoCura(curador=falso, arquivo=arquivo))._clicar_unico(
            pagina, sel.DROPDOWN_MODELO, "dropdown de modelo")

    # o erro que sobe e o ORIGINAL: fala do seletor que quebrou, nao da cura
    assert "arrow_drop_down" in str(erro.value)
    assert not arquivo.exists()
    assert sel.DROPDOWN_MODELO == 'button:has-text("arrow_drop_down")'
    assert pagina.locator(novo).clicado is False


async def test_seletor_malformado_nao_derruba_a_cura(tmp_path):
    """Modelo local devolve sintaxe invalida de vez em quando. Isso e recusa, nao crash."""
    class _Explode(_Pagina):
        def locator(self, seletor):
            if seletor == "css=[[[":
                raise ValueError("unknown engine")
            return super().locator(seletor)

    pagina = _Explode({sel.DROPDOWN_MODELO: 0})
    falso = CuradorFalso(Proposta(seletores=["css=[[["], porque="invalido"))

    with pytest.raises(GeracaoFalhou, match="arrow_drop_down"):
        await _transporte(AutoCura(curador=falso, arquivo=tmp_path / "c.json"))._clicar_unico(
            pagina, sel.DROPDOWN_MODELO, "dropdown de modelo")


# --- A REGRA DE SEGURANCA: nao curavel nem chega a perguntar ------------


@pytest.mark.parametrize("nome", [
    "MENU_LIXEIRA_CARD", "MENU_EXCLUIR", "MENU_BAIXAR", "MENU_ITEM",
    "BOTAO_EXCLUIR_PROJETO", "MENU_LIXEIRA",
])
async def test_seletor_destrutivo_nem_chega_a_perguntar(tmp_path, nome):
    """Falhar E o comportamento certo aqui.

    O menu do card tem "Mover para a lixeira". Uma proposta errada ali apaga trabalho
    do usuario — e "casa 1 elemento" nao protege de nada quando o elemento errado
    tambem casa 1. A assercao que importa e `chamadas == []`: o modelo nao foi nem
    consultado.
    """
    seletor = getattr(sel, nome)
    pagina = _Pagina({seletor: 0})
    falso = CuradorFalso(Proposta(seletores=['[role=menuitem]:has-text("x")'], porque="?"))
    arquivo = tmp_path / "curados.json"

    with pytest.raises(GeracaoFalhou):
        await _transporte(AutoCura(curador=falso, arquivo=arquivo))._clicar_unico(
            pagina, seletor, "item de menu")

    assert falso.chamadas == []
    assert not arquivo.exists()


async def test_seletor_desconhecido_nao_e_curavel(tmp_path):
    """Sem descricao nao ha o que perguntar, e adivinhar a intencao seria pior."""
    falso = CuradorFalso(Proposta(seletores=["button"], porque="?"))
    pagina = _Pagina({'a[href*="6f0c1d2e"]': 0})

    with pytest.raises(GeracaoFalhou):
        await _transporte(AutoCura(curador=falso, arquivo=tmp_path / "c.json"))._clicar_unico(
            pagina, 'a[href*="6f0c1d2e"]', "card do projeto")

    assert falso.chamadas == []


# --- teto por execucao --------------------------------------------------


async def test_teto_de_curas_por_execucao(tmp_path):
    """Interface quebrada em dez lugares nao e seletor obsoleto: e mudanca grande."""
    n1, n2 = 'button:has-text("a")', 'button:has-text("b")'
    pagina = _Pagina({sel.DROPDOWN_MODELO: 0, sel.MENU_LIXEIRA: 0, n1: 1, n2: 1})
    falso = CuradorFalso(Proposta(seletores=[n1], porque="um"),
                         Proposta(seletores=[n2], porque="dois"))
    auto = AutoCura(curador=falso, teto=1, arquivo=tmp_path / "curados.json")
    transporte = _transporte(auto)

    await transporte._clicar_unico(pagina, sel.DROPDOWN_MODELO, "dropdown")
    with pytest.raises(GeracaoFalhou, match="mover para a lixeira"):
        await transporte._clicar_unico(pagina, sel.MENU_LIXEIRA, "repetir")

    assert len(falso.chamadas) == 1
    assert auto.curas == 1


# --- degradacao: sem modelo, o comportamento e o de hoje ----------------


async def test_sem_modelo_respondendo_o_erro_original_sobe(tmp_path):
    pagina = _Pagina({sel.DROPDOWN_MODELO: 0})
    caido = CuradorQueCai()
    arquivo = tmp_path / "curados.json"

    with pytest.raises(GeracaoFalhou, match="arrow_drop_down"):
        await _transporte(AutoCura(curador=caido, arquivo=arquivo))._clicar_unico(
            pagina, sel.DROPDOWN_MODELO, "dropdown de modelo")

    assert caido.chamadas == 1     # tentou
    assert not arquivo.exists()    # e nao gravou nada


async def test_cura_desligada_nao_chama_nada(tmp_path):
    """Sem curador resolvido, `_clicar_unico` e exatamente o que era antes."""
    pagina = _Pagina({sel.DROPDOWN_MODELO: 0})
    auto = AutoCura(arquivo=tmp_path / "curados.json")   # padrao: `FLOWPOOL_CURA=0`

    with pytest.raises(GeracaoFalhou, match="arrow_drop_down"):
        await _transporte(auto)._clicar_unico(pagina, sel.DROPDOWN_MODELO, "dropdown")

    assert auto.curas == 0


async def test_cura_so_entra_quando_nao_ha_candidato_nenhum(tmp_path):
    """AMBIGUIDADE NAO SE CURA. Dois candidatos e escolha, e escolha e do humano."""
    pagina = _Pagina({sel.DROPDOWN_MODELO: 2})
    falso = CuradorFalso(Proposta(seletores=["button"], porque="?"))

    with pytest.raises(GeracaoFalhou, match="achei 2"):
        await _transporte(AutoCura(curador=falso, arquivo=tmp_path / "c.json"))._clicar_unico(
            pagina, sel.DROPDOWN_MODELO, "dropdown")

    assert falso.chamadas == []


# --- o arquivo de curados sobrepoe, e apagar reverte --------------------


def test_curados_sobrepoem_seletores_na_leitura(tmp_path, monkeypatch):
    arquivo = tmp_path / "curados.json"
    arquivo.write_text(json.dumps({
        "BOTAO_ENVIAR": {"valor": 'button:has-text("send")', "antigo": "x",
                         "em": "2026-08-20T10:00:00Z", "porque": "mudou"},
        "PROPORCAO[9:16]": {"valor": 'button:has-text("crop_vertical")'},
    }), encoding="utf-8")
    monkeypatch.setattr(sel, "CURADOS", arquivo)

    assert sel.recarregar_curados() == ["BOTAO_ENVIAR", "PROPORCAO[9:16]"]
    assert sel.BOTAO_ENVIAR == 'button:has-text("send")'
    assert sel.PROPORCAO["9:16"] == 'button:has-text("crop_vertical")'


def test_apagar_o_json_reverte(tmp_path, monkeypatch):
    arquivo = tmp_path / "curados.json"
    arquivo.write_text(json.dumps({"BOTAO_ENVIAR": {"valor": "button.novo"}}),
                       encoding="utf-8")
    monkeypatch.setattr(sel, "CURADOS", arquivo)
    sel.recarregar_curados()
    assert sel.BOTAO_ENVIAR == "button.novo"

    arquivo.unlink()
    sel.recarregar_curados()
    assert sel.BOTAO_ENVIAR == "flow-generate-icon-button button"


def test_json_ilegivel_nao_derruba_a_importacao(tmp_path, monkeypatch):
    """`seletores.py` tem que continuar importavel com um JSON corrompido no meio."""
    arquivo = tmp_path / "curados.json"
    arquivo.write_text("{ nao e json", encoding="utf-8")
    monkeypatch.setattr(sel, "CURADOS", arquivo)

    assert sel.recarregar_curados() == []
    assert sel.BOTAO_ENVIAR == "flow-generate-icon-button button"


def test_o_json_nao_consegue_sobrepor_seletor_destrutivo(tmp_path, monkeypatch):
    """Segunda barreira, e de proposito.

    A primeira barreira impede a IA de PROPOR. Esta impede o valor de VALER, venha ele
    de onde vier — arquivo editado a mao, JSON copiado de outra maquina, versao antiga
    do codigo que gravou o que hoje nao gravaria.
    """
    arquivo = tmp_path / "curados.json"
    arquivo.write_text(json.dumps({
        "MENU_LIXEIRA_CARD": {"valor": "[role=menuitem]"},
        "MENU_BAIXAR": {"valor": "[role=menuitem]"},
    }), encoding="utf-8")
    monkeypatch.setattr(sel, "CURADOS", arquivo)

    assert sel.recarregar_curados() == []
    assert sel.MENU_LIXEIRA_CARD == "flow-media-trash-context-menu"


def test_o_json_nao_inventa_seletor_que_nao_existe(tmp_path, monkeypatch):
    arquivo = tmp_path / "curados.json"
    arquivo.write_text(json.dumps({"BOTAO_INVENTADO": {"valor": "button"}}),
                       encoding="utf-8")
    monkeypatch.setattr(sel, "CURADOS", arquivo)

    assert sel.recarregar_curados() == []
    assert not hasattr(sel, "BOTAO_INVENTADO")


# --- o catalogo: de seletor para nome -----------------------------------


def test_o_nome_sai_do_valor_do_seletor():
    assert cura.nome_do_seletor(sel.BOTAO_ENVIAR) == "BOTAO_ENVIAR"
    assert cura.nome_do_seletor(sel.PROPORCAO["9:16"]) == "PROPORCAO[9:16]"


def test_seletor_montado_na_hora_nao_tem_nome():
    assert cura.nome_do_seletor('a[href*="6f0c1d2e-1111"]') is None


def test_seletor_ambiguo_nao_tem_nome(monkeypatch):
    """Dois nomes com o mesmo valor: nao da para saber o que se procurava. Recusa."""
    monkeypatch.setattr(sel, "BOTAO_VER_CENAS", sel.BOTAO_PERSONAGENS)
    assert cura.nome_do_seletor(sel.BOTAO_PERSONAGENS) is None


# --- as descricoes ------------------------------------------------------


def test_toda_descricao_aponta_para_um_seletor_que_existe():
    for nome in sel.DESCRICAO:
        assert cura.valor_do_nome(nome) is not None, nome


def test_nenhum_seletor_destrutivo_tem_descricao():
    """Descricao e convite a curar. Nos destrutivos, o convite nao pode existir."""
    assert set(sel.DESCRICAO) & set(sel.NAO_CURAVEIS) == set()


# --- a resposta do modelo -----------------------------------------------


def test_resposta_estruturada_vira_proposta():
    p = cura.proposta_do_json('{"seletores": ["button:has-text(\\"a\\")"], '
                              '"porque": "a ligadura mudou"}')
    assert p.seletores == ['button:has-text("a")']
    assert p.porque == "a ligadura mudou"


def test_resposta_embrulhada_em_crase_ainda_serve():
    """Modelo local as vezes cerca o JSON. Perder a cura por 3 caracteres seria bobo —
    e nao afrouxa nada, porque quem decide e a validacao contra a pagina."""
    p = cura.proposta_do_json('```json\n{"seletores": ["button"], "porque": "x"}\n```')
    assert p.seletores == ["button"]


def test_resposta_com_um_seletor_so_no_singular_tambem_serve():
    assert cura.proposta_do_json('{"seletor": "button", "porque": ""}').seletores == \
        ["button"]


@pytest.mark.parametrize("bruto", ["", "nao sei dizer", "{}", '{"seletores": []}',
                                   '{"seletores": ["   "]}'])
def test_resposta_inutil_nao_vira_proposta(bruto):
    assert cura.proposta_do_json(bruto) is None


def test_no_maximo_tres_alternativas():
    p = cura.proposta_do_json('{"seletores": ["a","b","c","d","e"], "porque": ""}')
    assert len(p.seletores) == 3


# --- o evento no banco --------------------------------------------------


async def test_a_cura_vira_evento_no_banco(tmp_path):
    """O JSON diz o estado; o evento diz QUANDO — e e a linha do tempo que mostra
    'a interface do Google mudou e o app se adaptou'."""
    class RepoFalso:
        def __init__(self):
            self.eventos = []

        def registrar_evento(self, job_id, conta_id, tipo, payload):
            self.eventos.append((tipo, json.loads(payload)))

    repo = RepoFalso()
    novo = 'button:has-text("expand_more")'
    pagina = _Pagina({sel.DROPDOWN_MODELO: 0, novo: 1})
    auto = AutoCura(curador=CuradorFalso(Proposta(seletores=[novo], porque="mudou")),
                    arquivo=tmp_path / "c.json")
    cura.ligar_no_banco(auto, repo)

    await _transporte(auto)._clicar_unico(pagina, sel.DROPDOWN_MODELO, "dropdown")

    tipo, payload = repo.eventos[0]
    assert tipo == "seletor_curado"
    assert payload["nome"] == "DROPDOWN_MODELO"
    assert payload["valor"] == novo


async def test_banco_que_falha_nao_derruba_a_cura(tmp_path):
    class RepoQuebrado:
        def registrar_evento(self, *a):
            raise RuntimeError("banco travado")

    novo = 'button:has-text("expand_more")'
    pagina = _Pagina({sel.DROPDOWN_MODELO: 0, novo: 1})
    auto = AutoCura(curador=CuradorFalso(Proposta(seletores=[novo], porque="")),
                    arquivo=tmp_path / "c.json")
    cura.ligar_no_banco(auto, RepoQuebrado())

    await _transporte(auto)._clicar_unico(pagina, sel.DROPDOWN_MODELO, "dropdown")
    assert pagina.locator(novo).clicado is True   # o seletor curado ja valia


def test_os_seletores_que_o_transporte_clica_tem_descricao():
    # os que passam por `_clicar_unico` e nao sao destrutivos: sao exatamente os
    # curaveis, e sem descricao a cura nao acontece
    for nome in ("DROPDOWN_MODELO", "CHIP_CONFIG", "CAMPO_PROMPT", "BOTAO_ENVIAR"):
        assert nome in sel.DESCRICAO
    for chave in sel.BOTAO_MODO:
        assert f"BOTAO_MODO[{chave}]" in sel.DESCRICAO


def test_o_botao_de_repetir_saiu_dos_curaveis():
    # Ele e clicado pelo transporte, mas nao pode ser curado: VISTO em 2026-08-21, o
    # card de falha tem tres botoes em fila e um deles APAGA a geracao. Um modelo
    # escolhendo entre vizinhos ali nao custa um clique perdido, custa o trabalho do
    # usuario. Mesmo motivo do menu do card.
    assert "BOTAO_REPETIR_CARD" in sel.NAO_CURAVEIS
    assert "BOTAO_REPETIR_CARD" not in sel.DESCRICAO
