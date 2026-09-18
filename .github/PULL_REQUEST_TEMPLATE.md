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
- [ ] `bash scripts/check.sh` (ou `./scripts/check.ps1`) passou: `ruff`, `pyright`, espelho da skill, âncoras e suíte.
- [ ] Documentação afetada atualizada (GUIDE, README/README.en, SKILL, QUALITY, CHANGELOG). O espelho em `skills/get-brolls/SKILL.md` é gerado: rode `python3 scripts/gen_skill_mirror.py`, nunca edite à mão.
- [ ] Nenhuma chave, sessão, URL assinada, original ou projeto de cliente no diff.

`--check` valida pré-requisitos do instalador; não instala bibliotecas nem testa sessão/rede. `doctor` informa disponibilidade. Nenhum deles substitui um ensaio real da fonte afetada — descreva abaixo o que foi testado ao vivo, se houver.

## Evidências
