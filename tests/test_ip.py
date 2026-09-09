"""A leitura do IP de saida, sem rede.

O transporte httpx entra por parametro, no mesmo formato de `api/colar.py` — e assim
que a suite exercita resposta feliz, resposta torta e queda sem sair da maquina.
"""

import httpx
import pytest

from flowpool.nucleo.erros import ProxyInvalido
from flowpool.transporte.ip import URL_DE_LEITURA, LeituraDeIpFalhou, ip_de_saida


class _Transporte(httpx.AsyncBaseTransport):
    """Responde o que se mandar, e ANOTA se foi chamado.

    O "foi chamado" e o ponto de um dos testes: proxy invalido tem que falhar ANTES de
    abrir socket, e a unica forma de provar isso e o transporte registrar zero chamadas.
    """

    def __init__(self, corpo="203.0.113.7", status=200, erro=None):
        self.corpo = corpo
        self.status = status
        self.erro = erro
        self.chamadas = []

    async def handle_async_request(self, request):
        self.chamadas.append(str(request.url))
        if self.erro:
            raise self.erro
        return httpx.Response(self.status, text=self.corpo)


async def test_resposta_feliz_devolve_o_ip():
    t = _Transporte(corpo="203.0.113.7")
    assert await ip_de_saida("http://proxy.exemplo:8080", transporte=t) == "203.0.113.7"
    assert t.chamadas == [URL_DE_LEITURA]


async def test_sem_proxy_mede_o_ip_desta_maquina():
    """`None` NAO e erro: e o que as nove contas de hoje usam, e duas contas sem proxy
    colidem entre si — saber esse IP e metade da resposta sobre colisao."""
    assert await ip_de_saida(None, transporte=_Transporte(corpo="198.51.100.4")) \
        == "198.51.100.4"


async def test_ipv6_tambem_vale():
    t = _Transporte(corpo="2001:db8::1")
    assert await ip_de_saida(None, transporte=t) == "2001:db8::1"


async def test_espaco_em_volta_nao_atrapalha():
    assert await ip_de_saida(None, transporte=_Transporte(corpo=" 203.0.113.7\n")) \
        == "203.0.113.7"


async def test_resposta_que_nao_e_ip_levanta_com_a_resposta_na_mensagem():
    """Portal de rede cativa, pagina de erro do proxy, bloqueio do provedor. Gravar
    isso como "IP de saida" seria pior que falhar: a conta apareceria conferida com um
    valor que nao e IP."""
    t = _Transporte(corpo="<html>Acesso bloqueado pela sua rede</html>")
    with pytest.raises(LeituraDeIpFalhou, match="Acesso bloqueado"):
        await ip_de_saida(None, transporte=t)


async def test_erro_http_levanta_dizendo_por_qual_proxy():
    """Sem o proxy na mensagem, a falha nao diz qual dos nove esta morto."""
    t = _Transporte(erro=httpx.ConnectTimeout("estourou"))
    with pytest.raises(LeituraDeIpFalhou, match=r"proxy http://morto\.exemplo:8080"):
        await ip_de_saida("http://morto.exemplo:8080", transporte=t)


async def test_status_de_erro_tambem_levanta():
    t = _Transporte(corpo="nao autorizado", status=407)
    with pytest.raises(LeituraDeIpFalhou):
        await ip_de_saida("http://proxy.exemplo:8080", transporte=t)


async def test_proxy_invalido_levanta_SEM_TOCAR_A_REDE():
    """Falhar cedo, com a URL na mensagem — e nao virar um timeout que nao explica
    nada. O transporte com zero chamadas e a prova."""
    t = _Transporte()
    with pytest.raises(ProxyInvalido):
        await ip_de_saida("proxy-sem-esquema:8080", transporte=t)
    assert t.chamadas == [], "nao pode ter aberto socket"


async def test_proxy_sem_porta_tambem_para_antes_da_rede():
    t = _Transporte()
    with pytest.raises(ProxyInvalido, match="sem porta"):
        await ip_de_saida("http://proxy.exemplo", transporte=t)
    assert t.chamadas == []
