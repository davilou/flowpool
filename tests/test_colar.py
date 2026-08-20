"""Colagem de imagem — as duas rotas e a cerca da URL.

`validar_url` ganha teste exaustivo proprio porque e a UNICA coisa entre uma caixa de
texto do painel e um `GET` saindo desta maquina para onde quer que o texto mande.
"""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from flowpool.api import colar
from flowpool.api.app import criar_app
from flowpool.dados.repo import Repo
from flowpool.transporte.falso import TransporteFalso

PNG = b"\x89PNG\r\n\x1a\n" + b"conteudo qualquer"
JPEG = b"\xff\xd8\xff\xe0" + b"conteudo qualquer"
GIF = b"GIF89a" + b"conteudo qualquer"
WEBP = b"RIFF" + b"\x20\x00\x00\x00" + b"WEBP" + b"VP8 conteudo"


@pytest.fixture
def cliente(tmp_path):
    repo = Repo(tmp_path / "t.db")
    repo.criar_esquema()
    app = criar_app(repo=repo, transporte=TransporteFalso(saldos={}), raiz=tmp_path)
    return TestClient(app), tmp_path


def publico(_host):
    return ["93.184.216.34"]


# ---------------- assinatura: o tipo sai dos BYTES ----------------


def test_reconhece_os_quatro_formatos_pela_assinatura():
    assert colar.extensao_da_imagem(PNG) == ".png"
    assert colar.extensao_da_imagem(JPEG) == ".jpg"
    assert colar.extensao_da_imagem(GIF) == ".gif"
    assert colar.extensao_da_imagem(WEBP) == ".webp"


def test_wav_nao_passa_por_webp():
    # os dois comecam com "RIFF"; o que separa e o rotulo em 8..12
    assert colar.extensao_da_imagem(b"RIFF\x20\x00\x00\x00WAVEfmt ") is None


def test_o_que_nao_e_imagem_nao_ganha_extensao():
    assert colar.extensao_da_imagem(b"MZ\x90\x00executavel") is None
    assert colar.extensao_da_imagem(b"<html><body>") is None
    assert colar.extensao_da_imagem(b"") is None


# ---------------- gravacao ----------------


def test_guardar_grava_em_coladas_com_nome_de_hash(tmp_path):
    r = colar.guardar(tmp_path, PNG)
    assert r["caminho"] == f"_coladas/{r['nome']}"
    assert r["nome"].endswith(".png")
    assert r["tamanho"] == len(PNG)
    assert (tmp_path / "_coladas" / r["nome"]).read_bytes() == PNG


def test_a_mesma_imagem_colada_duas_vezes_reaproveita_o_arquivo(tmp_path):
    # o nome e o hash do conteudo: colar de novo nao pode encher a pasta de copias
    a = colar.guardar(tmp_path, PNG)
    b = colar.guardar(tmp_path, PNG)
    assert a["nome"] == b["nome"]
    assert len(list((tmp_path / "_coladas").iterdir())) == 1


def test_imagens_diferentes_ganham_nomes_diferentes(tmp_path):
    a = colar.guardar(tmp_path, PNG)
    b = colar.guardar(tmp_path, PNG + b"outra coisa")
    assert a["nome"] != b["nome"]


def test_guardar_recusa_o_que_nao_e_imagem(tmp_path):
    with pytest.raises(colar.ConteudoRecusado):
        colar.guardar(tmp_path, b"MZ\x90\x00nem de longe")
    assert not (tmp_path / "_coladas").exists()


def test_guardar_recusa_acima_do_teto(tmp_path):
    with pytest.raises(colar.ConteudoGrande):
        colar.guardar(tmp_path, PNG + b"x" * colar.TETO_BYTES)


# ---------------- a cerca da URL ----------------


