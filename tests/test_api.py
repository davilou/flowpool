import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowpool.api.app import criar_app
from flowpool.dados.repo import Repo
from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.tipos import EstadoJob, Modelo, SaudeSessao, Tier
from flowpool.transporte.base import ErroTransporte
from flowpool.transporte.falso import TransporteFalso
from flowpool.transporte.pw.sessao import ChromeAusente


@pytest.fixture
def cliente(tmp_path):
    repo = Repo(tmp_path / "t.db")
    repo.criar_esquema()
    app = criar_app(repo=repo, transporte=TransporteFalso(saldos={}), raiz=tmp_path)
    return TestClient(app), repo


def test_criar_job_devolve_id_e_estado_pendente(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "uma gata",
                              "produto": "Video Roupa", "nome": "c1"})
    assert r.status_code == 201
    assert r.json()["estado"] == "pendente"
    assert isinstance(r.json()["id"], int)


def test_consultar_job_inexistente_da_404(cliente):
    c, _ = cliente
    assert c.get("/jobs/999").status_code == 404


def test_lote_cria_varios_jobs(cliente):
    c, _ = cliente
    corpo = {"jobs": [
        {"modo": "t2v", "modelo": "veo-fast", "prompt": "a", "produto": "Moda", "nome": "c1"},
        {"modo": "t2v", "modelo": "veo-lite", "prompt": "b", "produto": "Moda", "nome": "c2"},
    ]}
    r = c.post("/jobs/lote", json=corpo)
    assert r.status_code == 201
    assert len(r.json()["ids"]) == 2


def test_ingredients2v_recusa_mais_de_tres_referencias(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "ingredients2v", "modelo": "veo-fast",
                              "prompt": "p", "produto": "Moda", "nome": "n",
                              "referencias": ["1.png", "2.png", "3.png", "4.png"]})
    assert r.status_code == 422


def test_imagem_aceita_referencia(cliente):
    # O DEFEITO: imagem com referencia e o Nano Banana gerando a partir de uma foto, e
    # e a PRIMEIRA etapa do pipeline do usuario — foto da roupa -> modelo vestida.
    c, repo = cliente
    r = c.post("/jobs", json={"modo": "imagem", "prompt": "a modelo vestindo",
                              "produto": "Moda", "nome": "n",
                              "referencias": ["roupa.png"]})
    assert r.status_code == 201
    assert repo.job(r.json()["id"]).referencias == ["roupa.png"]


def test_imagem_recusa_mais_de_tres_referencias(cliente):
    # o mesmo teto do ingredients2v: tres
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "prompt": "p", "produto": "Moda",
                              "nome": "n",
                              "referencias": ["1.png", "2.png", "3.png", "4.png"]})
    assert r.status_code == 422


def test_imagem_com_referencia_continua_aceitando_quatro_variacoes(cliente):
    # as duas regras sao independentes: amarrar quantidade a referencia tiraria as
    # variacoes justamente do caso em que elas mais servem
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "prompt": "p", "produto": "Moda",
                              "nome": "n", "quantidade": 4,
                              "referencias": ["roupa.png"]})
    assert r.status_code == 201


def test_imagem_sem_referencia_continua_valendo(cliente):
    # texto puro e o caso comum de imagem; exigir arquivo aqui quebraria todos eles
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "prompt": "p", "produto": "Moda",
                              "nome": "n"})
    assert r.status_code == 201


def test_t2v_recusa_referencia_em_vez_de_descartar_calado(cliente):
    # em t2v o transporte nao anexa nada: aceitar seria prometer uma coisa e gerar
    # outra, com credito gasto
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "prompt": "p", "produto": "Moda",
                              "nome": "n", "referencias": ["roupa.png"]})
    assert r.status_code == 422


def test_frame_inicial_fora_do_frames2v_e_recusado(cliente):
    # frame inicial e final continuam exclusivos de frames2v: em imagem a foto entra
    # por `referencias`
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "prompt": "p", "produto": "Moda",
                              "nome": "n", "frame_inicial": "a.png"})
    assert r.status_code == 422


def test_frames2v_exige_frame_inicial(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "frames2v", "modelo": "veo-fast", "prompt": "p",
                              "produto": "Moda", "nome": "n"})
    assert r.status_code == 422


def test_produto_e_obrigatorio(cliente):
    # sem produto nao ha projeto a resolver: cada geracao criaria um projeto novo
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                              "destino": "d", "nome": "n"})
    assert r.status_code == 422


def test_destino_deriva_do_produto_quando_ausente(cliente):
    # na pasta do usuario cada produto ja e uma pasta; repetir o caminho e ruido
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "produto": "Panelas Monaco", "nome": "n"}).json()["id"]
    j = repo.job(jid)
    assert j.produto == "Panelas Monaco"
    assert j.destino == "Panelas Monaco/clipes"


def test_destino_explicito_e_respeitado(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "produto": "Panelas Monaco", "nome": "n",
                                "destino": "Benchmark Roupa/brutos"}).json()["id"]
    assert repo.job(jid).destino == "Benchmark Roupa/brutos"


def test_cancelar_job_pendente(cliente):
    c, _ = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
    assert c.delete(f"/jobs/{jid}").status_code == 200
    assert c.get(f"/jobs/{jid}").json()["estado"] == "cancelado"


