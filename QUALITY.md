---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, quality, qa, evidence]
---

# Qualidade e evidências — GET B-ROLLS 2.3.4

Este documento reúne o estado de qualidade, as regressões cobertas, os limites conhecidos e as evidências reais por provedor. Resultados ao vivo são registros datados, não promessa de disponibilidade futura nem aprovação editorial.

## QA da versão 2.3.4

### Estado atual

**Validação da árvore consolidada: 70 testes passaram localmente em 15/09/2026.** O repositório é a fonte oficial da entrega; não existe uma seleção paralela de arquivos ou pacote ZIP para manter sincronizado.

Uma revisão adversarial executou a suíte em fonte e cópia limpa, instalação completa, `doctor`, `--help`, sintaxe Bash e JavaScript, busca de caminhos locais/segredos e inspeção do branding. A instalação limpa reconheceu yt-dlp 2026.08.19, EJS 0.8.0, Playwright CLI 0.1.20, FFmpeg e ffprobe. Nenhuma biblioteca externa, credencial ou sessão de navegador faz parte do repositório.

### Evidência por capacidade

| Capacidade | Evidência | Limite |
|---|---|---|
| YouTube sem API key | Busca real, aquisição 0–3 s e GIF; nova instalação limpa repetiu trecho 3–5 s em 1920×1080/25fps | Um vídeo público; aprovação permaneceu pendente |
| Instagram pares | Captura no Chrome, curl dos dois canais, merge e decodificação integral reais; MP4 50,226 s, 1076×1912, H.264/AAC | Um Reel público; identidade dos canais conferida por asset e prévia |
| Instagram Chrome logado | Nova tentativa concluiu captura/CDN/merge e GIF de 5 s | Sessão do próprio usuário; sem copiar cookies para a skill |
| TikTok | Vídeo @nasa/7665358680627399966 baixado via instalação limpa; trecho 3 s, 576×1024/30fps, H.264/AAC; decodificação e GIF passaram | Link anterior indisponível no navegador; houve bloqueios/erros em tentativas anteriores |
| Pexels/Pixabay | Busca, refresh, download integral, GIF e corte técnico reais com chaves temporárias | Ensaio dos adapters na rodada anterior; sem aprovação editorial fictícia |
| Commons/NASA | Busca, refresh e HEAD de mídia reais na rodada anterior | Não equivalem a download integral de todos os itens |
| Prévia → coleta final | Fixture real FFmpeg, cache/hash/offset, aprovação sintética e corte verificado | Simulação explícita, não aprovação atribuída ao usuário |
| Instalação | venv + import yt-dlp/EJS; npm + Playwright --version; --check valida Node 22+ | Executáveis do sistema conforme GUIDE; não instala extensões/login |

### Regressões cobertas

- YouTube e fontes sociais têm transporte, sem exigir chave YouTube.
- URL Instagram pode conter perfil antes de `/reel/`.
- Preview remoto mantém origem, autor, aprovação pendente e intervalo original.
- Cache é reutilizado por hash e o fetch desconta `local_start_s`, preservando o trecho visto.
- A CLI encontra yt-dlp nos layouts `.venv/bin` e `.venv/Scripts`; a trava serial usa backend nativo de macOS/Linux e Windows.
- Utilitários opcionais YouTube encontram yt-dlp local, habilitam Node e usam arquivo temporário portátil em macOS/Linux; o coletor não reutiliza parcial de download fracassado.
- O coletor Instagram rejeita DNS privado/misto e IPv6 local, fixa o curl em IP público validado, recusa redirects, confina saídas batch e nunca sobrescreve um MP4 existente.
- Node antigo é rejeitado antes de instalar dependências.
- O instalador PowerShell valida o código de saída de venv, pip, imports, npm, Playwright e `doctor`; a CI executa instalação real em macOS e Windows/Python 3.13.
- Leitura, escrita, subprocessos e saída da CLI declaram UTF-8 explicitamente, sem depender da página de código padrão do Windows.
- CLI, coletor Instagram e utilitários YouTube vivem sob a única raiz `scripts/getbrolls/`.
- O Storyboard produzido por `review` incorpora a logo oficial e usa a mesma implementação coberta pela suíte; não há snapshot HTML paralelo preenchido com caso real.
- Arquivos de bibliotecas, fontes externas, mídia e credenciais não integram o repositório.
- Permanecem testes de recuperação do ledger, revisão obsoleta, rejeição, interrupção, proporção, GIF, regras e logs sem segredos.

### Verificação documental

Revisor independente conferiu transportes, runtime, download parcial, instalação, reuso de sessão, estrutura pública, Storyboard e documentação. O guia Instagram diferencia plugin do agente e extensão Playwright, mostra attach/tab-list/tab-select e captura por requests/response-body ou CDP. Não existe promessa de parser automático de captura: o agente opera o navegador autorizado e entrega os pares ao coletor.

### Limites preservados

