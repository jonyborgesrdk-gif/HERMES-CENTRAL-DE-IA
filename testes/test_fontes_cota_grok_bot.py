"""Fonte cota_grok_bot (E19, CONTRATOS §9): só blobs sintéticos em pasta temporária."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.fontes import cota_grok_bot as mod

AGORA = 2_000_000_000
LIDO_MS = (AGORA - 600) * 1000
RESET_MS = (AGORA + 86400) * 1000
CHAVE_OPACA = 'segredo-sintetico-chave-opaca-7f3a'


def registro(lido_ms=LIDO_MS, pct=41.5, reset_ms=RESET_MS, **mais):
    usage = {'percentUsed': pct, 'nextResetMs': reset_ms}
    usage.update(mais)
    return {'readAtMs': lido_ms, 'usage': usage}


def blob_real(lido_ms=LIDO_MS, pct=41.5, reset_ms=RESET_MS, chave=CHAVE_OPACA):
    return {'schemaVersion': 3, 'value': {chave: registro(lido_ms, pct, reset_ms)}}


def escrever(pasta, nome, conteudo, mtime=None):
    """Grava o blob (objeto vira JSON; texto e bytes vão como estão)."""
    caminho = Path(pasta) / nome
    if isinstance(conteudo, bytes):
        dados = conteudo
    elif isinstance(conteudo, str):
        dados = conteudo.encode('utf-8')
    else:
        dados = json.dumps(conteudo).encode('utf-8')
    with open(mod.caminho_longo(caminho), 'wb') as saida:
        saida.write(dados)
    if mtime is not None:
        os.utime(mod.caminho_longo(caminho), (mtime, mtime))
    return caminho


class CotaGrokBotTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.pasta = self.base / 'sand-client-persistence'
        self.pasta.mkdir()
        # Nenhum teste pode cair na pasta real do usuário (%APPDATA%).
        guarda = patch.dict(os.environ, {'APPDATA': str(self.base / 'appdata-falso')})
        guarda.start()
        self.addCleanup(guarda.stop)

    def ler(self, pasta=None):
        return mod.ler({'grok_bot_dir': str(pasta or self.pasta), 'agora': AGORA})

    def dado(self, pasta=None):
        return self.ler(pasta)[mod.CHAVE]

    def assertSemDado(self, pasta=None):
        self.assertEqual(self.ler(pasta), {'cota_grok_bot': mod.VAZIO})

    # 1
    def test_01_pasta_inexistente(self):
        self.assertIsNone(mod.pasta_padrao({}))
        self.assertIsNone(mod.pasta_padrao({'APPDATA': ''}))
        self.assertEqual(mod.pasta_padrao({'APPDATA': 'X'}),
                         os.path.join('X', 'Grok Bot', 'sand-client-persistence'))
        self.assertEqual(mod.VAZIO, {'estado': 'sem_dado', 'observado_epoch': None, 'barras': []})
        self.assertSemDado(self.pasta / 'nao-existe')
        arquivo = escrever(self.base, 'nao-e-pasta.blob', blob_real())
        self.assertSemDado(arquivo)

    # 2
    def test_02_blob_no_formato_real(self):
        escrever(self.pasta, 'a.blob', blob_real())
        d = self.dado()
        self.assertEqual(d['estado'], 'ok')
        self.assertEqual(d['observado_epoch'], LIDO_MS / 1000)
        self.assertEqual(d['barras'], [{'tipo': 'janela', 'usado_pct': 41.5,
                                        'renova_epoch': RESET_MS / 1000, 'janela_min': None}])
        self.assertIsInstance(d['barras'][0]['usado_pct'], float)

    # 3
    def test_03_uso_no_proprio_objeto(self):
        escrever(self.pasta, 'b.blob', registro(pct=12))
        d = self.dado()
        self.assertEqual(d['estado'], 'ok')
        self.assertEqual(d['observado_epoch'], LIDO_MS / 1000)
        self.assertEqual(d['barras'][0]['usado_pct'], 12.0)
        self.assertIsInstance(d['barras'][0]['usado_pct'], float)

    # 4
    def test_04_maior_read_at_ms_vence(self):
        novo_ms = LIDO_MS + 60_000
        # O registro mais novo está no arquivo de modificação mais ANTIGA e tem o MENOR percentual.
        escrever(self.pasta, 'velho.blob', blob_real(lido_ms=novo_ms, pct=5.0), mtime=AGORA - 9000)
        escrever(self.pasta, 'novo.blob', blob_real(lido_ms=LIDO_MS, pct=90.0), mtime=AGORA - 10)
        d = self.dado()
        self.assertEqual(d['observado_epoch'], novo_ms / 1000)
        self.assertEqual(d['barras'][0]['usado_pct'], 5.0)

    # 5
    def test_05_blobs_que_nao_contam(self):
        hostis = {
            'json-quebrado.blob': '{"schemaVersion": 3, "value": {',
            'lista.blob': [registro()],
            'value-texto.blob': {'schemaVersion': 3, 'value': 'texto'},
            'sem-read-at.blob': {'value': {'k': {'usage': {'percentUsed': 10}}}},
            'read-at-bool.blob': {'value': {'k': {'readAtMs': True, 'usage': {'percentUsed': 10}}}},
            'read-at-texto.blob': {'value': {'k': {'readAtMs': str(LIDO_MS),
                                                   'usage': {'percentUsed': 10}}}},
            'read-at-infinito.blob': '{"value": {"k": {"readAtMs": Infinity, '
                                     '"usage": {"percentUsed": 10}}}}',
            'read-at-zero.blob': registro(lido_ms=0),
            'pct-acima.blob': registro(pct=100.5),
            'pct-abaixo.blob': registro(pct=-1),
            'pct-bool.blob': registro(pct=True),
            'pct-texto.blob': registro(pct='41.5'),
            'pct-nan.blob': '{"readAtMs": %d, "usage": {"percentUsed": NaN}}' % LIDO_MS,
            'aviso.blob': {'schemaVersion': 3, 'value': {'k': {
                'thresholdPercent': 80, 'dismissedAtPercent': 82, 'nextResetMs': RESET_MS}}},
        }
        for nome, conteudo in hostis.items():
            escrever(self.pasta, nome, conteudo)
        self.assertSemDado()
        # Os mesmos blobs não atrapalham um registro válido ao lado.
        escrever(self.pasta, 'valido.blob', blob_real(pct=33.0))
        self.assertEqual(self.dado()['barras'][0]['usado_pct'], 33.0)

    # 6
    def test_06_read_at_no_futuro(self):
        escrever(self.pasta, 'futuro.blob', blob_real(lido_ms=(AGORA + 301) * 1000, pct=99.0))
        self.assertSemDado()
        escrever(self.pasta, 'passado.blob', blob_real(lido_ms=LIDO_MS, pct=20.0))
        d = self.dado()
        self.assertEqual(d['observado_epoch'], LIDO_MS / 1000)
        self.assertEqual(d['barras'][0]['usado_pct'], 20.0)
        escrever(self.pasta, 'limite.blob', blob_real(lido_ms=(AGORA + 300) * 1000, pct=70.0))
        d = self.dado()
        self.assertEqual(d['observado_epoch'], AGORA + 300)
        self.assertEqual(d['barras'][0]['usado_pct'], 70.0)

    # 7
    def test_07_next_reset_invalido(self):
        casos = {'ausente': None, 'zero': 0, 'texto': str(RESET_MS)}
        for nome, reset in casos.items():
            with self.subTest(nome):
                usage = {'percentUsed': 41.5}
                if nome != 'ausente':
                    usage['nextResetMs'] = reset
                escrever(self.pasta, 'u.blob', {'value': {'k': {'readAtMs': LIDO_MS, 'usage': usage}}})
                d = self.dado()
                self.assertEqual(d['estado'], 'ok')
                self.assertEqual(d['barras'], [{'tipo': 'janela', 'usado_pct': 41.5,
                                                'renova_epoch': None, 'janela_min': None}])

    # 8
    def test_08_so_arquivos_blob(self):
        escrever(self.pasta, 'sand-secrets.json', blob_real())
        escrever(self.pasta, 'sem-extensao', blob_real())
        escrever(self.pasta, 'quase.blob.bak', blob_real())
        self.assertSemDado()
        with patch.object(mod, 'MAX_BYTES', 50):
            escrever(self.pasta, 'grande.blob', blob_real())
            self.assertGreater(os.path.getsize(self.pasta / 'grande.blob'), 50)
            self.assertSemDado()
        self.assertEqual(self.dado()['estado'], 'ok')

    def test_08b_extensao_sem_diferenciar_maiusculas(self):
        escrever(self.pasta, 'MAIUSCULO.BLOB', blob_real(pct=8.0))
        self.assertEqual(self.dado()['barras'][0]['usado_pct'], 8.0)

    # 9
    def test_09_nome_longo(self):
        pasta = self.pasta / ('p' * 80)
        pasta.mkdir()
        nome = ('n' * 195) + '.blob'
        self.assertEqual(len(nome), 200)
        caminho = escrever(pasta, nome, blob_real(pct=55.0))
        self.addCleanup(os.remove, mod.caminho_longo(caminho))
        self.assertGreater(len(os.path.abspath(str(caminho))), 260)
        d = self.dado(pasta)
        self.assertEqual(d['estado'], 'ok')
        self.assertEqual(d['barras'][0]['usado_pct'], 55.0)

    def test_09b_caminho_longo(self):
        if os.name == 'nt':
            absoluto = os.path.abspath(str(self.pasta))
            self.assertEqual(mod.caminho_longo(self.pasta), '\\\\?\\' + absoluto)
            # Caminho absoluto que já começa com duas barras invertidas volta sem prefixo novo.
            for ja_longo in ('\\\\?\\' + absoluto, '\\\\servidor\\pasta'):
                self.assertEqual(mod.caminho_longo(ja_longo), os.path.abspath(ja_longo))
        else:
            self.assertEqual(mod.caminho_longo(self.pasta), str(self.pasta))

    # 10
    def test_10_erro_ao_listar(self):
        escrever(self.pasta, 'a.blob', blob_real())
        with patch.object(mod.os, 'scandir', side_effect=OSError('sem permissão')):
            self.assertEqual(self.ler(), {'erro': mod.ERRO_LISTAR})

    # 11
    def test_11_privacidade(self):
        nome = 'segredo-sintetico-nome-de-arquivo.blob'
        escrever(self.pasta, nome, {'schemaVersion': 3, 'value': {CHAVE_OPACA: {
            'readAtMs': LIDO_MS,
            'readForTeamId': 'segredo-sintetico-time',
            'grokPlanLabel': 'segredo-sintetico-rotulo',
            'unifiedPlanName': 'segredo-sintetico-plano',
            'usage': {'percentUsed': 41.5, 'nextResetMs': RESET_MS,
                      'prepaidBalanceCents': 'segredo-sintetico-saldo',
                      'grokPlanLabel': 'segredo-sintetico-rotulo-uso'},
        }}})
        saida = self.ler()
        d = saida[mod.CHAVE]
        self.assertEqual(d['estado'], 'ok')
        texto = json.dumps(saida)
        for proibido in ('segredo-sintetico', CHAVE_OPACA, 'readForTeamId', 'grokPlanLabel',
                         'unifiedPlanName', 'prepaidBalanceCents', nome, self.base.name):
            self.assertNotIn(proibido, texto)
        self.assertEqual(set(saida), {mod.CHAVE})
        self.assertEqual(set(d), {'estado', 'observado_epoch', 'barras'})
        self.assertEqual(set(d['barras'][0]), {'tipo', 'usado_pct', 'renova_epoch', 'janela_min'})


if __name__ == '__main__':
    unittest.main()
