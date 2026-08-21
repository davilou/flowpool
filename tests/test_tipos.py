from datetime import datetime, timezone

from flowpool.nucleo.tipos import (
    DURACAO_PADRAO, DURACOES, PROPORCOES, Conta, EstadoJob, Job, Modelo, Modo, Projeto,
    SaudeSessao, Tier, aceita_referencia, duracao_valida, exige_arquivo,
    max_referencias, modelo_valido, quantidade_maxima,
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


def test_job_nasce_com_clipe_de_quatro_segundos():
    # 4s custa 7 creditos e 8s custa 12: fixar 8s desperdicava 70% do credito por
    # video. Numa conta gratuita de 50/dia isso e a diferenca entre 4 e 7 videos.
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p", destino="x", nome="c1")
    assert j.duracao == "4s"


def test_duracao_explicita_sobrescreve_o_padrao():
    j = Job(id=1, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p", destino="x",
            nome="c1", duracao="10s")
    assert j.duracao == "10s"


def test_duracoes_aceitas_sao_as_quatro_do_painel():
    assert DURACOES == ("4s", "6s", "8s", "10s")
    assert DURACAO_PADRAO == "4s"


def test_imagem_nao_aceita_duracao():
    # nao existe grupo de duracao no painel em modo imagem: pedir "4s" ali nao teria
    # onde ser clicado. `None` e o unico valor coerente.
    assert duracao_valida(Modo.IMAGEM, None) is True
    for d in DURACOES:
        assert duracao_valida(Modo.IMAGEM, d) is False


def test_video_aceita_so_as_quatro_duracoes():
    # frames2v ficou de fora: ver o teste logo abaixo
    for modo in (Modo.T2V, Modo.INGREDIENTS2V):
        for d in DURACOES:
            assert duracao_valida(modo, d) is True
        assert duracao_valida(modo, "5s") is False
        assert duracao_valida(modo, None) is False


def test_frames2v_nao_tem_duracao_apesar_de_ser_video():
    # MEDIDO 2026-08-21 por print do painel aberto em Frames: ele tem `Imagem|Video`,
    # `Frames|Elementos`, proporcao, dropdown de modelo e quantidade — e NENHUM grupo
    # de duracao. O chip confirma, escrevendo "Video . 720p . x1" onde os outros modos
    # de video escrevem "Video . 720p . 4s . 9:16 x1".
    #
    # Enquanto isto valia como os outros videos, `_configurar` procurava o botao "4s"
    # num painel que nao o tem, a auto-cura era acionada, e o first/last frame do
    # usuario nunca passava da configuracao.
    assert duracao_valida(Modo.FRAMES2V, None) is True
    for d in DURACOES:
        assert duracao_valida(Modo.FRAMES2V, d) is False


def test_frames2v_so_aceita_veo_lite():
    # RELATADO pelo usuario depois de tentar: "para fazer esse video de first last
    # frame, a gente so pode fazer no Veo Lite. A gente nao consegue fazer no Omni
    # Flash." O painel DEIXA escolher outro — o print de 2026-08-21 mostra "Veo 3.1 -
    # Fast" selecionado em Frames, anunciando 20 creditos — e e por isso que a recusa
    # precisa vir do dominio: quem so olha a tela nao ve impedimento, ve o dobro do
    # custo e um video que ignora o frame final.
    assert modelo_valido(Modo.FRAMES2V, Modelo.VEO_LITE) is True
    for m in (Modelo.OMNI_FLASH, Modelo.VEO_FAST, Modelo.VEO_QUALITY,
              Modelo.NANO_BANANA):
        assert modelo_valido(Modo.FRAMES2V, m) is False


def test_os_outros_modos_de_video_aceitam_os_quatro_modelos():
    # a restricao e do frames2v; apertar os outros junto tiraria escolha sem motivo
    for modo in (Modo.T2V, Modo.INGREDIENTS2V):
        for m in (Modelo.OMNI_FLASH, Modelo.VEO_LITE, Modelo.VEO_FAST,
                  Modelo.VEO_QUALITY):
            assert modelo_valido(modo, m) is True
        assert modelo_valido(modo, Modelo.NANO_BANANA) is False


# --- referencia por modo: imagem aceita, e sempre aceitou ---------------


def test_imagem_aceita_referencia():
    # MEDIDO: o Flow gera imagem A PARTIR de uma foto (Nano Banana). Enquanto isto
    # valia zero, a referencia anexada era descartada sem aviso — e e a PRIMEIRA etapa
    # do pipeline do usuario: foto da roupa -> imagem da modelo vestida.
    assert max_referencias(Modo.IMAGEM) == 3
    assert aceita_referencia(Modo.IMAGEM) is True


def test_ingredients2v_continua_com_o_mesmo_teto():
    assert max_referencias(Modo.INGREDIENTS2V) == 3


def test_video_por_texto_e_por_frames_nao_aceitam_referencia():
    # la o arquivo tem outro papel (ou papel nenhum): aceitar seria o mesmo descarte
    # silencioso pela outra porta
    for modo in (Modo.T2V, Modo.FRAMES2V):
        assert max_referencias(modo) == 0
        assert aceita_referencia(modo) is False


def test_aceitar_arquivo_nao_e_o_mesmo_que_exigir():
    # imagem sem referencia e o caso comum (texto puro); confundir as duas coisas
    # transformaria toda geracao de imagem em erro
    assert exige_arquivo(Modo.IMAGEM) is False
    assert exige_arquivo(Modo.T2V) is False
    assert exige_arquivo(Modo.INGREDIENTS2V) is True
    assert exige_arquivo(Modo.FRAMES2V) is True


def test_referencia_em_imagem_nao_mexe_no_teto_de_quantidade():
    # imagem aceita x1..x4 e isso continua valendo com referencia: sao duas regras
    # independentes, e amarrar uma na outra tiraria as variacoes justamente do caso em
    # que elas mais servem
    assert quantidade_maxima(Modo.IMAGEM) == 4
    assert max_referencias(Modo.IMAGEM) == 3