def test_http_e_https_passam():
    assert colar.validar_url("http://exemplo.com/a.png", publico)
    assert colar.validar_url("https://exemplo.com/a.png", publico)


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/win.ini",
    "ftp://exemplo.com/a.png",
    "gopher://exemplo.com/a.png",
    "data:image/png;base64,iVBOR",
    "javascript:alert(1)",
    "exemplo.com/a.png",
])
def test_esquema_fora_de_http_e_https_e_recusado(url):
    with pytest.raises(colar.UrlRecusada):
        colar.validar_url(url, publico)


@pytest.mark.parametrize("url", [
    "http://localhost/a.png",
    "http://localhost:8777/a.png",
    "http://LOCALHOST/a.png",
    "http://qualquer.localhost/a.png",
])
def test_localhost_e_recusado_antes_mesmo_do_dns(url):
    def sem_dns(_host):
        raise AssertionError("nao devia nem tentar resolver")

    with pytest.raises(colar.UrlRecusada):
        colar.validar_url(url, sem_dns)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/a.png",
    "http://127.1.2.3/a.png",
    "http://192.168.1.1/a.png",
    "http://10.0.0.1/a.png",
    "http://172.16.0.1/a.png",
    "http://172.31.255.255/a.png",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/a.png",
    "http://[fe80::1]/a.png",
    "http://[fc00::1]/a.png",
    "http://0.0.0.0/a.png",
])
def test_rede_local_e_privada_e_recusada(url):
    # endereco literal nao passa por resolvedor nenhum: o teste falha alto se passar
    def sem_dns(_host):
        raise AssertionError("endereco literal nao se resolve")

    with pytest.raises(colar.UrlRecusada):
        colar.validar_url(url, sem_dns)


def test_nome_que_aponta_para_127_0_0_1_e_recusado():
    # o caso que um teste por TEXTO deixaria passar: nome de dominio comum, DNS comum,
    # apontando para dentro desta maquina
    with pytest.raises(colar.UrlRecusada) as e:
        colar.validar_url("https://parece-normal.com/a.png", lambda h: ["127.0.0.1"])
    assert "127.0.0.1" in str(e.value)


def test_nome_que_aponta_para_a_rede_de_casa_e_recusado():
    with pytest.raises(colar.UrlRecusada):
        colar.validar_url("https://parece-normal.com/a.png", lambda h: ["192.168.0.10"])


def test_loopback_escrito_como_ipv6_mapeado_e_recusado():
    # `::ffff:127.0.0.1` e loopback disfarcado; sem desembrulhar, passaria
    with pytest.raises(colar.UrlRecusada):
        colar.validar_url("https://x.com/a.png", lambda h: ["::ffff:127.0.0.1"])


def test_uma_resposta_publica_e_outra_local_ja_derruba():
    # DNS pode devolver varios enderecos; basta UM interno para a busca nao acontecer
    with pytest.raises(colar.UrlRecusada):
        colar.validar_url("https://x.com/a.png",
                          lambda h: ["93.184.216.34", "10.1.2.3"])


def test_nome_que_nao_resolve_e_recusado():
    def falha(_host):
        raise OSError("nao resolveu")

    with pytest.raises(colar.UrlRecusada):
        colar.validar_url("https://nao-existe.invalido/a.png", falha)


def test_url_sem_host_e_recusada():
    with pytest.raises(colar.UrlRecusada):
        colar.validar_url("http:///a.png", publico)


def test_url_normal_passa_e_volta_igual():
    url = "https://cdn.exemplo.com/pasta/foto.jpg?w=800"
    assert colar.validar_url(url, publico) == url


# ---------------- busca por URL ----------------


def _transporte(mapa):
    def responder(pedido: httpx.Request) -> httpx.Response:
        return mapa[str(pedido.url)]

    return httpx.MockTransport(responder)


@pytest.fixture
def dns_publico(monkeypatch):
    """Todo nome resolve para um endereco publico — nenhum teste sai para a rede."""
    monkeypatch.setattr(colar, "_resolver_padrao", publico)


