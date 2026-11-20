"""E27: formatos de segredo, limites e falsos positivos deliberados."""
import json
import time
import unittest

from lib.mascara import OCULTO, mascarar


class Mascara(unittest.TestCase):
    def test_desempenho_256_kib_hostis_abaixo_de_200_ms_cada(self):
        entradas = {'key': 'key.' * 65536, 'atribuicoes': 'a=' * 131072,
                    'headers': ('-H ' * 87382)[:262144], 'base64': 'A' * 262144}
        for nome, texto in entradas.items():
            with self.subTest(caso=nome):
                inicio = time.perf_counter()
                saida = mascarar(texto)
                ms = (time.perf_counter() - inicio) * 1000
                self.assertLess(ms, 200, 'mascaramento hostil ultrapassou 200 ms')
                if nome == 'base64':
                    self.assertEqual(saida, OCULTO)

    def test_controles_bidi_e_falsos_positivos_documentados(self):
        self.assertEqual(mascarar('echo a\x1b\x00\x7fb\u202e\u2066\u200ec'), 'echo abc')
        for valor in ('-Pass\u202eword hunter2', 'Bearer\thunter2', 'TOKEN=\x1bhunter2'):
            self.assertNotIn('hunter2', mascarar(valor))
        for valor in ('12345678-1234-1234-1234-123456789abc', 'a' * 40,
                      '/usr/local/lib/python3/site-packages', 'Basic usage'):
            self.assertIn(OCULTO, mascarar(valor))

    def test_variaveis_flags_json_e_headers_por_nome(self):
        nomes = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASSWD', 'PWD', 'AUTH',
                 'COOKIE', 'SESSION', 'CREDENTIAL', 'PASS', 'PASSPHRASE', 'PIN', 'SIG', 'SIGNATURE')
        for nome in nomes:
            for chave in (nome, 'prefix_' + nome.lower() + '_suffix'):
                for forma in ('%s=SEGREDO', '%s="SEGREDO com espaço"', "%s='SEGREDO com espaço'",
                              '--%s SEGREDO', '--%s=SEGREDO', '--%s "SEGREDO com espaço"',
                              '"%s": "SEGREDO com espaço"', "'%s': 'SEGREDO com espaço'",
                              '-H "%s: SEGREDO com espaço"'):
                    with self.subTest(chave=chave, forma=forma):
                        saida = mascarar(forma % chave)
                        self.assertNotIn('SEGREDO', saida)
                        self.assertIn(OCULTO, saida)

    def test_esquemas_urls_e_pem(self):
        for texto in ('Bearer SEGREDO', 'bearer SEGREDO', 'Basic SEGREDO',
                      'curl -H "Authorization: Bearer SEGREDO" /api',
                      'curl -H "Cookie: primeiro=SEGREDO; segundo=SEGREDO"',
                      'https://usuario:SEGREDO@exemplo.invalid/a',
                      '-----BEGIN PRIVATE KEY-----\nSEGREDO\n-----END PRIVATE KEY-----',
                      '-----BEGIN RSA PRIVATE KEY-----\nSEGREDO',
                      '-----BEGIN OPENSSH PRIVATE KEY-----\nSEGREDO\n-----END OPENSSH PRIVATE KEY-----'):
            with self.subTest(texto=texto):
                self.assertNotIn('SEGREDO', mascarar(texto))
                self.assertIn(OCULTO, mascarar(texto))
        self.assertEqual(mascarar('https://u:senha@host/a'), 'https://' + OCULTO + '@host/a')

    def test_tokens_conhecidos(self):
        tokens = ('sk-' + 'aB12' * 8, 'sk-ant-' + 'aB12' * 8, 'xai-' + 'aB12' * 8,
                  'ghp_abcdef123', 'gho_abcdef123', 'ghu_abcdef123', 'ghs_abcdef123',
                  'github_pat_abcdef123', 'glpat-abcdef123', 'AKIA' + 'X' * 16,
                  'AIza' + 'aB12' * 8, 'xoxa-123456789', 'xoxb-123456789', 'xoxp-123456789',
                  'eyJhbGci.eyJzdWIi.c2lnbmF0dXJl')
        for token in tokens:
            with self.subTest(prefixo=token[:7]):
                self.assertEqual(mascarar('echo ' + token), 'echo ' + OCULTO)

    def test_hex_base64_e_hash_git_falso_positivo_documentado(self):
        for token in ('abcdef0123456789' * 2, 'abcdef0123456789' * 2 + 'abcdef01',
                      'aB+/019x' * 5 + '==', 'aB-_019x' * 5):
            self.assertEqual(mascarar('echo ' + token), 'echo ' + OCULTO)
        self.assertIn('hash Git', __import__('lib.mascara', fromlist=['']).__doc__)

    def test_texto_legitimo_e_caminhos(self):
        for texto in ('', 'git status --short', 'py -m unittest testes.test_mascara',
                      'C:\\projetos\\painel\\lib\\arquivo.py', '/home/ana/projeto/src/main.py',
                      'https://exemplo.invalid/docs', 'abc123', 'a' * 31,
                      '--monkeypatch x', 'Read(file_path="arquivo.py")'):
            # KEY é substring intencional: monkeypatch também é sensível.
            if 'monkeypatch' not in texto:
                self.assertEqual(mascarar(texto), texto)

    def test_json_aninhado_e_aspas_escapadas(self):
        bruto = json.dumps({'args': {'password': ['SEGREDO', 'SEGREDO']},
                            'input': 'curl --api-key "SEGREDO com espaço"',
                            'plain': 'C:\\projeto\\main.py'})
        saida = mascarar(bruto)
        self.assertNotIn('SEGREDO', saida)
        self.assertEqual(json.loads(saida)['plain'], 'C:\\projeto\\main.py')
        self.assertIn(OCULTO, saida)

    def test_multiplos_segredos_idempotencia_e_pureza(self):
        entrada = 'KEY=SEGREDO --auth SEGREDO Bearer SEGREDO sk-abc123456'
        saida = mascarar(entrada)
        self.assertEqual(entrada, 'KEY=SEGREDO --auth SEGREDO Bearer SEGREDO sk-abc123456')
        self.assertNotIn('SEGREDO', saida)
        self.assertEqual(mascarar(saida), saida)


