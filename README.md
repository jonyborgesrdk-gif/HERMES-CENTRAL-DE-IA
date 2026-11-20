# Overview-Painel

Um painel local para acompanhar, numa tela só, vários agentes de IA rodando em terminais (Claude Code, Codex e
Antigravity): o que cada um está fazendo, quanto resta de cota, quais PRs estão abertos e o que depende de você.

Nenhuma IA participa do painel. Ele só **lê** as telas dos terminais (pelo `tmux`) e **grava** um arquivo que uma
página estática mostra. Nada escuta em porta pública e nada sai da sua máquina.

## O que você precisa

| Item | Para quê | Obrigatório? |
| --- | --- | --- |
| **Python 3.9 ou mais novo** | roda o coletor (só biblioteca padrão; nada de `pip install`) | sim |
| **tmux** | é por onde o painel lê os terminais dos agentes | sim, para uso real |
| Um navegador | abre http://127.0.0.1:7421/ (ou `publico/classico.html` como arquivo) | sim |
| `gh` (GitHub CLI) já logado | lista PRs abertos e o estado da CI | não (use `--sem-gh`) |
| `claude`, `codex`, `agy` | cotas lidas direto da CLI de cada ferramenta | não |
| Node.js | só para rodar os testes do navegador | não (o painel **não** usa Node) |

## Instalação em 4 passos

```bash
# 1. baixe
git clone https://github.com/cauecarneiro/Overview-Painel.git
cd Overview-Painel

# 2. veja funcionando com telas de exemplo (não precisa de tmux nem de agente nenhum)
./coletor --telas telas-de-exemplo --uma-vez --sem-gh
open publico/classico.html          # no Linux: xdg-open publico/classico.html (a página nova precisa do servidor, veja abaixo)

# 3. descreva o seu time
cp agentes.exemplo.json agentes.json
#    edite agentes.json: nome, ferramenta, conta, pasta e terminal de cada agente

# 4. suba o painel de verdade
./painel.sh                        # ou ./painel.sh --sem-gh para não consultar o GitHub
```

Deixe o `./painel.sh` rodando e abra `publico/classico.html` no navegador (modo `file://`). A página principal nova usa `fetch` e `EventSource` e só funciona por http: suba `servidor.py --com-coletor` e abra **http://127.0.0.1:7421/**. Aberta por `file://`, ela avisa isso na tela. A página atualiza sozinha. `Ctrl-C` para parar.

## Windows

O interpretador oficial no Windows é `py -3.14` (Python 3.14), com
`PYTHONUTF8=1`. No PowerShell:

```powershell
$env:PYTHONUTF8 = '1'
py -3.14 coletor --telas telas-de-exemplo --uma-vez --sem-gh
.\painel.cmd --sem-gh
py -3.14 -B -m unittest discover -s testes
```

Use também `py -3.14 refresh-completo`, `py -3.14 painel-missao` e
`py -3.14 painel-pendencia`. O `painel.cmd` define `PYTHONUTF8=1`, usa
`py -3.14` quando esse interpretador passa na verificação (senão usa `python`) e
chama `servidor.py --com-coletor`. Abra **http://127.0.0.1:7421/**.
O servidor impede duas instâncias por trava de arquivo, reinicia o coletor após
3 s se ele cair e encerra o filho ao sair. Python 3.15 não é alvo desta etapa.
"Ao vivo" indica uma conexão SSE recebendo atualizações sem recarregar a página.
Se a conexão cair, aparece "desconectado" e a leitura periódica assume até reconectar.
Em `file://` ou sem EventSource, a página clássica (`publico/classico.html`) continua lendo `dados.js` a cada 5 s.
O modo vivo também mostra Serviços, Processos e Fontes, com tema claro/escuro.
Use `py -3.14 servidor.py --com-coletor --sem-gh` para iniciar diretamente;
`--porta` ou `PAINEL_PORTA` muda a porta (padrão 7421). O refresh legado fica na 7420.
Para descobrir sessões locais automaticamente, sem precisar de `agentes.json`:

```powershell
$env:PYTHONUTF8 = '1'
py -3.14 coletor --vivo --sem-gh
# Uma passada para conferir:
py -3.14 coletor --vivo --uma-vez --sem-gh
```