def test_listar_contas_vazio(cliente):
    c, _ = cliente
    assert c.get("/contas").json() == []


def test_patch_conta_desativa(cliente):
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    r = c.patch(f"/contas/{cid}", json={"ativa": False})
    assert r.status_code == 200
    assert repo.conta(cid).ativa is False


def test_saude_resume_o_pool(cliente):
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    conta = repo.conta(cid); conta.saldo = 40; repo.salvar_conta(conta)
    c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                          "produto": "Moda", "nome": "n"})
    s = c.get("/saude").json()
    assert s["contas_vivas"] == 1
    assert s["credito_total"] == 40
    assert s["fila_pendente"] == 1


def test_listar_projetos_e_filtrar(cliente):
    c, repo = cliente
    g1 = repo.criar_conta("g1@b.com", Tier.FREE, "p/1")
    g2 = repo.criar_conta("g2@b.com", Tier.FREE, "p/2")
    repo.registrar_projeto(g1, "Moda", "rem-1", "Moda")
    repo.registrar_projeto(g1, "Vestir", "rem-2", "Vestir")
    repo.registrar_projeto(g2, "Moda", "rem-3", "Moda")

    assert len(c.get("/projetos").json()) == 3
    assert len(c.get("/projetos", params={"conta_id": g1}).json()) == 2
    por_produto = c.get("/projetos", params={"produto": "Moda"}).json()
    assert {p["id_remoto"] for p in por_produto} == {"rem-1", "rem-3"}


def test_esquecer_projeto_avisa_que_o_flow_nao_e_tocado(cliente):
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    pid = repo.registrar_projeto(cid, "Moda", "rem-1", "Moda")

    r = c.delete(f"/projetos/{pid}")
    assert r.status_code == 200
    assert "Flow" in r.json()["aviso"]
    assert repo.projeto_de(cid, "Moda") is None


def test_esquecer_projeto_inexistente_da_404(cliente):
    c, _ = cliente
    assert c.delete("/projetos/999").status_code == 404


# --- proporcao ---

def test_proporcao_padrao_e_vertical(cliente):
    # o canal do Davi e TikTok: gerar ja em 9:16 evita o recorte manual depois
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
    assert repo.job(jid).proporcao == "9:16"


def test_proporcao_explicita_e_respeitada(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n",
                                "proporcao": "16:9"}).json()["id"]
    assert repo.job(jid).proporcao == "16:9"


def test_proporcao_fora_da_lista_da_422(cliente):
    # o painel do Flow so tem cinco botoes; qualquer outro valor nao teria onde clicar
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                              "produto": "Moda", "nome": "n", "proporcao": "21:9"})
    assert r.status_code == 422


# --- quantidade ---

def test_quantidade_padrao_e_uma(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
    assert repo.job(jid).quantidade == 1


def test_video_com_quantidade_maior_que_um_da_422(cliente):
    # take de video e escolha do usuario, nao sorteio — e cada take extra custa cheio
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                              "produto": "Moda", "nome": "n", "quantidade": 2})
    assert r.status_code == 422


def test_imagem_aceita_lote_de_quatro(cliente):
    # variacao de imagem e barata e serve justamente para escolher
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                                "produto": "Moda", "nome": "n",
                                "quantidade": 4}).json()["id"]
    assert repo.job(jid).quantidade == 4


def test_imagem_acima_de_quatro_da_422(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                              "produto": "Moda", "nome": "n", "quantidade": 5})
    assert r.status_code == 422


def test_quantidade_zero_da_422(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                              "produto": "Moda", "nome": "n", "quantidade": 0})
    assert r.status_code == 422


# --- duracao ---

def test_duracao_padrao_e_de_quatro_segundos(cliente):
    # 4s custa 7 creditos e 8s custa 12: fixar 8s desperdicava 70% do credito por
    # video, e numa conta gratuita de 50/dia isso e a diferenca entre 4 e 7 videos
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
    assert repo.job(jid).duracao == "4s"


def test_duracao_explicita_e_respeitada(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n",
                                "duracao": "10s"}).json()["id"]
    assert repo.job(jid).duracao == "10s"


def test_duracao_fora_da_lista_da_422(cliente):
    # o painel do Flow so tem quatro botoes de duracao; "5s" nao teria onde clicar
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                              "produto": "Moda", "nome": "n", "duracao": "5s"})
    assert r.status_code == 422


def test_imagem_com_duracao_da_422(cliente):
    # nao existe grupo de duracao no painel em modo imagem: aceitar o campo seria
    # prometer uma configuracao que o transporte nao tem onde aplicar
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                              "produto": "Moda", "nome": "n", "duracao": "4s"})
    assert r.status_code == 422
    assert "duracao" in r.text or "duração" in r.text


def test_imagem_sem_duracao_passa(cliente):
    # o campo tem default no dominio ("4s"); se a recusa olhasse o VALOR em vez de o
    # campo ter vindo no corpo, todo pedido de imagem seria recusado
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                              "produto": "Moda", "nome": "n"})
    assert r.status_code == 201


def test_resposta_do_job_mostra_a_duracao(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                              "produto": "Moda", "nome": "n", "duracao": "6s"})
    assert r.json()["duracao"] == "6s"


# --- modelo padrao ---

