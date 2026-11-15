"""Canais isolados e injetáveis; credenciais nunca entram em fila ou log."""
import base64
from datetime import datetime
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
try:
    from .comum import gravar_atomico
    from .leitor import mascarar
    from .semjanela import sem_janela_kwargs
except ImportError:
    from comum import gravar_atomico
    from leitor import mascarar
    from semjanela import sem_janela_kwargs


def config(pasta, nome):
    try:
        linhas = (Path(pasta) / nome).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return {}
    return {k.strip(): v.strip() for linha in linhas if not linha.lstrip().startswith('#')
            for k, sep, v in [linha.partition('=')] if sep}


class SemRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http(method, url, payload, headers, timeout=10):
    request = Request(url, data=json.dumps(payload, ensure_ascii=True).encode('ascii'),
                      headers=dict(headers, **{'Content-Type': 'application/json'}), method=method)
    try:
        with build_opener(SemRedirect()).open(request, timeout=timeout) as resposta:
            return resposta.status
    except HTTPError as erro:
        codigo = erro.code
        erro.close()
        return codigo


class Log:
    def __init__(self, logger=None, ensaio=False):
        self.logger = logger or logging.getLogger('painel.sentinela')
        self.ensaio = ensaio

    def enviar(self, alerta):
        self.logger.info('%s %s %s: %s', 'ENSAIO' if self.ensaio else 'alerta',
                         alerta['tipo'], mascarar(alerta['chave']), mascarar(alerta.get('texto', '')))
        return True


TOAST_SCRIPT = r'''& { param([string]$Titulo64, [string]$Texto64)
$ErrorActionPreference = 'Stop'
$titulo = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Titulo64))
$texto = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Texto64))
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$null = $xml.GetElementsByTagName('text').Item(0).AppendChild($xml.CreateTextNode($titulo))
$null = $xml.GetElementsByTagName('text').Item(1).AppendChild($xml.CreateTextNode($texto))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($toast)
}'''


class Toast:
    def __init__(self, executar=None, logger=None):
        self.executar = executar
        self.logger = logger or logging.getLogger('painel.sentinela')

    def enviar(self, alerta):
        if os.name != 'nt' and self.executar is None:
            return False
        argumentos = ["'" + base64.b64encode(v.encode('utf-8')).decode('ascii') + "'"
                      for v in ('Overview-Painel', mascarar(alerta.get('texto', '')))]
        argv = ['powershell', '-NoProfile', '-NonInteractive', '-Command', TOAST_SCRIPT, *argumentos]
        try:
            if self.executar:
                retorno = self.executar(argv, timeout=10)
            else:
                retorno = subprocess.run(argv, capture_output=True, timeout=10, check=True, **sem_janela_kwargs())
            return getattr(retorno, 'returncode', retorno) == 0
        except Exception:
            self.logger.warning('toast falhou')
            return False


class Webhook:
    def __init__(self, pasta, http_post=None, agora=None, logger=None):
        self.pasta = Path(pasta)
        self.fila = self.pasta / 'alertas-pendentes.jsonl'
        self.http_post = http_post or (lambda url, payload, **kw: http('POST', url, payload, **kw))
        self.agora = agora or time.time
        self.logger = logger or logging.getLogger('painel.sentinela')
        cfg = config(pasta, 'webhook.conf')
        self.url, self.key = cfg.get('WEBHOOK_URL', ''), cfg.get('WEBHOOK_KEY', '')
        u = urlsplit(self.url)
        self.ligado = bool(self.url and self.key and u.scheme in ('https', 'http') and u.hostname and not u.username)

    def _seguro(self, alerta):
        r = {k: alerta[k] for k in ('chave', 'tipo', 'texto', 'hora', 'canais', 'atrasado') if k in alerta}
        def limpar(v):
            if isinstance(v, str):
                return mascarar(v.replace(self.key, '[oculto]')) if self.key else mascarar(v)
            if isinstance(v, list):
                return [limpar(x) for x in v]
            return v
        return {k: limpar(v) for k, v in r.items()}

    def _post(self, alerta):
        try:
            status = self.http_post(self.url, alerta, headers={'Authorization': 'Bearer ' + self.key}, timeout=10)
            return 200 <= status < 300
        except Exception:
            return False

    def _ler_fila(self):
        try:
            texto = self.fila.read_text(encoding='utf-8', errors='replace')
        except FileNotFoundError:
            return []
        itens = []
        for linha in texto.splitlines():
            try:
                item = json.loads(linha)
                if isinstance(item, dict) and isinstance(item.get('alerta'), dict):
                    itens.append(item)
            except ValueError:
                self.logger.warning('linha inválida na fila de webhook')
        return itens

    def _salvar(self, itens):
        gravar_atomico(self.fila, ''.join(json.dumps(i, ensure_ascii=True) + '\n' for i in itens))

    def enviar(self, alerta):
        if not self.ligado:
            return False
        try:
            seguro = self._seguro(alerta)
            if self._post(seguro):
                return True
            itens = self._ler_fila()
            itens.append({'alerta': seguro, 'criado_em': self.agora(), 'tentado_em': self.agora(), 'tentativas': 1})
            self._salvar(itens)
            self.logger.warning('webhook falhou; alerta enfileirado')
        except Exception:
            self.logger.warning('webhook falhou; fila sem escrita')
        return False

    def reenviar(self):
        if not self.ligado:
            return 0
        enviados, manter = 0, []
        try:
            if not self.fila.exists():
                return 0
            for item in self._ler_fila():
                agora = self.agora()
                if agora - item['criado_em'] >= 86400 or item['tentativas'] >= 50:
                    self.logger.warning('webhook descartado por idade/tentativas')
                    continue
                if agora - item['tentado_em'] < 300:
                    manter.append(item)
                    continue
                item['alerta'] = dict(self._seguro(item['alerta']), atrasado=True)
                item['tentativas'] += 1
                item['tentado_em'] = agora
                if self._post(item['alerta']):
                    enviados += 1
                elif item['tentativas'] < 50:
                    manter.append(item)
                else:
                    self.logger.warning('webhook descartado após 50 tentativas')
            self._salvar(manter)
        except Exception:
            self.logger.warning('falha ao reenviar fila de webhook')
        return enviados


