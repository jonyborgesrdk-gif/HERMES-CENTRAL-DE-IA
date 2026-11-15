"""Fonte cota_codex_local (E19, CONTRATOS §8): só arquivos sintéticos em pasta temporária."""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.fontes import cota_codex_local as mod

try:
    from compression import zstd as zstd_std
except ImportError:
    zstd_std = None

AGORA = 2_000_000_000
SEM_ZSTD = 'compression.zstd indisponível neste Python'


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def limites(p5=23.0, psem=61.5, r5=AGORA + 3600, rsem=AGORA + 259200):
    return {
        'limit_id': 'codex',
        'primary': {'used_percent': p5, 'window_minutes': 300, 'resets_at': r5},
        'secondary': {'used_percent': psem, 'window_minutes': 10080, 'resets_at': rsem},
        'credits': None,
        'plan_type': 'plus',
    }


def evento(ts, rate_limits):
    return {'timestamp': ts, 'type': 'event_msg',
            'payload': {'type': 'token_count', 'info': None, 'rate_limits': rate_limits}}


def linha_limites(ts, p5=23.0, psem=61.5, **mais):
    return json.dumps(evento(ts, limites(p5, psem, **mais)))


def linha_meta():
    return json.dumps({'timestamp': iso(AGORA - 9000), 'type': 'session_meta',
                       'payload': {'id': 'sessao-sintetica', 'cwd': 'C:/exemplo'}})


def escrever(raiz, relativo, linhas, mtime=None):
    caminho = Path(raiz) / relativo
    caminho.parent.mkdir(parents=True, exist_ok=True)
    texto = ''.join(linha + '\n' for linha in linhas)
    if caminho.name.endswith('.zst'):
        with zstd_std.open(caminho, 'wt', encoding='utf-8') as saida:
            saida.write(texto)
    else:
        caminho.write_text(texto, encoding='utf-8', newline='')
    if mtime is not None:
        os.utime(caminho, (mtime, mtime))
    return caminho


class CotaCodexLocalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.raiz = self.base / 'sessions'
        self.raiz.mkdir()
        # Nenhum teste pode cair na pasta real do usuário (~/.codex).
        guarda = patch.object(Path, 'home', side_effect=AssertionError('pasta real do usuário'))
        guarda.start()
        self.addCleanup(guarda.stop)

    def ler(self, raiz=None):
        return mod.ler({'sessoes_dir': str(raiz or self.raiz), 'agora': AGORA})

    def dado(self, raiz=None):
        return self.ler(raiz)[mod.CHAVE]

    def resumo(self, raiz=None):
        d = self.dado(raiz)
        return d['observado_epoch'], d['barras'][0]['usado_pct']

    # 1
    def test_01_pasta_inexistente(self):
        self.assertEqual(self.ler(self.raiz / 'nao-existe'), {'cota_codex_local': mod.VAZIO})
        self.assertEqual(mod.VAZIO, {'estado': 'sem_dado', 'observado_epoch': None, 'barras': []})
        arquivo = escrever(self.base, 'nao-e-pasta.jsonl', [linha_limites(iso(AGORA - 10))])
        self.assertEqual(self.ler(arquivo), {'cota_codex_local': mod.VAZIO})

    # 2
    def test_02_pasta_vazia_e_arquivos_sem_rate_limits(self):
        self.assertEqual(self.ler(), {mod.CHAVE: mod.VAZIO})
        sem_limites = json.dumps({'timestamp': iso(AGORA - 10), 'type': 'event_msg',
                                  'payload': {'type': 'token_count', 'info': None}})
        limites_nulos = json.dumps(evento(iso(AGORA - 10), None))
        escrever(self.raiz, '2026/11/14/a.jsonl', [linha_meta(), sem_limites, limites_nulos, 'texto solto'])
        escrever(self.raiz, 'vazio.jsonl', [])
        self.assertEqual(self.ler(), {mod.CHAVE: mod.VAZIO})

    # 3
    def test_03_maior_timestamp_vence_mesmo_aparecendo_antes(self):
        escrever(self.raiz, 'a.jsonl', [linha_meta(), linha_limites(iso(AGORA - 100), p5=40.0),
                                        linha_limites(iso(AGORA - 500), p5=10.0)])
        d = self.dado()
        self.assertEqual(d['estado'], 'ok')
        self.assertEqual(self.resumo(), (float(AGORA - 100), 40.0))

    # 4
    @unittest.skipIf(zstd_std is None, SEM_ZSTD)
    def test_04_zst_em_subpasta_e_lido(self):
        escrever(self.raiz, '2026/10/28/rollout.jsonl.zst',
                 [linha_meta(), linha_limites(iso(AGORA - 60), p5=23, psem=61.5)])
        d = self.dado()
        self.assertEqual(d, {'estado': 'ok', 'observado_epoch': float(AGORA - 60), 'barras': [
            {'tipo': '5h', 'usado_pct': 23.0, 'renova_epoch': AGORA + 3600, 'janela_min': 300},
            {'tipo': 'semana', 'usado_pct': 61.5, 'renova_epoch': AGORA + 259200, 'janela_min': 10080}]})
        self.assertIsInstance(d['barras'][0]['usado_pct'], float)

    # 5
    def test_05_horario_do_evento_vence_data_do_arquivo_e_empate(self):
        escrever(self.raiz, 'arquivo-recente.jsonl', [linha_limites(iso(AGORA - 1000), p5=10.0)],
                 mtime=AGORA - 10)
        escrever(self.raiz, 'arquivo-antigo.jsonl', [linha_limites(iso(AGORA - 100), p5=50.0)],
                 mtime=AGORA - 5000)
        self.assertEqual(self.resumo(), (float(AGORA - 100), 50.0))
        # Mesmo timestamp em dois arquivos: vale o de modificação mais recente,
        # qualquer que seja a ordem dos nomes na listagem.
        for recente, antigo in (('a.jsonl', 'b.jsonl'), ('b.jsonl', 'a.jsonl')):
            with self.subTest(recente=recente):
                pasta = self.base / ('empate-' + recente[0])
                escrever(pasta, recente, [linha_limites(iso(AGORA - 200), p5=70.0)], mtime=AGORA - 10)
                escrever(pasta, antigo, [linha_limites(iso(AGORA - 200), p5=30.0)], mtime=AGORA - 5000)
                self.assertEqual(self.resumo(pasta), (float(AGORA - 200), 70.0))

    # 6
    def test_06_so_os_oito_mais_recentes_sao_abertos(self):
        self.assertEqual(mod.MAX_ARQUIVOS, 8)
        for i in range(10):  # i == 9 é o mais recente; i == 1 é o 9º; i == 2 é o 8º
            if i == 1:
                linhas = [linha_limites(iso(AGORA - 10), p5=99.0)]
            elif i == 2:
                linhas = [linha_limites(iso(AGORA - 3000), p5=7.0)]
            else:
                linhas = [linha_limites(iso(AGORA - 5000), p5=5.0)]
            escrever(self.raiz, f'arquivo-{i:02d}.jsonl', linhas, mtime=AGORA - 10000 + i * 100)
        with patch.object(mod, '_linhas', wraps=mod._linhas) as espiao:
            resumo = self.resumo()
        abertos = [Path(chamada.args[0]).name for chamada in espiao.call_args_list]
        self.assertEqual(len(abertos), 8)
        self.assertEqual(set(abertos), {f'arquivo-{i:02d}.jsonl' for i in range(2, 10)})
        self.assertEqual(resumo, (float(AGORA - 3000), 7.0))

    # 7
    def test_07_observado_epoch_e_o_timestamp_do_evento(self):
        caminho = escrever(self.raiz, 'a.jsonl', [linha_limites('2033-05-18T03:33:20.000Z')],
                           mtime=1_900_000_000)
        d = self.dado()
        self.assertEqual(d['observado_epoch'], 2000000000.0)
        self.assertNotEqual(d['observado_epoch'], os.stat(caminho).st_mtime)

    # 8
    def test_08_evento_no_futuro(self):
        escrever(self.raiz, 'a.jsonl', [linha_limites(iso(AGORA + 301), p5=90.0)])
        self.assertEqual(self.ler(), {mod.CHAVE: mod.VAZIO})
        escrever(self.raiz, 'b.jsonl', [linha_limites(iso(AGORA + 300), p5=80.0)])
        self.assertEqual(self.resumo(), (float(AGORA + 300), 80.0))
        r = mod.ultimo_evento([linha_limites(iso(AGORA - 50), p5=1.0),
                               linha_limites(iso(AGORA + 301), p5=2.0)], AGORA)
        self.assertEqual((r[0], r[1][0]['usado_pct']), (float(AGORA - 50), 1.0))

    # 9
    def test_09_janelas(self):
        def jan(minutos=300, usado=10.0, reset=AGORA + 60):
            return {'used_percent': usado, 'window_minutes': minutos, 'resets_at': reset}

        janelas = mod.janelas
        self.assertEqual([b['tipo'] for b in janelas({'primary': jan(300), 'secondary': jan(10080)})],
                         ['5h', 'semana'])
        self.assertEqual(janelas({'primary': jan(1440)}),
                         [{'tipo': 'janela', 'usado_pct': 10.0, 'renova_epoch': AGORA + 60, 'janela_min': 1440}])
        self.assertEqual([b['tipo'] for b in janelas({'secondary': jan(10080), 'primary': jan(300)})],
                         ['5h', 'semana'])
        self.assertEqual([b['usado_pct'] for b in janelas({'primary': jan(usado=0), 'secondary': jan(usado=100)})],
                         [0.0, 100.0])
        self.assertEqual([b['janela_min'] for b in janelas({'primary': jan(1), 'secondary': jan(525600)})],
                         [1, 525600])
        for usado in (-0.1, 100.5, True, False, '50', math.inf, -math.inf, math.nan, None, 10 ** 400):
            with self.subTest(used_percent=usado):
                self.assertEqual(janelas({'primary': jan(usado=usado)}), [])
        sem_usado = jan()
        del sem_usado['used_percent']
        self.assertEqual(janelas({'primary': sem_usado}), [])
        for minutos in (0, -300, True, 300.0, '300', None, 525601):
            with self.subTest(window_minutes=minutos):
                self.assertEqual(janelas({'primary': jan(minutos=minutos)}), [])
        sem_minutos = jan()
        del sem_minutos['window_minutes']
        self.assertEqual(janelas({'primary': sem_minutos}), [])
        sem_reset = jan()
        del sem_reset['resets_at']
        self.assertIsNone(janelas({'primary': sem_reset})[0]['renova_epoch'])
        for reset in (0, -5, '2033-05-18', math.inf, math.nan, True, 10 ** 400):
            with self.subTest(resets_at=reset):
                barras = janelas({'primary': jan(reset=reset)})
                self.assertEqual(len(barras), 1)
                self.assertIsNone(barras[0]['renova_epoch'])
        self.assertEqual(len(janelas({'primary': jan(), 'secondary': jan(10080), 'tertiary': jan(1440)})), 2)
        self.assertEqual(janelas({'tertiary': jan(1440)}), [])
        self.assertEqual([b['tipo'] for b in janelas({'primary': jan(usado=150), 'secondary': jan(10080)})],
                         ['semana'])
        self.assertEqual(janelas({'primary': [1], 'secondary': 'x'}), [])
        self.assertEqual(janelas(None), [])

    # 10
    def test_10_instante(self):
        instante = mod.instante
        self.assertEqual(instante('2033-05-18T03:33:20.000Z'), 2000000000.0)
        self.assertEqual(instante('2033-05-18T03:33:20+00:00'), 2000000000.0)
        self.assertEqual(instante('2033-05-18T06:33:20+03:00'), 2000000000.0)
        self.assertEqual(instante('2033-05-18T03:33:20'), 2000000000.0)  # sem fuso = UTC
        self.assertEqual(instante(1_700_000_000), 1700000000.0)
        self.assertIsInstance(instante(1_700_000_000), float)
        self.assertEqual(instante(1.5), 1.5)
        for invalido in ('', 'ontem', None, True, False, -5, 0, math.inf, math.nan, 10 ** 400, [], {}):
            with self.subTest(valor=invalido):
                self.assertIsNone(instante(invalido))

    # 11
    def test_11_linhas_hostis_nao_derrubam_a_leitura(self):
        novo = iso(AGORA - 10)
        rl = limites(p5=95.0)
        hostis = [
            '{"timestamp": "' + novo + '", "rate_limits": {quebrado',
            json.dumps(['rate_limits', {'rate_limits': rl}]),
            '"rate_limits"',
            json.dumps({'timestamp': novo, 'type': 'event_msg', 'payload': 'texto', 'rate_limits': rl}),
            json.dumps({'timestamp': novo, 'type': 'event_msg', 'payload': ['rate_limits', rl]}),
            json.dumps({'timestamp': novo, 'type': 'response_item',
                        'payload': {'type': 'token_count', 'rate_limits': rl}}),
            json.dumps({'timestamp': novo, 'type': 'event_msg', 'payload': {'type': 'outro', 'rate_limits': rl}}),
            json.dumps({'timestamp': novo, 'type': 'event_msg',
                        'payload': {'type': 'token_count', 'rate_limits': [rl]}}),
            json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'rate_limits': rl}}),
            json.dumps(evento('ontem', rl)),
            json.dumps(evento(novo, {'primary': {'used_percent': 'muito', 'window_minutes': 300}})),
            '[' * 50000 + '"rate_limits"',
            '\udcff"rate_limits"\x00',
        ]
        valida = linha_limites(iso(AGORA - 500), p5=12.0)
        escrever(self.raiz, 'a.jsonl', hostis[:6] + [valida] + hostis[6:12])
        (self.raiz / 'b.jsonl').write_bytes(b'\xff\xfe"rate_limits"\n' + valida.encode('utf-8') + b'\n')
        d = self.dado()
        self.assertEqual(d['estado'], 'ok')
        self.assertEqual(self.resumo(), (float(AGORA - 500), 12.0))
        r = mod.ultimo_evento(hostis, AGORA)
        self.assertIsNone(r)

    # 12
    @unittest.skipIf(zstd_std is None, SEM_ZSTD)
    def test_12_zst_corrompido_e_ignorado(self):
        lixo = bytes((i * 37 + 11) % 256 for i in range(4096))
        (self.raiz / 'aleatorio.jsonl.zst').write_bytes(lixo)
        (self.raiz / 'magico.jsonl.zst').write_bytes(b'\x28\xb5\x2f\xfd' + lixo)
        inteiro = escrever(self.base, 'inteiro.jsonl.zst', [linha_limites(iso(AGORA - 5), p5=99.0)] * 50)
        dados = inteiro.read_bytes()
        (self.raiz / 'truncado.jsonl.zst').write_bytes(dados[:len(dados) // 2])
        for nome in ('aleatorio.jsonl.zst', 'magico.jsonl.zst', 'truncado.jsonl.zst'):
            os.utime(self.raiz / nome, (AGORA - 10, AGORA - 10))
        escrever(self.raiz, 'bom.jsonl', [linha_limites(iso(AGORA - 300), p5=33.0)], mtime=AGORA - 9000)
        escrever(self.raiz, 'bom.jsonl.zst', [linha_limites(iso(AGORA - 900), p5=44.0)], mtime=AGORA - 9500)
        d = self.dado()
        self.assertEqual(d['estado'], 'ok')
        self.assertEqual(self.resumo(), (float(AGORA - 300), 33.0))

    # 13
    @unittest.skipIf(zstd_std is None, SEM_ZSTD)
    def test_13_sem_zstd_os_zst_sao_ignorados(self):
        escrever(self.raiz, 'x.jsonl.zst', [linha_limites(iso(AGORA - 10), p5=88.0)], mtime=AGORA - 10)
        escrever(self.raiz, 'y.jsonl', [linha_limites(iso(AGORA - 1000), p5=11.0)], mtime=AGORA - 1000)
        self.assertEqual(self.resumo(), (float(AGORA - 10), 88.0))  # controle: o .zst vale com zstd
        with patch.object(mod, 'zstd', None), patch.object(mod, '_linhas', wraps=mod._linhas) as espiao:
            resumo = self.resumo()
        self.assertEqual(resumo, (float(AGORA - 1000), 11.0))
        self.assertEqual([Path(c.args[0]).name for c in espiao.call_args_list], ['y.jsonl'])

    # 14
    def test_14_limite_de_bytes_por_arquivo(self):
        self.assertEqual(mod.MAX_BYTES_POR_ARQUIVO, 64 * 1024 * 1024)
        curta = json.dumps(evento(iso(AGORA - 10), {'primary': {'used_percent': 5, 'window_minutes': 300}}))
        enchimento = json.dumps({'type': 'session_meta', 'payload': {'cwd': 'x' * 120}})
        self.assertLess(len(curta) + 1, 200)
        self.assertLess(len(enchimento) + 1, 200)
        self.assertGreater(len(enchimento) + 1 + len(curta) + 1, 200)
        tarde = self.base / 'tarde'
        cedo = self.base / 'cedo'
        escrever(tarde, 'a.jsonl', [enchimento, curta])
        escrever(cedo, 'a.jsonl', [curta, enchimento])
        self.assertEqual(self.dado(tarde)['estado'], 'ok')  # controle sem o limite reduzido
        with patch.object(mod, 'MAX_BYTES_POR_ARQUIVO', 200):
            self.assertEqual(self.ler(tarde), {mod.CHAVE: mod.VAZIO})
            self.assertEqual(self.dado(cedo)['estado'], 'ok')
            self.assertEqual(list(mod._linhas(tarde / 'a.jsonl')), [enchimento + '\n'])
        uma_linha = escrever(self.base, 'longa.jsonl', ['y' * 1000])
        with patch.object(mod, 'MAX_BYTES_POR_ARQUIVO', 200):
            self.assertEqual(list(mod._linhas(uma_linha)), [])

    # 15
    def test_15_erro_ao_listar(self):
        self.assertEqual(mod.ERRO_LISTAR, 'cota Codex local: pasta de sessões sem leitura')
        escrever(self.raiz, 'a.jsonl', [linha_limites(iso(AGORA - 10))])
        with patch.object(Path, 'rglob', side_effect=OSError('negado')):
            self.assertEqual(self.ler(), {'erro': mod.ERRO_LISTAR})

        def listagem_que_falha(*_args, **_kwargs):
            yield self.raiz / 'a.jsonl'
            raise PermissionError('negado no meio da listagem')

        with patch.object(Path, 'rglob', side_effect=listagem_que_falha):
            self.assertEqual(self.ler(), {'erro': mod.ERRO_LISTAR})

    # 16
    def test_16_privacidade(self):
        with tempfile.TemporaryDirectory(prefix='segredo-sintetico-dir-') as base:
            raiz = Path(base) / 'segredo-sintetico-subpasta'
            meta = json.dumps({'timestamp': iso(AGORA - 900), 'type': 'session_meta',
                               'payload': {'id': 'segredo-sintetico-id-sessao',
                                           'cwd': 'C:/segredo-sintetico-cwd',
                                           'originator': 'segredo-sintetico-origem'}})
            rl = limites()
            rl['limit_id'] = 'segredo-sintetico-limit-id'
            rl['plan_type'] = 'segredo-sintetico-plano'
            rl['credits'] = {'has_credits': True, 'balance': 'segredo-sintetico-credito'}
            rl['primary']['nome'] = 'segredo-sintetico-campo-extra'
            escrever(raiz, 'rollout-segredo-sintetico-arquivo.jsonl', [meta, json.dumps(evento(iso(AGORA - 60), rl))])
            saida = mod.ler({'sessoes_dir': str(raiz), 'agora': AGORA})
            nome_tmp = Path(base).name
        texto = json.dumps(saida, allow_nan=False)
        self.assertNotIn('segredo-sintetico', texto)
        self.assertNotIn(nome_tmp, texto)
        self.assertNotIn('rollout', texto)
        self.assertEqual(set(saida), {mod.CHAVE})
        d = saida[mod.CHAVE]
        self.assertEqual(set(d), {'estado', 'observado_epoch', 'barras'})
        self.assertEqual(d['estado'], 'ok')
        self.assertEqual(len(d['barras']), 2)
        for barra in d['barras']:
            self.assertEqual(set(barra), {'tipo', 'usado_pct', 'renova_epoch', 'janela_min'})

    # extra (§1 passo 3): arquivo que some entre a listagem e a consulta da data
    def test_17_arquivo_que_some_apos_listagem_e_pulado(self):
        escrever(self.raiz, 'a.jsonl', [linha_limites(iso(AGORA - 10), p5=21.0)])
        original = Path.rglob

        def rglob(caminho, padrao, **kwargs):
            itens = list(original(caminho, padrao, **kwargs))
            return itens + [caminho / 'sumiu.jsonl'] if padrao == '*.jsonl' else itens

        with patch.object(Path, 'rglob', rglob):
            self.assertEqual(self.resumo(), (float(AGORA - 10), 21.0))


if __name__ == '__main__':
    unittest.main()