async def test_buscar_traz_os_bytes(dns_publico):
    t = _transporte({"https://x.com/a.png": httpx.Response(200, content=PNG)})
    assert await colar.buscar("https://x.com/a.png", t) == PNG


async def test_buscar_recusa_redirecionamento_para_a_rede_local(dns_publico):
    # a cerca vale em CADA salto: sem isso, um 302 passaria por baixo dela
    t = _transporte({
        "https://x.com/a.png": httpx.Response(
            302, headers={"location": "http://127.0.0.1:8777/painel.js"}),
    })
    with pytest.raises(colar.UrlRecusada):
        await colar.buscar("https://x.com/a.png", t)


async def test_buscar_segue_redirecionamento_para_endereco_publico(dns_publico):
    t = _transporte({
        "https://x.com/a.png": httpx.Response(
            302, headers={"location": "https://cdn.x.com/real.png"}),
        "https://cdn.x.com/real.png": httpx.Response(200, content=JPEG),
    })
    assert await colar.buscar("https://x.com/a.png", t) == JPEG


async def test_buscar_desiste_de_redirecionamento_sem_fim(dns_publico):
    t = _transporte({
        "https://x.com/a.png": httpx.Response(
            302, headers={"location": "https://x.com/a.png"}),
    })
    with pytest.raises(colar.BuscaFalhou):
        await colar.buscar("https://x.com/a.png", t)


async def test_buscar_leva_erro_do_site_como_busca_falhou(dns_publico):
    t = _transporte({"https://x.com/a.png": httpx.Response(404)})
    with pytest.raises(colar.BuscaFalhou):
        await colar.buscar("https://x.com/a.png", t)


async def test_buscar_para_no_teto_mesmo_com_content_length_mentiroso(dns_publico):
    # o teto e conferido enquanto se le: acreditar no cabecalho deixaria um site
    # mentiroso encher a memoria deste servico
    gordo = PNG + b"x" * (colar.TETO_BYTES + 1)
    t = _transporte({"https://x.com/a.png": httpx.Response(
        200, content=gordo, headers={"content-length": "10"})})
    with pytest.raises(colar.ConteudoGrande):
        await colar.buscar("https://x.com/a.png", t)


# ---------------- POST /colar ----------------


def test_colar_grava_e_devolve_o_caminho_que_o_painel_ja_usa(cliente):
    c, raiz = cliente
    r = c.post("/colar", content=PNG)
    assert r.status_code == 201
    corpo = r.json()
    assert corpo["caminho"].startswith("_coladas/")
    assert corpo["tamanho"] == len(PNG)
    assert (Path(raiz) / corpo["caminho"]).read_bytes() == PNG


def test_o_caminho_devolvido_serve_na_miniatura(cliente):
    # e a prova de que o formato do caminho casa com o resto do painel sem traducao
    c, _ = cliente
    caminho = c.post("/colar", content=PNG).json()["caminho"]
    r = c.get("/miniatura", params={"caminho": caminho})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_o_caminho_devolvido_serve_de_referencia_num_job(cliente):
    c, _ = cliente
    caminho = c.post("/colar", content=WEBP).json()["caminho"]
    r = c.post("/jobs", json={"modo": "ingredients2v", "modelo": "veo-fast",
                              "prompt": "p", "produto": "Moda", "nome": "n",
                              "referencias": [caminho]})
    assert r.status_code == 201


def test_colar_aceita_os_quatro_formatos(cliente):
    c, _ = cliente
    for dados, ext in [(PNG, ".png"), (JPEG, ".jpg"), (GIF, ".gif"), (WEBP, ".webp")]:
        r = c.post("/colar", content=dados)
        assert r.status_code == 201
        assert r.json()["nome"].endswith(ext)


def test_colar_recusa_o_que_nao_e_imagem(cliente):
    # a assinatura e conferida nos BYTES: o `Content-Type` declarado nao vale nada aqui
    c, _ = cliente
    r = c.post("/colar", content=b"MZ\x90\x00executavel",
               headers={"content-type": "image/png"})
    assert r.status_code == 400


