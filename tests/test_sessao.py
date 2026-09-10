from datetime import datetime, timezone

import pytest

from flowpool.nucleo.erros import ProxyInvalido
from flowpool.transporte.base import SessaoMorta
from flowpool.transporte.pw.sessao import (
    ChromeAusente,
    argumentos_chrome,
    argumentos_playwright,
    caminho_perfil,
    checar_chrome,
    interpretar_sessao,
    perfil_em_uso,
)

TOKEN = "ya29.a0AfB_byQFAKE_TOKEN_DE_TESTE"


def test_caminho_de_perfil_e_derivado_do_email(tmp_path):
    p = caminho_perfil(tmp_path, "davi.teste+3@gmail.com")
    assert p.parent == tmp_path
    assert "@" not in p.name and "+" not in p.name
    assert p.name.startswith("davi")


def test_emails_diferentes_dao_perfis_diferentes(tmp_path):
    assert caminho_perfil(tmp_path, "a@gmail.com") != caminho_perfil(tmp_path, "b@gmail.com")


def test_mesmo_email_da_sempre_o_mesmo_perfil(tmp_path):
    assert caminho_perfil(tmp_path, "a@b.com") == caminho_perfil(tmp_path, "a@b.com")


def test_checar_chrome_levanta_quando_nao_acha(monkeypatch):
    monkeypatch.setattr("flowpool.transporte.pw.sessao._CANDIDATOS", [])
    with pytest.raises(ChromeAusente, match="Chrome"):
        checar_chrome()


def test_argumentos_do_chrome_levam_perfil_e_url(tmp_path):
    args = argumentos_chrome(tmp_path / "chrome.exe", tmp_path / "perfil", "https://x/y")
    assert args[0].endswith("chrome.exe")
    assert any(a.startswith("--user-data-dir=") and "perfil" in a for a in args)
    assert args[-1] == "https://x/y"


def test_argumentos_do_chrome_nao_pedem_porta_de_depuracao(tmp_path):
    # --remote-debugging-port e o que faz o Google recusar o login. Uma flag dessas
    # entrando aqui por engano reproduziria exatamente o bug que este task existe para
    # evitar, e o sintoma seria "o Google nao deixa logar", nao "flag errada".
    args = argumentos_chrome(tmp_path / "chrome.exe", tmp_path / "perfil", "https://x")
    assert not any("remote-debugging" in a or "--headless" in a for a in args)


def test_o_chrome_de_trabalho_esconde_o_carimbo_de_automacao():
    # MEDIDO 2026-09-10: sem esta flag `navigator.webdriver` responde true, e foi o
    # UNICO dos 19 sinais medidos que separava o Chrome do Playwright do Chrome que o
    # botao Abrir sobe — o mesmo par onde o video gerava a mao e era recusado por
    # "atividade incomum" pelo robo. Perder a flag reabre aquilo, e o sintoma
    # apareceria dias depois, longe da causa.
    assert "--disable-blink-features=AutomationControlled" in argumentos_playwright()


def test_o_chrome_de_trabalho_nao_pede_porta_de_depuracao_nem_headless():
    # irmao do teste do Chrome de login: `--remote-debugging-port` faz o Google recusar,
    # e `--headless` mata o IntersectionObserver que a pagina do Flow usa.
    args = argumentos_playwright()
    assert not any("remote-debugging" in a or "--headless" in a for a in args)


def test_sem_proxy_a_linha_de_comando_nao_ganha_flag_nenhuma(tmp_path):
    # o caso comum hoje: campo vazio, e o Chrome sai pelo IP da maquina (ou pela VPN
    # de sistema, que ele nem enxerga)
    args = argumentos_chrome(tmp_path / "chrome.exe", tmp_path / "perfil", "https://x")
    # `"--proxy" in a` e nao `"proxy" in a`: o tmp_path do pytest e batizado com o nome
    # do teste, e o caminho do perfil carrega a palavra "proxy" dentro do
    # `--user-data-dir=`. A busca solta reprovaria a montagem correta.
    assert not any("--proxy" in a for a in args)


def test_o_chrome_de_login_sai_pelo_proxy_da_conta(tmp_path):
    # o login tem que sair do MESMO IP do trabalho: conta que nasce no IP de casa e
    # depois opera por proxy mostra ao Google a mudanca que o proxy evita
    args = argumentos_chrome(tmp_path / "chrome.exe", tmp_path / "perfil", "https://x",
                             proxy="http://1.2.3.4:8080")
    assert "--proxy-server=http://1.2.3.4:8080" in args
    assert args[-1] == "https://x"      # a URL continua sendo o ultimo argumento


def test_o_chrome_de_login_recusa_proxy_com_credencial(tmp_path):
    # o Chrome cru abre um dialogo de usuario e senha, e o `proc.wait()` fica
    # pendurado ate o teto de 15 minutos sem erro nenhum. Recusar antes do Popen e o
    # que transforma isso numa mensagem em vez de numa janela parada.
    with pytest.raises(ProxyInvalido, match="retransmissor"):
        argumentos_chrome(tmp_path / "chrome.exe", tmp_path / "perfil", "https://x",
                          proxy="http://davi:segredo@1.2.3.4:8080")


def test_perfil_livre_quando_nenhum_chrome_usa_a_pasta(tmp_path, monkeypatch):
    monkeypatch.setattr("flowpool.transporte.pw.sessao._linhas_de_comando",
                        lambda: ['chrome.exe --user-data-dir="C:\\outro"'])
    assert perfil_em_uso(tmp_path / "perfil") is False


def test_perfil_ocupado_quando_um_chrome_usa_a_pasta(tmp_path, monkeypatch):
    alvo = (tmp_path / "perfil").resolve()
    monkeypatch.setattr("flowpool.transporte.pw.sessao._linhas_de_comando",
                        lambda: [f'chrome.exe --user-data-dir={alvo}'])
    assert perfil_em_uso(tmp_path / "perfil") is True


def test_sessao_valida_devolve_a_expiracao():
    quando = interpretar_sessao(
        {"access_token": TOKEN, "expires": "2026-08-22T15:04:05.000Z"})
    assert quando == datetime(2026, 8, 22, 15, 4, 5, tzinfo=timezone.utc)


def test_sessao_morta_quando_falta_access_token():
    with pytest.raises(SessaoMorta):
        interpretar_sessao({"user": {"email": "a@b.com"}})


def test_sessao_morta_quando_a_resposta_vem_vazia():
    # deslogado, o Flow devolve `{}` com 200 — nao um erro HTTP. Tratar so o status
    # deixaria uma sessao morta passar por viva ate a primeira geracao falhar.
    with pytest.raises(SessaoMorta):
        interpretar_sessao({})


def test_interpretar_sessao_nao_devolve_o_token():
    # o valor de retorno vai para log e para evento. Se o token puder sair por aqui,
    # a regra 2 nao adianta nada: o vazamento so muda de porta.
    quando = interpretar_sessao({"access_token": TOKEN, "expires": "2026-08-22T15:04:05Z"})
    assert TOKEN not in repr(quando)
