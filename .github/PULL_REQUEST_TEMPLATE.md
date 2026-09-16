## O que muda

Descreva a mudança e o problema que ela resolve. Referencie a issue (`Closes #N`) quando houver.

## Verificação

Execute e cole o resultado real (AGENTS.md, seção Verificação):

```sh
bash scripts/install.sh --check
python3 scripts/gb.py doctor
python3 -m unittest discover -s tests -v
```

No Windows PowerShell, troque o primeiro comando por `powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -Check` e use `python` nos dois seguintes.

- [ ] Suíte completa passou localmente (informe o total de testes).
- [ ] Regressão adicionada para o defeito corrigido ou para o comportamento novo.
- [ ] Documentação afetada atualizada (GUIDE, README/README.en, SKILL e espelho, QUALITY, CHANGELOG).
- [ ] Nenhuma chave, sessão, URL assinada, original ou projeto de cliente no diff.

`--check` valida pré-requisitos do instalador; não instala bibliotecas nem testa sessão/rede. `doctor` informa disponibilidade. Nenhum deles substitui um ensaio real da fonte afetada — descreva abaixo o que foi testado ao vivo, se houver.

## Evidências
