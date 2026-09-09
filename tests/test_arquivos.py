"""A barreira do seletor de imagens.

`resolver` e a unica coisa entre uma querystring e o disco desta maquina: o servico e
local e nao tem autenticacao. Por isso os testes de escape vem primeiro e sao
exaustivos — cada forma de sair da raiz tem o seu.
"""

import sys

import pytest

from flowpool.api.arquivos import (
    CaminhoForaDaRaiz,
    arquivo_de_imagem,
    e_ruido,
    listar,
    resolver,
)


@pytest.fixture
def raiz(tmp_path):
    (tmp_path / "Panelas Monaco" / "imagens").mkdir(parents=True)
    (tmp_path / "Panelas Monaco" / "imagens" / "b.png").write_bytes(b"x" * 10)
    (tmp_path / "Panelas Monaco" / "imagens" / "a.jpg").write_bytes(b"y" * 3)
    (tmp_path / "Panelas Monaco" / "notas.md").write_text("nao e imagem")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("segredo")
    (tmp_path / "flowpool" / "perfis" / "conta1").mkdir(parents=True)
    (tmp_path / "flowpool" / "perfis" / "conta1" / "Cookies").write_text("sessao")
    return tmp_path


# ---------------- resolver: o que NAO pode passar ----------------

def test_ponto_ponto_simples_e_recusado(raiz):
    with pytest.raises(CaminhoForaDaRaiz):
        resolver(raiz, "..")


def test_ponto_ponto_disfarcado_no_meio_e_recusado(raiz):
    # o caso que um `if ".." in caminho` no meio de uma rota costuma deixar passar
    # depois de alguem "simplificar" a checagem para o prefixo
    with pytest.raises(CaminhoForaDaRaiz):
        resolver(raiz, "Panelas Monaco/../../fora")


def test_ponto_ponto_que_volta_para_dentro_e_aceito(raiz):
    # `a/../a` nunca sai da raiz. Recusar aqui seria a funcao contando `..` em vez de
    # comparar o caminho resolvido, que e o unico criterio que nao erra.
    assert resolver(raiz, "Panelas Monaco/../Panelas Monaco") == \
        (raiz / "Panelas Monaco").resolve()


def test_caminho_absoluto_do_windows_e_recusado(raiz):
    with pytest.raises(CaminhoForaDaRaiz):
        resolver(raiz, r"C:\Windows\System32\drivers\etc\hosts")


def test_caminho_absoluto_do_posix_e_recusado(raiz):
    # recusado nos DOIS sistemas: no Linux `raiz / "C:\\Windows"` cairia dentro da raiz
    # com um nome esquisito, e a regra passaria a valer so no Windows
    with pytest.raises(CaminhoForaDaRaiz):
        resolver(raiz, "/etc/passwd")


def test_unc_e_recusado(raiz):
    with pytest.raises(CaminhoForaDaRaiz):
        resolver(raiz, r"\\servidor\share\x.png")


def test_barra_invertida_nao_escapa(raiz):
    # o usuario cola caminho do Windows; a normalizacao das barras nao pode virar porta
    with pytest.raises(CaminhoForaDaRaiz):
        resolver(raiz, r"..\..\fora")


def _ligar(link, destino):
    """Cria um link de pasta apontando para `destino`, do jeito que a maquina permitir.

    No Windows o symlink exige Modo Desenvolvedor ou admin, mas a JUNCTION nao — e o
    `resolve()` segue as duas igual. Sem esta queda para junction o teste da barreira
    contra link viveria permanentemente pulado nesta maquina, que e o mesmo que nao
    existir.
    """
    try:
        link.symlink_to(destino, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(destino), str(link))
        return
    pytest.skip("esta maquina nao deixa criar link de pasta")


def test_link_simbolico_apontando_para_fora_e_recusado(raiz, tmp_path_factory):
    fora = tmp_path_factory.mktemp("fora")
    (fora / "segredo.png").write_bytes(b"z")
    _ligar(raiz / "atalho", fora)
    # o `resolve()` segue o link e entrega o destino de verdade — que e justamente o que
    # a comparacao final precisa ver
    with pytest.raises(CaminhoForaDaRaiz):
        resolver(raiz, "atalho")
    with pytest.raises(CaminhoForaDaRaiz):
        resolver(raiz, "atalho/segredo.png")


# ---------------- resolver: o que TEM que passar ----------------

def test_caminho_vazio_e_a_raiz(raiz):
    assert resolver(raiz, "") == raiz.resolve()


def test_caminho_none_e_a_raiz(raiz):
    assert resolver(raiz, None) == raiz.resolve()


def test_ponto_sozinho_e_a_raiz(raiz):
    assert resolver(raiz, ".") == raiz.resolve()


def test_caminho_normal_resolve_para_dentro(raiz):
    assert resolver(raiz, "Panelas Monaco/imagens/b.png") == \
        (raiz / "Panelas Monaco" / "imagens" / "b.png").resolve()