YouTube, Instagram e TikTok têm agora pelo menos um ensaio real concluído nesta rodada. Isso não garante qualquer URL/sessão: o primeiro link TikTok estava indisponível no site e o extrator retornou bloqueio em tentativas anteriores. Reteste com URL disponível pelo navegador e com as dependências instaladas pelo [guia](GUIDE.md#instalação). Não confundir esses erros com ausência de implementação.

macOS e Windows são as plataformas principais. O Windows tem instalador e launcher Playwright em PowerShell, layout de venv próprio e trava nativa; os helpers Bash de YouTube são opcionais e ficam no caminho macOS/Linux. A [matriz remota da entrega](https://github.com/engenheirodevideo/get-brolls/actions/runs/35040887806) passou em macOS 3.11/3.13, Windows 3.11/3.13 e Ubuntu 3.13; nas combinações principais 3.13, executou também a instalação completa. A descoberta automática em novas sessões Codex/Claude continua dependente da instalação/configuração do agente destinatário. O Storyboard é gerado pelo próprio CLI, incorpora apenas a logo oficial e usa fontes do sistema. Aprovação editorial continua humana.

Comandos para repetir na pasta da skill:

```sh
bash scripts/install.sh --check
bash scripts/install.sh
python3 -m unittest discover -s tests -v
python3 scripts/gb.py doctor
```

No Windows PowerShell, use `scripts/install.ps1 -Check`, `scripts/install.ps1` e `python` nos comandos da suíte/CLI.

`doctor --live` executa buscas limitadas reais e pode consumir quota dos bancos configurados; não testa toda a captura Instagram/TikTok. Os detalhes de origem aparecem na seção [Rotas e evidências](#rotas-e-evidências--15092026).

### Revisão documental

README, GUIDE, SKILL, AGENTS, CHANGELOG, CONTRIBUTING, SECURITY e avisos de terceiros foram conferidos contra a árvore atual. `agents/openai.yaml` mantém a invocação `$get-brolls`; caminhos documentados apontam para o núcleo `scripts/getbrolls/`. A validação documental não substitui os ensaios ao vivo registrados abaixo.

## Rotas e evidências — 15/09/2026

YouTube usa yt-dlp, sem YouTube Data API e sem API key. Pexels/Pixabay usam suas próprias APIs; Instagram usa o navegador e o coletor de dois canais; TikTok usa URL completa e yt-dlp.

| Fonte | Ensaio real | Resultado |
|---|---|---|
| YouTube | Busca NASA Artemis sem variável de chave; preview 0–3 s e repetição 3–5 s com yt-dlp instalado em pasta limpa | Busca/download/decodificação/GIF passaram, 1920×1080, 25fps; pendente de revisão |
| Instagram | Reel DcMXl1IPNtB; Chrome → dois streams → curl → coletor/FFmpeg | Download e merge completos: 50,226009 s, 1076×1912, H.264/AAC, 16.528.141 bytes; decodificação integral e GIF 5 s passaram |
| TikTok | @nasa/7665358680627399966 descoberto no navegador, via yt-dlp instalado pelo GUIDE | Trecho 0–3 s: 576×1024/30fps, H.264/AAC, 269.139 bytes; decodificação e GIF passaram |
| Pexels | coffee, ID 31264406; busca/refresh/download | 1080×1920, 12 s, 1.682.522 bytes |
| Pixabay | coffee, ID 46989; busca/refresh/download | 1920×1080, 35 s, 12.258.063 bytes |
| Commons | earth, 1 resultado; refresh e HEAD | 200 video/webm; corpo não baixado nesse ensaio |
| NASA API | busca, refresh e HEAD | 200 video/mp4; corpo não baixado nesse ensaio |

### Procedência

- [YouTube — lançamento Artemis](https://www.youtube.com/watch?v=B_7EUmCxcvE): metadados públicos e trecho técnico; não houve aprovação editorial do agente.
- [Instagram — NASA Johnson](https://www.instagram.com/nasajohnson/reel/DcMXl1IPNtB/): Captura e download dos dois canais concluídos no Chrome logado, com o coletor desta pasta.
- [TikTok — NASA, Earthset](https://www.tiktok.com/@nasa/video/7665358680627399966): busca no navegador e aquisição do trecho passaram. A URL anterior @nasa_space9/7512513421288492334 aparece indisponível no próprio navegador; seu erro não certificava o estado de toda a plataforma.
- [Pexels — espresso, İsa Kılavuzoğlu](https://www.pexels.com/video/close-up-of-espresso-brewing-into-elegant-cup-31264406/).
- [Pixabay — 46989, NickyPe](https://pixabay.com/videos/id-46989/).

Pexels/Pixabay geraram GIFs de 5 s (1.030.433 / 720.171 bytes) e cortes técnicos de 2 s verificados com ffprobe e FFmpeg. As chaves fornecidas foram usadas só no processo; não integram código, documentação, configs distribuídos ou exemplos. Esses ensaios validam os adapters; não representam aprovação/licença inventada nem garantia de disponibilidade futura.

O coletor Instagram passou também ensaio de captura/CDN real nesta nova tentativa. Fixtures continuam cobrindo detecção de áudio duplicado e falha de transferência. Na captura real, o asset_id e a duração relacionaram os dois canais; seletores de faixa bytestart/byteend foram removidos sem alterar a assinatura. Configs privados não integram a skill.

### Dependências oficiais

[yt-dlp/EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS) exige runtime suportado; o setup completo usa Node 22+ e instala yt-dlp com extras `default`. [Playwright CLI](https://github.com/microsoft/playwright-cli) é instalada via npm, separadamente do código da skill. [Pexels](https://www.pexels.com/api/documentation/) e [Pixabay](https://pixabay.com/api/docs/) documentam suas APIs; documentação não substitui teste autenticado.

A correção NASA promove HTTP somente no host exato images-assets.nasa.gov antes da validação. `doctor --live` busca nas fontes disponíveis e faz refresh de bancos; sem --live, inspeciona o ambiente local.
