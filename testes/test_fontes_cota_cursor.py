"""Fonte cota_cursor (E19, CONTRATOS §10): só bancos SQLite sintéticos em pasta temporária."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from lib.fontes import cota_cursor as mod

PLANO = mod.CHAVE_PLANO
ASSINATURA = mod.CHAVE_ASSINATURA


def criar_banco(caminho, itens, wal=True, tabela=True):
    """Banco real com ItemTable; a conexão é fechada (o Windows trava arquivo aberto)."""
    con = sqlite3.connect(str(caminho))
    try:
        if wal:
            con.execute('PRAGMA journal_mode=WAL')
        if tabela:
            con.execute('CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)')
            con.executemany('INSERT INTO ItemTable (key, value) VALUES (?, ?)', list(itens))
        else:
            con.execute('CREATE TABLE OutraTabela (k TEXT)')
        con.commit()
    finally:
        con.close()
    return caminho


def listagem(pasta, com_tamanho=True):
    itens = []
    for nome in sorted(os.listdir(pasta)):
        itens.append((nome, os.path.getsize(os.path.join(pasta, nome))) if com_tamanho else nome)
    return itens


class CotaCursorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.pasta = self.base / 'globalStorage'
        self.pasta.mkdir()
        self.banco = self.pasta / 'state.vscdb'
        # Nenhum teste pode cair no banco real do usuário (%APPDATA%).
        guarda = patch.dict(os.environ, {'APPDATA': str(self.base / 'appdata-falso')})
        guarda.start()
        self.addCleanup(guarda.stop)

    def ler(self, banco=None):
        return mod.ler({'cursor_db': str(banco or self.banco), 'agora': 2_000_000_000})

    def ler_espiando(self, banco=None):
        original = sqlite3.connect
        with patch.object(mod.sqlite3, 'connect', wraps=original) as espiao:
            saida = self.ler(banco)
        self.assertEqual(espiao.call_count, 1)
        args, kwargs = espiao.call_args
        self.assertEqual(len(args), 1)
        self.assertEqual(kwargs, {'uri': True, 'timeout': 1})
        self.assertTrue(args[0].startswith('file:'))
        return saida, args[0]

    # 1
    def test_01_arquivo_inexistente(self):
        self.assertIsNone(mod.caminho_padrao({}))
        self.assertIsNone(mod.caminho_padrao({'APPDATA': ''}))
        self.assertEqual(mod.caminho_padrao({'APPDATA': 'X'}),
                         os.path.join('X', 'Cursor', 'User', 'globalStorage', 'state.vscdb'))
        self.assertEqual(mod.VAZIO, {'estado': 'sem_dado', 'plano': None, 'assinatura': None})
        self.assertEqual(self.ler(), {'cota_cursor': mod.VAZIO})
        self.assertEqual(self.ler(self.pasta), {'cota_cursor': mod.VAZIO})
        self.assertEqual(listagem(self.pasta), [])

    # 2
    def test_02_duas_chaves(self):
        criar_banco(self.banco, [(PLANO, 'pro_plus'), (ASSINATURA, 'active')])
        self.assertEqual(self.ler(), {'cota_cursor': {
            'estado': 'ok', 'plano': 'pro_plus', 'assinatura': 'active'}})

    # 3
    def test_03_valor_em_bytes(self):
        criar_banco(self.banco, [(PLANO, b'pro'), (ASSINATURA, b' trialing\n')])
        self.assertEqual(self.ler()[mod.CHAVE], {
            'estado': 'ok', 'plano': 'pro', 'assinatura': 'trialing'})

    # 4
    def test_04_valores_fora_da_lista(self):
        longo = 'texto-longo-sintetico-' * 40
        casos = [
            ('plano-secreto-sintetico', '<img>'),
            (longo, longo),
            ('PRO', 'Active'),
            (7, b'\xff\xfe-segredo-sintetico'),
        ]
        for plano, assinatura in casos:
            with self.subTest(plano=str(plano)[:30]):
                if self.banco.exists():
                    os.remove(self.banco)
                criar_banco(self.banco, [(PLANO, plano), (ASSINATURA, assinatura)])
                saida = self.ler()
                self.assertEqual(saida[mod.CHAVE], {
                    'estado': 'ok', 'plano': 'outra', 'assinatura': 'outra'})
                texto = json.dumps(saida)
                for original in ('plano-secreto-sintetico', '<img>', 'texto-longo-sintetico',
                                 'segredo-sintetico', 'PRO', 'Active'):
                    self.assertNotIn(original, texto)

    # 5
    def test_05_uma_ou_nenhuma_chave(self):
        criar_banco(self.banco, [(PLANO, 'ultra')])
        self.assertEqual(self.ler()[mod.CHAVE], {'estado': 'ok', 'plano': 'ultra', 'assinatura': None})
        os.remove(self.banco)
        criar_banco(self.banco, [(ASSINATURA, 'past_due')])
        self.assertEqual(self.ler()[mod.CHAVE], {'estado': 'ok', 'plano': None, 'assinatura': 'past_due'})
        os.remove(self.banco)
        criar_banco(self.banco, [('outra/chave', 'pro')])
        self.assertEqual(self.ler(), {'cota_cursor': mod.VAZIO})

    # 6
    def test_06_sem_tabela_ou_nao_e_sqlite(self):
        criar_banco(self.banco, [], tabela=False)
        self.assertEqual(self.ler(), {'erro': mod.ERRO_LER})
        lixo = self.pasta / 'lixo.vscdb'
        lixo.write_bytes(b'isto nao e um banco sqlite\n' * 50)
        self.assertEqual(self.ler(lixo), {'erro': mod.ERRO_LER})

    # 7a
    def test_07a_banco_fechado_nao_cria_auxiliar(self):
        criar_banco(self.banco, [(PLANO, 'business'), (ASSINATURA, 'active')])
        antes = listagem(self.pasta)
        self.assertEqual([nome for nome, _ in antes], ['state.vscdb'])
        saida, uri = self.ler_espiando()
        self.assertTrue(uri.endswith('?mode=ro&immutable=1'), uri)
        self.assertEqual(saida[mod.CHAVE]['plano'], 'business')
        self.assertEqual(listagem(self.pasta), antes)

    # 7b
    def test_07b_aplicativo_aberto_le_o_valor_novo(self):
        criar_banco(self.banco, [(PLANO, 'free'), (ASSINATURA, 'active')])
        escritora = sqlite3.connect(str(self.banco))
        self.addCleanup(escritora.close)
        escritora.execute('UPDATE ItemTable SET value = ? WHERE key = ?', ('pro', PLANO))
        escritora.commit()
        self.assertTrue(os.path.isfile(str(self.banco) + '-wal'))
        self.assertTrue(os.path.isfile(str(self.banco) + '-shm'))
        antes = listagem(self.pasta, com_tamanho=False)
        saida, uri = self.ler_espiando()
        self.assertTrue(uri.endswith('?mode=ro'), uri)
        self.assertNotIn('immutable', uri)
        self.assertEqual(saida[mod.CHAVE], {'estado': 'ok', 'plano': 'pro', 'assinatura': 'active'})
        self.assertEqual(listagem(self.pasta, com_tamanho=False), antes)

    # 7c
    def test_07c_so_um_auxiliar(self):
        criar_banco(self.banco, [(PLANO, 'team')])
        Path(str(self.banco) + '-wal').write_bytes(b'')
        antes = listagem(self.pasta, com_tamanho=False)
        self.assertEqual(antes, ['state.vscdb', 'state.vscdb-wal'])
        saida, uri = self.ler_espiando()
        self.assertTrue(uri.endswith('?mode=ro&immutable=1'), uri)
        self.assertEqual(saida[mod.CHAVE]['plano'], 'team')
        self.assertEqual(listagem(self.pasta, com_tamanho=False), antes)

    # 8
    def test_08_outras_chaves_nunca_saem(self):
        criar_banco(self.banco, [
            (PLANO, 'enterprise'), (ASSINATURA, 'canceled'),
            ('cursorAuth/accessToken', 'segredo-sintetico-token'),
            ('cursorAuth/cachedEmail', 'segredo-sintetico@exemplo.invalid'),
        ])
        saida = self.ler()
        texto = json.dumps(saida)
        self.assertNotIn('segredo-sintetico', texto)
        self.assertNotIn('accessToken', texto)
        self.assertEqual(set(saida), {mod.CHAVE})
        self.assertEqual(set(saida[mod.CHAVE]), {'estado', 'plano', 'assinatura'})
        self.assertEqual(saida[mod.CHAVE]['plano'], 'enterprise')

    # 9
    def test_09_caminho_com_espaco_e_cerquilha(self):
        pasta = self.base / 'pasta com espaco #1'
        pasta.mkdir()
        banco = criar_banco(pasta / 'state.vscdb', [(PLANO, 'free_trial'), (ASSINATURA, 'paused')])
        self.assertEqual(self.ler(banco)[mod.CHAVE], {
            'estado': 'ok', 'plano': 'free_trial', 'assinatura': 'paused'})
        self.assertEqual(listagem(pasta, com_tamanho=False), ['state.vscdb'])

    def test_enumerar(self):
        self.assertIsNone(mod.enumerar(None, mod.PLANOS))
        self.assertEqual(mod.enumerar(' pro ', mod.PLANOS), 'pro')
        self.assertEqual(mod.enumerar(b'unpaid', mod.ASSINATURAS), 'unpaid')
        self.assertEqual(mod.enumerar(3.5, mod.PLANOS), 'outra')
        self.assertEqual(mod.enumerar('', mod.PLANOS), 'outra')


if __name__ == '__main__':
    unittest.main()
