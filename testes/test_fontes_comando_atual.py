"""E27: fontes só leitura, fixtures sem dados reais, custo limitado e privacidade."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from lib.fontes import comando_atual as fonte
from lib.mascara import OCULTO

TOKEN = 'sk-ant-e27FalsoNaoReal1234567890'
TOKENS_FORMATOS = ('sk-E27Falso123456789', 'xai-E27Falso123456789', 'ghp_E27Falso123456789',
                  'AKIA' + 'X' * 16, 'eyJhbGci.eyJzdWIi.c2lnbmF0dXJl',
                  '-----BEGIN PRIVATE KEY-----\nE27CORPOFALSO\n-----END PRIVATE KEY-----')
INSERT_MENSAGEM = 'INSERT INTO messages(session_id,role,tool_calls,tool_call_id,timestamp) VALUES(?,?,?,?,?)'


def claude(ident='a', nome='Bash', argumentos=None):
    return {'type': 'assistant', 'timestamp': 9900, 'cwd': 'C:\\projeto\\painel',
            'message': {'content': [{'type': 'tool_use', 'id': ident, 'name': nome,
                                     'input': argumentos or {'command': 'git status'}}]}}


def resultado_claude(ident='a'):
    return {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': ident}]}}


def codex(ident='a', tipo='custom_tool_call', nome='exec_command', argumentos=None):
    return {'type': 'response_item', 'timestamp': 9900,
            'payload': {'type': tipo, 'call_id': ident, 'name': nome,
                        'input' if tipo == 'custom_tool_call' else 'arguments':
                            json.dumps(argumentos or {'cmd': 'node --test testes/js/a.test.js'})}}


def resultado_codex(ident='a', tipo='custom_tool_call_output'):
    return {'type': 'response_item', 'payload': {'type': tipo, 'call_id': ident}}


def grok(ident='a', nome='run_terminal_command', argumentos=None):
    return {'type': 'assistant', 'timestamp': 9900,
            'tool_calls': [{'id': ident, 'function': {'name': nome,
                           'arguments': json.dumps(argumentos or {'command': 'npm run build'})}}]}


class FontesComando(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.ctx = {'claude_dir': self.raiz / 'claude', 'sessoes_dir': self.raiz / 'codex',
                    'grok_dir': self.raiz / 'grok', 'hermes_db': self.raiz / 'hermes/state.db',
                    'agora': 10000, 'comando_cache': {}}

    def arquivo(self, agente, linhas, nome=None):
        if agente == 'claude':
            caminho = self.ctx['claude_dir'] / 'pasta' / (nome or 'claude-123456789.jsonl')
        elif agente == 'codex':
            caminho = self.ctx['sessoes_dir'] / (nome or 'rollout-2026-11-19-12345678-1234-1234-1234-123456789abc.jsonl')
        else:
            caminho = self.ctx['grok_dir'] / 'sessions/pasta/grok-123456789' / (nome or 'chat_history.jsonl')
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(''.join(json.dumps(l) + '\n' for l in linhas), encoding='utf-8')
        os.utime(caminho, (10000, 10000))
        self.ctx['comando_cache'].pop('_descoberta', None)  # Nova fotografia na fixture.
        return caminho

    def adicionar(self, caminho, linhas):
        with caminho.open('a', encoding='utf-8') as arquivo:
            arquivo.write(''.join(json.dumps(l) + '\n' for l in linhas))
        os.utime(caminho, (10000, 10000))

    def banco(self, chamadas=None, completa=False, quantidade=0):
        caminho = self.ctx['hermes_db']
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(caminho)) as db, db:
            db.executescript('CREATE TABLE sessions(id TEXT, last_activity_at REAL, ended_at REAL);'
                             'CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, tool_calls TEXT, '
                             'tool_call_id TEXT, timestamp REAL);'
                             'CREATE INDEX por_sessao ON messages(session_id, timestamp);')
            db.executemany('INSERT INTO sessions VALUES(?,?,?)',
                           [('hermes-123456789', 9950, None), ('inativa', 1, None), ('encerrada', 9999, 9999)])
            for i in range(quantidade):
                db.execute(INSERT_MENSAGEM,
                           ('inativa', 'assistant', json.dumps([{'id': str(i)}]), None, i))
            db.execute(INSERT_MENSAGEM,
                       ('hermes-123456789', 'assistant', json.dumps(chamadas or [
                           {'id': 'h', 'function': {'name': 'terminal', 'arguments': '{"command":"git status"}'}}]),
                        None, 9900))
            if completa:
                db.execute(INSERT_MENSAGEM, ('hermes-123456789', 'tool', None, 'h', 9901))
        return caminho

    def ler(self):
        return fonte.ler(self.ctx)['comando']

    def test_ausencia_normal_e_cursor_fixo(self):
        self.assertEqual(self.ler(), {'estado': 'ok', 'sessoes': [], 'sem_leitura': ['cursor-agent']})
        self.assertEqual(list(self.raiz.iterdir()), [])

    def test_claude_concluida_nova_prevalece_sobre_orfa_antiga(self):
        caminho = self.arquivo('claude', [claude('a'), claude('b', 'Read', {'file_path': 'arquivo.py'}), resultado_claude('b')])
        item = self.ler()['sessoes'][0]
        self.assertEqual((item['agente'], item['sessao'], item['projeto']), ('claude', 'claude-1', 'painel'))
        self.assertEqual((item['texto'], item['em_curso'], item['tipo_tarefa']), ('arquivo.py', False, 'leitura'))
        self.adicionar(caminho, [resultado_claude('a')])
        item = self.ler()['sessoes'][0]
        self.assertEqual((item['texto'], item['em_curso']), ('arquivo.py', False))

    def test_codex_custom_function_e_resultados_sem_par(self):
        for tipo, saida in (('custom_tool_call', 'custom_tool_call_output'), ('function_call', 'function_call_output')):
            self.ctx['comando_cache'].clear()
            caminho = self.arquivo('codex', [resultado_codex('orfao', saida),
                {'type': 'session_meta', 'payload': {'id': 'codex-123456789', 'cwd': '/repo/teste'}},
                codex(tipo=tipo)])
            item = self.ler()['sessoes'][0]
            self.assertEqual((item['sessao'], item['projeto'], item['tipo_tarefa'], item['em_curso']),
                             ('codex-12', 'teste', 'testes', True))
            self.adicionar(caminho, [resultado_codex(tipo=saida)])
            self.assertFalse(self.ler()['sessoes'][0]['em_curso'])

    def test_codex_exec_command_begin_end(self):
        caminho = self.arquivo('codex', [{'type': 'event_msg', 'payload': {
            'type': 'exec_command_begin', 'call_id': 'x', 'command': 'git status'}}])
        self.assertTrue(self.ler()['sessoes'][0]['em_curso'])
        self.adicionar(caminho, [{'type': 'event_msg', 'payload': {'type': 'exec_command_end', 'call_id': 'x'}}])
        self.assertFalse(self.ler()['sessoes'][0]['em_curso'])

    def test_grok_historico_e_eventos_reserva(self):
        caminho = self.arquivo('grok', [grok()])
        item = self.ler()['sessoes'][0]
        self.assertEqual((item['tipo_tarefa'], item['em_curso']), ('build', True))
        self.adicionar(caminho, [{'type': 'tool_result', 'tool_call_id': 'a'}])
        self.assertFalse(self.ler()['sessoes'][0]['em_curso'])
        caminho.unlink()
        eventos = self.arquivo('grok', [{'type': 'tool_started', 'tool_name': 'read_file', 'ts': 9950}], 'events.jsonl')
        item = self.ler()['sessoes'][0]
        self.assertEqual((item['ferramenta'], item['texto'], item['em_curso']), ('read_file', '', True))
        self.adicionar(eventos, [{'type': 'tool_completed', 'ts': 9960}])
        self.assertFalse(self.ler()['sessoes'][0]['em_curso'])

    def test_hermes_sessoes_recentes_e_pares(self):
        self.banco(quantidade=1000)
        item = self.ler()['sessoes'][0]
        self.assertEqual((item['agente'], item['projeto'], item['texto'], item['em_curso']),
                         ('hermes', None, 'git status', True))
        with closing(sqlite3.connect(self.ctx['hermes_db'])) as db, db:
            db.execute(INSERT_MENSAGEM, ('hermes-123456789', 'tool', None, 'h', 9901))
        self.assertFalse(self.ler()['sessoes'][0]['em_curso'])

    def test_grok_evento_mais_novo_vence_historico(self):
        historico = self.arquivo('grok', [grok()])
        eventos = self.arquivo('grok', [{'type': 'tool_started', 'tool_call_id': 'a',
                                       'tool_name': 'run_terminal_command', 'ts': 9950}], 'events.jsonl')
        item = self.ler()['sessoes'][0]
        self.assertEqual((item['texto'], item['em_curso']), ('', True))
        self.adicionar(eventos, [{'type': 'tool_completed', 'tool_call_id': 'a', 'ts': 9960}])
        self.adicionar(historico, [{'type': 'tool_result', 'tool_call_id': 'a'}])
        item = self.ler()['sessoes'][0]
        self.assertEqual((item['texto'], item['em_curso']), ('', False))

    def test_hermes_limita_linhas_por_sessao(self):
        self.banco()
        with closing(sqlite3.connect(self.ctx['hermes_db'])) as db, db:
            db.executemany(INSERT_MENSAGEM,
                           [('hermes-123456789', 'user', None, None, 9901 + i / 1000) for i in range(200)])
        self.assertEqual(self.ler()['sessoes'], [])

    def test_hermes_ausente_corrompido_e_travado_sem_logs(self):
        self.assertEqual(self.ler()['sessoes'], [])
        self.ctx['hermes_db'].parent.mkdir()
        self.ctx['hermes_db'].write_bytes(b'fixture invalida')
        self.assertIn('hermes', self.ler()['sem_leitura'])
        self.ctx['hermes_db'].unlink()
        self.banco()
        with patch.object(fonte.sqlite3, 'connect', side_effect=sqlite3.OperationalError(TOKEN)):
            resultado = self.ler()
        self.assertIn('hermes', resultado['sem_leitura'])
        self.assertNotIn(TOKEN, json.dumps(resultado))

    def test_banco_so_leitura_sem_arquivos_auxiliares(self):
        caminho = self.banco(completa=True)
        antes = {p.name: p.read_bytes() for p in caminho.parent.iterdir()}
        self.assertFalse(self.ler()['sessoes'][0]['em_curso'])
        depois = {p.name: p.read_bytes() for p in caminho.parent.iterdir()}
        self.assertEqual(antes, depois)
        real = fonte.sqlite3.connect
        with patch.object(fonte.sqlite3, 'connect', wraps=real) as conectar:
            self.ler()
        self.assertIn('?mode=ro&immutable=1', conectar.call_args.args[0])
        self.assertTrue(conectar.call_args.kwargs['uri'])

    def test_truncamento_linha_invalida_e_incompleta(self):
        caminho = self.arquivo('claude', [claude('a')])
        self.assertTrue(self.ler()['sessoes'][0]['em_curso'])
        caminho.write_bytes(b'{invalido}\n[]\nnull\n')
        os.utime(caminho, (10000, 10000))
        self.assertEqual(self.ler()['sessoes'], [])
        with caminho.open('ab') as arquivo:
            arquivo.write(json.dumps(claude('b')).encode())
        os.utime(caminho, (10000, 10000))
        self.assertEqual(self.ler()['sessoes'], [])
        with caminho.open('ab') as arquivo:
            arquivo.write(b'\n')
        os.utime(caminho, (10000, 10000))
        self.assertTrue(self.ler()['sessoes'][0]['em_curso'])

    def test_cauda_e_cache_nao_releem_arquivo_inteiro(self):
        caminho = self.arquivo('claude', [])
        caminho.write_bytes(b'x' * (fonte.JANELA * 3) + b'\n' + json.dumps(claude()).encode() + b'\n')
        os.utime(caminho, (10000, 10000))
        abrir = Path.open
        leituras = []
        class Espiao:
            def __init__(self, arquivo): self.arquivo = arquivo
            def __enter__(self): return self
            def __exit__(self, *args): self.arquivo.close()
            def __getattr__(self, nome): return getattr(self.arquivo, nome)
            def read(self, tamanho=-1):
                leituras.append((self.arquivo.tell(), tamanho))
                return self.arquivo.read(tamanho)
        def ler(p, *args, **kwargs): return Espiao(abrir(p, *args, **kwargs))
        with patch.object(Path, 'open', ler):
            self.assertEqual(len(self.ler()['sessoes']), 1)
            self.assertEqual(len(self.ler()['sessoes']), 1)
        self.assertEqual(len(leituras), 1)
        self.assertEqual(leituras[0], (caminho.stat().st_size - fonte.JANELA, fonte.JANELA))
        self.adicionar(caminho, [resultado_claude()])
        with patch.object(Path, 'open', ler):
            self.assertFalse(self.ler()['sessoes'][0]['em_curso'])
        self.assertGreater(leituras[-1][0], fonte.JANELA * 3)

    @unittest.skipIf(fonte.zstd is None, 'zstd requer Python 3.14')
    def test_zst_incremental_frames_e_aquecimento_limitado(self):
        caminho = self.arquivo('codex', [], 'sessao-12345678.jsonl.zst')
        bruto = (json.dumps({'type': 'ignorado', 'padding': 'x' * 2000}) + '\n').encode() * 400
        bruto += (json.dumps(codex()) + '\n').encode()
        caminho.write_bytes(fonte.zstd.compress(bruto))
        os.utime(caminho, (10000, 10000))
        self.assertEqual(self.ler()['sessoes'], [])
        cache = self.ctx['comando_cache'][str(caminho)]
        for _ in range(5):
            resultado = self.ler()
            if resultado['sessoes']:
                break
        self.assertTrue(resultado['sessoes'][0]['em_curso'])
        # O cursor não recomeçou durante o aquecimento, mesmo sem mudança de mtime.
        self.assertIs(cache, self.ctx['comando_cache'][str(caminho)])
        with caminho.open('ab') as arquivo:
            arquivo.write(fonte.zstd.compress((json.dumps(resultado_codex()) + '\n').encode()))
        os.utime(caminho, (10000, 10000))
        self.assertFalse(self.ler()['sessoes'][0]['em_curso'])
        caminho.write_bytes(b'corrompido')
        os.utime(caminho, (10000, 10000))
        self.assertIn('codex', self.ler()['sem_leitura'])

    def test_limites_de_sessoes_atividade_campos_e_pendentes(self):
        for i in range(35):
            self.arquivo('claude', [claude(str(i), 'f' * 80, {'x': 'á ' * 3000})], str(i) + '-sessao.jsonl')
        antiga = self.arquivo('codex', [codex()])
        os.utime(antiga, (9000, 9000))
        itens = self.ler()['sessoes']
        self.assertEqual(len(itens), 30)
        for item in itens:
            self.assertLessEqual(len(item['sessao']), 8)
            self.assertEqual(item['ferramenta'], OCULTO)  # Nome cru também passa pela máscara.
            self.assertEqual(len(item['texto']), 2000)
            self.assertTrue(item['texto'].endswith('…'))
            self.assertTrue(item['tipo_tarefa_inferido'])
        self.assertLessEqual(sum(not k.startswith('_') for k in self.ctx['comando_cache']), 30)
        estado = fonte._novo('id')
        for i in range(fonte.MAX_CHAMADAS + 10):
            fonte._chamada(estado, str(i), 'Read', {'path': 'arquivo'}, 9900)
        self.assertEqual(len(estado['chamadas']), fonte.MAX_CHAMADAS)

    def test_mascara_antes_do_corte_e_argumento_principal(self):
        self.arquivo('claude', [claude(argumentos={'command': 'A_KEY="' + 'á ' * 3000 + TOKEN + '"'})])
        item = self.ler()['sessoes'][0]
        self.assertNotIn('á ', item['texto'])
        self.assertIn(OCULTO, item['texto'])
        for nome, args, esperado in (('Edit', {'file_path': 'a.py', 'new_string': TOKEN}, 'a.py'),
                                     ('Grep', {'pattern': 'teste'}, 'teste'),
                                     ('Glob', {'pattern': '*.py'}, '*.py')):
            self.assertEqual(fonte._principal(nome, args), esperado)

    def test_tokens_falsos_nas_quatro_fontes_so_saida_mascarada(self):
        self.arquivo('claude', [claude(argumentos={'command': 'echo ' + TOKEN})])
        self.arquivo('codex', [codex(argumentos={'cmd': 'echo ' + TOKEN})])
        self.arquivo('grok', [grok(argumentos={'command': 'echo ' + TOKEN})])
        self.banco([{'call_id': 'h', 'function': {'name': 'terminal', 'arguments': json.dumps({'command': 'echo ' + TOKEN})}}])
        secao = self.ler()
        self.assertEqual({i['agente'] for i in secao['sessoes']}, set(fonte.AGENTES))
        self.assertNotIn(TOKEN, json.dumps(secao))
        self.assertTrue(all(OCULTO in i['texto'] for i in secao['sessoes']))
        self.assertNotIn(TOKEN, repr(self.ctx['comando_cache']))

    def test_tipos_fixos(self):
        for nome, texto, esperado in (('Read', 'x', 'leitura'), ('Grep', 'x', 'busca'),
                                     ('Write', 'x', 'edição'), ('web_search', 'x', 'rede'),
                                     ('Bash', 'echo teste', 'shell'), ('desconhecida', 'x', 'outro')):
            self.assertEqual(fonte.tipo_tarefa(nome, texto), esperado)

    def test_codex_apply_patch_so_caminhos_sem_corpo(self):
        corpo = '*** Begin Patch\n*** Update File: a.py\n@@\n-' + TOKEN + '\n+CORPO_PRIVADO\n*** Add File: b.py\n+CORPO_PRIVADO\n*** Delete File: c.py\n*** End Patch'
        self.arquivo('codex', [codex(nome='apply_patch', argumentos=corpo)])
        item = self.ler()['sessoes'][0]
        self.assertEqual(item['texto'], 'a.py; b.py; c.py')
        self.assertNotIn('CORPO_PRIVADO', json.dumps(item))

    def test_hermes_patch_v4a_so_caminhos_sem_corpo(self):
        corpo = '*** Begin Patch\n*** Update File: h.py\n@@\n+CORPO_PRIVADO\n*** End Patch'
        self.banco([{'id': 'h', 'function': {'name': 'patch', 'arguments': json.dumps({'patch': corpo})}}])
        item = self.ler()['sessoes'][0]
        self.assertEqual(item['texto'], 'h.py')
        self.assertNotIn('CORPO_PRIVADO', json.dumps(item))

    def test_editores_desconhecidos_nao_exibem_corpo(self):
        for nome, args, esperado in (
                ('write_to_file', {'path': 'a.py', 'content': 'CORPO_PRIVADO'}, 'a.py'),
                ('str_replace_editor', {'file_path': 'b.py', 'new_string': 'CORPO_PRIVADO'}, 'b.py'),
                ('edit_file', 'CORPO_PRIVADO', 'sem caminho'),
                ('apply_diff', {'diff': 'CORPO_PRIVADO'}, 'sem caminho'),
                ('update_file', 'CORPO_PRIVADO', 'sem caminho'),
                ('create_file', {'path': 'd.py', 'content': 'CORPO_PRIVADO'}, 'd.py'),
                ('wri\u202ete_file', 'CORPO_PRIVADO', 'sem caminho'),
                ('custom', {'file_path': 'e.py', 'content': 'CORPO_PRIVADO'}, 'e.py'),
                ('custom', {'file_path': 'c.py', 'old_string': 'CORPO_PRIVADO'}, 'c.py')):
            self.assertEqual(fonte._principal(nome, args), esperado)

    def test_entrada_limitada_antes_da_mascara_e_nome_controles(self):
        estado = fonte._novo('s')
        with patch.object(fonte, 'mascarar', wraps=fonte.mascarar) as aplicar:
            fonte._chamada(estado, 'a', 'Bash\u202e\x1b', {'command': 'á ' * 10000 + TOKEN}, 9900)
        self.assertTrue(all(len(c.args[0]) <= fonte.MAX_ENTRADA for c in aplicar.call_args_list))
        item = fonte._item(estado, 'claude')
        self.assertEqual(item['ferramenta'], 'Bash')
        self.assertEqual(len(item['texto']), 2000)
        self.assertTrue(item['texto'].endswith('…'))

    def test_pendente_apos_dez_minutos_sem_atividade_ultima_sem_resultado(self):
        self.arquivo('claude', [claude()])
        self.assertTrue(self.ler()['sessoes'][0]['em_curso'])
        self.ctx['agora'] = 10601
        item = self.ler()['sessoes'][0]
        self.assertFalse(item['em_curso'])
        self.assertTrue(item['sem_resultado'])
        self.assertEqual(item['texto'], 'git status')

    def test_codex_orfa_antiga_nao_esconde_concluida_nova(self):
        self.arquivo('codex', [codex('a'), codex('b', argumentos={'cmd': 'git status'}), resultado_codex('b')])
        item = self.ler()['sessoes'][0]
        self.assertEqual((item['texto'], item['em_curso']), ('git status', False))

    def test_grok_eventos_sem_id_mesmo_instante_nao_se_sobrescrevem(self):
        caminho = self.arquivo('grok', [
            {'type': 'tool_started', 'tool_name': 'read_file', 'ts': 9900},
            {'type': 'tool_started', 'tool_name': 'grep', 'ts': 9900}], 'events.jsonl')
        self.ler()
        estado = self.ctx['comando_cache'][str(caminho)]
        self.assertEqual(len(estado['chamadas']), 2)
        self.adicionar(caminho, [{'type': 'tool_completed', 'tool_name': 'read_file', 'ts': 9901}])
        item = self.ler()['sessoes'][0]
        self.assertEqual((item['ferramenta'], item['em_curso']), ('grep', True))

    def test_descoberta_30_s_e_so_stat_de_candidatos_entre_passadas(self):
        self.arquivo('claude', [claude()])
        antigo = self.arquivo('claude', [claude()], 'antigo.jsonl')
        os.utime(antigo, (8000, 8000))
        self.ler()
        self.ctx['agora'] = 10029
        with patch.object(Path, 'glob', side_effect=AssertionError('redescoberta antecipada')):
            self.assertEqual(len(self.ler()['sessoes']), 1)
        # Um arquivo novo só aparece na próxima fotografia, sem invalidar o cache à mão.
        novo = antigo.parent / 'novo.jsonl'
        novo.write_text(json.dumps(claude()) + '\n', encoding='utf-8')
        os.utime(novo, (10029, 10029))
        with patch.object(Path, 'glob', side_effect=AssertionError('redescoberta antecipada')):
            self.assertEqual(len(self.ler()['sessoes']), 1)
        self.ctx['agora'] = 10030
        self.assertEqual(len(self.ler()['sessoes']), 2)

    def test_trinta_aplicado_depois_de_filtrar_arquivos_sem_chamada(self):
        for i in range(35):
            self.arquivo('claude', [], 'a%02d.jsonl' % i)
        self.arquivo('claude', [claude()], 'zz-valida.jsonl')
        self.assertEqual(len(self.ler()['sessoes']), 1)

    def test_hermes_max_id_cache_tamanho_e_texto_invalido_por_linha(self):
        self.banco()
        with patch.object(fonte, '_funcoes', wraps=fonte._funcoes) as interpretar:
            self.assertEqual(len(self.ler()['sessoes']), 1)
            self.ler()
            self.assertEqual(interpretar.call_count, 1)
        with closing(sqlite3.connect(self.ctx['hermes_db'])) as db, db:
            db.execute(INSERT_MENSAGEM, ('hermes-123456789', 'assistant', 'x' * 65537, None, 9901))
            db.execute("INSERT INTO messages(session_id,role,tool_calls,timestamp) VALUES('hermes-123456789','assistant',CAST(x'ff' AS TEXT),9902)")
            db.execute(INSERT_MENSAGEM, ('hermes-123456789', 'tool', None, 'h', 9903))
        with patch.object(fonte, '_funcoes', wraps=fonte._funcoes) as interpretar:
            secao = self.ler()
            self.assertNotIn('hermes', secao['sem_leitura'])
            self.assertFalse(secao['sessoes'][0]['em_curso'])
            self.assertTrue(all(not isinstance(c.args[1], str) or len(c.args[1]) <= 65536
                                for c in interpretar.call_args_list))
        self.assertIn('length(tool_calls) <= 65536', fonte.SQL_MENSAGENS)

    def test_hermes_limite_depois_do_filtro_e_linha_invalida_nao_afeta_outra_sessao(self):
        self.banco()
        with closing(sqlite3.connect(self.ctx['hermes_db'])) as db, db:
            db.executemany('INSERT INTO sessions VALUES(?,?,NULL)', [('vazia-%02d' % i, 9999) for i in range(35)])
            db.execute("INSERT INTO sessions VALUES(CAST(x'ff' AS TEXT),9999,NULL)")
            db.execute("INSERT INTO sessions VALUES('ruim','data invalida',NULL)")
        self.assertEqual(len(self.ler()['sessoes']), 1)


if __name__ == '__main__':
    unittest.main()
