from datetime import datetime, timezone

from flowpool.nucleo.tipos import (
    PROPORCOES, Conta, EstadoJob, Job, Modelo, Modo, Projeto, SaudeSessao, Tier,
    quantidade_maxima,
)


def test_conta_nasce_viva_e_ativa():
    c = Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil="perfis/1")
    assert c.saude is SaudeSessao.VIVA
    assert c.ativa is True
    assert c.saldo == 0
    assert c.falhas == 0


def test_job_nasce_pendente_sem_tentativas():
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="uma gata", destino="x", nome="c1")
    assert j.estado is EstadoJob.PENDENTE
    assert j.tentativas == 0
    assert j.referencias == []
    assert j.conta_id is None


def test_job_nasce_sem_custo_estimado():
    # sem registrar o reservado nao ha como estornar um job que morreu antes de gerar
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="x", nome="c1")
    assert j.custo_estimado is None


def test_enums_serializam_como_texto():
    assert Tier.ULTRA.value == "ultra"
    assert Modo.INGREDIENTS2V.value == "ingredients2v"
    assert Modelo.VEO_QUALITY.value == "veo-quality"
    assert EstadoJob.PRONTO.value == "pronto"
    assert SaudeSessao.EXPIRADA.value == "expirada"


def test_reavaliar_em_aceita_datetime():
    j = Job(id=1, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA, prompt="p", destino="x", nome="i1")
    assert j.reavaliar_em is None
    j.reavaliar_em = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)
    assert j.reavaliar_em.year == 2026


def test_conta_nasce_sem_prazo_de_seca():
    # `seca_ate` e o que tira do pool a conta que o Flow recusou apesar do saldo
    c = Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil="perfis/1")
    assert c.seca_ate is None
    c.seca_ate = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
    assert c.seca_ate.hour == 13


def test_projeto_liga_uma_conta_a_um_produto():
    # projeto pertence a UMA conta: a chave e o par (conta, produto), nunca o produto
    # sozinho — a conta g2 nao enxerga nem abre projeto da g1
    p = Projeto(id=1, conta_id=2, produto="Panelas Monaco",
                id_remoto="a1b2c3", nome_remoto="Panelas Monaco")
    assert p.conta_id == 2
    assert p.produto == "Panelas Monaco"
    assert p.id_remoto == "a1b2c3"
    assert p.criado_em is None


def test_job_nasce_sem_produto():
    # o default existe so para nao quebrar a ordem dos campos da dataclass;
    # na pratica a API exige `produto`
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="x", nome="c1")
    assert j.produto == ""
    outro = Job(id=2, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="x",
                nome="c1", produto="Panelas Monaco")
    assert outro.produto == "Panelas Monaco"


def test_omni_flash_e_o_modelo_de_video_padrao_do_flow():
    # medido em campo: o dropdown de video do Flow abre em "Omni Flash", nao em Veo
    assert Modelo.OMNI_FLASH.value == "omni-flash"


def test_job_nasce_vertical():
    # o canal e TikTok: gerar ja em 9:16 evita o recorte manual depois
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p", destino="x", nome="c1")
    assert j.proporcao == "9:16"


def test_proporcao_explicita_sobrescreve_o_padrao():
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p", destino="x",
            nome="c1", proporcao="16:9")
    assert j.proporcao == "16:9"


def test_proporcoes_aceitas_sao_as_cinco_do_painel():
    assert PROPORCOES == ("16:9", "4:3", "1:1", "3:4", "9:16")


def test_job_nasce_com_uma_geracao_so():
    # quantidade multiplica o custo; o default nunca pode gastar mais do que o pedido
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p", destino="x", nome="c1")
    assert j.quantidade == 1


def test_imagem_aceita_lote_e_video_nao():
    # take de video e escolha do usuario, nao sorteio; variacao de imagem e barata
    assert quantidade_maxima(Modo.IMAGEM) == 4
    for modo in (Modo.T2V, Modo.FRAMES2V, Modo.INGREDIENTS2V):
        assert quantidade_maxima(modo) == 1


def test_conta_nasce_sem_proxy():
    # o campo e desenhado agora e fica vazio: enquanto nao ha IP contratado, o Davi
    # destrava com uma VPN de sistema, que o app nem enxerga
    c = Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil="perfis/1")
    assert c.proxy is None
    c.proxy = "http://1.2.3.4:8080"
    assert c.proxy == "http://1.2.3.4:8080"
