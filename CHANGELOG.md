---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-16
tags: [get-brolls]
---

# Changelog

## 2.3.5 — 2026-09-16

- Recusa JSONs de revisão baseados em decisões antigas, inclusive depois de rejeição ou de outra importação. Valida `reviewEpoch` além da assinatura do conteúdo, sem gravar parcialmente os itens de um lote inválido.
- Fixa conexões HTTP de APIs e downloads nos endereços públicos validados, preservando SNI, hostname e certificados HTTPS. Revalida DNS a cada nova tentativa; mantém redirects bloqueados e conecta diretamente, sem proxies automáticos do ambiente.
- Fixa as dependências Python nas versões instaladas pela CI anterior, registra o conjunto npm em `package-lock.json` e instala com `npm ci`. Os instaladores continuam usando `.venv/` e `.tools/` locais.
- Fixa as Actions pelos commits já utilizados na CI e prepara atualizações de dependências por PR com Dependabot.
- Acrescenta regressões offline para decisões obsoletas, DNS rebinding IPv4/IPv6, proxy, retries, redirects e validação TLS. Atualiza a orientação de migração.

## 2.3.4 — 2026-09-15

- Consolida todo o produto sob `scripts/getbrolls/`: CLI, provedores, Storyboard, coletor de pares Instagram e utilitários YouTube.
- Padroniza a marca pública como **GET B-ROLLS — ENGENHEIRO DE VÍDEO**.
- Faz o Storyboard gerado pela CLI usar a logo transparente oficial e remove o snapshot HTML preenchido que duplicava a interface real.
- Corrige o contact sheet para usar arquivo temporário portátil em macOS e Linux e cobre o comportamento com regressão automatizada.
- Adiciona descoberta de fontes Linux para manter título e numeração do contact sheet; `GB_FONT_FILE` permite definir uma fonte válida explicitamente.
- Torna a CLI nativa em Windows com trava `msvcrt`, descoberta da `.venv\\Scripts`, instalador e launcher Playwright em PowerShell.
- Promove macOS e Windows à matriz principal de CI; Linux permanece como plataforma secundária.
- Endurece o coletor Instagram com pinagem de DNS público, redirects desativados, nomes batch validados, saída confinada e publicação sem sobrescrever arquivos existentes.
- Torna o instalador PowerShell fail-fast e exercita a instalação completa em macOS e Windows na CI.
- Define UTF-8 explicitamente na CLI, nos arquivos e nos subprocessos para funcionar de forma consistente no Windows.
- Restringe configs Instagram a HTTPS público e impede que `output=` escape de `--config-output-root`.
- Mantém YouTube via yt-dlp sem API key e Instagram via navegador autorizado, dois streams, curl, FFmpeg e ffprobe.
- Instala dependências oficiais via PyPI e npm na máquina do usuário; nenhuma biblioteca de runtime ou sessão de navegador integra o repositório.
- Preview remoto preserva origem, autoria e intervalo absoluto. Fetch usa os mesmos bytes revisados depois da decisão humana e do registro das condições de uso.
- Corrige reuso de download parcial, descoberta da venv, runtime JavaScript e URLs Instagram com perfil antes de `/reel/`.
- Consolida README, GUIDE, QUALITY, AGENTS, segurança, contribuição e avisos de terceiros como documentação autônoma do produto.
- Define o repositório GitHub como fonte oficial da entrega; não há fluxo paralelo de pacote ZIP.
- Publica um README completo em inglês e adiciona navegação de idioma entre as duas versões.
- Credita **Bruno Moreira — Engenheiro de Vídeo** como autor e mantenedor, com o Instagram [@zbrunomoreira](https://www.instagram.com/zbrunomoreira/) nas duas versões do README.

## Histórico anterior

- **2.3.3:** estabilização de downloads, bancos Pexels/Pixabay e prévias; não foi considerada a entrega consolidada.
- **2.3.2:** organização inicial da skill para Codex e Claude Code.
- **2.3.1:** recuperação do ledger, regras, logs, GIFs e separação interna da CLI.
- **2.3.0:** RULES por projeto, memória de referências, imagens locais e captura pelo navegador.
- **2.2.0:** Storyboard com revisão exportável/importável e prévias estáticas ou animadas.
- **2.1.0:** roteamento multi-fonte, aprovação, evidência de uso, corte e verificação.