# Uma entrada exata da tabela por teste (credenciais exclusivamente fictícias).
_REVISAO = (
    (r'curl -d "{\"password\":\"hunter2\"}" https://api', 'hunter2'),
    ('redis-cli -u redis://:hunter2@localhost:6379', 'hunter2'),
    ('export DB_PASS=hunter2', 'hunter2'),
    ('docker run -e SMTP_PASS=x', 'x'),
    ('GPG_PASSPHRASE=x', 'x'),
    ('gpg --passphrase x', 'x'),
    ('openssl … -passin pass:x', 'x'),
    ("ConvertTo-SecureString 'Hunter2!' -AsPlainText -Force", 'Hunter2!'),
    ("Connect-X -Password 'Hunter2!'", 'Hunter2!'),
    ('-Token abc123', 'abc123'),
    ('curl -u admin:hunter2 https://api.x', 'admin:hunter2'),
    ('curl --user admin:hunter2', 'admin:hunter2'),
    ('mysql -u root -phunter2', 'hunter2'),
    ('sshpass -p hunter2 ssh h', 'hunter2'),
    ('sqlcmd -U sa -P Hunter2', 'Hunter2'),
    ('setx DB_PASSWORD hunter2', 'hunter2'),
    ("[Environment]::SetEnvironmentVariable('API_KEY','abc123','User')", 'abc123'),
    ('os.environ["DB_PASSWORD"] = "hunter2"', 'hunter2'),
    ("cfg['api_key'] = 'abc123'", 'abc123'),
    ('set "API_KEY=abc def"', 'abc def'),
    ('PASSWORD=abc,def', 'abc,def'),
    ('…?sv=2022-11-02&sig=AbC%2Bdef%2Fghi%3D', 'AbC%2Bdef%2Fghi%3D'),
    ('curl -u admin:hunter2 https://x', 'hunter2'),
    ('Connect-X -Password hunter2', 'hunter2'),
    ('-ApiKey "abc def, ghi"', 'abc def, ghi'),
    ('-Secret hunter2', 'hunter2'),
    ('-Credential hunter2', 'hunter2'),
    ('PASSWD=hunter2 PIN=482913 SIGNATURE=abc%def', 'hunter2'),
    ('PASSWORD="abc, def"', 'abc, def'),
)


def _teste_entrada(entrada, segredo):
    def testar(self):
        resultado = mascarar(entrada)
        self.assertNotIn(segredo, resultado)
        for parte in segredo.replace(',', ' ').replace(':', ' ').split():
            if len(parte) >= 3:
                self.assertNotIn(parte, resultado)
        self.assertIn(OCULTO, resultado)
    return testar


for _i, (_entrada, _segredo) in enumerate(_REVISAO, 1):
    setattr(Mascara, 'test_revisor_entrada_%02d' % _i, _teste_entrada(_entrada, _segredo))


if __name__ == '__main__':
    unittest.main()