class Hindsight:
    BASE = 'http://127.0.0.1:8888/v1/default/banks/ops-painel'

    def __init__(self, pasta, http_fn=None, env=None, logger=None):
        self.http = http_fn or http
        self.logger = logger or logging.getLogger('painel.sentinela')
        env = os.environ if env is None else env
        self.ligado = False
        self.token = ''
        if env.get('PAINEL_HINDSIGHT') != '1':
            return
        self.token = env.get('HINDSIGHT_API_TOKEN') or config(pasta, 'hindsight.conf').get('HINDSIGHT_API_TOKEN', '')
        if not self.token:
            return
        try:
            status = self.http('PUT', self.BASE, {}, headers=self.headers(), timeout=10)
            self.ligado = 200 <= status < 300
            if not self.ligado:
                self.logger.warning('Hindsight: banco indisponível')
        except Exception:
            self.logger.warning('Hindsight: banco indisponível')

    def headers(self):
        return {'Authorization': 'Bearer ' + self.token}

    def _conteudo(self, alerta):
        conteudo = {'tipo': alerta['tipo'], 'chave': alerta.get('chave')}
        if alerta['tipo'] == 'resumo_diario':
            resumo = alerta.get('resumo', {})
            def percentual(v):
                return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 100
            tipos = {'atencao', 'travado', 'parado', 'cota', 'servico', 'fonte', 'dados-velhos'}
            conteudo['resumo'] = {
                'data': str(resumo.get('data', ''))[:10],
                'alertas_por_tipo': {k: v for k, v in resumo.get('alertas_por_tipo', {}).items()
                                    if k in tipos and isinstance(v, int) and not isinstance(v, bool) and v >= 0},
                'agentes_vistos': [v[:80] for v in resumo.get('agentes_vistos', []) if isinstance(v, str)],
                'cotas_maximas': {nome: {k: v for k, v in janelas.items()
                                        if k in ('sessao_5h', 'semanal') and percentual(v)}
                                 for nome, janelas in resumo.get('cotas_maximas', {}).items()
                                 if isinstance(janelas, dict)}
            }
        return mascarar(json.dumps(conteudo, ensure_ascii=False)).replace(self.token, '[oculto]')

    def enviar(self, alerta):
        if not self.ligado or alerta.get('tipo') not in ('aberto', 'resolvido', 'resumo_diario'):
            return False
        # Não serializar texto, atividade ou preview, nem objetos arbitrários.
        try:
            conteudo = self._conteudo(alerta)
            timestamp = datetime.fromtimestamp(alerta['hora']).astimezone().isoformat()
            payload = {'items': [{'content': conteudo, 'context': 'painel-ias',
                       'tags': ['painel-ias', 'host:win'], 'timestamp': timestamp}], 'async': True}
            status = self.http('POST', self.BASE + '/memories', payload, headers=self.headers(), timeout=10)
            if 200 <= status < 300:
                return True
        except Exception:
            pass
        self.logger.warning('Hindsight: registro falhou')
        return False