def test_job_de_video_sem_modelo_sai_com_omni_flash(cliente):
    # e o que o dropdown de video do Flow ja mostra; pedir Veo sem dizer seria mentira
    c, repo = cliente
    r = c.post("/jobs", json={"modo": "t2v", "prompt": "p", "produto": "Moda", "nome": "n"})
    assert r.status_code == 201
    assert repo.job(r.json()["id"]).modelo is Modelo.OMNI_FLASH


def test_job_de_imagem_sem_modelo_sai_com_nano_banana(cliente):
    c, repo = cliente
    r = c.post("/jobs", json={"modo": "imagem", "prompt": "p", "produto": "Moda", "nome": "n"})
    assert r.status_code == 201
    assert repo.job(r.json()["id"]).modelo is Modelo.NANO_BANANA_2   # o ilimitado, nao o Pro


def test_modelo_explicito_continua_valendo(cliente):
    c, repo = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-quality", "prompt": "p",
                              "produto": "Moda", "nome": "n"})
    assert repo.job(r.json()["id"]).modelo is Modelo.VEO_QUALITY


def test_patch_grava_o_proxy_da_conta(cliente):
    # cada conta sai por um IP proprio: e o que impede o Google de correlacionar as
    # contas entre si
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    r = c.patch(f"/contas/{cid}", json={"proxy": "http://1.2.3.4:8080"})
    assert r.status_code == 200
    assert repo.conta(cid).proxy == "http://1.2.3.4:8080"
    assert r.json()["proxy"] == "http://1.2.3.4:8080"


def test_patch_recusa_proxy_que_nao_da_para_usar(cliente):
    # falhar no cadastro e melhor do que abrir um navegador que sai pelo IP errado
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    r = c.patch(f"/contas/{cid}", json={"proxy": "1.2.3.4:8080"})
    assert r.status_code == 422
    assert repo.conta(cid).proxy is None


def test_patch_apaga_o_proxy_com_string_vazia(cliente):
    # sem isso nao ha como VOLTAR para "sem proxy" — `None` no PATCH significa
    # "nao mexe", entao tirar o proxy seria impossivel pela API
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    con = repo.conta(cid); con.proxy = "http://1.2.3.4:8080"; repo.salvar_conta(con)
    r = c.patch(f"/contas/{cid}", json={"proxy": ""})
    assert r.status_code == 200
    assert repo.conta(cid).proxy is None


def test_resposta_de_conta_mascara_a_senha_do_proxy(cliente):
    # a resposta vai para o painel e para log; credencial de proxy nao precisa
    # passear por ali
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    con = repo.conta(cid)
    con.proxy = "http://davi:segredo@1.2.3.4:8080"
    repo.salvar_conta(con)

    corpo = c.get("/contas").json()[0]
    assert corpo["proxy"] == "http://davi:***@1.2.3.4:8080"
    assert "segredo" not in c.get("/contas").text


def test_conta_sem_proxy_responde_null(cliente):
    c, repo = cliente
    repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    assert c.get("/contas").json()[0]["proxy"] is None


def test_nova_conta_aceita_proxy_e_recusa_url_torta():
    # a rota `POST /contas` entra no Task 10 (precisa abrir navegador); o esquema
    # existe antes dela para que o cadastro ja nasca no IP certo — conta que nasce no
    # IP de casa e depois muda para proxy denuncia exatamente o que o proxy evita
    from flowpool.api.esquemas import NovaConta

    assert NovaConta(email="a@b.com").proxy is None
    assert NovaConta(email="a@b.com", proxy="http://1.2.3.4:8080").proxy == \
        "http://1.2.3.4:8080"
    with pytest.raises(ValueError):
        NovaConta(email="a@b.com", proxy="ftp://1.2.3.4:8080")


def test_raiz_serve_o_painel(cliente):
    # o painel e servido pelo proprio FastAPI: sem build step, sem segundo servidor e
    # sem StaticFiles apontando para fora do pacote
    c, _ = cliente
    r = c.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "FlowPool" in r.text


def test_painel_referencia_o_css_e_o_js_que_a_api_serve(cliente):
    # o HTML pede dois caminhos absolutos; se algum deles nao tiver rota, o painel sobe
    # sem estilo ou sem comportamento nenhum e a falha nao aparece em teste algum
    c, _ = cliente
    html = c.get("/").text
    assert "/painel.css" in html
    assert "/painel.js" in html
    assert c.get("/painel.css").status_code == 200
    assert c.get("/painel.js").status_code == 200


# MENSAGENS REAIS, copiadas de producao em 2026-09-09. Servem de corpo de prova para
# o casador do painel: sao exatamente o texto que o usuario ve no campo de erro.
MENSAGENS_REAIS = [
    "conta pmara5537@gmail.com: o Flow recusou por atividade incomum (o antiabuso do "
    "Google; costuma ser concorrencia demais no mesmo IP) (sem cobranca por esta "
    "geracao; o saldo continua valendo)",
    "CardTrocado: card-0 e um card de imagem e o job e video: o card aberto nao e o "
    "resultado deste job",
    "GeracaoFalhou: card-0 sumiu da grade",
    "GeracaoFalhou: o painel de configuracao nao abriu",
    "daviright@gmail.com caiu no portao de idade do Flow (/age-restricted): a conta "
    "precisa de verificacao de idade",
    "TargetClosedError: BrowserType.launch_persistent_context: Target page, context or "
    "browser has been closed",
]


