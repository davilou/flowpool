"""A geometria da janela do Chrome de trabalho: o que nasce escondido e o que aparece."""

import pytest

from flowpool.transporte.pw import janela
from flowpool.transporte.pw.sessao import argumentos_playwright


def test_visivel_e_o_default_e_sobe_maximizada():
    # O default visivel e deliberado: com a janela fora da tela o log de passo e o
    # unico sinal de que ha trabalho, e "a tela parada me fez achar que travou" e o
    # que fez aquele log existir. Quem esconde tem que ter pedido.
    assert janela.argumentos(oculta=False) == ["--start-maximized"]


def test_oculta_vai_para_fora_da_tela_com_tamanho_explicito():
    args = janela.argumentos(oculta=True)
    assert "--window-position=-32000,-32000" in args
    # sem tamanho explicito a janela nasceria no padrao pequeno do Chrome, e medir a
    # grade do Flow num viewport estreito ja produziu seletor casando zero
    assert any(a.startswith("--window-size=") for a in args)


def test_os_dois_modos_nao_se_misturam():
    # ⚠️ `--start-maximized` VENCE `--window-position` no Chrome. Passar os dois
    # deixaria a janela no meio da tela de quem pediu para nao ve-la — e o teste que
    # so conferisse "a flag de posicao esta na lista" aprovaria esse bug.
    assert "--start-maximized" not in janela.argumentos(oculta=True)
    assert not any("--window-position" in a for a in janela.argumentos(oculta=False))


@pytest.mark.parametrize("oculta", [True, False])
def test_a_flag_de_automacao_sobrevive_aos_dois_modos(oculta):
    # o conserto do `navigator.webdriver` nao pode depender de onde a janela esta
    assert "--disable-blink-features=AutomationControlled" in argumentos_playwright(oculta)


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("oculta", True), ("OCULTA", True), (" oculta ", True),
     ("", False), ("1", False), ("sim", False), ("visivel", False)],
)
def test_o_ambiente_liga_so_com_a_palavra_exata(monkeypatch, valor, esperado):
    # `1` e `sim` NAO ligam de proposito: a variavel nomeia um modo, nao um booleano, e
    # um dia havera um terceiro modo. `FLOWPOOL_JANELA=1` significando "oculta" seria
    # uma armadilha no dia em que alguem escrever `FLOWPOOL_JANELA=minimizada`.
    monkeypatch.setenv("FLOWPOOL_JANELA", valor)
    assert janela.oculta_por_ambiente() is esperado


def test_sem_a_variavel_a_janela_aparece(monkeypatch):
    monkeypatch.delenv("FLOWPOOL_JANELA", raising=False)
    assert janela.oculta_por_ambiente() is False
    assert argumentos_playwright() == ["--start-maximized",
                                       "--disable-blink-features=AutomationControlled"]


def test_o_ambiente_decide_quando_ninguem_passa_o_parametro(monkeypatch):
    monkeypatch.setenv("FLOWPOOL_JANELA", "oculta")
    assert any("--window-position" in a for a in argumentos_playwright())


# --- mostrar a janela: a conversa com o CDP ----------------------------------


class _CdpFalso:
    """Duplo do CDP com as ARESTAS do real, senao ele aprova codigo quebrado.

    `setWindowBounds` sem `windowId` e com bounds vazio estouram, que e o que o Chrome
    faz. Duplo que aceita qualquer coisa daria a impressao de guarda sem guardar nada.
    """

    def __init__(self, window_id: int = 77):
        self.window_id = window_id
        self.chamadas: list[tuple[str, dict]] = []

    async def send(self, metodo: str, params: dict | None = None):
        self.chamadas.append((metodo, params or {}))
        if metodo == "Browser.getWindowForTarget":
            return {"windowId": self.window_id,
                    "bounds": {"left": -32000, "top": -32000,
                               "width": 1920, "height": 1080}}
        if metodo == "Browser.setWindowBounds":
            p = params or {}
            if "windowId" not in p:
                raise RuntimeError("Browser.setWindowBounds exige windowId")
            if not p.get("bounds"):
                raise RuntimeError("Browser.setWindowBounds exige bounds")
            return {}
        raise RuntimeError(f"metodo nao esperado: {metodo}")


class _ContextoFalso:
    def __init__(self, cdp): self.cdp = cdp
    async def new_cdp_session(self, _pagina): return self.cdp


class _PaginaFalsa:
    def __init__(self, cdp): self.context = _ContextoFalso(cdp)


async def test_mostrar_pede_a_janela_do_alvo_e_a_poe_na_tela():
    cdp = _CdpFalso(window_id=99)
    await janela.mostrar(_PaginaFalsa(cdp))

    metodos = [m for m, _ in cdp.chamadas]
    assert metodos == ["Browser.getWindowForTarget", "Browser.setWindowBounds"]

    _, params = cdp.chamadas[1]
    assert params["windowId"] == 99, "a janela mexida tem que ser a do alvo do CDP"
    b = params["bounds"]
    assert b["left"] >= 0 and b["top"] >= 0, "posicao negativa deixaria a janela escondida"
    assert b["width"] > 0 and b["height"] > 0


async def test_mostrar_nao_maximiza():
    # ⚠️ A janela que toma a tela inteira e a reclamacao que fez este modulo existir.
    # Reaparecer maximizada seria entregar de volta o problema que se foi resolver.
    cdp = _CdpFalso()
    await janela.mostrar(_PaginaFalsa(cdp))
    assert cdp.chamadas[1][1]["bounds"].get("windowState") != "maximized"


async def test_mostrar_nao_engole_recusa_do_cdp():
    """Falhar alto: janela que nao apareceu com o painel dizendo "ok" e pior que erro.

    O usuario ficaria procurando na tela uma janela que continua em -32000.
    """
    class _Recusa(_CdpFalso):
        async def send(self, metodo, params=None):
            if metodo == "Browser.setWindowBounds":
                raise RuntimeError("Browser.setWindowBounds: window not found")
            return await super().send(metodo, params)

    with pytest.raises(RuntimeError, match="window not found"):
        await janela.mostrar(_PaginaFalsa(_Recusa()))


async def test_esconder_devolve_a_janela_para_fora_da_tela():
    """⚠️ `mostrar` e PERMANENTE, e isso era o defeito.

    A sessao do Flow fica guardada e e reusada entre jobs: sem `esconder`, olhar uma
    conta uma vez deixava o Chrome dela visivel em todos os jobs seguintes — o incomodo
    que o modo oculto existe para resolver, de volta.
    """
    cdp = _CdpFalso()
    await janela.esconder(_PaginaFalsa(cdp))

    bounds = cdp.chamadas[1][1]["bounds"]
    assert (bounds["left"], bounds["top"]) == janela.POSICAO_OCULTA
    assert bounds["left"] < 0 and bounds["top"] < 0


async def test_esconder_e_mostrar_sao_opostos_de_verdade():
    # o teste que pega a troca de constante: se as duas apontarem para o mesmo lugar,
    # uma delas nao faz nada e o sintoma aparece so na tela do usuario
    ida, volta = _CdpFalso(), _CdpFalso()
    await janela.esconder(_PaginaFalsa(ida))
    await janela.mostrar(_PaginaFalsa(volta))

    fora = ida.chamadas[1][1]["bounds"]
    dentro = volta.chamadas[1][1]["bounds"]
    assert fora["left"] < 0 <= dentro["left"]
    assert fora["top"] < 0 <= dentro["top"]
