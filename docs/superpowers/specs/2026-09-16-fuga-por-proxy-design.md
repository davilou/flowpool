# Fuga por proxy: a conta recusada por antiabuso sai por outro IP

Data: 2026-09-16. Aprovado pelo Davi na conversa que gerou este documento.

## O problema

Em 2026-09-16 20:45 UTC o motor abriu oito Chromes em 36 s para o produto
"Luisenthal 06". Quatro contas (7, 8, 9 e 2) levaram "Notamos uma atividade
incomum" em sequência, uma a cada 5 s, e cada uma foi descansar uma hora
(`limite_ate`). As sete restantes tinham 2 a 4 créditos, e o vídeo de 8 s custa 12.
Resultado: `sem_conta_apta`, fila parada, com quatro contas de saldo 50 "livres" no
painel.

Nenhuma das 11 contas tem proxy hoje: todas saem pelo IP de casa. A medição de
2026-08-20 registrada em `nucleo/proxy.py` já dizia que o Flow recusa a partir desse
IP e que uma VPN gratuita destrava. O Davi confirmou na mão em 2026-09-16: abriu a
conta recusada, ligou uma extensão de VPN, deu F5 e gerou.

## O que muda

Conta recusada por **antiabuso** (`LimiteDeUso.motivo == "atividade_incomum"`) não
descansa uma hora: entra em **fuga**. O próximo job dela sobe o Chrome pelo **proxy
de fuga** e gera. Se for recusada de novo **em fuga**, aí sim descansa uma hora como
hoje. Recusa por **cota** (`limite_de_uso`) segue como hoje: outro IP não resolve
cota.

## De onde vem o outro IP

Cloudflare WARP em modo proxy (`warp-cli mode proxy`): SOCKS5 local em
`127.0.0.1:40000`, só para o Chrome que for apontado para lá. Medido em 2026-09-16:
IP de casa 177.55.227.80; pelo WARP, 104.28.202.63, 104.28.234.62 e 104.28.202.67
em três reconexões (muda a cada conexão, sempre Cloudflare/GRU). Instalado por
`winget install Cloudflare.Warp`.

A extensão de VPN que o Davi usou não serve ao robô: o Playwright sobe o Chrome com
`--disable-extensions`, e a extensão exigiria clicar em "conectar" num popup.

## Peças

1. **Configuração**: `FLOWPOOL_PROXY_FUGA` com a URL (`http`, `https`, `socks5`,
   `socks5h`). Lida por `orquestrador.proxy_de_fuga_do_ambiente()`, validada por
   `nucleo.proxy.analisar` na subida: URL inválida derruba o serviço com
   `ProxyInvalido`, e não a primeira recusa. Vazia = comportamento de hoje.
2. **Constantes de motivo** em `LimiteDeUso`: `MOTIVO_ANTIABUSO = "atividade_incomum"`
   e `MOTIVO_COTA = "limite_de_uso"`. O transporte passa a usá-las em vez do literal;
   o motor compara contra elas.
3. **Motor** (`Orquestrador`):
   - `proxy_fuga: str | None` no construtor; `_em_fuga: set[int]` em memória.
   - `_ao_bater_limite`: se motivo é antiabuso, há proxy de fuga e a conta não está
     em fuga → entra em `_em_fuga`, evento `conta_em_fuga` (payload com o proxy
     mascarado), **sem** `limite_ate`. Estorno, soltura de `id_remoto`/`conta_id` e
     devolução do job continuam iguais. Qualquer outro caso: caminho de hoje.
   - `_obter_sessao`: conta em fuga abre a sessão com `replace(conta,
     proxy=proxy_fuga)`. Sessão guardada só é reusada se `sessao.conta.proxy` for o
     proxy pelo qual a conta deve sair agora; senão fecha e abre outra. É o que
     garante que a tentativa de fuga não herda o Chrome que saiu pelo IP de casa.
   - A fronteira `FlowTransport` não muda; `transporte.py` não sabe que fuga existe.
4. **Duração**: a fuga dura enquanto o processo viver. Reiniciou, a conta volta ao
   IP de casa; se for recusada, entra em fuga de novo ao custo de uma recusa sem
   cobrança. Sem coluna nova no banco.
5. **Bug de passagem, na mesma família**: `_destinar` adia o job uma hora fixa quando
   nenhuma conta serve, enquanto `_quando_reavaliar` já calcula o instante em que a
   primeira conta volta. O job 1654 foi para 22:11 com contas voltando às 21:45.
   Passa a usar `_quando_reavaliar`.

## Testes (transporte falso)

- Recusa por antiabuso com proxy de fuga: conta sem `limite_ate`, evento
  `conta_em_fuga`, e a sessão seguinte da conta é aberta com `proxy == proxy de fuga`.
- Segunda recusa em fuga: `limite_ate` no futuro, como hoje.
- Recusa por cota com proxy de fuga: `limite_ate`, sem fuga.
- Sem `FLOWPOOL_PROXY_FUGA`: nada muda.
- `proxy_de_fuga_do_ambiente`: vazia → None; inválida → `ProxyInvalido`.
- `_destinar` sem conta apta: `reavaliar_em` é o menor `limite_ate` futuro.

## Riscos assumidos

- Todas as contas em fuga saem pela mesma faixa de IP. Lista de proxies só depois
  de medido.
- A sessão do Google pode pedir login ao trocar de IP. O teste manual diz que não,
  mas foi uma conta e uma vez.
- O experimento manual continua confundido com "abrir na mão": este recurso é
  também a medição limpa (robô, mesma cadência, só o IP muda).

## Fora do escopo

Mostrar `limite_ate` e "em fuga" na linha da conta do painel. Rotação do WARP
(`warp-cli disconnect/connect`) entre fugas.