O modo vivo usa hooks Claude, sessões Codex e Wire do Maestri; hooks e configuração
Wire precisam ser preparados separadamente. `agentes.json`, quando existir, só
fornece apelidos/overrides por pasta ou nome. A sonda que digita em terminais fica
sempre desligada. O intervalo padrão é 2 s; serviços são consultados a cada 10 s,
processos a cada 5 s e cota Claude a cada 5 min. `--sem-contas` usa apenas o cache
da cota Claude. `--vivo` não pode ser combinado com `--telas`.
O gatilho HTTP `/refresh` do modo antigo não é iniciado no modo vivo.
Sem `--vivo`, o comportamento antigo via tmux ou telas de exemplo é preservado.

No modo vivo o painel não publica a pasta de trabalho nem texto das sessões:
cada sessão do Claude e do Codex aparece como `Claude · xxxx` ou `Codex · xxxx`
(os quatro últimos caracteres do identificador da sessão), e cada terminal do
Maestri pelo nome do workspace e do terminal. Para dar um apelido a quem trabalha
numa pasta, crie `agentes.json` com `pasta` e `nome`: a pasta serve só para casar
com a sessão e não é publicada.

No modo vivo, os cartões de cota usam as fontes Claude/Codex: 5 h e semanal são
porcentagens **usadas**, sem estimativas quando falta leitura. Plano/e-mail do
Claude vêm de `claude auth status --json`, no mesmo cache de 5 min do `/usage`;
a privacidade do e-mail continua sendo a tarja já existente na página.
Sessões Codex com identidade de conta disponível são agrupadas; sem identidade,
há um cartão "Codex" com a leitura mais recente disponível. `--sem-contas`
impede novas consultas Claude, mas ainda permite mostrar seu cache e as cotas
passivas Codex. Reset textual permanece textual, sem inventar um horário para
o cronômetro. Maestri mantém workspace/terminal como título e o tipo no subtítulo.
O servidor recusa outros sites nos endpoints de dados; `curl` sem `Referer`
continua permitido no host local. O indicador de fallback volta a mostrar a
leitura periódica após uma carga bem-sucedida.

## Como o painel acha os seus agentes

Cada agente do `agentes.json` aponta para um terminal do tmux pelo campo `"terminal"`:

```json
{ "nome": "Agente 02", "terminal": "agente-02", "ferramenta": "claude", "modelo": "Sonnet 5.5",
  "conta": "Claude Max 5x", "grupo_cota": "Claude Max 5x", "pasta": "~/projetos/meu-app" }
```

O valor de `"terminal"` pode ser o **nome de uma sessão** (`agente-02`), um alvo completo
(`sessao:janela.painel`) ou o **id do painel** (`%3`). Sem o campo, vale o próprio `nome` do agente. Para criar uma
sessão por agente:

```bash
tmux new-session -d -s agente-02 -c ~/projetos/meu-app    # depois, entre nela e rode claude, codex ou agy
```

As contas (e-mail, plano, cotas) são descobertas pelos processos que rodam **dentro** desses terminais do tmux.
Terminal que não existe ou tela que não dá para ler aparece como "sem leitura", com o motivo. O painel nunca
inventa número.

## Variáveis de ambiente

