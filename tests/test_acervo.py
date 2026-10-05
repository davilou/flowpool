"""A memoria do acervo: reconhece o arquivo pelo CONTEUDO e acha o item nas duas formas
do `src` — a com uuid (logo depois do envio) e a opaca `asb/...` (depois que a lista
se redesenha). MEDIDO 2026-10-05 na conta 14, ver `pw/acervo.py`."""

from flowpool.transporte.pw.acervo import (
    Lembranca,
    MemoriaDoAcervo,
    impressao,
    localizar,
    retrato,
    token_asb,
)

UUID = "2355c7be-cc53-4f7d-9e58-226c26613199"
TOKEN = "AB-nOUYV7-07j0kPxxBVStUX"


def _uuid_src(uuid=UUID):
    return f"https://flow-content.google/image/{uuid}?Expires=1&Signature=x"


def _asb_src(token=TOKEN):
    return f"https://flow.google.com/asb/{token}=s512-rw"


def _item(nome, src):
    return {"texto": f"{nome}\nImagem", "src": src, "miniaturas": 1}


def _lembranca(asb=None, antes=frozenset()):
    return Lembranca(uuid=UUID, nome="ref.png", asb=asb, antes=antes)


# --- o token opaco ------------------------------------------------------------

def test_token_asb_sai_dos_dois_hosts_sem_o_sufixo_de_tamanho():
    assert token_asb(_asb_src()) == TOKEN
    # a forma de 05/09, no host de cache
    assert token_asb(f"https://lh3.googleusercontent.com/asb/{TOKEN}=s256") == TOKEN
    assert token_asb(_uuid_src()) == ""
    assert token_asb(None) == ""


# --- localizar: as tres formas de achar, e a de NAO achar ---------------------

def test_uuid_ainda_no_src_acha_pelo_uuid():
    assert localizar(_lembranca(), [_item("ref.png", _uuid_src())]) == (UUID, None)


def test_token_ja_aprendido_acha_pelo_token():
    achado = localizar(_lembranca(asb=TOKEN), [_item("ref.png", _asb_src())])
    assert achado == (TOKEN, None)


def test_aprende_o_token_do_unico_item_novo_com_o_mesmo_nome():
    velho = _item("ref.png", _asb_src("TOKEN-VELHO"))       # homonimo de antes do envio
    nosso = _item("ref.png", _asb_src())
    gerado = _item("a scene of a castle", _asb_src("TOKEN-GERADO"))
    lem = _lembranca(antes=frozenset({"TOKEN-VELHO"}))
    assert localizar(lem, [velho, nosso, gerado]) == (TOKEN, TOKEN)


def test_dois_candidatos_novos_e_ambiguo_e_nao_arrisca():
    # ambiguidade nao se desempata: envia de novo, que e o comportamento seguro
    a = _item("ref.png", _asb_src("T1"))
    b = _item("ref.png", _asb_src("T2"))
    assert localizar(_lembranca(), [a, b]) is None


def test_item_que_sumiu_nao_e_achado():
    assert localizar(_lembranca(asb=TOKEN), [_item("outra.png", _asb_src("X"))]) is None


def test_token_aprendido_que_aparece_DUAS_vezes_nao_serve():
    itens = [_item("ref.png", _asb_src()), _item("ref.png", _asb_src())]
    assert localizar(_lembranca(asb=TOKEN), itens) is None


def test_retrato_guarda_tokens_e_uuids():
    itens = [_item("a", _asb_src("T1")), _item("b", _uuid_src("u-2")), _item("c", "")]
    assert retrato(itens) == frozenset({"T1", "u-2"})


# --- a memoria -----------------------------------------------------------------

def _arquivo(tmp_path, nome, conteudo):
    p = tmp_path / nome
    p.write_bytes(conteudo)
    return str(p)


def test_mesmo_conteudo_no_mesmo_projeto_e_lembrado(tmp_path):
    m = MemoriaDoAcervo()
    a = _arquivo(tmp_path, "ref.png", b"foto")
    m.lembrar("proj-1", a, _lembranca())
    assert m.buscar("proj-1", a) == _lembranca()


def test_arquivo_que_mudou_com_o_mesmo_nome_NAO_e_lembrado(tmp_path):
    # o homonimo: o caso que o nome nao distingue e o conteudo distingue
    m = MemoriaDoAcervo()
    a = _arquivo(tmp_path, "ref.png", b"foto velha")
    m.lembrar("proj-1", a, _lembranca())
    _arquivo(tmp_path, "ref.png", b"foto nova")
    assert m.buscar("proj-1", a) is None


def test_outro_projeto_tem_outro_acervo(tmp_path):
    m = MemoriaDoAcervo()
    a = _arquivo(tmp_path, "ref.png", b"foto")
    m.lembrar("proj-1", a, _lembranca())
    assert m.buscar("proj-2", a) is None


def test_sem_projeto_nada_e_lembrado(tmp_path):
    m = MemoriaDoAcervo()
    a = _arquivo(tmp_path, "ref.png", b"foto")
    m.lembrar("", a, _lembranca())
    assert m.buscar("", a) is None


def test_arquivo_que_nao_existe_nao_quebra_so_nao_e_lembrado(tmp_path):
    m = MemoriaDoAcervo()
    sumiu = str(tmp_path / "nao-existe.png")
    assert impressao(sumiu) is None
    m.lembrar("proj-1", sumiu, _lembranca())
    assert m.buscar("proj-1", sumiu) is None


def test_esquecer_o_projeto_apaga_so_ele(tmp_path):
    m = MemoriaDoAcervo()
    a = _arquivo(tmp_path, "ref.png", b"foto")
    m.lembrar("proj-1", a, _lembranca())
    m.lembrar("proj-2", a, _lembranca())
    m.esquecer_projeto("proj-1")
    assert m.buscar("proj-1", a) is None
    assert m.buscar("proj-2", a) is not None