def test_nenhum_termo_do_painel_casa_dentro_de_outra_palavra(cliente):
    """⚠️ `includes("idade")` casa DENTRO de "atividade" — MEDIDO 2026-09-09.

    A recusa por ATIVIDADE incomum aparecia no painel como "a conta caiu no portao de
    idade do Flow", mandando o usuario verificar a idade da conta Google por um
    problema que nao existia. A regra do portao e a segunda da lista, entao ela atirava
    antes de qualquer regra mais especifica poder responder.

    O painel nao tem como ser testado por dentro (sem Node, por decisao do projeto),
    entao o que da para garantir daqui e a materia-prima: TERMO QUE APARECE DENTRO DE
    OUTRA PALAVRA numa mensagem real e uma armadilha esperando quem trocar `casaTermo`
    de volta por `includes`. "identidade", "qualidade" e "prioridade" caem igual, e as
    tres aparecem em mensagens deste app.
    """
    c, _ = cliente
    js = c.get("/painel.js").text
    termos = {t.lower() for bloco in re.findall(r"casaTermo\(e,\s*\[(.*?)\]\)", js,
                                                re.S)
              for t in re.findall(r'"([^"]+)"', bloco)}
    assert termos, "nao achei os termos do casador — o formato mudou, refaca o teste"

    for msg in MENSAGENS_REAIS:
        baixo = msg.lower()
        for termo in termos:
            if termo not in baixo:
                continue
            inteiro = re.search(rf"(?<![a-z0-9]){re.escape(termo)}(?![a-z0-9])", baixo)
            assert inteiro, (
                f"o termo {termo!r} aparece DENTRO de outra palavra em {msg!r}. "
                "Um casador por substring explicaria o erro errado — foi o que "
                "'idade' fez com 'atividade' em 2026-09-09.")


def test_css_e_js_saem_com_o_tipo_certo(cliente):
    # CSS entregue como text/plain e ignorado pelo navegador em silencio
    c, _ = cliente
    assert c.get("/painel.css").headers["content-type"].startswith("text/css")
    assert c.get("/painel.js").headers["content-type"].startswith("text/javascript")


# ---------- saldo lido no cadastro, na revalidacao e sob demanda ----------
#
# Sem isto o pool nasce morto: a conta entra com saldo 0, `escolher` exige
# `custo <= saldo` e nenhum job roda — sem erro, sem alerta, para sempre.

class TransporteSemContador(TransporteFalso):
    """Sessao viva, leitura de saldo quebrada. E o caso que nao pode recusar cadastro."""

    async def saldo(self, s):
        raise ErroTransporte("nao achei o contador de credito no menu do avatar")


def _fingir_cadastro(monkeypatch):
    """Troca o cadastro por Chrome real por um que so devolve perfil e validade.

    `cadastrar` sobe um `chrome.exe` de verdade e espera uma pessoa logar. O que estas
    rotas precisam provar e o que acontece DEPOIS disso.
    """
    async def falso(raiz_perfis, email, proxy=None):
        return Path(raiz_perfis) / email, datetime(2026, 8, 22, tzinfo=timezone.utc)

    monkeypatch.setattr("flowpool.api.rotas.contas.cadastrar", falso)


def _cliente_com(tmp_path, monkeypatch, transporte):
    repo = Repo(tmp_path / "t.db")
    repo.criar_esquema()
    _fingir_cadastro(monkeypatch)
    app = criar_app(repo=repo, transporte=transporte, raiz=tmp_path)
    return TestClient(app), repo


def test_cadastro_devolve_o_saldo_lido_do_flow(tmp_path, monkeypatch):
    # a primeira conta do banco recebe id 1 — e e ela que o transporte falso conhece
    c, repo = _cliente_com(tmp_path, monkeypatch, TransporteFalso(saldos={1: 50}))
    r = c.post("/contas", json={"email": "a@b.com", "tier": "free"})

    assert r.status_code == 201
    assert r.json()["saldo"] == 50
    assert r.json()["saldo_confiavel"] is True
    assert repo.conta(1).saldo == 50


def test_conta_cadastrada_ja_pode_rodar_job(tmp_path, monkeypatch):
    # o impasse visto da porta de entrada: cadastrar e enfileirar tem que bastar
    c, repo = _cliente_com(tmp_path, monkeypatch, TransporteFalso(saldos={1: 50}))
    c.post("/contas", json={"email": "a@b.com", "tier": "free"})

    conta = repo.conta(1)
    assert escolher([conta], Modelo.VEO_FAST, datetime.now(timezone.utc),
                    duracao="8s") is conta


def test_cadastro_sobrevive_a_leitura_de_saldo_quebrada(tmp_path, monkeypatch):
    # a sessao e a parte cara (login manual); o saldo e recuperavel. Recusar o cadastro
    # por causa do saldo jogaria fora o trabalho que so uma pessoa consegue fazer.
    c, repo = _cliente_com(tmp_path, monkeypatch, TransporteSemContador(saldos={1: 50}))
    r = c.post("/contas", json={"email": "a@b.com", "tier": "free"})

    assert r.status_code == 201
    assert r.json()["saldo"] == 0
    assert r.json()["saldo_confiavel"] is False
    assert repo.conta(1) is not None
    assert "saldo_nao_semeado" in [e["tipo"] for e in repo.eventos()]


