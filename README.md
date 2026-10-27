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
| Um navegador | abre a página `publico/index.html` | sim |
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
open publico/index.html            # no Linux: xdg-open publico/index.html

# 3. descreva o seu time
cp agentes.exemplo.json agentes.json
#    edite agentes.json: nome, ferramenta, conta, pasta e terminal de cada agente

# 4. suba o painel de verdade
./painel.sh                        # ou ./painel.sh --sem-gh para não consultar o GitHub
```

Deixe o `./painel.sh` rodando e abra `publico/index.html` no navegador. A página atualiza sozinha. `Ctrl-C` para parar.

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
Em `file://` ou sem EventSource, o painel continua lendo `dados.js` a cada 5 s.
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

O GitHub é consultado pelo `gh` já logado na sua máquina; o painel não guarda token nenhum.

## Quadro: missão, previsão e pendências

O quadro (`quadro.json`) é editado por dois comandos pequenos:

```bash
./painel-missao "Agente 02" --missao "o que está fazendo" --situacao trabalhando --previsao 22h40
./painel-pendencia --help
```

O nome do agente não depende de acento nem de maiúscula.

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