| Variável | Para quê |
| --- | --- |
| `PAINEL_GH_ORG` | Organização do GitHub usada para repositórios listados sem `dono/` no `agentes.json`. |
| `PAINEL_QUADRO` | Caminho do `quadro.json` (missão, situação, previsão e pendências). |
| `PAINEL_PRESENCA` | `1` liga a presença por processo (opt-in, desligada por padrão): produtos de IA com processo principal vivo aparecem como "online" na seção Agentes. Qualquer outro valor mantém a saída antiga. |
| `PAINEL_PRESENCA_LINUX` | `1` liga a presença no WSL (opt-in, desligada por padrão): produtos de IA com processo vivo na distro `Ubuntu-26.04` aparecem num bloco próprio da seção Agentes. A distro só é consultada quando já está ligada; o painel nunca a liga. Qualquer outro valor mantém a saída antiga. |
| `PAINEL_COTA_CODEX_LOCAL` | `1` liga a leitura local da cota do Codex (opt-in, desligada por padrão): o último uso observado nos arquivos de sessão de `~/.codex/sessions` aparece no bloco "Observado em arquivos locais" da seção Limites de uso, com o horário da observação. Sem rede. Qualquer outro valor mantém a saída antiga. Na página, o Codex aparece num cartão só: o da conta quando ela tem números (sessão aberta) e o observado localmente quando não tem. |
| `PAINEL_COTA_GROK_BOT` | `1` liga a leitura local do uso do Grok Bot (opt-in, desligada por padrão): percentual e próximo reset do último registro local do aplicativo, com o horário da leitura. Sem rede. Qualquer outro valor mantém a saída antiga. |
| `PAINEL_COTA_CURSOR` | `1` liga a leitura local do plano do Cursor (opt-in, desligada por padrão): mostra o plano e a situação da assinatura guardados em cache pelo aplicativo e avisa que não há dados de cota. Sem rede. Qualquer outro valor mantém a saída antiga. O banco do Cursor é lido sem trava e sem gravar nada, com o aplicativo aberto ou fechado; com ele aberto, uma mudança de plano pode demorar a aparecer. |
| `PAINEL_VIGILANTE` | `1` liga o bloco do Vigilante 2 (opt-in, desligado por padrão): o painel lê só `%LOCALAPPDATA%\vigilante2\ultimo.json` e mostra, na seção Saúde dos serviços, contagens, alertas ativos por tipo e se o Vigilante 2 está parado (sem atualização há mais de 3 min). Qualquer outro valor mantém a saída antiga. |
| `PAINEL_ERROS` | `1` liga a captura de erros de ferramenta dos agentes (opt-in, desligada por padrão): o painel lê só os transcripts do Claude Code (`~/.claude/projects`) e as sessões do Codex (`~/.codex/sessions`), sem rede e sem subprocesso, e publica só categoria, ferramenta, estado, código de saída e contagens; nenhum texto de erro sai da máquina nem é copiado. A primeira passada só marca a posição de cada arquivo: nada anterior a ela é capturado. Qualquer outro valor mantém a saída antiga. Para ver, corrigir e verificar cada erro, veja a seção "Erros de ferramenta: ver, corrigir e verificar". |
| `PAINEL_AGENTES_EXTRA` | `1` liga as sessões de outros agentes (opt-in, desligada por padrão): sessões vivas do Grok CLI (`~/.grok`), sessões do Hermes abertas com atividade nos últimos 15 minutos (`state.db`, só leitura) e a data do último chat do cursor-agent (`~/.cursor/chats`). Publica só estado, modelo, contagens de tokens, categoria da última ferramenta e horários; nenhum título, resumo, pasta, prompt ou identificador de sessão. Sem rede e sem subprocesso. Qualquer outro valor mantém a saída de antes. |
| `PAINEL_REDE` | `1` liga a leitura do resumo de rede (opt-in, desligada por padrão). O coletor só lê `rede-resumo.json`; quem captura é o comando `painel-rede capturar`, iniciado à parte num terminal de administrador (veja "Uso de rede por programa"). Saem só bytes e conexões por programa da tabela fixa e por categoria de destino (local, rede privada, tailnet, internet); nenhum endereço, porta, PID ou nome de processo é gravado. Qualquer outro valor mantém a saída de antes. |
| `PAINEL_MAQUINA` | `1` liga o cartão "Máquina" (opt-in, desligado por padrão): uso de CPU (delta entre duas leituras de `GetSystemTimes`; a primeira passada mostra "sem leitura") e espaço dos discos fixos, na seção Saúde dos serviços. Sem rede, sem subprocesso e sem biblioteca extra; saem só números e a letra da unidade. Qualquer outro valor mantém a saída antiga. |
| `PAINEL_CUSTO` | Somente `1` liga o cartão "Custo" e a fonte `custo_agentes`, a cada 300 s: últimos 7 dias e até 20 sessões mais caras de hoje, US$ e R$, tokens por tipo. Claude Code e Codex são equivalentes de API da assinatura; Grok informa o próprio custo; Hermes é estimado e rateado por mensagens; Cursor-agent fica sem leitura. Sem a variável, nenhuma chave é adicionada. |