def test_revalidar_relê_o_saldo(tmp_path, monkeypatch):
    # sessao caida e ~48h sem leitura: o saldo guardado nao vale mais nada
    c, repo = _cliente_com(tmp_path, monkeypatch, TransporteFalso(saldos={1: 42}))
    cid = repo.criar_conta("a@b.com", Tier.FREE, str(tmp_path / "p" / "a@b.com"))
    con = repo.conta(cid); con.saude = SaudeSessao.EXPIRADA; repo.salvar_conta(con)

    r = c.post(f"/contas/{cid}/revalidar")

    assert r.status_code == 200
    assert r.json()["saldo"] == 42
    assert r.json()["saude"] == "viva"
    assert repo.conta(cid).saldo == 42


def test_rota_de_saldo_atualiza_a_conta_sob_demanda(tmp_path, monkeypatch):
    c, repo = _cliente_com(tmp_path, monkeypatch, TransporteFalso(saldos={1: 31}))
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")

    r = c.post(f"/contas/{cid}/saldo")

    assert r.status_code == 200
    assert r.json()["saldo"] == 31
    assert repo.conta(cid).saldo_confiavel is True
    assert "saldo_semeado" in [e["tipo"] for e in repo.eventos()]


def test_rota_de_saldo_em_conta_inexistente_da_404(tmp_path, monkeypatch):
    c, _ = _cliente_com(tmp_path, monkeypatch, TransporteFalso(saldos={}))
    assert c.post("/contas/999/saldo").status_code == 404


def test_rota_de_saldo_que_nao_conseguiu_ler_avisa_em_vez_de_mentir(tmp_path, monkeypatch):
    # 409 e nao 200 com o numero velho: o botao existe justamente para conferir, e
    # devolver o valor antigo como se fosse fresco seria pior do que nao ter botao
    c, repo = _cliente_com(tmp_path, monkeypatch, TransporteSemContador(saldos={1: 50}))
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")

    r = c.post(f"/contas/{cid}/saldo")

    assert r.status_code == 409
    assert "a@b.com" in r.json()["detail"]


# ---------------- seletor de imagens ----------------


@pytest.fixture
def com_imagens(cliente, tmp_path):
    """Arvore de arquivos DENTRO da raiz que a app recebeu (a fixture usa `tmp_path`)."""
    (tmp_path / "Panelas Monaco" / "imagens").mkdir(parents=True)
    (tmp_path / "Panelas Monaco" / "imagens" / "b.png").write_bytes(b"\x89PNG" + b"x" * 20)
    (tmp_path / "Panelas Monaco" / "imagens" / "a.jpg").write_bytes(b"\xff\xd8" + b"y" * 5)
    (tmp_path / "Panelas Monaco" / "notas.md").write_text("nao e imagem")
    (tmp_path / ".git").mkdir()
    return cliente


def test_arquivos_lista_a_raiz_sem_o_ruido(com_imagens):
    c, _ = com_imagens
    r = c.get("/arquivos")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["caminho"] == ""
    assert corpo["pai"] is None
    assert "Panelas Monaco" in [p["nome"] for p in corpo["pastas"]]
    assert ".git" not in [p["nome"] for p in corpo["pastas"]]


def test_arquivos_lista_so_imagem_com_nome_caminho_e_tamanho(com_imagens):
    c, _ = com_imagens
    corpo = c.get("/arquivos", params={"caminho": "Panelas Monaco/imagens"}).json()
    assert [a["nome"] for a in corpo["arquivos"]] == ["a.jpg", "b.png"]
    assert corpo["arquivos"][1]["caminho"] == "Panelas Monaco/imagens/b.png"
    assert corpo["arquivos"][1]["tamanho"] == 24
    assert corpo["pai"] == "Panelas Monaco"


def test_arquivos_recusa_sair_da_raiz(com_imagens):
    # 400 e nao 404: o caminho nao "nao existe", ele nao pode ser pedido
    c, _ = com_imagens
    assert c.get("/arquivos", params={"caminho": "../.."}).status_code == 400
    assert c.get("/arquivos", params={"caminho": "a/../../b"}).status_code == 400
    assert c.get("/arquivos", params={"caminho": "/etc"}).status_code == 400
    assert c.get("/arquivos", params={"caminho": r"C:\Windows"}).status_code == 400


def test_arquivos_recusa_pasta_de_ruido_pedida_na_querystring(com_imagens):
    c, _ = com_imagens
    assert c.get("/arquivos", params={"caminho": ".git"}).status_code == 400


def test_arquivos_de_pasta_inexistente_da_404(com_imagens):
    c, _ = com_imagens
    assert c.get("/arquivos", params={"caminho": "nao existe"}).status_code == 404


def test_arquivos_de_um_arquivo_da_400(com_imagens):
    c, _ = com_imagens
    r = c.get("/arquivos", params={"caminho": "Panelas Monaco/notas.md"})
    assert r.status_code == 400


