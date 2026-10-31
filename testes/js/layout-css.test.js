'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const cotas = fs.readFileSync(path.join(__dirname, '../../publico/cotas.js'), 'utf8');
const index = fs.readFileSync(path.join(__dirname, '../../publico/index.html'), 'utf8');
function regra(fonte, seletor) {
  const i = fonte.indexOf(seletor + ' {');
  assert.ok(i >= 0, 'regra ausente: ' + seletor);
  return fonte.slice(i, fonte.indexOf('}', i));
}

test('B9 verso dos cards de cota não tem rolagem interna e divide a célula com a frente', () => {
  assert.doesNotMatch(regra(cotas, '.verso-corpo'), /overflow\s*:\s*(auto|scroll)/);
  assert.doesNotMatch(regra(cotas, '.card-verso'), /position:\s*absolute/);
  assert.match(cotas, /\.card-pilha > \.card, \.card-pilha > \.card-verso \{ grid-area: 1 \/ 1; \}/);
  // Sem altura mínima fixa: a célula cresce com o maior entre frente e verso.
  assert.doesNotMatch(cotas, /\.card-pilha > \.card \{[^}]*min-height/);
  assert.doesNotMatch(regra(cotas, '.card-verso'), /(^|[\s;])(max-)?height\s*:/);
  assert.doesNotMatch(regra(cotas, '.verso-corpo'), /(^|[\s;])(max-)?height\s*:/);
});

test('B5 gastos usa variáveis do tema (claro e escuro) nos controles e h3 menor que h2', () => {
  const controles = regra(index, '#gastos-cartao .gastos-seletor, #gastos-cartao .gastos-limpar');
  for (const v of ['--tinta', '--sup2', '--fio-forte']) assert.ok(controles.includes('var(' + v + ')'), v);
  const h3 = Number(/font-size:\s*(\d+)px/.exec(regra(index, '#gastos-cartao h3'))[1]);
  const h2 = Number(/font-size:\s*(\d+)px/.exec(regra(index, 'h2'))[1]);
  assert.ok(h3 < h2);
  assert.match(regra(index, '#gastos-cartao .gastos-coluna'), /white-space:\s*nowrap/);
});