As tarifas aprovadas (US$ por 1 milhão de tokens) ficam em `lib/tarifas_padrao.json`,
no formato `{"modelos": {"nome-do-modelo": {"entrada": 2, "cache_leitura": 0.1,
"cache_escrita_30m": 2.5, "saida": 10, "fonte": "https://exemplo.com/precos",
"conferido_em": "2026-11-19"}}}`. Para Claude, use `cache_escrita_5m` e
`cache_escrita_1h` no lugar de `cache_escrita_30m`; Haiku inclui `acima_100k`
com os mesmos cinco preços. Copie o arquivo ou forneça modelos completos em
`%LOCALAPPDATA%/painel-ias/tarifas.json` (ou no diretório de `PAINEL_IAS_DIR`);
os modelos informados sobrepõem os padrões. `{"modelos": {}}` é válido e mantém
os padrões sem aviso. Arquivo inválido usa o padrão e avisa.
Cada modelo exige `fonte` HTTPS e `conferido_em`; mais de 30 dias gera aviso.
Modelo desconhecido preserva tokens e tem custo nulo. Os agregados por agente,
dia, semana e sessão somam os eventos com preço e publicam `parcial: true` e
`eventos_sem_custo: N` quando faltam preços; a página mostra "≥ R$ X (parcial)"
e "≥ US$ X". Se todos os eventos estiverem sem custo, o agregado fica nulo
("sem leitura"); ausência de atividade mantém zero. Não são valores de fatura
nem cobrança adicional da assinatura.

A conversão reutiliza `gastos_hermes.cotacao`, seu cache AwesomeAPI e
`PAINEL_USD_BRL` manual. A página mostra data, origem e desatualização; sem
cotação já obtida, R$ fica "sem leitura" e US$ continua disponível.
Claude é deduplicado por `message.id` (última ocorrência). Quando os quatro
contadores superiores de uso estão zerados, uma lista não vazia de `iterations`
com dicionários fornece a soma dos contadores e do cache 5m/1h; se o topo tiver
uso, as iterações não são somadas novamente. Codex soma
`last_token_usage` por evento: entrada comum = `input_tokens` menos
`cached_input_tokens` menos `cache_write_input_tokens`, cobrando cache à parte.
A inclusão da escrita no input é hipótese sem amostra local positiva; raciocínio
já está na saída. Eventos Codex sem entrada, cache ou saída contam zero, mesmo
que `total_tokens` informe o novo contexto após compactação. Entrada de turno
acima de 272k usa tarifa curta com aviso.
Contadores inconsistentes ou cache Claude sem duração preservam tokens, com custo
nulo. Custos/tokens de sessões exibidos referem-se à parcela de hoje; horários
cobrem os registros lidos da sessão. No Hermes, rateio e horários são aproximados;
os tokens são arredondados para inteiros na saída e exibidos com separador de milhar.
Só arquivos modificados nos últimos 8 dias são lidos; `.jsonl.zst` exige Python
3.14. Arquivos inalterados usam cache em memória. A saída contém apenas agentes,
modelos filtrados, números e avisos constantes, sem nomes de projeto ou sessão.

O GitHub é consultado pelo `gh` já logado na sua máquina; o painel não guarda token nenhum.

## Quadro: missão, previsão e pendências

O quadro (`quadro.json`) é editado por dois comandos pequenos:

```bash
./painel-missao "Agente 02" --missao "o que está fazendo" --situacao trabalhando --previsao 22h40
./painel-pendencia --help
```

O nome do agente não depende de acento nem de maiúscula.

## Erros de ferramenta: ver, corrigir e verificar

Com `PAINEL_ERROS=1` no coletor, cada erro de ferramenta do Claude Code e do Codex vira uma ocorrência com um identificador curto (10 caracteres hexadecimais) e um estado:

- **aberto**: erro novo, ainda sem registro.
- **corrigido**: o agente registrou que corrigiu, com uma nota opcional.
- **verificado**: o agente anexou uma evidência (a saída de um teste ou de um comando, com o código de saída). Quer dizer "evidência anexada pelo agente": o painel guarda o arquivo e mostra o tipo e o código, mas não confere se a evidência é verdadeira.
- **sem tratamento**: ficou aberto por mais de 7 dias.
- **só contado**: permissão negada, interrupção e erro de API entram só nas contagens; não há o que corrigir, e o comando recusa registrar correção ou evidência para eles.

```bash
./painel-erro listar                # abertos; --estado todos|corrigido|verificado|sem_tratamento|contado, --agente claude|codex, --json
./painel-erro ver 0123abcdef        # entrada da ferramenta e erro, lidos do transcript, com os segredos trocados por [oculto]
./painel-erro corrigido 0123abcdef --nota "o que foi corrigido"
./painel-erro verificado 0123abcdef --tipo teste --comando "py -m unittest" --codigo 0 --saida saida.txt
```

