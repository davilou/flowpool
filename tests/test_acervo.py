"""A memoria do acervo: reconhece pelo conteudo, nunca pelo nome."""

from flowpool.transporte.pw.acervo import MemoriaDoAcervo, impressao


def _arquivo(tmp_path, nome, conteudo):
    p = tmp_path / nome
    p.write_bytes(conteudo)
    return str(p)


def test_mesmo_conteudo_no_mesmo_projeto_e_lembrado(tmp_path):
    m = MemoriaDoAcervo()
    a = _arquivo(tmp_path, "a.png", b"foto")
    m.lembrar("proj-1", a, {"id": "midia-a", "nome": "a.png"})
    assert m.buscar("proj-1", a) == {"id": "midia-a", "nome": "a.png"}


def test_arquivo_que_mudou_com_o_mesmo_nome_NAO_e_lembrado(tmp_path):
    # o homonimo: e exatamente o caso que o nome nao distingue e o conteudo distingue
    m = MemoriaDoAcervo()
    a = _arquivo(tmp_path, "a.png", b"foto velha")
    m.lembrar("proj-1", a, {"id": "midia-a", "nome": "a.png"})
    _arquivo(tmp_path, "a.png", b"foto nova")
    assert m.buscar("proj-1", a) is None


def test_outro_projeto_tem_outro_acervo(tmp_path):
    m = MemoriaDoAcervo()
    a = _arquivo(tmp_path, "a.png", b"foto")
    m.lembrar("proj-1", a, {"id": "midia-a", "nome": "a.png"})
    assert m.buscar("proj-2", a) is None


def test_sem_projeto_nada_e_lembrado(tmp_path):
    m = MemoriaDoAcervo()
    a = _arquivo(tmp_path, "a.png", b"foto")
    m.lembrar("", a, {"id": "midia-a", "nome": "a.png"})
    assert m.buscar("", a) is None


def test_arquivo_que_nao_existe_nao_quebra_so_nao_e_lembrado(tmp_path):
    m = MemoriaDoAcervo()
    sumiu = str(tmp_path / "nao-existe.png")
    assert impressao(sumiu) is None
    m.lembrar("proj-1", sumiu, {"id": "x", "nome": "x"})
    assert m.buscar("proj-1", sumiu) is None


def test_esquecer_tira_da_memoria(tmp_path):
    m = MemoriaDoAcervo()
    a = _arquivo(tmp_path, "a.png", b"foto")
    m.lembrar("proj-1", a, {"id": "midia-a", "nome": "a.png"})
    m.esquecer("proj-1", a)
    assert m.buscar("proj-1", a) is None
