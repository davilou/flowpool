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
    # a mensagem vai para a resposta HTTP e para o log — e o motivo de X5 existir
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