`ver` mostra o texto só no terminal de quem roda (se for outro agente, o texto passa para o transcript dele); `listar` e `ver` não gravam nada. Os arquivos ficam no diretório de dados do painel (`PAINEL_IAS_DIR`, ou `%LOCALAPPDATA%\painel-ias`), que o servidor não serve: `erros-ocorrencias.json` (gravado só pelo coletor; guarda, de cada erro, a classe, a ferramenta, o código, a hora e o ponteiro para o transcript (caminho do arquivo, posição e chave da chamada); nenhum texto do erro nem do comando (o caminho do transcript do Claude Code traz o nome da pasta do projeto)), `erros-registro.json` e a pasta `erros-evidencias/` (gravados só pelo `painel-erro`). Para zerar, pare o coletor e apague esses três; a passada seguinte recomeça só marcando a posição atual, sem capturar o que veio antes.

## Uso de rede por programa

Com `PAINEL_REDE=1` a seção de agentes ganha o bloco "Rede · por programa · último minuto": bytes enviados e recebidos e conexões de cada programa da tabela fixa, por categoria de destino (local, rede privada, tailnet, internet). O coletor só lê o arquivo `rede-resumo.json`; quem captura é o comando `painel-rede`, num processo à parte. Cada programa tem também um gráfico pequeno da taxa enviada e recebida nos últimos 10 minutos, com a escala no máximo atingido e o valor do pico ao lado ("máx."). Esse histórico fica só na memória da página (recarregar zera) e cada ponto é a média entre duas leituras do coletor, cerca de 10 s.

- **Iniciar:** num terminal de administrador, na pasta do painel, `py -3.14 -I -B painel-rede capturar`. De preferência chame o `python.exe` do Python 3.14 pelo caminho completo, com os mesmos argumentos (`-I -B painel-rede capturar`), porque o lançador `py` consulta configuração gravável sem privilégio. Sem elevação o comando recusa, não grava nada e não tenta se elevar.
- **Parar:** Ctrl+C no terminal da captura, ou `painel-rede parar` em outro terminal de administrador.
- **Ver o estado:** `painel-rede estado` (mostra só o que a fonte do painel validou).
- **Teto de carga:** a captura mede a própria CPU a cada 2 s; se a média passar de 5% de um núcleo, ela fecha a sessão e pausa, com esperas de 60, 120, 300 e 600 s (fica em 600) antes de tentar de novo.
- **O que fica onde:** PID, endereço e porta ficam só na memória do processo de captura. O arquivo `rede-resumo.json` (no diretório de dados, até 16 KiB, regravado a cada 4 s) tem só números, o produto da tabela fixa e valores de lista fixa; o que não está na tabela é somado em "Outros programas", sem nome.
- **Sessão ETW:** a captura usa a sessão `painel-ias-rede`. Uma sessão órfã (de uma captura encerrada à força) é limpa na próxima captura, pelo `painel-rede parar` ou no reinício do Windows.
- **Diretório de dados:** precisa existir antes (o coletor ou o servidor o criam) e não pode ser junção nem link; a captura não cria pasta.
- **Outra conta:** num terminal de administrador de OUTRA conta, aponte `PAINEL_IAS_DIR` para a pasta que o coletor lê; nesse caso os programas instalados no perfil do dono aparecem em "Outros programas".

O painel não cria tarefa agendada nem serviço; iniciar a captura junto com o Windows é uma mudança permanente no sistema e fica por conta do dono.

## Canal local para dados sensíveis (E26a)

No servidor, `PAINEL_CONEXOES=1`, `PAINEL_COMANDO=1` ou `PAINEL_REDE_SESSAO=1`
habilita um segundo listener, sempre em `127.0.0.1`. Sem nenhuma delas exatamente
`1`, nenhum socket ou log adicional é criado. Abra `http://127.0.0.1:7422/` na
própria máquina; com a E26, a página mostra também conexões e portas por processo.
`PAINEL_PORTA_LOCAL` aceita inteiro de 1024 a 65535 diferente da porta principal;
valor inválido usa 7422 com aviso constante. Se a porta resultante, inclusive o
padrão 7422, coincidir com a principal, o listener local não sobe e registra
aviso constante. A porta local não permite reutilização e usa bind exclusivo
no Windows; porta ocupada desabilita só o listener local.