def test_miniatura_serve_a_imagem_com_cache(com_imagens):
    c, _ = com_imagens
    r = c.get("/miniatura", params={"caminho": "Panelas Monaco/imagens/b.png"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert "max-age" in r.headers["cache-control"]
    assert r.content.startswith(b"\x89PNG")


def test_miniatura_recusa_sair_da_raiz(com_imagens):
    c, _ = com_imagens
    assert c.get("/miniatura", params={"caminho": "../segredo.png"}).status_code == 400


def test_miniatura_recusa_arquivo_que_nao_e_imagem(com_imagens):
    # a checagem de extensao e o que impede esta rota de servir o .db do projeto
    c, _ = com_imagens
    r = c.get("/miniatura", params={"caminho": "Panelas Monaco/notas.md"})
    assert r.status_code == 400


def test_miniatura_de_imagem_inexistente_da_404(com_imagens):
    c, _ = com_imagens
    r = c.get("/miniatura", params={"caminho": "Panelas Monaco/imagens/sumiu.png"})
    assert r.status_code == 404


# ---------------- galeria: midia, download e renomear ----------------


@pytest.fixture
def com_geracoes(cliente, tmp_path):
    """Duas geracoes JA em disco, como o motor as deixa: caminho ABSOLUTO no job."""
    clipes = tmp_path / "Panelas Monaco" / "clipes"
    clipes.mkdir(parents=True)
    (clipes / "c1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 40)
    (clipes / "c2.png").write_bytes(b"\x89PNG" + b"i" * 30)
    (tmp_path / "Panelas Monaco" / "notas.md").write_text("nao e midia")
    c, repo = cliente

    def job(nome, arquivo, modo="t2v"):
        corpo = {"modo": modo, "modelo": "veo-fast" if modo == "t2v" else "nano-banana",
                 "prompt": "uma panela girando", "produto": "Panelas Monaco",
                 "nome": nome}
        jid = c.post("/jobs", json=corpo).json()["id"]
        if arquivo is not None:
            j = repo.job(jid)
            j.arquivo = str(arquivo)
            j.estado = EstadoJob.PRONTO
            j.custo_estimado = 20
            repo.salvar_job(j)
        return jid

    return c, repo, job, clipes


def test_job_traz_o_caminho_relativo_ao_lado_do_absoluto(com_geracoes):
    # o motor grava `Job.arquivo` ABSOLUTO, e `resolver` recusa caminho absoluto de
    # proposito: sem esta conversao no servidor, a galeria nao teria como pedir a midia
    c, _, job, clipes = com_geracoes
    jid = job("c1", clipes / "c1.mp4")
    corpo = c.get(f"/jobs/{jid}").json()
    assert corpo["caminho"] == "Panelas Monaco/clipes/c1.mp4"
    assert corpo["arquivo"].endswith("c1.mp4")
    assert corpo["prompt"] == "uma panela girando"
    assert corpo["custo_estimado"] == 20


def test_job_sem_arquivo_tem_caminho_nulo(com_geracoes):
    c, _, job, _ = com_geracoes
    jid = job("c9", None)
    assert c.get(f"/jobs/{jid}").json()["caminho"] is None


def test_job_com_arquivo_fora_da_raiz_nao_ganha_caminho(com_geracoes, tmp_path):
    # job antigo, gerado com outra raiz: card sem link e melhor do que listagem quebrada
    c, _, job, _ = com_geracoes
    jid = job("c8", tmp_path.parent / "de_outra_raiz.mp4")
    assert c.get(f"/jobs/{jid}").json()["caminho"] is None


def test_midia_serve_video_com_o_tipo_certo(com_geracoes):
    c, _, _, _ = com_geracoes
    r = c.get("/midia", params={"caminho": "Panelas Monaco/clipes/c1.mp4"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert "max-age" in r.headers["cache-control"]


def test_midia_atende_range_para_o_primeiro_quadro(com_geracoes):
    # `<video preload="metadata">` pede os primeiros bytes; sem 206 ele baixaria o
    # clipe inteiro so para desenhar a miniatura da grade
    c, _, _, _ = com_geracoes
    r = c.get("/midia", params={"caminho": "Panelas Monaco/clipes/c1.mp4"},
              headers={"Range": "bytes=0-7"})
    assert r.status_code == 206
    assert r.content == b"\x00\x00\x00\x18"[:4] + b"ftyp"


def test_midia_serve_imagem(com_geracoes):
    c, _, _, _ = com_geracoes
    r = c.get("/midia", params={"caminho": "Panelas Monaco/clipes/c2.png"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_midia_recusa_sair_da_raiz(com_geracoes):
    c, _, _, _ = com_geracoes
    assert c.get("/midia", params={"caminho": "../fora.mp4"}).status_code == 400
    assert c.get("/midia", params={"caminho": "/etc/passwd"}).status_code == 400


def test_baixar_entrega_como_anexo_com_o_nome_original(com_geracoes):
    c, _, _, _ = com_geracoes
    r = c.get("/baixar", params={"caminho": "Panelas Monaco/clipes/c1.mp4"})
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "c1.mp4" in r.headers["content-disposition"]
    assert r.content.startswith(b"\x00\x00\x00\x18ftyp")


def test_baixar_recusa_travessia_de_caminho(com_geracoes, tmp_path):
    # a mesma barreira do seletor (`arquivos.resolver`), e nao um `if` proprio: e a
    # UNICA coisa entre uma querystring e o disco inteiro desta maquina
    (tmp_path.parent / "segredo.png").write_bytes(b"\x89PNG")
    c, _, _, _ = com_geracoes
    for torto in ["../segredo.png", "a/../../segredo.png", "/etc/passwd",
                  r"C:\Windows\win.ini", r"..\..\segredo.png"]:
        assert c.get("/baixar", params={"caminho": torto}).status_code == 400, torto


def test_baixar_recusa_arquivo_que_nao_e_midia(com_geracoes):
    # sem o filtro de extensao esta rota entregaria o .db e o .env do projeto
    c, _, _, _ = com_geracoes
    assert c.get("/baixar",
                 params={"caminho": "Panelas Monaco/notas.md"}).status_code == 400


def test_baixar_de_arquivo_inexistente_da_404(com_geracoes):
    c, _, _, _ = com_geracoes
    r = c.get("/baixar", params={"caminho": "Panelas Monaco/clipes/sumiu.mp4"})
    assert r.status_code == 404


def test_baixar_lote_junta_tudo_num_zip(com_geracoes):
    import io
    import zipfile

    c, _, _, _ = com_geracoes
    r = c.get("/baixar-lote", params={"caminho": ["Panelas Monaco/clipes/c1.mp4",
                                                  "Panelas Monaco/clipes/c2.png"]})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert sorted(z.namelist()) == ["c1.mp4", "c2.png"]
        assert z.read("c2.png").startswith(b"\x89PNG")


def test_baixar_lote_desempata_nomes_repetidos(com_geracoes, tmp_path):
    # dois produtos com um `c1.mp4` cada: sem desempate, um dos dois sumiria na extracao
    import io
    import zipfile

    outra = tmp_path / "Outro Produto" / "clipes"
    outra.mkdir(parents=True)
    (outra / "c1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"z" * 10)
    c, _, _, _ = com_geracoes
    r = c.get("/baixar-lote", params={"caminho": ["Panelas Monaco/clipes/c1.mp4",
                                                  "Outro Produto/clipes/c1.mp4"]})
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert z.namelist() == ["c1.mp4", "c1 (2).mp4"]


def test_baixar_lote_recusa_travessia_em_qualquer_item(com_geracoes):
    # o caminho torto no meio da lista derruba o lote inteiro, e nao sai do zip calado
    c, _, _, _ = com_geracoes
    r = c.get("/baixar-lote", params={"caminho": ["Panelas Monaco/clipes/c1.mp4",
                                                  "../../segredo.png"]})
    assert r.status_code == 400


def test_baixar_lote_sem_nada_selecionado_da_400(com_geracoes):
    c, _, _, _ = com_geracoes
    assert c.get("/baixar-lote").status_code == 400


def test_renomear_job_pendente(com_geracoes):
    c, repo, job, _ = com_geracoes
    jid = job("provisorio", None)
    r = c.patch(f"/jobs/{jid}", json={"nome": "heroi-abertura"})
    assert r.status_code == 200
    assert r.json()["nome"] == "heroi-abertura"
    assert repo.job(jid).nome == "heroi-abertura"


def test_renomear_job_pronto_da_409(com_geracoes):
    # o arquivo ja existe com o nome antigo; trocar o campo faria o job mentir
    c, _, job, clipes = com_geracoes
    jid = job("c1", clipes / "c1.mp4")
    r = c.patch(f"/jobs/{jid}", json={"nome": "outro"})
    assert r.status_code == 409
    assert "disco" in r.json()["detail"]


def test_renomear_job_inexistente_da_404(com_geracoes):
    c, _, _, _ = com_geracoes
    assert c.patch("/jobs/999", json={"nome": "x"}).status_code == 404


def test_renomear_com_nome_em_branco_da_422(com_geracoes):
    # nome vira nome de arquivo: espaco em branco produziria " .mp4" em disco
    c, _, job, _ = com_geracoes
    jid = job("provisorio", None)
    assert c.patch(f"/jobs/{jid}", json={"nome": "   "}).status_code == 422


# --- o painel precisa enxergar a ocupacao do pool -----------------------


def test_saude_mostra_o_pool_ocioso(cliente):
    # "a fila esta parada" e "as janelas estao todas cheias" sao estados diferentes e
    # eram indistinguiveis no painel
    c, repo = cliente
    repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    s = c.get("/saude").json()
    assert s["gerando"] == 0
    assert s["contas_ocupadas"] == 0
    assert s["max_simultaneos"] >= 1


def test_saude_conta_as_contas_ocupadas(cliente):
    c, repo = cliente
    a = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    repo.criar_conta("b@b.com", Tier.FREE, "p/2")
    c.app.state.orq._ocupadas.add(a)

    s = c.get("/saude").json()
    assert s["contas_ocupadas"] == 1
    assert s["contas_vivas"] == 2       # e o "1 de 2" que o painel mostra


def test_reler_saldo_de_conta_ocupada_e_409_sem_falar_em_sessao(cliente):
    # ler saldo abre navegador, e naquele perfil ja ha um. A mensagem NAO pode mandar
    # revalidar: a sessao esta viva, e o login manual seria por nada
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c.app.state.orq._ocupadas.add(cid)

    r = c.post(f"/contas/{cid}/saldo")
    assert r.status_code == 409
    assert "gerando" in r.json()["detail"]
    assert "revalide" not in r.json()["detail"]


def test_revalidar_conta_ocupada_e_409(cliente):
    # revalidar abre um Chrome de LOGIN no mesmo --user-data-dir da geracao em curso:
    # o segundo entrega a URL ao primeiro e morre, derrubando o job junto
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c.app.state.orq._ocupadas.add(cid)

    r = c.post(f"/contas/{cid}/revalidar")
    assert r.status_code == 409


def test_a_lista_de_jobs_diz_em_qual_conta_cada_um_roda(cliente):
    # com varias geracoes ao mesmo tempo, "qual job esta em qual conta" deixa de ser
    # curiosidade e vira a unica forma de ler o painel
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                              "produto": "Moda", "nome": "n"})
    jid = r.json()["id"]
    j = repo.job(jid); j.conta_id = cid; repo.salvar_job(j)

    achado = next(x for x in c.get("/jobs").json() if x["id"] == jid)
    assert achado["conta_id"] == cid


# ---------------------------------------------------------- abrir para conferir

def test_abrir_conta_sobe_o_chrome_no_perfil_dela(cliente, monkeypatch):
    # o botao nasceu de uma necessidade real: tres jobs marcados como falhos com o
    # video GERADO na conta, e nenhuma forma de conferir sem achar a conta na mao
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    con = repo.conta(cid); con.proxy = "socks5h://h:1"; repo.salvar_conta(con)
    chamadas = []
    monkeypatch.setattr("flowpool.api.rotas.contas.abrir_para_conferir",
                        lambda perfil, proxy=None: chamadas.append((str(perfil), proxy)))

    r = c.post(f"/contas/{cid}/abrir")

    assert r.status_code == 202
    assert r.json()["email"] == "a@b.com"
    # o PERFIL da conta e o PROXY dela: conferir por outro IP mostra ao Google
    # exatamente a mudanca que o proxy existe para evitar.
    # Comparado por `Path` e nao por texto: a barra e diferente por sistema, e um teste
    # que so passa no Windows nao serve de rede.
    assert len(chamadas) == 1
    caminho, proxy = chamadas[0]
    assert Path(caminho) == Path("p/1")
    assert proxy == "socks5h://h:1"
    assert "conta_aberta_para_conferir" in [e["tipo"] for e in repo.eventos()]


def test_abrir_conta_ocupada_e_409_e_diz_o_motivo_certo(cliente, monkeypatch):
    # com a conta gerando, o perfil esta nas maos do Playwright: um Chrome comum no
    # mesmo --user-data-dir derruba o job. E a mensagem NAO pode mandar revalidar
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c.app.state.orq._ocupadas.add(cid)
    monkeypatch.setattr("flowpool.api.rotas.contas.abrir_para_conferir",
                        lambda *a, **k: pytest.fail("nao podia ter aberto"))

    r = c.post(f"/contas/{cid}/abrir")

    assert r.status_code == 409
    assert "gerando" in r.json()["detail"]
    assert "revalide" not in r.json()["detail"].lower()


def test_abrir_conta_inexistente_da_404(cliente):
    c, _ = cliente
    assert c.post("/contas/999/abrir").status_code == 404


def test_abrir_conta_sem_chrome_vira_409_em_vez_de_500(cliente, monkeypatch):
    # ChromeAusente e condicao do ambiente, nao defeito do servidor: o painel mostra a
    # mensagem, e um 500 viraria "Erro interno" sem dizer o que fazer
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")

    def sem_chrome(*a, **k):
        raise ChromeAusente("nao achei o chrome.exe")
    monkeypatch.setattr("flowpool.api.rotas.contas.abrir_para_conferir", sem_chrome)

    r = c.post(f"/contas/{cid}/abrir")

    assert r.status_code == 409
    assert "chrome" in r.json()["detail"].lower()


def test_diagnosticar_job_pela_rota(tmp_path):
    from tests.test_diagnostico import ModeloFalso
    repo = Repo(tmp_path / "t.db"); repo.criar_esquema()
    app = criar_app(repo=repo, transporte=TransporteFalso(saldos={}), raiz=tmp_path,
                    diagnosticador=ModeloFalso(resposta="a conta caiu no portao de idade"))
    c = TestClient(app)
    jid = c.post("/jobs", json={"modo": "imagem", "prompt": "x", "produto": "P",
                                "nome": "n"}).json()["id"]
    assert c.post(f"/jobs/{jid}/diagnosticar").status_code == 409      # sem falha ainda
    j = repo.job(jid); j.estado = EstadoJob.FALHOU; j.erro = "age-restricted"; repo.salvar_job(j)
    r = c.post(f"/jobs/{jid}/diagnosticar")
    assert r.status_code == 200
    assert r.json()["tipo"] == "diagnostico"
    assert r.json()["payload"]["texto"] == "a conta caiu no portao de idade"
    assert c.post("/jobs/999/diagnosticar").status_code == 404


def test_diagnosticar_sem_modelo_da_503(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "imagem", "prompt": "x", "produto": "P",
                                "nome": "n"}).json()["id"]
    j = repo.job(jid); j.estado = EstadoJob.FALHOU; j.erro = "x"; repo.salvar_job(j)
    assert c.post(f"/jobs/{jid}/diagnosticar").status_code == 503