def test_caminho_com_espaco_e_acento_funciona(raiz):
    (raiz / "Ação").mkdir()
    assert resolver(raiz, "Ação") == (raiz / "Ação").resolve()


def test_caminho_que_nao_existe_ainda_assim_resolve(raiz):
    # `resolver` responde uma pergunta so: cai dentro da raiz? Se existe e problema de
    # quem chama — separar as duas e o que deixa o 400 e o 404 dizerem coisas diferentes
    assert resolver(raiz, "nao/existe.png").is_relative_to(raiz.resolve())


# ---------------- ruido ----------------

def test_pasta_de_ruido_e_reconhecida():
    assert e_ruido(".git")
    assert e_ruido("a/__pycache__/b")
    assert e_ruido("flowpool/perfis")
    assert e_ruido("flowpool/perfis/conta1/Cookies")


def test_pasta_oculta_e_ruido_mesmo_fora_da_lista():
    # `.git`, `.venv` e `.pytest_cache` sao tres casos da mesma regra; `.claude` e
    # `.obsidian` existem neste projeto e apareceriam na primeira tela do seletor
    assert e_ruido(".claude")
    assert e_ruido(".obsidian/plugins")


def test_pasta_normal_nao_e_ruido():
    assert not e_ruido("")
    assert not e_ruido("Panelas Monaco/imagens")
    # `flowpool` inteiro nao e ruido — so `flowpool/perfis`
    assert not e_ruido("flowpool")
    # e o teste e por PARTE do caminho, nao por prefixo de texto
    assert not e_ruido("flowpool/perfilado")


# ---------------- listar ----------------

def test_listar_separa_pastas_de_arquivos_e_ordena(raiz):
    r = listar(raiz, "Panelas Monaco")
    assert [p["nome"] for p in r["pastas"]] == ["imagens"]
    # `notas.md` fica de fora: so imagem
    assert r["arquivos"] == []

    r = listar(raiz, "Panelas Monaco/imagens")
    assert [a["nome"] for a in r["arquivos"]] == ["a.jpg", "b.png"]
    assert r["arquivos"][1]["tamanho"] == 10
    assert r["arquivos"][0]["caminho"] == "Panelas Monaco/imagens/a.jpg"


def test_listar_a_raiz_esconde_o_ruido(raiz):
    nomes = [p["nome"] for p in listar(raiz, "")["pastas"]]
    assert "Panelas Monaco" in nomes
    assert ".git" not in nomes


def test_listar_raiz_nao_tem_para_onde_voltar(raiz):
    assert listar(raiz, "")["pai"] is None


def test_pai_de_pasta_de_primeiro_nivel_e_a_raiz(raiz):
    # "" e um pai valido (a raiz); `None` significa "nao ha para onde voltar"
    assert listar(raiz, "Panelas Monaco")["pai"] == ""


def test_pai_de_pasta_com_ponto_no_nome_nao_perde_o_ponto(raiz):
    (raiz / "v1.2" / "dentro").mkdir(parents=True)
    assert listar(raiz, "v1.2/dentro")["pai"] == "v1.2"


def test_listar_pasta_ignorada_e_recusado(raiz):
    # esconder da grade nao bastaria: bastaria pedir na querystring
    with pytest.raises(CaminhoForaDaRaiz):
        listar(raiz, "flowpool/perfis/conta1")


def test_listar_pasta_inexistente_levanta_nao_encontrado(raiz):
    with pytest.raises(FileNotFoundError):
        listar(raiz, "nao existe")


def test_listar_arquivo_levanta_nao_e_pasta(raiz):
    with pytest.raises(NotADirectoryError):
        listar(raiz, "Panelas Monaco/notas.md")


# ---------------- imagem para a miniatura ----------------

def test_imagem_valida_resolve(raiz):
    assert arquivo_de_imagem(raiz, "Panelas Monaco/imagens/b.png").name == "b.png"


def test_miniatura_recusa_arquivo_que_nao_e_imagem(raiz):
    # sem a checagem de extensao esta rota serviria o .db e o .env do projeto inteiro
    with pytest.raises(CaminhoForaDaRaiz):
        arquivo_de_imagem(raiz, "Panelas Monaco/notas.md")


def test_miniatura_recusa_caminho_dentro_de_pasta_ignorada(raiz):
    (raiz / "flowpool" / "perfis" / "conta1" / "avatar.png").write_bytes(b"x")
    with pytest.raises(CaminhoForaDaRaiz):
        arquivo_de_imagem(raiz, "flowpool/perfis/conta1/avatar.png")


def test_miniatura_recusa_a_raiz(raiz):
    with pytest.raises(CaminhoForaDaRaiz):
        arquivo_de_imagem(raiz, "")


def test_miniatura_de_imagem_inexistente_levanta_nao_encontrado(raiz):
    with pytest.raises(FileNotFoundError):
        arquivo_de_imagem(raiz, "Panelas Monaco/imagens/sumiu.png")