A página fica em `publico_local/`, fora de `publico/`, e lê apenas `local.json`
do diretório `PAINEL_IAS_DIR` ou `%LOCALAPPDATA%\painel-ias`, limitado a 4 MiB e a
um objeto JSON. Sem leitura válida, responde 503. Não há ligação no painel
principal nem publicação desses dados em `dados.json` ou na sentinela.
Se o diretório de dados resolvido for `publico/` ou estiver dentro dele, o
listener local não sobe, com aviso constante; configure os dados fora da raiz
pública. A página local recebe CSP estrita com hashes SHA-256 calculados do
script e estilo inline ao carregar o arquivo. Na parada, o servidor aguarda o
fio local por até 5 s; se exceder, registra aviso constante e o processo pode
encerrar mesmo assim (fios locais daemon).

A porta separada é necessária porque `tailscale serve` repassa o Host do
cliente: checar Host na 7421 não distingue acesso local de tailnet. **Mantenha
o encaminhamento Tailscale somente para a porta principal; nunca encaminhe a
porta local.** O listener local ignora `PAINEL_HOSTS` e recusa cabeçalhos de
proxy/Tailscale. Para desligar, remova as três variáveis e inicie novamente o
servidor sob seu controle; para desfazer o código, reverta o futuro commit E26a.
Risco residual: o bind da porta principal (7421) mantém o comportamento de
reutilização anterior; a exclusividade desta etapa vale só para a porta local.

### Conexões e portas por programa (E26)

Com `PAINEL_CONEXOES` exatamente `1` no coletor `--vivo` e no servidor, a página
local mostra portas abertas (TCP em LISTEN e todos os endpoints UDP vinculados)
e conexões TCP estabelecidas, com filtro por texto e protocolo e ordenação por
processo. A coleta e a página atualizam a cada 5 s (a coleta depende da cadência
do laço); o arquivo só é regravado quando há nova leitura, sem regravar o cache
a cada passada. O filtro de texto espera 200 ms após a última tecla. A página
mostra a idade dos dados e avisa quando passa de 30 s. UDP vinculado pode ser
usado só para envio; não comprova um serviço recebendo tráfego. Não há ação
sobre processos ou conexões.

A fonte usa apenas ctypes (`GetExtendedTcpTable`/`GetExtendedUdpTable`, IPv4 e
IPv6, com scope id em host order e portas em network order), sem subprocesso,
DNS ou tráfego de rede. Estado TCP fora da lista conhecida vira `DESCONHECIDO`.
O buffer tem folga de 25% e até três tentativas de cópia. O máximo é 5.000
conexões por leitura; `omitidas` conta o restante. Portas são agrupadas por
protocolo/endereço/porta, preservando todos os PIDs donos. As contagens por PID
consideram somente as conexões incluídas. O executável é apenas o basename;
acesso negado ou processo encerrado vira `sem leitura` (PID 0: `System Idle`;
PID 4: `System`). PID pode ser reutilizado entre a leitura da tabela e do nome.

O coletor grava atomicamente `diretorio(ctx)/local.json`, o mesmo diretório de
dados da E26a, com `gerado_em`, `gerado_em_epoch` e seção `conexoes`. Recusa
destinos dentro de `publico/` ou da saída pública configurada (`--saida`). O
gravador mescla seções habilitadas (`conexoes`, `comando`, `rede_sessao`) para
as próximas etapas, com um único escritor; uma seção desligada sai na próxima
gravação de outra seção ativa. Sem a flag, não cria nem altera `local.json`:
um arquivo anterior permanece no disco. `dados.json`/`dados.js` recebem só
`fontes.conexoes` com `ok` e erro constante, sem conteúdo da fonte. Falha de
leitura substitui a seção local por erro constante, sem reter conexões antigas.
O JSON completo tem teto de 3,5 MiB (contando escapes ASCII); se necessário,
corta conexões, soma o corte em `omitidas` e recalcula portas e contagens,
preservando as outras seções. As etapas futuras precisam respeitar esse teto
global. Arquivo anterior inválido ou maior que 4 MiB é descartado na mescla.
Na troca atômica, `PermissionError` permite cinco novas tentativas de 50 ms;
se persistir, mantém o último arquivo e registra aviso constante, sem marcar
a fonte como erro. Outras falhas de gravação sinalizam erro no feed público.
Fora do Windows a fonte retorna erro constante. Esta autorização de dados
sensíveis vale apenas para o canal local; não encaminhe a 7422 pela tailnet.

## Solução de problemas