def test_colar_recusa_corpo_vazio(cliente):
    c, _ = cliente
    assert c.post("/colar", content=b"").status_code == 400


def test_colar_recusa_acima_do_teto(cliente):
    c, _ = cliente
    r = c.post("/colar", content=PNG + b"x" * colar.TETO_BYTES)
    assert r.status_code == 413


def test_colar_recusa_pelo_content_length_declarado_sem_ler_o_corpo(cliente):
    c, _ = cliente
    r = c.post("/colar", content=PNG,
               headers={"content-length": str(colar.TETO_BYTES + 1)})
    assert r.status_code == 413


def test_colar_nao_deixa_o_cliente_escolher_onde_o_arquivo_cai(cliente):
    # nao ha campo de nome NENHUM na rota — o nome sai do hash. Este teste guarda a
    # ausencia: acrescentar um nome vindo do cliente teria que quebrar aqui.
    c, raiz = cliente
    corpo = c.post("/colar", content=PNG,
                   headers={"x-filename": "../../fora.png",
                            "content-disposition": 'attachment; filename="../fora.png"'}).json()
    assert corpo["nome"] not in ("fora.png", "../fora.png")
    assert (Path(raiz) / "_coladas" / corpo["nome"]).is_file()
    assert not (Path(raiz).parent / "fora.png").exists()


# ---------------- POST /colar-url ----------------


def test_colar_url_grava_a_imagem_buscada(cliente, monkeypatch):
    c, raiz = cliente

    async def falsa(url, transporte=None):
        assert url == "https://x.com/a.png"
        return PNG

    monkeypatch.setattr(colar, "buscar", falsa)
    r = c.post("/colar-url", json={"url": "https://x.com/a.png"})
    assert r.status_code == 201
    assert (Path(raiz) / r.json()["caminho"]).read_bytes() == PNG


@pytest.mark.parametrize("url", [
    "http://localhost:8777/painel.js",
    "http://127.0.0.1/a.png",
    "http://192.168.1.1/a.png",
    "http://10.0.0.1/a.png",
    "http://[::1]/a.png",
    "file:///C:/Windows/win.ini",
    "ftp://exemplo.com/a.png",
])
def test_colar_url_recusa_o_que_a_cerca_recusa(cliente, url):
    c, _ = cliente
    r = c.post("/colar-url", json={"url": url})
    assert r.status_code == 400, url


def test_colar_url_devolve_502_quando_o_outro_lado_falha(cliente, monkeypatch):
    # a URL era aceitavel e quem falhou foi o site: nao ha nada a corrigir no texto
    c, _ = cliente

    async def falsa(url, transporte=None):
        raise colar.BuscaFalhou("o site respondeu 404")

    monkeypatch.setattr(colar, "buscar", falsa)
    assert c.post("/colar-url", json={"url": "https://x.com/a.png"}).status_code == 502


def test_colar_url_recusa_conteudo_que_nao_e_imagem(cliente, monkeypatch):
    c, _ = cliente

    async def falsa(url, transporte=None):
        return b"<html>pagina de erro bonitinha</html>"

    monkeypatch.setattr(colar, "buscar", falsa)
    assert c.post("/colar-url", json={"url": "https://x.com/a.png"}).status_code == 400


def test_colar_url_devolve_413_para_imagem_grande_demais(cliente, monkeypatch):
    c, _ = cliente

    async def falsa(url, transporte=None):
        raise colar.ConteudoGrande("a imagem passa de 20 MB")

    monkeypatch.setattr(colar, "buscar", falsa)
    assert c.post("/colar-url", json={"url": "https://x.com/a.png"}).status_code == 413


def test_colar_url_sem_url_da_422(cliente):
    c, _ = cliente
    assert c.post("/colar-url", json={}).status_code == 422
    assert c.post("/colar-url", json={"url": ""}).status_code == 422
