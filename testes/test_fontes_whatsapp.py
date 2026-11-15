"""Ponte Hermes: fixtures locais, sem acessar gateway/sessão reais."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class WhatsAppTests(unittest.TestCase):
    def setUp(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.caminho = Path(pasta.name) / 'gateway_state.json'
        self.ctx = {'whatsapp_path': self.caminho, 'agora': 1000,
                    'pid_vivo': lambda pid: pid == 123}

    def fonte(self):
        self.assertIsNotNone(importlib.util.find_spec('lib.fontes.whatsapp'))
        return importlib.import_module('lib.fontes.whatsapp')

    def gravar(self, estado='connected', **extras):
        plataforma = {'state': estado, 'needs_attention': False,
                      'updated_at': '1970-01-01T00:16:30+00:00', **extras}
        self.caminho.write_text(json.dumps({'pid': 123, 'gateway_state': 'running',
            'updated_at': '1970-01-01T00:16:35+00:00',
            'platforms': {'whatsapp': plataforma}}), encoding='utf-8')

    def test_connected(self):
        self.gravar()
        d = self.fonte().ler(self.ctx)['whatsapp']
        self.assertEqual(d['estado'], 'connected')
        self.assertTrue(d['pid_vivo'])
        self.assertFalse(d['needs_attention'])
        self.assertEqual(d['gateway_state'], 'running')
        self.assertEqual(d['idade_s'], 5)
        self.assertEqual(d['updated_at'], '1970-01-01T00:16:30+00:00')

    def test_retrying_aceito_e_estado_estranho_desconhecido(self):
        for estado, esperado in (('retrying', 'retrying'), ('inventado', 'desconhecido')):
            with self.subTest(estado=estado), patch.object(Path, 'read_text', return_value=json.dumps({
                    'platforms': {'whatsapp': {'state': estado}}})):
                self.assertEqual(self.fonte().ler(self.ctx)['whatsapp']['estado'], esperado)

    def test_disconnected_e_atencao(self):
        self.gravar('disconnected', needs_attention=True, error_code='logged_out')
        d = self.fonte().ler(self.ctx)['whatsapp']
        self.assertEqual(d['estado'], 'disconnected')
        self.assertTrue(d['needs_attention'])
        self.assertEqual(d['error_code'], 'logged_out')

    def test_arquivo_ausente(self):
        d = self.fonte().ler(self.ctx)
        self.assertEqual(d['whatsapp']['estado'], 'ausente')
        self.assertTrue(d['erro'])

    def test_json_invalido_e_estrutura_invalida(self):
        for texto in ('{', '[]', '{"platforms": []}', '{"platforms":{"whatsapp":[]}}'):
            with self.subTest(texto=texto):
                self.caminho.write_text(texto, encoding='utf-8')
                d = self.fonte().ler(self.ctx)
                self.assertEqual(d['whatsapp']['estado'], 'erro')
                self.assertTrue(d['erro'])

    def test_sem_plataforma(self):
        self.caminho.write_text('{"platforms":{}}', encoding='utf-8')
        self.assertEqual(self.fonte().ler(self.ctx)['whatsapp']['estado'], 'ausente')

    def test_pid_morto(self):
        self.gravar()
        self.ctx['pid_vivo'] = lambda pid: False
        self.assertFalse(self.fonte().ler(self.ctx)['whatsapp']['pid_vivo'])

    def test_pid_nativo_sem_subprocesso(self):
        fonte = self.fonte()
        with patch('subprocess.run', side_effect=AssertionError('não criar processos')):
            self.assertTrue(fonte.pid_vivo(os.getpid()))
            for pid in (None, -1, 0, True, '123', 2**60):
                self.assertFalse(fonte.pid_vivo(pid))

    def test_updated_at_invalido_extremo_ou_naive_pede_atencao(self):
        valores = ('0001-01-01', '0001-01-01T00:00:00',
                   '0001-01-01T00:00:00+00:00', '9999-12-31',
                   '9999-12-31T23:59:59+00:00', '1970-01-01T00:16:35',
                   'lixo', 123, None)
        for valor in valores:
            with self.subTest(updated_at=valor):
                self.gravar()
                bruto = json.loads(self.caminho.read_text(encoding='utf-8'))
                bruto['updated_at'] = valor
                self.caminho.write_text(json.dumps(bruto), encoding='utf-8')
                d = self.fonte().ler(self.ctx)['whatsapp']
                self.assertIsNone(d['idade_s'])
                self.assertIs(d['needs_attention'], True)

    def test_updated_at_da_ponte_invalido_nao_exporta_data(self):
        for valor in ('0001-01-01T00:00:00', '9999-12-31T23:59:59+00:00',
                      '1970-01-01T00:16:30', 'lixo', 123):
            with self.subTest(updated_at=valor):
                self.gravar(updated_at=valor)
                d = self.fonte().ler(self.ctx)['whatsapp']
                self.assertIsNone(d['updated_at'])
                self.assertIs(d['needs_attention'], True)

    def test_error_message_nunca_exporta_identificadores(self):
        uuid = '12345678-1234-5678-9abc-123456789abc'
        telefone = '5511999998888'
        jid = telefone + '@s.whatsapp.net'
        mensagem = f'Falha chat_id={uuid} jid={jid} telefone={telefone} uuid={uuid}'
        self.gravar(error_code='logged_out', error_message=mensagem)
        saida = self.fonte().ler(self.ctx)
        texto = json.dumps(saida)
        for proibido in ('error_message', 'chat_id=', uuid, jid, telefone, mensagem):
            self.assertNotIn(proibido, texto)
        self.assertEqual(saida['whatsapp']['error_code'], 'logged_out')
        self.assertIs(saida['whatsapp']['tem_mensagem_erro'], True)

    def test_tem_mensagem_erro_e_booleano_sem_texto_livre(self):
        for valor, esperado in ((None, False), ('', False), ('falha privada', True),
                                (123, False), ({'chat_id': 'privado'}, False)):
            with self.subTest(valor=valor):
                self.gravar(error_message=valor)
                d = self.fonte().ler(self.ctx)['whatsapp']
                self.assertIs(d['tem_mensagem_erro'], esperado)
                self.assertNotIn('error_message', d)
        self.gravar()
        self.assertIs(self.fonte().ler(self.ctx)['whatsapp']['tem_mensagem_erro'], False)

    def test_exporta_so_allowlist_sem_mensagem_livre(self):
        segredo = 'sk-' + 'A' * 32
        self.gravar(error_message='Falha ' + segredo + ' 5511999998888@s.whatsapp.net '
                    '+55 (11) 99999-8888 pessoa@example.org ' + 'x' * 400,
                    jid='nao-exportar', chat_id='nao-exportar', retrying_since='privado')
        bruto = json.loads(self.caminho.read_text(encoding='utf-8'))
        bruto.update(served_profiles=['privado'], argv=['privado'], phone='privado')
        self.caminho.write_text(json.dumps(bruto), encoding='utf-8')
        d = self.fonte().ler(self.ctx)['whatsapp']
        self.assertEqual(set(d), {'estado', 'needs_attention', 'error_code', 'tem_mensagem_erro',
                                 'updated_at', 'gateway_state', 'pid_vivo', 'idade_s'})
        texto = json.dumps(d)
        for proibido in (segredo, '5511999998888', '99999-8888', 'pessoa@', 'privado', 'nao-exportar'):
            self.assertNotIn(proibido, texto)
        self.assertIs(d['tem_mensagem_erro'], True)
        self.assertNotIn('error_message', d)

    def test_campos_inesperados_nao_vazam_texto_pessoal(self):
        self.gravar('5511999998888@s.whatsapp.net', error_code={'jid': 'privado'},
                    updated_at='privado', error_message={'chat_id': 'privado'})
        d = self.fonte().ler(self.ctx)['whatsapp']
        self.assertEqual(d['estado'], 'desconhecido')
        self.assertIsNone(d['error_code'])
        self.assertIsNone(d['updated_at'])
        self.assertNotIn('privado', json.dumps(d))