- **A página diz que os dados estão velhos.** O `./painel.sh` não está rodando. Suba de novo.
- **Todos os agentes aparecem "sem leitura".** O tmux não enxerga a sessão. Confira com `tmux ls` e com o campo
  `"terminal"` do agente. Rode o painel como o mesmo usuário que criou as sessões.
- **Mensagem "tmux não está instalado".** Instale (`brew install tmux` ou `sudo apt install tmux`). Para só ver a
  demonstração, use `./coletor --telas telas-de-exemplo --uma-vez`.
- **Nenhum PR aparece.** Entre no GitHub com `gh auth login`, ou use `--sem-gh`.
- **`python3: command not found`.** Instale o Python 3.9 ou mais novo.

## Testes

```bash
python3 -B -m unittest discover -s testes
node --test testes/js/*.test.js     # opcional: testes da página, só com Node instalado
```

`testes/telas/` e `telas-de-exemplo/` guardam telas **sintéticas** de terminal, com texto genérico.

## Alertas

A sentinela é opcional e fica desligada no servidor por padrão. Para ligar,
defina `$env:SENTINELA = '1'` antes de iniciar `painel.cmd` (ou o servidor com
`--com-coletor`). Ela também roda sozinha: `py -3.14 sentinela.py`.
Para conferir sem enviar nada: `py -3.14 sentinela.py --ensaio --uma-vez`.

As regras avisam sobre espera por mais de 60 s, atividade sem mudança por mais
de 15 min, agente parado, cotas usadas a partir de 85%, serviço fora em duas
avaliações, fonte falhando em três e dados velhos por mais de 60 s.
No modo vivo nenhum agente publica atividade, então o aviso de atividade sem
mudança só vale no modo antigo (terminais lidos pela tela).
Cada avaliação ocorre a cada 5 s; a mesma chave espera pelo menos 30 min para
novo aviso. Agente parado avisa uma vez por ocorrência; cota só rearma abaixo
de 70%. Fechamentos são registrados uma vez, sem toast extra.

Dados ficam em `PAINEL_IAS_DIR` ou `%LOCALAPPDATA%\painel-ias`: estado em
`alertas-estado.json`, log rotativo em `sentinela.log`, fila em
`alertas-pendentes.jsonl`. O ensaio usa `alertas-ensaio-estado.json`, não lê
configurações de canais nem modifica a deduplicação de produção.
Toast usa o PowerShell existente; falhas não bloqueiam outros canais.
Para webhook, crie `webhook.conf` nessa pasta com as chaves `WEBHOOK_URL` e
`WEBHOOK_KEY`. Sem arquivo, o canal fica desligado. Redirecionamentos são falha;
a fila tenta novamente a cada 5 min com `atrasado: true` e desiste após
24 h ou 50 tentativas. Credenciais não são gravadas na fila nem no log.

## Hindsight

Opt-in separado: `PAINEL_HINDSIGHT=1` e token em `HINDSIGHT_API_TOKEN` ou na
chave de mesmo nome em `hindsight.conf` dentro de `PAINEL_IAS_DIR`.
Sem opt-in ou token, não há conexão. O banco separado é `ops-painel`.
São gravadas apenas aberturas/fechamentos de alertas e um resumo diário às
23:55 (quantidade por tipo, agentes vistos e cotas máximas). Não são enviados
atividade, preview, texto de tela, credenciais ou texto completo do alerta.
Falhas são registradas e o painel continua. Ensaio nunca escreve no Hindsight.

## Privacidade

Nos cards de cota, o miolo do e-mail aparece como uma tarja de pixels que varia com a inclinação do card; o botão
**Privacidade** (no alto da página) liga e desliga a tarja, e a escolha fica no navegador. Ela vem ligada.

Os arquivos gerados (`publico/dados.*`, `quadro.json`, `estado.json`) e o seu `agentes.json` ficam fora do git
(veja `.gitignore`). Veja também `SECURITY.md`.

## Créditos e licença

- As consultas às CLIs e o desenho dos cards de cota vêm do
  [cotas-dashboard](https://github.com/fbtostadev/cotas-dashboard), de fbtostadev (MIT), copiado em `cotas/` com a
  licença original. O que mudou está em `cotas/ORIGEM.txt`.
- O card em cápsula, a tarja de privacidade e o `publico/cartao-apoio.js` foram contribuição de fbtostadev.
- Licença deste repositório: MIT (`LICENSE`).
