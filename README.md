# FlowPool

Serviço local que gera **vídeo e imagem no Google Flow** dirigindo um **pool de contas
Google** pelo navegador (Playwright). Você manda o pedido; ele escolhe a conta com
crédito, gera, baixa e guarda o arquivo na pasta do produto.

- **Painel web** para operar: fila, contas, saídas, histórico, diagnóstico de falhas.
- **API HTTP** para scripts e agentes — o painel é só mais um cliente dela.
- **Escolha de conta automática:** contas free primeiro (o crédito delas expira no dia),
  pagas como reserva; conta sem saldo, no limite ou com sessão caída sai da escolha.
- **Auto-cura de seletor:** quando o Google muda a interface, um modelo de visão local
  tenta achar o botão de novo — nunca em ação destrutiva.

## Requisitos

- Windows, com **Google Chrome** instalado (o Chromium do Playwright é recusado pelo
  login do Google).
- Python **3.13+**.
- Uma ou mais contas Google com acesso ao Flow.

## Instalar

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

## Subir

```bash
.venv/Scripts/python.exe -m flowpool
```

Abra **http://127.0.0.1:8900/**. A mesma porta serve a API; `FLOWPOOL_PORTA` muda.
Subir o serviço já liga o motor: jobs pendentes saem assim que houver conta com crédito.

Sem login e escutando só em `127.0.0.1`: é serviço de um usuário, numa máquina.

## Primeiro uso

1. Na página **Contas**, cadastre uma conta — o Chrome abre e você faz o login uma vez.
   **Marque o tier certo** (free, pro, ultra): ele decide a ordem de uso.
2. Clique em **Nova geração** (ou tecle `n`), escreva o prompt, escolha o tipo e envie.
3. O arquivo aparece em **Saídas** e em disco, em `<produto>/clipes/`.

## Documentação

| arquivo | para quê |
|---|---|
| [`flowpool/LEIA-ME.md`](flowpool/LEIA-ME.md) | manual de operação: painel, cadastro, proxy, auto-cura, variáveis de ambiente |
| [`docs/API.md`](docs/API.md) | guia da API com exemplos: criar job, lote, acompanhar, baixar |
| [`CLAUDE.md`](CLAUDE.md) | para quem mexe no código: checks, camadas, o que quebra em silêncio |
| [`docs/superpowers/specs/`](docs/superpowers/specs/) | o desenho original |

## Testes

```bash
.venv/Scripts/python.exe -m pytest -q
```

A suíte não abre navegador, não gasta crédito e não fala com o Google.

## O que nunca vai para o git

Os perfis do Chrome com as sessões logadas (`flowpool/perfis/`) e o banco
(`flowpool/flowpool.db`) ficam só na máquina — estão no `.gitignore`.
