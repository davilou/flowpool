"""Duas contas saindo pelo mesmo IP — a checagem que o proxy existia para permitir.

Proxy morto falha alto: o Chrome nao conecta. Proxy VIVO e repetido entre duas contas
passa em silencio, e e exatamente o defeito que o proxy existe para evitar. Estas
funcoes sao a parte pura disso: recebem o mapa de IPs lidos e dizem quem colide.
"""

from flowpool.nucleo.proxy import agrupar_por_ip, colisoes


def test_mapa_vazio_nao_colide():
    assert colisoes({}) == {}


def test_contas_em_ips_distintos_nao_colidem():
    assert colisoes({1: "203.0.113.7", 2: "198.51.100.4"}) == {}


def test_duas_contas_no_mesmo_ip_sao_colisao():
    assert colisoes({1: "203.0.113.7", 2: "203.0.113.7"}) == {"203.0.113.7": [1, 2]}


def test_tres_contas_no_mesmo_ip_saem_todas_na_lista():
    """O aviso tem que nomear as tres: saber que "ha colisao" nao diz o que desligar."""
    assert colisoes({4: "203.0.113.7", 1: "203.0.113.7", 9: "203.0.113.7"}) == {
        "203.0.113.7": [1, 4, 9]}


def test_ip_nunca_conferido_e_ignorado():
    """AUSENCIA DE LEITURA NAO E EVIDENCIA DE COLISAO. Tratar `None` como um valor faria
    todas as contas nunca conferidas aparecerem como se saissem pelo mesmo lugar — o
    aviso nasceria gritando sobre o que ninguem mediu."""
    assert colisoes({1: None, 2: None, 3: "203.0.113.7"}) == {}


def test_uma_conta_sozinha_no_ip_nao_e_colisao():
    assert colisoes({1: "203.0.113.7", 2: "198.51.100.4", 3: "198.51.100.4"}) == {
        "198.51.100.4": [2, 3]}


def test_agrupar_mostra_tambem_quem_esta_sozinho():
    """`agrupar_por_ip` e a visao inteira; `colisoes` e o recorte que pede acao."""
    assert agrupar_por_ip({1: "203.0.113.7", 2: "198.51.100.4", 3: "203.0.113.7"}) == {
        "203.0.113.7": [1, 3], "198.51.100.4": [2]}


def test_agrupar_tambem_ignora_quem_nunca_foi_conferido():
    assert agrupar_por_ip({1: None, 2: "203.0.113.7"}) == {"203.0.113.7": [2]}
